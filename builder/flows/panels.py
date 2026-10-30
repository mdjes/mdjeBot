"""Customer-facing panel sales: buy a panel, renew it, add volume, see credentials."""

from __future__ import annotations

from sqlalchemy import select

from builder.flows.user import BACK_HOME, FlowError
from builder.models import (
    BuilderUser,
    Card,
    MasterPanel,
    PanelPlan,
    PanelSub,
    PanelSubStatus,
    PaymentKind,
    PaymentStatus,
    Tenant,
    TenantStatus,
    utcnow,
)
from builder.routing import on_state, route
from builder.services import billing
from builder.ui import Btn, Buttons
from builder.util import days_left, esc, fmt_bytes, fmt_date, fmt_money, usage_bar

PANEL_STATUS = {
    PanelSubStatus.PENDING: "🟡 در انتظار پرداخت",
    PanelSubStatus.ACTIVE: "🟢 فعال",
    PanelSubStatus.LIMITED: "🚫 حجم تمام شده",
    PanelSubStatus.EXPIRED: "⛔️ منقضی‌شده",
    PanelSubStatus.DISABLED: "⏸ غیرفعال (توسط مدیریت)",
    PanelSubStatus.ERROR: "⚠️ خطا (در حال بررسی)",
}
BACK_PANELS: Buttons = [[Btn("🔙 پنل‌های من", "u:panels")]]
_MODE_KIND = {
    "new": PaymentKind.PANEL,
    "renew": PaymentKind.PANEL_RENEW,
    "volume": PaymentKind.PANEL_VOLUME,
}


