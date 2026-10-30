"""Builder admin panel: stats, plans, bots, users, payments, cards, discount codes, settings, broadcast."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, or_, select

from builder.flows.user import STATUS_LABEL
from builder.models import (
    BuilderUser,
    Card,
    DiscountCode,
    PanelPlan,
    PanelSub,
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
from builder.services.cfg import EDITABLE, EDITABLE_KEYS, set_cfg
from builder.ui import Btn, Buttons
from builder.util import days_left, esc, fmt_date, fmt_money, parse_int

log = logging.getLogger("builder.admin")

PAGE_SIZE = 8
BACK: Buttons = [[Btn("🔙 پنل مدیریت", "a:home")]]
KIND_LABEL = {
    PaymentKind.TOPUP: "شارژ کیف پول",
    PaymentKind.ORDER: "ساخت ربات",
    PaymentKind.RENEW: "تمدید",
    PaymentKind.PANEL: "خرید پنل",
    PaymentKind.PANEL_RENEW: "تمدید پنل",
    PaymentKind.PANEL_VOLUME: "حجم اضافه پنل",
}


class AdminFlows:
    # ── home / stats ──────────────────────────────────────────────────────────
    @route("a:home")
    async def admin_home(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        async with self.db.session() as s:
            stats = await billing.collect_stats(s)
        pending = stats["pending_payments"]
        rows: Buttons = [
            [Btn("📊 آمار", "a:stats"), Btn(f"💳 پرداخت‌ها ({pending})", "a:pays")],
            [Btn("🤖 ربات‌ها", "a:tenants"), Btn("👥 کاربران", "a:users")],
            [Btn("📦 تعرفه‌ها", "a:plans"), Btn("🏦 کارت‌ها", "a:cards")],
            [Btn("🏷 کدهای تخفیف", "a:codes"), Btn("⚙️ تنظیمات", "a:settings")],
            [Btn("🧩 فروش پنل", "a:pmenu")],
            [Btn("📢 ارسال همگانی", "a:bc"), Btn("🖥 سرور", "a:server")],
            [Btn("🏠 منوی کاربر", "u:home")],
        ]
        text = (
            "⚙️ <b>پنل مدیریت ربات‌ساز</b>\n\n"
            f"👥 کاربران: {stats['users']}\n"
            f"🟢 ربات‌های فعال: {stats['tenants_running']}\n"
            f"💳 پرداخت در انتظار: {pending}"
        )
        await self.show(ctx, text, rows)

    @route("a:stats")
    async def stats(self, ctx) -> None:
        async with self.db.session() as s:
            st = await billing.collect_stats(s)
        lines = [
            "📊 <b>آمار</b>\n",
            f"👥 کاربران: {st['users']}",
            f"🟢 فعال: {st['tenants_running']} | 🔴 منقضی: {st['tenants_expired']} | ⏸ متوقف: {st['tenants_stopped']}",
            f"🟡 در انتظار: {st['tenants_pending']} | ⚠️ خطا: {st['tenants_error']}",
            f"💳 پرداخت در انتظار: {st['pending_payments']}",
            "",
            f"💵 درآمد کل: {fmt_money(st['revenue_total'])}",
            f"📅 درآمد ۳۰ روز اخیر: {fmt_money(st['revenue_30d'])}",
            f"➕ مجموع شارژ کیف پول: {fmt_money(st['topups_total'])}",
            f"👛 مجموع موجودی کیف پول‌ها: {fmt_money(st['wallet_total'])}",
        ]
        await self.show(ctx, "\n".join(lines), BACK)

    # ── plans ─────────────────────────────────────────────────────────────────
    @route("a:plans")
    async def plans(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        async with self.db.session() as s:
            plans = await billing.list_plans(s, active_only=False)
        rows: Buttons = [
            [
                Btn(
                    f"{'🟢' if p.active else '⚪️'} {p.name} · {fmt_money(p.price)} · {p.duration_days}d",
                    f"a:plan:{p.id}",
                )
            ]
            for p in plans
        ]
        rows += [[Btn("➕ افزودن تعرفه", "a:planadd")], *BACK]
        await self.show(ctx, "📦 <b>تعرفه‌ها</b>", rows)

    async def _plan_screen(self, ctx, plan_id: int) -> None:
        async with self.db.session() as s:
            plan = await s.get(Plan, plan_id)
        if plan is None:
            await self.show(ctx, "تعرفه پیدا نشد.", [[Btn("🔙", "a:plans")]])
            return
        text = (
            f"📦 <b>{esc(plan.name)}</b>\nقیمت: {fmt_money(plan.price)}\nمدت: {plan.duration_days} روز\n"
            f"وضعیت: {'فعال' if plan.active else 'غیرفعال'}"
        )
        rows: Buttons = [
            [Btn("🔁 فعال/غیرفعال", f"a:plant:{plan.id}")],
            [Btn("✏️ نام", f"a:plane:{plan.id}:name"), Btn("💵 قیمت", f"a:plane:{plan.id}:price")],
            [Btn("⏳ مدت (روز)", f"a:plane:{plan.id}:days")],
            [Btn("🔙 تعرفه‌ها", "a:plans")],
        ]
        await self.show(ctx, text, rows)

    @route("a:plan")
    async def plan_detail(self, ctx, plan_id: str = "") -> None:
        if plan_id.isdigit():
            await self._plan_screen(ctx, int(plan_id))

    @route("a:plant")
    async def plan_toggle(self, ctx, plan_id: str = "") -> None:
        async with self.db.session() as s:
            plan = await s.get(Plan, int(plan_id))
            if plan is not None:
                plan.active = not plan.active
                await s.commit()
        await self._plan_screen(ctx, int(plan_id))

    @route("a:plane")
    async def plan_edit(self, ctx, plan_id: str = "", field: str = "") -> None:
        if field not in ("name", "price", "days") or not plan_id.isdigit():
            return
        self.set_state(ctx.user_id, "plan_edit", plan_id=int(plan_id), field=field)
        prompt = {"name": "نام جدید تعرفه:", "price": "قیمت جدید (تومان):", "days": "مدت جدید (روز):"}[field]
        await self.show(ctx, prompt, [[Btn("❌ انصراف", f"a:plan:{plan_id}")]])

    @on_state("plan_edit")
    async def got_plan_edit(self, ctx, state) -> None:
        field, plan_id = state.data["field"], state.data["plan_id"]
        value: str | int | None = ctx.text.strip()
        if field == "price":
            value = parse_int(ctx.text, minimum=0)
        elif field == "days":
            value = parse_int(ctx.text, minimum=1, maximum=3650)
        elif not value:
            value = None
        if value is None:
            await ctx.reply("❌ مقدار نامعتبر است. دوباره بفرستید.")
            return
        async with self.db.session() as s:
            plan = await s.get(Plan, plan_id)
            if plan is not None:
                setattr(plan, {"name": "name", "price": "price", "days": "duration_days"}[field], value)
                await s.commit()
        self.clear_state(ctx.user_id)
        await self._plan_screen(ctx, plan_id)

    @route("a:planadd")
    async def plan_add(self, ctx) -> None:
        self.set_state(ctx.user_id, "plan_new_name")
        await self.show(ctx, "نام تعرفه را بفرستید (مثلاً «ماهانه»):", [[Btn("❌ انصراف", "a:plans")]])

    @on_state("plan_new_name")
    async def got_plan_name(self, ctx, state) -> None:
        name = ctx.text.strip()
        if not name or len(name) > 100:
            await ctx.reply("❌ نام نامعتبر است.")
            return
        state.data["name"], state.name = name, "plan_new_price"
        await ctx.reply("قیمت را به تومان بفرستید:")

    @on_state("plan_new_price")
    async def got_plan_price(self, ctx, state) -> None:
        price = parse_int(ctx.text, minimum=0)
        if price is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        state.data["price"], state.name = price, "plan_new_days"
        await ctx.reply("مدت اعتبار (روز) را بفرستید:")

    @on_state("plan_new_days")
    async def got_plan_days(self, ctx, state) -> None:
        days = parse_int(ctx.text, minimum=1, maximum=3650)
        if days is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        async with self.db.session() as s:
            plan = Plan(name=state.data["name"], price=state.data["price"], duration_days=days)
            s.add(plan)
            await s.commit()
        self.clear_state(ctx.user_id)
        await self._plan_screen(ctx, plan.id)

    # ── bots (tenants) ────────────────────────────────────────────────────────
    @route("a:tenants")
    async def tenants(self, ctx, page: str = "0") -> None:
        self.clear_state(ctx.user_id)
        page_no = int(page) if page.isdigit() else 0
        async with self.db.session() as s:
            total = int(
                (
                    await s.execute(select(func.count(Tenant.id)).where(Tenant.status != TenantStatus.DELETED))
                ).scalar_one()
            )
            rows_db = (
                (
                    await s.execute(
                        select(Tenant)
                        .where(Tenant.status != TenantStatus.DELETED)
                        .order_by(Tenant.id.desc())
                        .offset(page_no * PAGE_SIZE)
                        .limit(PAGE_SIZE)
                    )
                )
                .scalars()
                .all()
            )
        await self._tenant_list(ctx, rows_db, f"🤖 <b>ربات‌ها</b> ({total})", page_no, total)

    async def _tenant_list(self, ctx, tenants, title: str, page_no: int = 0, total: int = 0) -> None:
        rows: Buttons = [
            [
                Btn(
                    f"{STATUS_LABEL.get(t.status, t.status).split()[0]} #{t.id} {('@' + t.bot_username) if t.bot_username else '—'} · {t.owner_id}",
                    f"a:tenant:{t.id}",
                )
            ]
            for t in tenants
        ]
        nav: list[Btn] = []
        if page_no > 0:
            nav.append(Btn("◀️", f"a:tenants:{page_no - 1}"))
        if (page_no + 1) * PAGE_SIZE < total:
            nav.append(Btn("▶️", f"a:tenants:{page_no + 1}"))
        if nav:
            rows.append(nav)
        rows += [[Btn("🔎 جستجو", "a:tsearch")], *BACK]
        await self.show(ctx, title if tenants else f"{title}\n\nموردی نیست.", rows)

    @route("a:tsearch")
    async def tenant_search(self, ctx) -> None:
        self.set_state(ctx.user_id, "tenant_search")
        await self.show(
            ctx,
            "شمارهٔ ربات، آیدی عددی مالک، آیدی بات یا بخشی از یوزرنیم را بفرستید:",
            [[Btn("❌ انصراف", "a:tenants")]],
        )

    @on_state("tenant_search")
    async def got_tenant_search(self, ctx, state) -> None:
        query = ctx.text.strip().lstrip("@")
        number = parse_int(query)
        async with self.db.session() as s:
            conds = [Tenant.bot_username.ilike(f"%{query}%")]
            if number is not None:
                conds += [Tenant.id == number, Tenant.owner_id == number, Tenant.bot_id == number]
            found = (
                (await s.execute(select(Tenant).where(Tenant.status != TenantStatus.DELETED, or_(*conds)).limit(10)))
                .scalars()
                .all()
            )
        self.clear_state(ctx.user_id)
        await self._tenant_list(ctx, found, f"🔎 نتیجهٔ جستجو برای «{esc(query)}»")

    async def _tenant_screen(self, ctx, tenant_id: int, note: str = "") -> None:
        async with self.db.session() as s:
            tenant = await s.get(Tenant, tenant_id)
        if tenant is None or tenant.status == TenantStatus.DELETED:
            await self.show(ctx, "ربات پیدا نشد.", [[Btn("🔙", "a:tenants")]])
            return
        left = days_left(tenant.expires_at, utcnow())
        lines = [
            f"🤖 <b>#{tenant.id} {esc(self.tenant_label(tenant))}</b>",
            f"مالک: <code>{tenant.owner_id}</code>",
            f"وضعیت: {STATUS_LABEL.get(tenant.status, tenant.status)}",
            f"اعتبار تا: {fmt_date(tenant.expires_at)}" + (f" ({left} روز)" if left is not None else ""),
            f"ادمین‌ها: <code>{esc(tenant.admin_ids)}</code>",
            f"پورت میزبان: {tenant.host_port or '—'}",
        ]
        if tenant.is_trial:
            lines.append("🎁 آزمایشی")
        if tenant.last_error:
            lines.append(f"\n⚠️ آخرین خطا:\n<pre>{esc(tenant.last_error[:600])}</pre>")
        if note:
            lines += ["", note]
        i = tenant.id
        rows: Buttons = [
            [Btn("▶️ شروع", f"a:tstart:{i}"), Btn("⏸ توقف", f"a:tstop:{i}"), Btn("🔁 ریستارت", f"a:trestart:{i}")],
            [Btn("🚀 دیپلوی مجدد", f"a:tdeploy:{i}"), Btn("📜 لاگ", f"a:tlogs:{i}")],
            [Btn("➕ تمدید (روز)", f"a:text:{i}"), Btn("👤 ادمین‌ها", f"a:tadm:{i}")],
            [Btn("👥 مالک", f"a:user:{tenant.owner_id}"), Btn("🗑 حذف", f"a:tdel:{i}")],
            [Btn("🔙 ربات‌ها", "a:tenants")],
        ]
        await self.show(ctx, "\n".join(lines), rows)

    @route("a:tenant")
    async def tenant_detail(self, ctx, tenant_id: str = "") -> None:
        if tenant_id.isdigit():
            await self._tenant_screen(ctx, int(tenant_id))

    async def _tenant_action(self, ctx, tenant_id: str, verb: str, ok_note: str) -> None:
        if not tenant_id.isdigit():
            return
        result = await getattr(self.ops, verb)(int(tenant_id))
        await self._tenant_screen(
            ctx, int(tenant_id), ok_note if result.ok else f"❌ {esc((result.error or '')[:300])}"
        )

    @route("a:tstart")
    async def tenant_start(self, ctx, tenant_id: str = "") -> None:
        await self._tenant_action(ctx, tenant_id, "start", "✅ شروع شد.")

    @route("a:tstop")
    async def tenant_stop(self, ctx, tenant_id: str = "") -> None:
        await self._tenant_action(ctx, tenant_id, "stop", "✅ متوقف شد.")

    @route("a:trestart")
    async def tenant_restart(self, ctx, tenant_id: str = "") -> None:
        await self._tenant_action(ctx, tenant_id, "restart", "✅ ریستارت شد.")

    @route("a:tdeploy")
    async def tenant_deploy(self, ctx, tenant_id: str = "") -> None:
        if not tenant_id.isdigit():
            return
        await ctx.answer("⏳ در حال دیپلوی…")
        result = await self.ops.deploy(int(tenant_id))
        if result.ok:
            self.panels.link_tenant_panels(int(tenant_id))
            async with self.db.session() as s:
                tenant = await s.get(Tenant, int(tenant_id))
            await self.activate_for_owner(billing.Fulfillment(tenant_id=tenant.id, needs_deploy=False), tenant.owner_id)
        await self._tenant_screen(
            ctx, int(tenant_id), "✅ دیپلوی شد." if result.ok else f"❌ {esc((result.error or '')[:300])}"
        )

    @route("a:tlogs")
    async def tenant_logs(self, ctx, tenant_id: str = "") -> None:
        if not tenant_id.isdigit():
            return
        logs = await self.ops.logs(int(tenant_id), tail=30)
        await self.show(
            ctx,
            f"📜 <b>لاگ #{tenant_id}</b>\n<pre>{esc(logs[-3200:] or '— خالی —')}</pre>",
            [[Btn("🔙", f"a:tenant:{tenant_id}")]],
        )

    @route("a:text")
    async def tenant_extend_ask(self, ctx, tenant_id: str = "") -> None:
        if not tenant_id.isdigit():
            return
        self.set_state(ctx.user_id, "tenant_extend", tenant_id=int(tenant_id))
        await self.show(ctx, "چند روز به اعتبار اضافه شود؟", [[Btn("❌ انصراف", f"a:tenant:{tenant_id}")]])

    @on_state("tenant_extend")
    async def got_tenant_extend(self, ctx, state) -> None:
        days = parse_int(ctx.text, minimum=1, maximum=3650)
        if days is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        tenant_id = state.data["tenant_id"]
        async with self.db.session() as s:
            tenant = await s.get(Tenant, tenant_id)
            if tenant is None or tenant.status == TenantStatus.DELETED:
                self.clear_state(ctx.user_id)
                await ctx.reply("ربات پیدا نشد.", BACK)
                return
            banked = tenant.token_enc is None  # no bot yet: the days start when it is running
            needs_deploy = not banked and tenant.status in (
                TenantStatus.PENDING,
                TenantStatus.EXPIRED,
                TenantStatus.ERROR,
            )
            if banked:
                tenant.pending_days += days
            else:
                billing.extend_tenant(tenant, days)
            owner_id = tenant.owner_id
            await s.commit()
        self.clear_state(ctx.user_id)
        if not banked:
            await self.activate_for_owner(billing.Fulfillment(tenant_id=tenant_id, needs_deploy=needs_deploy), owner_id)
        await self._tenant_screen(ctx, tenant_id, f"✅ {days} روز اضافه شد.")

    @route("a:tadm")
    async def tenant_admins_ask(self, ctx, tenant_id: str = "") -> None:
        if not tenant_id.isdigit():
            return
        self.set_state(ctx.user_id, "tenant_admins", tenant_id=int(tenant_id))
        await self.show(ctx, "آیدی(های) عددی جدید ادمین را بفرستید:", [[Btn("❌ انصراف", f"a:tenant:{tenant_id}")]])

    @on_state("tenant_admins")
    async def got_tenant_admins(self, ctx, state) -> None:
        from builder.util import parse_admin_ids

        ids = parse_admin_ids(ctx.text)
        if ids is None:
            await ctx.reply("❌ آیدی نامعتبر است.")
            return
        tenant_id = state.data["tenant_id"]
        self.clear_state(ctx.user_id)
        result = await self.ops.set_admins(tenant_id, ids)
        await self._tenant_screen(
            ctx, tenant_id, "✅ انجام شد." if result.ok else f"❌ {esc((result.error or '')[:300])}"
        )

    @route("a:tdel")
    async def tenant_delete_ask(self, ctx, tenant_id: str = "") -> None:
        await self.show(
            ctx,
            f"⚠️ ربات #{esc(tenant_id)} با دیتابیس و فایل‌هایش حذف می‌شود. مطمئنید؟",
            [[Btn("✅ حذف", f"a:tdelok:{tenant_id}")], [Btn("🔙 خیر", f"a:tenant:{tenant_id}")]],
        )

    @route("a:tdelok")
    async def tenant_delete(self, ctx, tenant_id: str = "") -> None:
        if not tenant_id.isdigit():
            return
        result = await self.ops.delete(int(tenant_id))
        await self.show(
            ctx,
            "🗑 حذف شد." if result.ok else f"❌ {esc((result.error or '')[:300])}",
            [[Btn("🔙 ربات‌ها", "a:tenants")]],
        )

    # ── users ─────────────────────────────────────────────────────────────────
    @route("a:users")
    async def users(self, ctx) -> None:
        self.set_state(ctx.user_id, "user_search")
        await self.show(ctx, "آیدی عددی کاربر را بفرستید:", BACK)

    @on_state("user_search")
    async def got_user_search(self, ctx, state) -> None:
        uid = parse_int(ctx.text, minimum=1)
        if uid is None:
            await ctx.reply("❌ آیدی عددی معتبر بفرستید.")
            return
        self.clear_state(ctx.user_id)
        await self._user_screen(ctx, uid)

    async def _user_screen(self, ctx, uid: int, note: str = "") -> None:
        async with self.db.session() as s:
            user = await s.get(BuilderUser, uid)
            owned = await billing.count_owner_tenants(s, uid) if user else 0
        if user is None:
            await self.show(ctx, "کاربری با این آیدی پیدا نشد.", BACK)
            return
        lines = [
            f"👤 <b>{esc(user.first_name)}</b> {('@' + esc(user.username)) if user.username else ''}",
            f"آیدی: <code>{user.id}</code>",
            f"موجودی: {fmt_money(user.balance)}",
            f"ربات‌ها: {owned}",
            f"وضعیت: {'🚫 مسدود' if user.banned else '✅ عادی'}",
            f"عضویت: {fmt_date(user.created_at)}",
        ]
        if note:
            lines += ["", note]
        rows: Buttons = [
            [
                Btn("✅ رفع مسدودی" if user.banned else "🚫 مسدود", f"a:uban:{uid}"),
                Btn("💰 تغییر موجودی", f"a:ubal:{uid}"),
            ],
            [Btn("✉️ پیام", f"a:umsg:{uid}")],
            *BACK,
        ]
        await self.show(ctx, "\n".join(lines), rows)

    @route("a:user")
    async def user_detail(self, ctx, uid: str = "") -> None:
        if uid.isdigit():
            await self._user_screen(ctx, int(uid))

    @route("a:uban")
    async def user_ban(self, ctx, uid: str = "") -> None:
        if not uid.isdigit():
            return
        async with self.db.session() as s:
            user = await s.get(BuilderUser, int(uid))
            if user is not None and int(uid) not in self.settings.admin_ids:
                user.banned = not user.banned
                await s.commit()
        await self._user_screen(ctx, int(uid))

    @route("a:ubal")
    async def user_balance_ask(self, ctx, uid: str = "") -> None:
        if not uid.isdigit():
            return
        self.set_state(ctx.user_id, "user_balance", uid=int(uid))
        await self.show(
            ctx, "مبلغ تغییر موجودی را بفرستید (مثبت = افزایش، منفی = کاهش):", [[Btn("❌ انصراف", f"a:user:{uid}")]]
        )

    @on_state("user_balance")
    async def got_user_balance(self, ctx, state) -> None:
        delta = parse_int(ctx.text)
        if delta is None or delta == 0:
            await ctx.reply("❌ عدد معتبر بفرستید (مثل 50000 یا -20000).")
            return
        uid = state.data["uid"]
        async with self.db.session() as s:
            ok = await billing.adjust_balance(s, uid, delta)
            await s.commit()
        self.clear_state(ctx.user_id)
        log.info("admin %s adjusted wallet of %s by %s (ok=%s)", ctx.user_id, uid, delta, ok)
        if ok:
            await self.notifier.send(
                uid,
                f"💰 موجودی کیف پول شما توسط مدیریت {fmt_money(abs(delta))} {'افزایش' if delta > 0 else 'کاهش'} یافت.",
            )
        await self._user_screen(ctx, uid, "✅ انجام شد." if ok else "❌ موجودی کاربر برای این کاهش کافی نیست.")

    @route("a:umsg")
    async def user_message_ask(self, ctx, uid: str = "") -> None:
        if not uid.isdigit():
            return
        self.set_state(ctx.user_id, "user_msg", uid=int(uid))
        await self.show(ctx, "متن پیام را بفرستید:", [[Btn("❌ انصراف", f"a:user:{uid}")]])

    @on_state("user_msg")
    async def got_user_message(self, ctx, state) -> None:
        uid = state.data["uid"]
        sent = await self.notifier.send(uid, f"📩 <b>پیام مدیریت</b>\n\n{esc(ctx.text)}")
        self.clear_state(ctx.user_id)
        await self._user_screen(ctx, uid, "✅ ارسال شد." if sent else "⚠️ ارسال نشد (کاربر ربات را مسدود کرده؟).")

    # ── payments ──────────────────────────────────────────────────────────────
    async def payment_summary(self, payment_id: int) -> str:
        async with self.db.session() as s:
            payment = await s.get(Payment, payment_id)
            user = await s.get(BuilderUser, payment.user_id)
            plan = await s.get(Plan, payment.plan_id) if payment.plan_id else None
            tenant = await s.get(Tenant, payment.tenant_id) if payment.tenant_id else None
            panel_plan = await s.get(PanelPlan, payment.panel_plan_id) if payment.panel_plan_id else None
            panel_sub = await s.get(PanelSub, payment.panel_sub_id) if payment.panel_sub_id else None
            panel_bot = await s.get(Tenant, panel_sub.tenant_id) if panel_sub and panel_sub.tenant_id else None
        who = f"{esc(user.first_name)} (<code>{user.id}</code>)" if user else f"<code>{payment.user_id}</code>"
        lines = [
            f"💳 <b>پرداخت #{payment.id}</b> — {KIND_LABEL.get(payment.kind, payment.kind)}",
            f"کاربر: {who}",
            f"مبلغ: <b>{fmt_money(payment.amount)}</b>",
        ]
        if plan:
            lines.append(f"تعرفه: {esc(plan.name)} ({plan.duration_days} روز)")
        if tenant:
            lines.append(f"ربات: {esc(self.tenant_label(tenant))}")
        if panel_plan:
            lines.append(f"پلن پنل: {esc(panel_plan.name)} ({panel_plan.data_gb} GB)")
        if panel_sub and panel_sub.admin_username:
            lines.append(f"ادمین پنل: <code>{esc(panel_sub.admin_username)}</code>")
        if panel_bot:
            lines.append(f"ربات متصل‌شونده: {esc(self.tenant_label(panel_bot))}")
        if payment.discount_code:
            lines.append(f"کد تخفیف: {esc(payment.discount_code)}")
        lines.append(f"وضعیت: {payment.status}")
        return "\n".join(lines)

    @route("a:pays")
    async def pending_payments(self, ctx) -> None:
        async with self.db.session() as s:
            pending = (
                (
                    await s.execute(
                        select(Payment)
                        .where(Payment.status == PaymentStatus.PENDING, Payment.note == "receipt")
                        .order_by(Payment.id)
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
        rows: Buttons = [
            [Btn(f"#{p.id} · {KIND_LABEL.get(p.kind, p.kind)} · {fmt_money(p.amount)}", f"a:pay:{p.id}")]
            for p in pending
        ]
        rows += [[Btn("🗂 تاریخچه", "a:payh")], *BACK]
        await self.show(ctx, "💳 <b>پرداخت‌های در انتظار تایید</b>" if pending else "💳 پرداختی در انتظار نیست.", rows)

    @route("a:pay")
    async def payment_detail(self, ctx, payment_id: str = "") -> None:
        if not payment_id.isdigit():
            return
        text = await self.payment_summary(int(payment_id))
        rows: Buttons = [
            [Btn("✅ تایید", f"a:pok:{payment_id}"), Btn("❌ رد", f"a:pno:{payment_id}")],
            [Btn("🔙", "a:pays")],
        ]
        await self.show(ctx, text, rows)

    @route("a:payh")
    async def payment_history(self, ctx) -> None:
        async with self.db.session() as s:
            recent = (await s.execute(select(Payment).order_by(Payment.id.desc()).limit(15))).scalars().all()
        icon = {PaymentStatus.APPROVED: "✅", PaymentStatus.REJECTED: "❌", PaymentStatus.PENDING: "⏳"}
        lines = ["🗂 <b>۱۵ پرداخت اخیر</b>\n"] + [
            f"{icon.get(p.status, '•')} #{p.id} · {p.user_id} · {KIND_LABEL.get(p.kind, p.kind)} · {fmt_money(p.amount)} · {p.method}"
            for p in recent
        ]
        await self.show(ctx, "\n".join(lines) if recent else "پرداختی ثبت نشده است.", [[Btn("🔙", "a:pays")]])

    async def _decide(self, ctx, payment_id: str, approve: bool) -> None:
        if not payment_id.isdigit():
            return
        async with self.db.session() as s:
            decided = await billing.decide_payment(s, int(payment_id), approve=approve, admin_id=ctx.user_id)
            await s.commit()
        if decided is None:
            await ctx.answer("این پرداخت قبلاً بررسی شده است.", alert=True)
            await self.show(ctx, await self.payment_summary(int(payment_id)), BACK)
            return
        payment, fulfillment = decided
        verdict = "✅ تایید شد" if approve else "❌ رد شد"
        await self.show(
            ctx, f"{await self.payment_summary(payment.id)}\n\n{verdict} توسط <code>{ctx.user_id}</code>", BACK
        )
        if approve:
            await self.notifier.send(payment.user_id, f"✅ پرداخت #{payment.id} شما تایید شد.")
            await self.activate_for_owner(fulfillment, payment.user_id)
        else:
            await self.notifier.send(
                payment.user_id, f"❌ پرداخت #{payment.id} شما تایید نشد. در صورت اشتباه با پشتیبانی تماس بگیرید."
            )

    @route("a:pok")
    async def payment_ok(self, ctx, payment_id: str = "") -> None:
        await self._decide(ctx, payment_id, True)

    @route("a:pno")
    async def payment_no(self, ctx, payment_id: str = "") -> None:
        await self._decide(ctx, payment_id, False)

    # ── cards ─────────────────────────────────────────────────────────────────
    @route("a:cards")
    async def cards(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        async with self.db.session() as s:
            cards = (await s.execute(select(Card).order_by(Card.id))).scalars().all()
        rows: Buttons = [
            [Btn(f"{'🟢' if c.active else '⚪️'} {c.number}", f"a:cardt:{c.id}"), Btn("🗑", f"a:cardd:{c.id}")]
            for c in cards
        ]
        rows += [[Btn("➕ افزودن کارت", "a:cardadd")], *BACK]
        await self.show(ctx, "🏦 <b>کارت‌های دریافت وجه</b>\n(روی شماره بزنید تا فعال/غیرفعال شود)", rows)

    @route("a:cardt")
    async def card_toggle(self, ctx, card_id: str = "") -> None:
        async with self.db.session() as s:
            card = await s.get(Card, int(card_id)) if card_id.isdigit() else None
            if card is not None:
                card.active = not card.active
                await s.commit()
        await self.cards(ctx)

    @route("a:cardd")
    async def card_delete(self, ctx, card_id: str = "") -> None:
        async with self.db.session() as s:
            card = await s.get(Card, int(card_id)) if card_id.isdigit() else None
            if card is not None:
                await s.delete(card)
                await s.commit()
        await self.cards(ctx)

    @route("a:cardadd")
    async def card_add(self, ctx) -> None:
        self.set_state(ctx.user_id, "card_number")
        await self.show(ctx, "شمارهٔ کارت را بفرستید:", [[Btn("❌ انصراف", "a:cards")]])

    @on_state("card_number")
    async def got_card_number(self, ctx, state) -> None:
        from builder.util import normalize_digits

        number = normalize_digits(ctx.text).replace(" ", "").replace("-", "")
        if not number.isdigit() or not (12 <= len(number) <= 19):
            await ctx.reply("❌ شمارهٔ کارت معتبر نیست (۱۶ رقم).")
            return
        state.data["number"], state.name = number, "card_holder"
        await ctx.reply("نام صاحب کارت را بفرستید (یا - برای خالی):")

    @on_state("card_holder")
    async def got_card_holder(self, ctx, state) -> None:
        holder = "" if ctx.text.strip() == "-" else ctx.text.strip()[:100]
        number = state.data["number"]
        async with self.db.session() as s:
            s.add(Card(number=" ".join(number[i : i + 4] for i in range(0, len(number), 4)), holder=holder))
            await s.commit()
        await self.cards(ctx)

    # ── discount codes ────────────────────────────────────────────────────────
    @route("a:codes")
    async def codes(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        async with self.db.session() as s:
            codes = (await s.execute(select(DiscountCode).order_by(DiscountCode.code))).scalars().all()
        rows: Buttons = [
            [
                Btn(
                    f"{'🟢' if c.active else '⚪️'} {c.code} · {c.percent}٪ · {c.used}/{c.max_uses or '∞'}",
                    f"a:codet:{c.code}",
                ),
                Btn("🗑", f"a:coded:{c.code}"),
            ]
            for c in codes
        ]
        rows += [[Btn("➕ کد جدید", "a:codeadd")], *BACK]
        await self.show(ctx, "🏷 <b>کدهای تخفیف</b>", rows)

    @route("a:codet")
    async def code_toggle(self, ctx, code: str = "") -> None:
        async with self.db.session() as s:
            row = await s.get(DiscountCode, code)
            if row is not None:
                row.active = not row.active
                await s.commit()
        await self.codes(ctx)

    @route("a:coded")
    async def code_delete(self, ctx, code: str = "") -> None:
        async with self.db.session() as s:
            row = await s.get(DiscountCode, code)
            if row is not None:
                await s.delete(row)
                await s.commit()
        await self.codes(ctx)

    @route("a:codeadd")
    async def code_add(self, ctx) -> None:
        self.set_state(ctx.user_id, "code_name")
        await self.show(ctx, "متن کد را بفرستید (حروف انگلیسی/عدد، مثلاً OFF20):", [[Btn("❌ انصراف", "a:codes")]])

    @on_state("code_name")
    async def got_code_name(self, ctx, state) -> None:
        code = ctx.text.strip().upper()
        if not code.replace("_", "").isalnum() or not code.isascii() or len(code) > 32:
            await ctx.reply("❌ فقط حروف انگلیسی، عدد و _ (حداکثر ۳۲ کاراکتر).")
            return
        async with self.db.session() as s:
            if await s.get(DiscountCode, code) is not None:
                await ctx.reply("❌ این کد قبلاً ثبت شده است.")
                return
        state.data["code"], state.name = code, "code_percent"
        await ctx.reply("درصد تخفیف (۱ تا ۱۰۰):")

    @on_state("code_percent")
    async def got_code_percent(self, ctx, state) -> None:
        percent = parse_int(ctx.text, minimum=1, maximum=100)
        if percent is None:
            await ctx.reply("❌ عددی بین ۱ تا ۱۰۰ بفرستید.")
            return
        state.data["percent"], state.name = percent, "code_max"
        await ctx.reply("حداکثر تعداد استفاده (۰ = نامحدود):")

    @on_state("code_max")
    async def got_code_max(self, ctx, state) -> None:
        max_uses = parse_int(ctx.text, minimum=0, maximum=10_000_000)
        if max_uses is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        state.data["max_uses"], state.name = max_uses, "code_days"
        await ctx.reply("اعتبار کد چند روز باشد؟ (۰ = بدون انقضا)")

    @on_state("code_days")
    async def got_code_days(self, ctx, state) -> None:
        from datetime import timedelta

        days = parse_int(ctx.text, minimum=0, maximum=3650)
        if days is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        d = state.data
        async with self.db.session() as s:
            s.add(
                DiscountCode(
                    code=d["code"],
                    percent=d["percent"],
                    max_uses=d["max_uses"],
                    expires_at=utcnow() + timedelta(days=days) if days else None,
                )
            )
            await s.commit()
        await self.codes(ctx)

    # ── settings ──────────────────────────────────────────────────────────────
    @route("a:settings")
    async def settings_screen(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        cfg = await self.cfg()
        rows: Buttons = []
        for key, label, kind in EDITABLE:
            if kind == "bool":
                shown = "✅" if cfg.bool(key) else "❌"
            else:
                value = cfg.str(key).replace("\n", " ")
                shown = (value[:18] + "…") if len(value) > 18 else (value or "—")
            rows.append([Btn(f"{label}: {shown}", f"a:set:{key}")])
        rows += BACK
        await self.show(ctx, "⚙️ <b>تنظیمات</b>\nروی هر مورد بزنید تا تغییر کند.", rows)

    @route("a:set")
    async def setting_edit(self, ctx, key: str = "") -> None:
        meta = EDITABLE_KEYS.get(key)
        if meta is None:
            return
        label, kind = meta
        if kind == "bool":
            cfg = await self.cfg()
            async with self.db.session() as s:
                await set_cfg(s, key, "0" if cfg.bool(key) else "1")
                await s.commit()
            await self.settings_screen(ctx)
            return
        cfg = await self.cfg()
        self.set_state(ctx.user_id, "set_value", key=key)
        hint = "عدد بفرستید." if kind == "int" else "متن را بفرستید (یا - برای خالی کردن)."
        await self.show(
            ctx,
            f"{label}\nمقدار فعلی: <code>{esc(cfg.str(key) or '—')}</code>\n\n{hint}",
            [[Btn("❌ انصراف", "a:settings")]],
        )

    @on_state("set_value")
    async def got_setting(self, ctx, state) -> None:
        key = state.data["key"]
        _, kind = EDITABLE_KEYS[key]
        if kind == "int":
            number = parse_int(ctx.text, minimum=0, maximum=10_000_000_000)
            if number is None:
                await ctx.reply("❌ عدد معتبر بفرستید.")
                return
            value = str(number)
        else:
            value = "" if ctx.text.strip() == "-" else ctx.text.strip()
        async with self.db.session() as s:
            await set_cfg(s, key, value)
            await s.commit()
        await self.settings_screen(ctx)

    # ── broadcast ─────────────────────────────────────────────────────────────
    @route("a:bc")
    async def broadcast_ask(self, ctx) -> None:
        self.set_state(ctx.user_id, "bc_text")
        await self.show(ctx, "📢 متن پیام همگانی را بفرستید:", BACK)

    @on_state("bc_text")
    async def got_broadcast(self, ctx, state) -> None:
        text = ctx.text.strip()
        if not text:
            await ctx.reply("❌ متن خالی است.")
            return
        state.data["text"], state.name = text, "bc_confirm"
        async with self.db.session() as s:
            total = int(
                (await s.execute(select(func.count(BuilderUser.id)).where(BuilderUser.banned.is_(False)))).scalar_one()
            )
        await ctx.reply(
            f"پیش‌نمایش:\n\n{esc(text)}\n\nبه {total} کاربر ارسال شود؟",
            [[Btn("✅ ارسال", "a:bcok")], [Btn("❌ انصراف", "a:home")]],
        )

    @route("a:bcok")
    async def broadcast_send(self, ctx) -> None:
        state = self._need_state(ctx, "bc_confirm")
        if state is None:
            await self._expired_session(ctx)
            return
        text = state.data["text"]
        self.clear_state(ctx.user_id)
        await self.show(ctx, "⏳ ارسال آغاز شد؛ پس از پایان گزارش می‌دهم.", BACK)
        task = asyncio.create_task(self._run_broadcast(ctx.user_id, text))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _run_broadcast(self, admin_id: int, text: str) -> None:
        async with self.db.session() as s:
            ids = (await s.execute(select(BuilderUser.id).where(BuilderUser.banned.is_(False)))).scalars().all()
        sent = failed = 0
        for uid in ids:
            if await self.notifier.send(uid, esc(text)):
                sent += 1
            else:
                failed += 1
            await asyncio.sleep(0.06)  # stay well under Telegram's ~30 msg/s limit
        await self.notifier.send(admin_id, f"📢 ارسال همگانی تمام شد.\n✅ {sent} | ❌ {failed}", BACK)

    # ── server ────────────────────────────────────────────────────────────────
    @route("a:server")
    async def server(self, ctx) -> None:
        summary = await self.ops.prov.server_summary()
        cfg = await self.cfg()
        text = (
            "🖥 <b>سرور</b>\n\n"
            f"<code>{esc(summary)}</code>\n\n"
            f"ایمیج ربات‌ها: <code>{esc(self.settings.tenant_image)}</code>\n"
            f"محدودیت هر ربات: {esc(cfg.str('tenant_memory'))} RAM / {esc(cfg.str('tenant_cpus'))} CPU"
        )
        await self.show(ctx, text, [[Btn("⬆️ به‌روزرسانی همهٔ ربات‌ها", "a:updall")], *BACK])

    @route("a:updall")
    async def update_all_ask(self, ctx) -> None:
        await self.show(
            ctx,
            "آخرین ایمیج دانلود و همهٔ ربات‌های فعال با آن دوباره ساخته می‌شوند (چند ثانیه قطعی برای هر ربات). ادامه؟",
            [[Btn("✅ بله", "a:updallok")], [Btn("🔙 خیر", "a:server")]],
        )

    @route("a:updallok")
    async def update_all(self, ctx) -> None:
        await self.show(ctx, "⏳ در حال به‌روزرسانی… (ممکن است چند دقیقه طول بکشد)")
        try:
            ok, failed = await self.ops.update_all()
        except Exception as exc:
            await self.show(ctx, f"❌ به‌روزرسانی ناموفق: <code>{esc(str(exc)[:500])}</code>", [[Btn("🔙", "a:server")]])
            return
        await self.show(ctx, f"✅ به‌روزرسانی تمام شد.\nموفق: {ok} | ناموفق: {failed}", [[Btn("🔙", "a:server")]])
