"""User-facing conversation flows: create a bot, pay, renew, wallet, my bots."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from builder.models import (
    BuilderUser,
    Card,
    Payment,
    PaymentKind,
    PaymentStatus,
    Plan,
    Tenant,
    TenantStatus,
    utcnow,
)
from builder.routing import on_state, route
from builder.services import billing
from builder.services.cfg import load_cfg
from builder.services.telegram_api import TelegramUnavailable, TokenError, looks_like_token
from builder.ui import Btn, Buttons
from builder.util import days_left, esc, fmt_date, fmt_money, parse_admin_ids, parse_int

log = logging.getLogger("builder.user")

STATUS_LABEL = {
    TenantStatus.PENDING: "🟡 در انتظار پرداخت",
    TenantStatus.RUNNING: "🟢 فعال",
    TenantStatus.STOPPED: "⏸ متوقف (توسط مدیریت)",
    TenantStatus.EXPIRED: "🔴 منقضی‌شده",
    TenantStatus.ERROR: "⚠️ خطا (در حال بررسی)",
}
BACK_HOME: Buttons = [[Btn("🏠 منوی اصلی", "u:home")]]


class FlowError(Exception):
    """A user-facing problem; the message is shown to the user as is."""


class UserFlows:
    # ── helpers ───────────────────────────────────────────────────────────────
    async def _user(self, user_id: int) -> BuilderUser:
        async with self.db.session() as s:
            user = await s.get(BuilderUser, user_id)
            return user or BuilderUser(id=user_id)

    def _need_state(self, ctx, *names: str):
        state = self.get_state(ctx.user_id)
        return state if state is not None and state.name in names else None

    async def _expired_session(self, ctx) -> None:
        await ctx.answer("⌛️ این مرحله منقضی شده است. دوباره شروع کنید.", alert=True)
        await self.home(ctx)

    @staticmethod
    def _bot_link(tenant: Tenant) -> str:
        return f"https://t.me/{tenant.bot_username}" if tenant.bot_username else ""

    # ── home / info ───────────────────────────────────────────────────────────
    @route("u:home")
    async def home(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        cfg = await self.cfg()
        user = await self._user(ctx.user_id)
        rows: Buttons = [
            [Btn("🤖 ساخت ربات جدید", "u:new"), Btn("📂 ربات‌های من", "u:bots")],
            [Btn("💰 کیف پول", "u:wallet"), Btn("🏷 تعرفه‌ها", "u:plans")],
        ]
        if cfg.bool("trial_enabled") and not user.trial_used:
            rows.append([Btn(f"🎁 تست رایگان {cfg.int('trial_days')} روزه", "u:trial")])
        rows.append([Btn("📞 پشتیبانی", "u:support")])
        if self.is_admin(ctx.user_id):
            rows.append([Btn("⚙️ پنل مدیریت", "a:home")])
        await self.show(ctx, esc(cfg.str("welcome_text")), rows)

    @route("u:support")
    async def support(self, ctx) -> None:
        cfg = await self.cfg()
        await self.show(ctx, f"📞 <b>پشتیبانی</b>\n\n{esc(cfg.str('support_text'))}", BACK_HOME)

    @route("u:plans")
    async def plans(self, ctx) -> None:
        async with self.db.session() as s:
            plans = await billing.list_plans(s)
        if not plans:
            await self.show(ctx, "فعلاً تعرفه‌ای تعریف نشده است.", BACK_HOME)
            return
        lines = ["🏷 <b>تعرفه‌ها</b>\n"]
        for plan in plans:
            lines.append(f"• <b>{esc(plan.name)}</b> — {fmt_money(plan.price)} / {plan.duration_days} روز")
        await self.show(ctx, "\n".join(lines), [[Btn("🤖 ساخت ربات", "u:new")], *BACK_HOME])

    # ── create a bot ──────────────────────────────────────────────────────────
    async def _start_creation(self, ctx, *, trial: bool) -> None:
        cfg = await self.cfg()
        async with self.db.session() as s:
            owned = await billing.count_owner_tenants(s, ctx.user_id)
            plans = await billing.list_plans(s)
            user = await s.get(BuilderUser, ctx.user_id)
        if not self.is_admin(ctx.user_id) and owned >= cfg.int("max_bots_per_user"):
            await self.show(ctx, f"⚠️ حداکثر {cfg.int('max_bots_per_user')} ربات می‌توانید داشته باشید.", BACK_HOME)
            return
        if trial and (not cfg.bool("trial_enabled") or (user and user.trial_used)):
            await self.show(ctx, "🎁 تست رایگان برای شما در دسترس نیست.", BACK_HOME)
            return
        if not trial and not plans:
            await self.show(ctx, "فعلاً تعرفه‌ای برای ساخت ربات تعریف نشده است.", BACK_HOME)
            return
        self.set_state(ctx.user_id, "new_token", trial=trial)
        await self.show(
            ctx,
            "🤖 <b>ساخت ربات جدید</b>\n\n"
            "۱) در تلگرام به @BotFather پیام بدهید و با /newbot یک ربات بسازید.\n"
            "۲) <b>توکن</b> ربات را همین‌جا ارسال کنید.\n\n"
            "⚠️ توکن را فقط برای همین ربات می‌فرستید؛ پیام شما پس از دریافت پاک می‌شود.",
            [[Btn("❌ انصراف", "u:home")]],
        )

    @route("u:new")
    async def new_bot(self, ctx) -> None:
        await self._start_creation(ctx, trial=False)

    @route("u:trial")
    async def trial(self, ctx) -> None:
        await self._start_creation(ctx, trial=True)

    @on_state("new_token")
    async def got_token(self, ctx, state) -> None:
        token = ctx.text.strip()
        await ctx.delete_incoming()
        if not looks_like_token(token):
            await ctx.reply("❌ قالب توکن درست نیست. توکن را از @BotFather کپی کنید و دوباره بفرستید.")
            return
        try:
            info = await self.check_token(token)
        except TokenError:
            await ctx.reply("❌ این توکن توسط تلگرام تأیید نشد. توکن را بررسی کنید.")
            return
        except TelegramUnavailable:
            await ctx.reply("⚠️ اتصال به تلگرام برقرار نشد. چند لحظه بعد دوباره بفرستید.")
            return
        if info.id == int(self.settings.bot_token.split(":")[0]):
            await ctx.reply("❌ این توکن مربوط به خود ربات‌ساز است. یک ربات جدید بسازید.")
            return
        async with self.db.session() as s:
            existing = await billing.find_live_tenant_by_bot(s, info.id)
        if existing is not None and not (existing.owner_id == ctx.user_id and existing.status == TenantStatus.PENDING):
            await ctx.reply("❌ این ربات قبلاً در ربات‌ساز ثبت شده است.")
            return
        state.data.update(token=token, bot_id=info.id, username=info.username)
        state.name = "new_admin"
        await ctx.reply(
            f"✅ ربات @{esc(info.username)} تأیید شد.\n\n"
            "حالا <b>آیدی عددی ادمین</b> ربات را بفرستید (چند ادمین را با کاما جدا کنید).\n"
            "آیدی عددی خود را می‌توانید از @userinfobot بگیرید.",
            [[Btn("🙋 خودم ادمین باشم", "u:new_self")], [Btn("❌ انصراف", "u:home")]],
        )

    @route("u:new_self")
    async def new_self(self, ctx) -> None:
        state = self._need_state(ctx, "new_admin")
        if state is None:
            await self._expired_session(ctx)
            return
        state.data["admin_ids"] = [ctx.user_id]
        await self._after_admins(ctx, state)

    @on_state("new_admin")
    async def got_admins(self, ctx, state) -> None:
        ids = parse_admin_ids(ctx.text)
        if ids is None:
            await ctx.reply("❌ آیدی عددی معتبر نیست. فقط عدد (۵ تا ۱۵ رقم) بفرستید؛ چند آیدی را با کاما جدا کنید.")
            return
        state.data["admin_ids"] = ids
        await self._after_admins(ctx, state)

    async def _after_admins(self, ctx, state) -> None:
        if state.data.get("trial"):
            await self._finish_trial(ctx, state)
            return
        async with self.db.session() as s:
            plans = await billing.list_plans(s)
        state.name = "pick_plan"
        state.data["mode"] = "order"
        rows = [[Btn(f"{p.name} — {fmt_money(p.price)} / {p.duration_days} روز", f"u:plan:{p.id}")] for p in plans]
        rows.append([Btn("❌ انصراف", "u:home")])
        await self.show(ctx, "📦 <b>تعرفه</b> مورد نظر را انتخاب کنید:", rows)

    # ── trial ─────────────────────────────────────────────────────────────────
    async def _create_tenant_checked(
        self, session, user_id: int, data: dict, *, plan_id: int | None, trial: bool = False, expires_at=None
    ) -> Tenant:
        cfg = await load_cfg(session)
        if not self.is_admin(user_id) and await billing.count_owner_tenants(session, user_id) >= cfg.int(
            "max_bots_per_user"
        ):
            raise FlowError(f"⚠️ حداکثر {cfg.int('max_bots_per_user')} ربات می‌توانید داشته باشید.")
        existing = await billing.find_live_tenant_by_bot(session, data["bot_id"])
        if existing is not None:
            if existing.owner_id == user_id and existing.status == TenantStatus.PENDING:
                existing.status = TenantStatus.DELETED  # abandoned earlier attempt for the same bot
                await session.flush()
            else:
                raise FlowError("❌ این ربات قبلاً در ربات‌ساز ثبت شده است.")
        return await billing.create_tenant(
            session,
            self.box,
            owner_id=user_id,
            bot_id=data["bot_id"],
            bot_username=data["username"],
            token=data["token"],
            admin_ids=data["admin_ids"],
            plan_id=plan_id,
            is_trial=trial,
            expires_at=expires_at,
        )

    async def _finish_trial(self, ctx, state) -> None:
        cfg = await self.cfg()
        data = state.data
        try:
            async with self.db.session() as s:
                user = await billing.get_or_create_user(s, ctx.user_id, ctx.first_name, ctx.username)
                if user.trial_used or not cfg.bool("trial_enabled"):
                    raise FlowError("🎁 تست رایگان برای شما در دسترس نیست.")
                tenant = await self._create_tenant_checked(
                    s,
                    ctx.user_id,
                    data,
                    plan_id=None,
                    trial=True,
                    expires_at=utcnow() + timedelta(days=max(1, cfg.int("trial_days"))),
                )
                user.trial_used = True
                await s.commit()
        except FlowError as exc:
            self.clear_state(ctx.user_id)
            await self.show(ctx, str(exc), BACK_HOME)
            return
        self.clear_state(ctx.user_id)
        await self.show(ctx, "⏳ در حال ساخت ربات شما… (چند ثانیه)")
        await self.activate_for_owner(billing.Fulfillment(tenant_id=tenant.id, needs_deploy=True), ctx.user_id)

    # ── checkout ──────────────────────────────────────────────────────────────
    @route("u:plan")
    async def choose_plan(self, ctx, plan_id: str = "") -> None:
        state = self._need_state(ctx, "pick_plan", "checkout")
        if state is None or not plan_id.isdigit():
            await self._expired_session(ctx)
            return
        async with self.db.session() as s:
            plan = await s.get(Plan, int(plan_id))
        if plan is None or not plan.active:
            await ctx.answer("این تعرفه دیگر فعال نیست.", alert=True)
            return
        state.name = "checkout"
        state.data["plan_id"] = plan.id
        state.data.pop("discount", None)
        await self._show_checkout(ctx, state)

    async def _price_for(self, session, plan: Plan, discount: str | None) -> tuple[int, str | None]:
        if discount:
            found = await billing.find_discount(session, discount)
            if found is not None:
                return billing.apply_discount(plan.price, found.percent), found.code
        return plan.price, None

    async def _show_checkout(self, ctx, state, note: str = "") -> None:
        data = state.data
        cfg = await self.cfg()
        async with self.db.session() as s:
            plan = await s.get(Plan, data["plan_id"])
            user = await s.get(BuilderUser, ctx.user_id)
            price, code = await self._price_for(s, plan, data.get("discount"))
            cards = (await s.execute(select(Card).where(Card.active.is_(True)))).scalars().all()
        if data.get("discount") and code is None:
            data.pop("discount", None)
            note = note or "⚠️ کد تخفیف دیگر معتبر نیست."
        balance = user.balance if user else 0
        title = "🔄 <b>تمدید ربات</b>" if data.get("mode") == "renew" else "🧾 <b>خلاصهٔ سفارش</b>"
        lines = [
            title,
            "",
            f"📦 تعرفه: <b>{esc(plan.name)}</b> ({plan.duration_days} روز)",
            f"💵 قیمت: {fmt_money(plan.price)}",
        ]
        if code:
            lines.append(f"🏷 کد تخفیف <code>{esc(code)}</code>: {fmt_money(plan.price - price)} کسر شد")
        lines += [f"✅ مبلغ نهایی: <b>{fmt_money(price)}</b>", f"👛 موجودی کیف پول: {fmt_money(balance)}"]
        if note:
            lines += ["", note]
        rows: Buttons = []
        if price == 0:
            rows.append([Btn("✅ فعال‌سازی رایگان", "u:pay_wallet")])
        else:
            if cfg.bool("wallet_enabled"):
                rows.append([Btn("👛 پرداخت از کیف پول", "u:pay_wallet")])
            if cfg.bool("card_payment_enabled") and cards:
                rows.append([Btn("💳 کارت به کارت", "u:pay_card")])
        if not rows:
            lines += ["", "⚠️ فعلاً روش پرداختی فعال نیست؛ با پشتیبانی تماس بگیرید."]
        rows.append([Btn("🏷 کد تخفیف", "u:disc"), Btn("❌ انصراف", "u:home")])
        await self.show(ctx, "\n".join(lines), rows)

    @route("u:disc")
    async def ask_discount(self, ctx) -> None:
        state = self._need_state(ctx, "checkout")
        if state is None:
            await self._expired_session(ctx)
            return
        state.name = "discount_code"
        await self.show(ctx, "🏷 کد تخفیف را بفرستید:", [[Btn("🔙 بازگشت", "u:checkout")]])

    @route("u:checkout")
    async def back_to_checkout(self, ctx) -> None:
        state = self._need_state(ctx, "checkout", "discount_code")
        if state is None:
            await self._expired_session(ctx)
            return
        state.name = "checkout"
        await self._show_checkout(ctx, state)

    @on_state("discount_code")
    async def got_discount(self, ctx, state) -> None:
        code = ctx.text.strip().upper()
        async with self.db.session() as s:
            found = await billing.find_discount(s, code)
        state.name = "checkout"
        if found is None:
            await self._show_checkout(ctx, state, "❌ کد تخفیف نامعتبر یا منقضی است.")
            return
        state.data["discount"] = found.code
        await self._show_checkout(ctx, state, f"✅ کد تخفیف {found.percent}٪ اعمال شد.")

    async def _tenant_for_checkout(self, session, ctx, data: dict, plan: Plan) -> Tenant:
        if data.get("mode") == "renew":
            tenant = await session.get(Tenant, data["tenant_id"])
            if tenant is None or tenant.owner_id != ctx.user_id or tenant.status == TenantStatus.DELETED:
                raise FlowError("❌ این ربات دیگر وجود ندارد.")
            return tenant
        return await self._create_tenant_checked(session, ctx.user_id, data, plan_id=plan.id)

    @route("u:pay_wallet")
    async def pay_wallet(self, ctx) -> None:
        state = self._need_state(ctx, "checkout")
        if state is None:
            await self._expired_session(ctx)
            return
        data, cfg = state.data, await self.cfg()
        kind = PaymentKind.RENEW if data.get("mode") == "renew" else PaymentKind.ORDER
        try:
            async with self.db.session() as s:
                plan = await s.get(Plan, data["plan_id"])
                if plan is None or not plan.active:
                    raise FlowError("این تعرفه دیگر فعال نیست.")
                price, _ = await self._price_for(s, plan, data.get("discount"))
                if price > 0 and not cfg.bool("wallet_enabled"):
                    raise FlowError("پرداخت از کیف پول فعال نیست.")
                await billing.get_or_create_user(s, ctx.user_id, ctx.first_name, ctx.username)
                tenant = await self._tenant_for_checkout(s, ctx, data, plan)
                try:
                    _, fulfillment = await billing.purchase_with_wallet(
                        s, user_id=ctx.user_id, kind=kind, plan=plan, tenant=tenant, discount_code=data.get("discount")
                    )
                except billing.InsufficientBalance:
                    await s.rollback()
                    await ctx.answer("موجودی کیف پول کافی نیست. ابتدا کیف پول را شارژ کنید.", alert=True)
                    return
                except billing.DiscountInvalid:
                    await s.rollback()
                    data.pop("discount", None)
                    await self._show_checkout(ctx, state, "⚠️ کد تخفیف دیگر معتبر نیست.")
                    return
                await s.commit()
        except FlowError as exc:
            self.clear_state(ctx.user_id)
            await self.show(ctx, str(exc), BACK_HOME)
            return
        self.clear_state(ctx.user_id)
        await self.show(ctx, "⏳ پرداخت انجام شد؛ در حال آماده‌سازی…")
        await self.activate_for_owner(fulfillment, ctx.user_id)

    @route("u:pay_card")
    async def pay_card(self, ctx) -> None:
        state = self._need_state(ctx, "checkout")
        if state is None:
            await self._expired_session(ctx)
            return
        data, cfg = state.data, await self.cfg()
        kind = PaymentKind.RENEW if data.get("mode") == "renew" else PaymentKind.ORDER
        if not cfg.bool("card_payment_enabled"):
            await ctx.answer("کارت به کارت فعال نیست.", alert=True)
            return
        try:
            async with self.db.session() as s:
                cards = (await s.execute(select(Card).where(Card.active.is_(True)))).scalars().all()
                if not cards:
                    raise FlowError("فعلاً شماره کارتی ثبت نشده است؛ با پشتیبانی تماس بگیرید.")
                plan = await s.get(Plan, data["plan_id"])
                if plan is None or not plan.active:
                    raise FlowError("این تعرفه دیگر فعال نیست.")
                price, code = await self._price_for(s, plan, data.get("discount"))
                if data.get("discount") and (code is None or not await billing.consume_discount(s, code)):
                    data.pop("discount", None)
                    await s.rollback()
                    await self._show_checkout(ctx, state, "⚠️ کد تخفیف دیگر معتبر نیست.")
                    return
                await billing.get_or_create_user(s, ctx.user_id, ctx.first_name, ctx.username)
                tenant = await self._tenant_for_checkout(s, ctx, data, plan)
                payment = await billing.create_payment(
                    s,
                    user_id=ctx.user_id,
                    kind=kind,
                    amount=price,
                    method="card",
                    plan_id=plan.id,
                    tenant_id=tenant.id,
                    discount_code=code,
                )
                await s.commit()
        except FlowError as exc:
            self.clear_state(ctx.user_id)
            await self.show(ctx, str(exc), BACK_HOME)
            return
        await self._ask_receipt(ctx, payment.id, price, cards, cfg.str("card_note"))

    async def _ask_receipt(self, ctx, payment_id: int, amount: int, cards, note: str) -> None:
        self.set_state(ctx.user_id, "receipt", payment_id=payment_id)
        card_lines = "\n".join(
            f"💳 <code>{esc(c.number)}</code>" + (f"\n👤 {esc(c.holder)}" if c.holder else "") for c in cards
        )
        text = (
            f"💳 <b>پرداخت کارت به کارت</b>\n\nمبلغ: <b>{fmt_money(amount)}</b>\n\n{card_lines}\n\n"
            "پس از واریز، <b>عکس رسید</b> (یا شمارهٔ پیگیری) را همین‌جا بفرستید."
        )
        if note:
            text += f"\n\nℹ️ {esc(note)}"
        await self.show(ctx, text, [[Btn("❌ انصراف", "u:home")]])

    @on_state("receipt")
    async def got_receipt(self, ctx, state) -> None:
        if not ctx.has_media and not ctx.text.strip():
            await ctx.reply("لطفاً عکس رسید یا شمارهٔ پیگیری را بفرستید.")
            return
        async with self.db.session() as s:
            payment = await s.get(Payment, state.data["payment_id"])
            if payment is None or payment.status != PaymentStatus.PENDING:
                self.clear_state(ctx.user_id)
                await ctx.reply("این پرداخت دیگر فعال نیست.", BACK_HOME)
                return
            payment.note = "receipt"
            await s.commit()
        summary = await self.payment_summary(payment.id)
        buttons = [[Btn("✅ تایید", f"a:pok:{payment.id}"), Btn("❌ رد", f"a:pno:{payment.id}")]]
        delivered = 0
        for admin_id in self.settings.admin_ids:
            await ctx.forward_to(admin_id)
            delivered += await self.notifier.send(admin_id, summary, buttons)
        self.clear_state(ctx.user_id)
        await ctx.reply(
            "✅ رسید ثبت شد و برای مدیریت ارسال شد. پس از تایید، نتیجه همین‌جا اعلام می‌شود."
            if delivered
            else "✅ رسید ثبت شد. مدیریت به‌زودی بررسی می‌کند.",
            BACK_HOME,
        )

    # ── wallet ────────────────────────────────────────────────────────────────
    @route("u:wallet")
    async def wallet(self, ctx) -> None:
        user = await self._user(ctx.user_id)
        cfg = await self.cfg()
        rows: Buttons = []
        if cfg.bool("card_payment_enabled"):
            rows.append([Btn("➕ شارژ کیف پول", "u:topup")])
        rows += BACK_HOME
        await self.show(ctx, f"💰 <b>کیف پول</b>\n\nموجودی: <b>{fmt_money(user.balance)}</b>", rows)

    @route("u:topup")
    async def topup(self, ctx) -> None:
        cfg = await self.cfg()
        if not cfg.bool("card_payment_enabled"):
            await ctx.answer("شارژ کیف پول فعال نیست.", alert=True)
            return
        self.set_state(ctx.user_id, "topup_amount")
        await self.show(
            ctx,
            f"مبلغ شارژ را به <b>تومان</b> بفرستید (حداقل {fmt_money(cfg.int('min_topup'))}):",
            [[Btn("❌ انصراف", "u:wallet")]],
        )

    @on_state("topup_amount")
    async def got_topup(self, ctx, state) -> None:
        cfg = await self.cfg()
        amount = parse_int(ctx.text, minimum=max(1, cfg.int("min_topup")), maximum=1_000_000_000)
        if amount is None:
            await ctx.reply(f"❌ مبلغ معتبر نیست (حداقل {fmt_money(cfg.int('min_topup'))}).")
            return
        async with self.db.session() as s:
            cards = (await s.execute(select(Card).where(Card.active.is_(True)))).scalars().all()
            if not cards:
                self.clear_state(ctx.user_id)
                await ctx.reply("فعلاً شماره کارتی ثبت نشده است؛ با پشتیبانی تماس بگیرید.", BACK_HOME)
                return
            await billing.get_or_create_user(s, ctx.user_id, ctx.first_name, ctx.username)
            payment = await billing.create_payment(
                s, user_id=ctx.user_id, kind=PaymentKind.TOPUP, amount=amount, method="card"
            )
            await s.commit()
        await self._ask_receipt(ctx, payment.id, amount, cards, cfg.str("card_note"))

    # ── my bots ───────────────────────────────────────────────────────────────
    @route("u:bots")
    async def my_bots(self, ctx) -> None:
        async with self.db.session() as s:
            tenants = await billing.list_owner_tenants(s, ctx.user_id)
        if not tenants:
            await self.show(ctx, "هنوز رباتی نساخته‌اید.", [[Btn("🤖 ساخت ربات جدید", "u:new")], *BACK_HOME])
            return
        rows: Buttons = [
            [Btn(f"{STATUS_LABEL.get(t.status, t.status).split()[0]} @{t.bot_username or t.id}", f"u:bot:{t.id}")]
            for t in tenants
        ]
        await self.show(ctx, "📂 <b>ربات‌های من</b>", [*rows, *BACK_HOME])

    async def _owned(self, ctx, tenant_id: str) -> Tenant | None:
        if not tenant_id.isdigit():
            return None
        async with self.db.session() as s:
            tenant = await s.get(Tenant, int(tenant_id))
        if tenant is None or tenant.owner_id != ctx.user_id or tenant.status == TenantStatus.DELETED:
            return None
        return tenant

    @route("u:bot")
    async def bot_detail(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owned(ctx, tenant_id)
        if tenant is None:
            await ctx.answer("این ربات پیدا نشد.", alert=True)
            return
        cfg = await self.cfg()
        left = days_left(tenant.expires_at, utcnow())
        lines = [
            f"🤖 <b>@{esc(tenant.bot_username)}</b>",
            f"وضعیت: {STATUS_LABEL.get(tenant.status, tenant.status)}",
            f"اعتبار تا: {fmt_date(tenant.expires_at)}" + (f" ({left} روز مانده)" if left is not None else ""),
            f"ادمین‌ها: <code>{esc(tenant.admin_ids)}</code>",
        ]
        if tenant.is_trial:
            lines.append("🎁 نسخهٔ آزمایشی")
        host = cfg.str("public_host").strip()
        if host and tenant.host_port and tenant.status == TenantStatus.RUNNING:
            lines.append(f"🌐 آدرس سرویس (وب‌هوک/وب‌اپ): <code>{esc(host)}:{tenant.host_port}</code>")
        if tenant.status == TenantStatus.ERROR:
            lines.append("⚠️ راه‌اندازی با خطا روبه‌رو شد؛ مدیریت در حال بررسی است.")
        rows: Buttons = [
            [Btn("🔄 تمدید", f"u:renew:{tenant.id}"), Btn("👤 تغییر ادمین", f"u:chadm:{tenant.id}")],
            [Btn("🗑 حذف ربات", f"u:del:{tenant.id}")],
        ]
        if tenant.bot_username:
            rows.insert(0, [Btn("🔗 باز کردن ربات", url=self._bot_link(tenant))])
        await self.show(ctx, "\n".join(lines), [*rows, [Btn("🔙 بازگشت", "u:bots")]])

    @route("u:renew")
    async def renew(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owned(ctx, tenant_id)
        if tenant is None:
            await ctx.answer("این ربات پیدا نشد.", alert=True)
            return
        async with self.db.session() as s:
            plans = await billing.list_plans(s)
        if not plans:
            await self.show(ctx, "فعلاً تعرفه‌ای تعریف نشده است.", BACK_HOME)
            return
        self.set_state(ctx.user_id, "pick_plan", mode="renew", tenant_id=tenant.id)
        rows = [[Btn(f"{p.name} — {fmt_money(p.price)} / {p.duration_days} روز", f"u:plan:{p.id}")] for p in plans]
        rows.append([Btn("🔙 بازگشت", f"u:bot:{tenant.id}")])
        await self.show(ctx, f"🔄 تمدید <b>@{esc(tenant.bot_username)}</b>\nتعرفه را انتخاب کنید:", rows)

    @route("u:chadm")
    async def change_admin(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owned(ctx, tenant_id)
        if tenant is None:
            await ctx.answer("این ربات پیدا نشد.", alert=True)
            return
        self.set_state(ctx.user_id, "chadm", tenant_id=tenant.id)
        await self.show(
            ctx,
            f"آیدی(های) عددی جدید ادمین را بفرستید (فعلی: <code>{esc(tenant.admin_ids)}</code>):",
            [[Btn("❌ انصراف", f"u:bot:{tenant.id}")]],
        )

    @on_state("chadm")
    async def got_new_admins(self, ctx, state) -> None:
        ids = parse_admin_ids(ctx.text)
        if ids is None:
            await ctx.reply("❌ آیدی عددی معتبر نیست. فقط عدد بفرستید؛ چند آیدی را با کاما جدا کنید.")
            return
        tenant = await self._owned(ctx, str(state.data["tenant_id"]))
        self.clear_state(ctx.user_id)
        if tenant is None:
            await ctx.reply("این ربات پیدا نشد.", BACK_HOME)
            return
        result = await self.ops.set_admins(tenant.id, ids)
        await ctx.reply(
            "✅ ادمین‌ها به‌روزرسانی شد." if result.ok else "⚠️ ذخیره شد ولی راه‌اندازی مجدد ربات با خطا روبه‌رو شد.",
            [[Btn("🔙 بازگشت", f"u:bot:{tenant.id}")]],
        )

    @route("u:del")
    async def delete_ask(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owned(ctx, tenant_id)
        if tenant is None:
            await ctx.answer("این ربات پیدا نشد.", alert=True)
            return
        await self.show(
            ctx,
            f"⚠️ ربات <b>@{esc(tenant.bot_username)}</b> و <b>تمام اطلاعات آن</b> برای همیشه حذف می‌شود. مطمئنید؟",
            [[Btn("✅ بله، حذف شود", f"u:delok:{tenant.id}")], [Btn("🔙 خیر", f"u:bot:{tenant.id}")]],
        )

    @route("u:delok")
    async def delete_confirm(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owned(ctx, tenant_id)
        if tenant is None:
            await ctx.answer("این ربات پیدا نشد.", alert=True)
            return
        result = await self.ops.delete(tenant.id)
        await self.show(
            ctx, "🗑 ربات حذف شد." if result.ok else "⚠️ حذف با خطا روبه‌رو شد؛ با پشتیبانی تماس بگیرید.", BACK_HOME
        )

    # ── shared: after money is settled ────────────────────────────────────────
    async def activate_for_owner(self, fulfillment, owner_id: int) -> None:
        """Deploy/extend after a payment was applied and tell the owner what happened."""
        if fulfillment.credited:
            await self.notifier.send(
                owner_id, f"💰 مبلغ {fmt_money(fulfillment.credited)} به کیف پول شما اضافه شد.", BACK_HOME
            )
        if fulfillment.tenant_id is None:
            return
        result = await self.ops.deploy(fulfillment.tenant_id) if fulfillment.needs_deploy else None
        async with self.db.session() as s:
            tenant = await s.get(Tenant, fulfillment.tenant_id)
        cfg = await self.cfg()
        if result is not None and not result.ok:
            await self.notifier.send(
                owner_id,
                "⚠️ پرداخت شما ثبت شد اما راه‌اندازی ربات با خطا روبه‌رو شد. مدیریت بررسی می‌کند و نتیجه اعلام می‌شود.",
                BACK_HOME,
            )
            for admin_id in self.settings.admin_ids:
                await self.notifier.send(
                    admin_id,
                    f"🚨 خطا در راه‌اندازی ربات #{tenant.id} (@{esc(tenant.bot_username)}) کاربر {owner_id}:\n"
                    f"<pre>{esc((result.error or '')[:900])}</pre>",
                    [[Btn("🔁 تلاش مجدد", f"a:tdeploy:{tenant.id}")]],
                )
            return
        lines = [
            f"✅ ربات <b>@{esc(tenant.bot_username)}</b> آماده است.",
            f"اعتبار تا: {fmt_date(tenant.expires_at)}",
            "برای شروع به ربات خود پیام /start بدهید؛ آیدی‌های ادمین به‌عنوان مدیر تعریف شده‌اند.",
        ]
        host = cfg.str("public_host").strip()
        if host and tenant.host_port:
            lines.append(f"🌐 آدرس سرویس: <code>{esc(host)}:{tenant.host_port}</code>")
        rows: Buttons = [[Btn("📂 ربات‌های من", "u:bots")]]
        if tenant.bot_username:
            rows.insert(0, [Btn("🔗 باز کردن ربات", url=self._bot_link(tenant))])
        await self.notifier.send(owner_id, "\n".join(lines), rows)