class PanelFlows:
    # ── list / detail ─────────────────────────────────────────────────────────
    @route("u:panels")
    async def my_panels(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        async with self.db.session() as s:
            subs = await billing.list_owner_panel_subs(s, ctx.user_id)
        rows: Buttons = [
            [Btn(f"{PANEL_STATUS.get(sub.status, sub.status).split()[0]} {sub.admin_username}", f"u:psub:{sub.id}")]
            for sub in subs
        ]
        rows += [[Btn("➕ خرید پنل جدید", "u:pbuy")], *BACK_HOME]
        text = (
            "🧩 <b>پنل‌های من</b>\n\nپنل، یک حساب مدیریتی روی پنل اصلی با حجم مشخص است و خودکار به ربات شما وصل می‌شود."
        )
        await self.show(ctx, text if subs else f"{text}\n\nهنوز پنلی ندارید.", rows)

    async def _own_sub(self, ctx, sub_id: str) -> PanelSub | None:
        if not sub_id.isdigit():
            return None
        async with self.db.session() as s:
            sub = await s.get(PanelSub, int(sub_id))
        if sub is None or sub.owner_id != ctx.user_id or sub.status in (PanelSubStatus.DELETED, PanelSubStatus.PENDING):
            return None
        return sub

    @route("u:psub")
    async def panel_detail(self, ctx, sub_id: str = "") -> None:
        sub = await self._own_sub(ctx, sub_id)
        if sub is None:
            await ctx.answer("این پنل پیدا نشد.", alert=True)
            return
        await self.panels.refresh_usage(sub.id)
        async with self.db.session() as s:
            sub = await s.get(PanelSub, sub.id)
            master = await s.get(MasterPanel, sub.master_id)
            plan = await s.get(PanelPlan, sub.plan_id) if sub.plan_id else None
            bot = await s.get(Tenant, sub.tenant_id) if sub.tenant_id else None
        left = days_left(sub.expires_at, utcnow())
        lines = [
            f"🧩 <b>{esc(master.name if master else 'پنل')}</b> — <code>{esc(sub.admin_username)}</code>",
            f"وضعیت: {PANEL_STATUS.get(sub.status, sub.status)}",
            f"📦 پلن: {esc(plan.name) if plan else '—'}",
            f"📊 مصرف: {fmt_bytes(sub.used_bytes)} از {fmt_bytes(sub.data_limit)}",
            usage_bar(sub.used_bytes, sub.data_limit),
            f"📅 اعتبار تا: {fmt_date(sub.expires_at)}" + (f" ({left} روز مانده)" if left is not None else ""),
        ]
        if bot is not None:
            connected = "✅ متصل" if sub.linked else "⏳ در انتظار اتصال"
            lines.append(f"🤖 ربات: {esc(self.tenant_label(bot))} — {connected}")
        if sub.status == PanelSubStatus.ERROR:
            lines.append("\n⚠️ ساخت یا اتصال پنل با خطا روبه‌رو شد؛ مدیریت در حال بررسی است.")
        i = sub.id
        rows: Buttons = [
            [Btn("🔑 اطلاعات ورود", f"u:pcred:{i}"), Btn("🔐 تغییر رمز", f"u:ppw:{i}")],
            [Btn("🔄 تمدید", f"u:prenew:{i}"), Btn("➕ حجم اضافه", f"u:pvol:{i}")],
        ]
        if not sub.linked and bot is not None and bot.status == TenantStatus.RUNNING:
            rows.append([Btn("🔗 اتصال مجدد به ربات", f"u:prelink:{i}")])
        await self.show(ctx, "\n".join(lines), [*rows, *BACK_PANELS])

    @route("u:pcred")
    async def panel_credentials(self, ctx, sub_id: str = "") -> None:
        sub = await self._own_sub(ctx, sub_id)
        if sub is None or not sub.admin_password_enc:
            await ctx.answer("این پنل پیدا نشد.", alert=True)
            return
        async with self.db.session() as s:
            master = await s.get(MasterPanel, sub.master_id)
        text = (
            "🔑 <b>اطلاعات ورود به پنل</b>\n\n"
            f"🌐 آدرس: <code>{esc(master.base_url)}</code>\n"
            f"👤 نام کاربری: <code>{esc(sub.admin_username)}</code>\n"
            f"🔒 رمز عبور: <code>{esc(self.box.decrypt(sub.admin_password_enc))}</code>\n\n"
            "⚠️ این اطلاعات را در اختیار کسی نگذارید. ربات شما با همین حساب به پنل وصل شده است."
        )
        await self.show(ctx, text, [[Btn("🔙 بازگشت", f"u:psub:{sub.id}")]])

    @route("u:ppw")
    async def panel_password_ask(self, ctx, sub_id: str = "") -> None:
        sub = await self._own_sub(ctx, sub_id)
        if sub is None:
            await ctx.answer("این پنل پیدا نشد.", alert=True)
            return
        await self.show(
            ctx,
            "🔐 رمز جدیدی ساخته می‌شود و رمز قبلی از کار می‌افتد (ربات شما خودکار به‌روز می‌شود). ادامه؟",
            [[Btn("✅ بله", f"u:ppwok:{sub.id}")], [Btn("🔙 خیر", f"u:psub:{sub.id}")]],
        )

    @route("u:ppwok")
    async def panel_password_reset(self, ctx, sub_id: str = "") -> None:
        sub = await self._own_sub(ctx, sub_id)
        if sub is None:
            await ctx.answer("این پنل پیدا نشد.", alert=True)
            return
        result = await self.panels.reset_password(sub.id)
        if not result.ok:
            await self.show(
                ctx, f"❌ تغییر رمز انجام نشد: {esc((result.error or '')[:200])}", [[Btn("🔙", f"u:psub:{sub.id}")]]
            )
            return
        await self.show(
            ctx,
            f"✅ رمز جدید: <code>{esc(result.password)}</code>\n\nربات شما با رمز جدید دوباره وصل شد.",
            [[Btn("🔙 بازگشت", f"u:psub:{sub.id}")]],
        )

    @route("u:prelink")
    async def panel_relink(self, ctx, sub_id: str = "") -> None:
        sub = await self._own_sub(ctx, sub_id)
        if sub is None:
            await ctx.answer("این پنل پیدا نشد.", alert=True)
            return
        result = await self.panels.link(sub.id)
        await ctx.answer("✅ متصل شد." if result.ok else f"❌ {(result.error or '')[:150]}", alert=not result.ok)
        await self.panel_detail(ctx, str(sub.id))

    # ── buying ────────────────────────────────────────────────────────────────
    @route("u:pbuy")
    async def panel_buy(self, ctx) -> None:
        cfg = await self.cfg()
        if not cfg.bool("panel_sales_enabled"):
            await ctx.answer("فروش پنل فعال نیست.", alert=True)
            return
        async with self.db.session() as s:
            plans = await billing.list_panel_plans(s, kind="plan")
            bots = await billing.list_owner_tenants(s, ctx.user_id)
        if not plans:
            await self.show(ctx, "فعلاً پلن پنلی برای فروش تعریف نشده است.", BACK_PANELS)
            return
        if not bots:
            await self.show(
                ctx,
                "🤖 پنل به یک ربات وصل می‌شود؛ ابتدا ربات خودتان را بسازید.",
                [[Btn("🤖 ساخت ربات", "u:new")], *BACK_PANELS],
            )
            return
        self.set_state(ctx.user_id, "ppick", mode="new")
        rows = [[Btn(self._plan_label(plan), f"u:pplan:{plan.id}")] for plan in plans]
        rows.append([Btn("❌ انصراف", "u:panels")])
        await self.show(ctx, "🧩 <b>خرید پنل</b>\n\nپلن مورد نظر را انتخاب کنید:", rows)

    @staticmethod
    def _plan_label(plan: PanelPlan) -> str:
        if plan.kind == "addon":
            return f"+{plan.data_gb} GB — {fmt_money(plan.price)}"
        return f"{plan.name} · {plan.data_gb} GB · {plan.duration_days} روز — {fmt_money(plan.price)}"

    @route("u:pplan")
    async def panel_pick_plan(self, ctx, plan_id: str = "") -> None:
        state = self._need_state(ctx, "ppick", "pcheckout")
        if state is None or not plan_id.isdigit():
            await self._expired_session(ctx)
            return
        async with self.db.session() as s:
            plan = await s.get(PanelPlan, int(plan_id))
            bots = await billing.list_owner_tenants(s, ctx.user_id)
        if plan is None or not plan.active:
            await ctx.answer("این پلن دیگر فعال نیست.", alert=True)
            return
        state.data["plan_id"] = plan.id
        state.data.pop("discount", None)
        if state.data.get("mode") != "new":
            state.name = "pcheckout"
            await self._show_panel_checkout(ctx, state)
            return
        if len(bots) == 1:
            state.data["tenant_id"] = bots[0].id
            state.name = "pcheckout"
            await self._show_panel_checkout(ctx, state)
            return
        state.name = "pbot"
        rows = [[Btn(f"🤖 {self.tenant_label(bot)}", f"u:pbot:{bot.id}")] for bot in bots]
        rows.append([Btn("❌ انصراف", "u:panels")])
        await self.show(ctx, "پنل به کدام ربات شما وصل شود؟", rows)

    @route("u:pbot")
    async def panel_pick_bot(self, ctx, tenant_id: str = "") -> None:
        state = self._need_state(ctx, "pbot")
        if state is None or not tenant_id.isdigit():
            await self._expired_session(ctx)
            return
        async with self.db.session() as s:
            bot = await s.get(Tenant, int(tenant_id))
        if bot is None or bot.owner_id != ctx.user_id or bot.status == TenantStatus.DELETED:
            await ctx.answer("این ربات پیدا نشد.", alert=True)
            return
        state.data["tenant_id"] = bot.id
        state.name = "pcheckout"
        await self._show_panel_checkout(ctx, state)

    async def _show_panel_checkout(self, ctx, state, note: str = "") -> None:
        data = state.data
        cfg = await self.cfg()
        async with self.db.session() as s:
            plan = await s.get(PanelPlan, data["plan_id"])
            user = await s.get(BuilderUser, ctx.user_id)
            price, code = await self._price_for(s, plan, data.get("discount"))
            cards = (await s.execute(select(Card).where(Card.active.is_(True)))).scalars().all()
            bot = await s.get(Tenant, data["tenant_id"]) if data.get("tenant_id") else None
        if data.get("discount") and code is None:
            data.pop("discount", None)
            note = note or "⚠️ کد تخفیف دیگر معتبر نیست."
        title = {"new": "🧾 <b>خلاصهٔ خرید پنل</b>", "renew": "🔄 <b>تمدید پنل</b>", "volume": "➕ <b>حجم اضافه</b>"}[
            data["mode"]
        ]
        lines = [title, "", f"📦 {esc(plan.name)}", f"📊 حجم: <b>{plan.data_gb} GB</b>"]
        if plan.kind != "addon":
            lines.append(f"📅 مدت: {plan.duration_days} روز")
            if plan.max_users:
                lines.append(f"👥 سقف کاربر: {plan.max_users}")
        if bot is not None and data["mode"] == "new":
            lines.append(f"🤖 اتصال به ربات: {esc(self.tenant_label(bot))}")
        lines.append(f"💵 قیمت: {fmt_money(plan.price)}")
        if code:
            lines.append(f"🏷 کد تخفیف <code>{esc(code)}</code>: {fmt_money(plan.price - price)} کسر شد")
        lines += [
            f"✅ مبلغ نهایی: <b>{fmt_money(price)}</b>",
            f"👛 موجودی کیف پول: {fmt_money(user.balance if user else 0)}",
        ]
        if data["mode"] == "new":
            lines += ["", "پس از پرداخت، پنل خودکار ساخته و به ربات شما وصل می‌شود."]
        if note:
            lines += ["", note]
        rows: Buttons = []
        if price == 0:
            rows.append([Btn("✅ فعال‌سازی رایگان", "u:ppay_wallet")])
        else:
            if cfg.bool("wallet_enabled"):
                rows.append([Btn("👛 پرداخت از کیف پول", "u:ppay_wallet")])
            if cfg.bool("card_payment_enabled") and cards:
                rows.append([Btn("💳 کارت به کارت", "u:ppay_card")])
        if not rows:
            lines += ["", "⚠️ فعلاً روش پرداختی فعال نیست؛ با پشتیبانی تماس بگیرید."]
        if self.is_admin(ctx.user_id) and price > 0:
            rows.append([Btn("🆓 رایگان (مدیر)", "u:ppay_free")])
        rows.append([Btn("🏷 کد تخفیف", "u:pdisc"), Btn("❌ انصراف", "u:panels")])
        await self.show(ctx, "\n".join(lines), rows)

    @route("u:pdisc")
    async def panel_ask_discount(self, ctx) -> None:
        state = self._need_state(ctx, "pcheckout")
        if state is None:
            await self._expired_session(ctx)
            return
        state.name = "pdiscount"
        await self.show(ctx, "🏷 کد تخفیف را بفرستید:", [[Btn("🔙 بازگشت", "u:pcheckout")]])

    @route("u:pcheckout")
    async def panel_back_to_checkout(self, ctx) -> None:
        state = self._need_state(ctx, "pcheckout", "pdiscount")
        if state is None:
            await self._expired_session(ctx)
            return
        state.name = "pcheckout"
        await self._show_panel_checkout(ctx, state)

    @on_state("pdiscount")
    async def panel_got_discount(self, ctx, state) -> None:
        code = ctx.text.strip().upper()
        async with self.db.session() as s:
            found = await billing.find_discount(s, code)
        state.name = "pcheckout"
        if found is None:
            await self._show_panel_checkout(ctx, state, "❌ کد تخفیف نامعتبر یا منقضی است.")
            return
        state.data["discount"] = found.code
        await self._show_panel_checkout(ctx, state, f"✅ کد تخفیف {found.percent}٪ اعمال شد.")

    async def _panel_sub_for_checkout(self, session, ctx, data: dict, plan: PanelPlan) -> PanelSub:
        if data["mode"] == "new":
            bot = await session.get(Tenant, data["tenant_id"])
            if bot is None or bot.owner_id != ctx.user_id or bot.status == TenantStatus.DELETED:
                raise FlowError("❌ این ربات دیگر وجود ندارد.")
            return await billing.create_panel_sub(session, owner_id=ctx.user_id, plan=plan, tenant_id=bot.id)
        sub = await session.get(PanelSub, data["sub_id"])
        if sub is None or sub.owner_id != ctx.user_id or sub.status in (PanelSubStatus.DELETED, PanelSubStatus.PENDING):
            raise FlowError("❌ این پنل دیگر وجود ندارد.")
        if plan.master_id != sub.master_id:
            raise FlowError("❌ این پلن برای پنل شما نیست.")
        return sub

    async def _pay_panel_instant(self, ctx, *, free: bool) -> None:
        state = self._need_state(ctx, "pcheckout")
        if state is None:
            await self._expired_session(ctx)
            return
        if free and not self.is_admin(ctx.user_id):
            await ctx.answer("⛔️ فقط مدیر می‌تواند رایگان بسازد.", alert=True)
            return
        data, cfg = state.data, await self.cfg()
        kind = _MODE_KIND[data["mode"]]
        try:
            async with self.db.session() as s:
                plan = await s.get(PanelPlan, data["plan_id"])
                if plan is None or not plan.active:
                    raise FlowError("این پلن دیگر فعال نیست.")
                price, _ = await self._price_for(s, plan, data.get("discount"))
                if not free and price > 0 and not cfg.bool("wallet_enabled"):
                    raise FlowError("پرداخت از کیف پول فعال نیست.")
                await billing.get_or_create_user(s, ctx.user_id, ctx.first_name, ctx.username)
                sub = await self._panel_sub_for_checkout(s, ctx, data, plan)
                if free:
                    payment = await billing.create_payment(
                        s,
                        user_id=ctx.user_id,
                        kind=kind,
                        amount=0,
                        method="admin",
                        panel_plan_id=plan.id,
                        panel_sub_id=sub.id,
                        status=PaymentStatus.APPROVED,
                        note=f"free by admin {ctx.user_id}",
                    )
                    fulfillment = await billing.fulfill_payment(s, payment)
                else:
                    try:
                        _, fulfillment = await billing.purchase_panel_with_wallet(
                            s,
                            user_id=ctx.user_id,
                            kind=kind,
                            plan=plan,
                            sub=sub,
                            discount_code=data.get("discount"),
                        )
                    except billing.InsufficientBalance:
                        await s.rollback()
                        await ctx.answer("موجودی کیف پول کافی نیست. ابتدا کیف پول را شارژ کنید.", alert=True)
                        return
                    except billing.DiscountInvalid:
                        await s.rollback()
                        data.pop("discount", None)
                        await self._show_panel_checkout(ctx, state, "⚠️ کد تخفیف دیگر معتبر نیست.")
                        return
                await s.commit()
        except FlowError as exc:
            self.clear_state(ctx.user_id)
            await self.show(ctx, str(exc), BACK_PANELS)
            return
        self.clear_state(ctx.user_id)
        await self.show(ctx, "⏳ پرداخت انجام شد؛ در حال ساخت پنل…")
        await self.activate_for_owner(fulfillment, ctx.user_id)

    @route("u:ppay_wallet")
    async def panel_pay_wallet(self, ctx) -> None:
        await self._pay_panel_instant(ctx, free=False)

    @route("u:ppay_free")
    async def panel_pay_free(self, ctx) -> None:
        await self._pay_panel_instant(ctx, free=True)

    @route("u:ppay_card")
    async def panel_pay_card(self, ctx) -> None:
        state = self._need_state(ctx, "pcheckout")
        if state is None:
            await self._expired_session(ctx)
            return
        data, cfg = state.data, await self.cfg()
        if not cfg.bool("card_payment_enabled"):
            await ctx.answer("کارت به کارت فعال نیست.", alert=True)
            return
        try:
            async with self.db.session() as s:
                cards = (await s.execute(select(Card).where(Card.active.is_(True)))).scalars().all()
                if not cards:
                    raise FlowError("فعلاً شماره کارتی ثبت نشده است؛ با پشتیبانی تماس بگیرید.")
                plan = await s.get(PanelPlan, data["plan_id"])
                if plan is None or not plan.active:
                    raise FlowError("این پلن دیگر فعال نیست.")
                price, code = await self._price_for(s, plan, data.get("discount"))
                if data.get("discount") and (code is None or not await billing.consume_discount(s, code)):
                    data.pop("discount", None)
                    await s.rollback()
                    await self._show_panel_checkout(ctx, state, "⚠️ کد تخفیف دیگر معتبر نیست.")
                    return
                await billing.get_or_create_user(s, ctx.user_id, ctx.first_name, ctx.username)
                sub = await self._panel_sub_for_checkout(s, ctx, data, plan)
                payment = await billing.create_payment(
                    s,
                    user_id=ctx.user_id,
                    kind=_MODE_KIND[data["mode"]],
                    amount=price,
                    method="card",
                    panel_plan_id=plan.id,
                    panel_sub_id=sub.id,
                    discount_code=code,
                )
                await s.commit()
        except FlowError as exc:
            self.clear_state(ctx.user_id)
            await self.show(ctx, str(exc), BACK_PANELS)
            return
        await self._ask_receipt(ctx, payment.id, price, cards, cfg.str("card_note"))

    # ── renew / extra volume ──────────────────────────────────────────────────
    async def _start_panel_change(self, ctx, sub_id: str, mode: str) -> None:
        sub = await self._own_sub(ctx, sub_id)
        if sub is None:
            await ctx.answer("این پنل پیدا نشد.", alert=True)
            return
        kind = "plan" if mode == "renew" else "addon"
        async with self.db.session() as s:
            plans = await billing.list_panel_plans(s, kind=kind, master_id=sub.master_id)
        if not plans:
            await self.show(ctx, "فعلاً مورد قابل خریدی تعریف نشده است.", [[Btn("🔙 بازگشت", f"u:psub:{sub.id}")]])
            return
        self.set_state(ctx.user_id, "ppick", mode=mode, sub_id=sub.id, tenant_id=sub.tenant_id)
        rows = [[Btn(self._plan_label(plan), f"u:pplan:{plan.id}")] for plan in plans]
        rows.append([Btn("🔙 بازگشت", f"u:psub:{sub.id}")])
        title = "🔄 تمدید پنل (حجم و مدت یک دورهٔ تازه)" if mode == "renew" else "➕ بستهٔ حجم اضافه"
        await self.show(ctx, f"{title}\n\nیکی را انتخاب کنید:", rows)

    @route("u:prenew")
    async def panel_renew(self, ctx, sub_id: str = "") -> None:
        await self._start_panel_change(ctx, sub_id, "renew")

    @route("u:pvol")
    async def panel_volume(self, ctx, sub_id: str = "") -> None:
        await self._start_panel_change(ctx, sub_id, "volume")

    # ── after a payment: do the work on the master panel ──────────────────────
    async def apply_panel_fulfillment(self, fulfillment, owner_id: int) -> None:
        """Create / renew / top-up the panel for an approved payment and tell the owner."""
        sub_id, plan_id, action = fulfillment.panel_sub_id, fulfillment.panel_plan_id, fulfillment.panel_action
        if action == "provision":
            result = await self.panels.provision(sub_id)
        elif action == "renew":
            result = await self.panels.renew(sub_id, plan_id)
        else:
            result = await self.panels.add_volume(sub_id, plan_id)

        if not result.ok:
            await self.notifier.send(
                owner_id,
                "⚠️ پرداخت شما ثبت شد اما انجام کار روی پنل با خطا روبه‌رو شد. مدیریت بررسی می‌کند و نتیجه اعلام می‌شود.",
                BACK_PANELS,
            )
            for admin_id in self.settings.admin_ids:
                await self.notifier.send(
                    admin_id,
                    f"🚨 خطا در {action} پنل #{sub_id} کاربر {owner_id}:\n<pre>{esc((result.error or '')[:900])}</pre>",
                    [[Btn("🔁 تلاش مجدد", f"a:psretry:{sub_id}:{action}:{plan_id}")]],
                )
            return

        async with self.db.session() as s:
            sub = await s.get(PanelSub, sub_id)
            master = await s.get(MasterPanel, sub.master_id)
            bot = await s.get(Tenant, sub.tenant_id) if sub.tenant_id else None
        if action == "provision":
            connected = (
                f"✅ به ربات {esc(self.tenant_label(bot))} وصل شد."
                if sub.linked
                else "⏳ به‌محض آماده شدن ربات، خودکار وصل می‌شود."
            )
            text = (
                "✅ <b>پنل شما ساخته شد</b>\n\n"
                f"🌐 آدرس: <code>{esc(master.base_url)}</code>\n"
                f"👤 نام کاربری: <code>{esc(sub.admin_username)}</code>\n"
                f"🔒 رمز عبور: <code>{esc(self.box.decrypt(sub.admin_password_enc))}</code>\n\n"
                f"📊 حجم: {fmt_bytes(sub.data_limit)}\n📅 اعتبار تا: {fmt_date(sub.expires_at)}\n\n{connected}"
            )
        elif action == "renew":
            text = f"✅ پنل <code>{esc(sub.admin_username)}</code> تمدید شد.\n📊 حجم تازه: {fmt_bytes(sub.data_limit)}\n📅 اعتبار تا: {fmt_date(sub.expires_at)}"
        else:
            text = f"✅ حجم اضافه شد. حجم کل پنل <code>{esc(sub.admin_username)}</code>: {fmt_bytes(sub.data_limit)}"
        await self.notifier.send(owner_id, text, [[Btn("🧩 پنل‌های من", "u:panels")], *BACK_HOME])
