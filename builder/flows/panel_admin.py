"""Builder admin: master panels, panel plans and the panels sold to customers."""

from __future__ import annotations

from sqlalchemy import func, select

from builder.flows.panels import PANEL_STATUS
from builder.models import (
    MasterPanel,
    PanelAuth,
    PanelPlan,
    PanelSub,
    PanelSubStatus,
    Tenant,
    utcnow,
)
from builder.routing import on_state, route
from builder.services import billing
from builder.services.panels.backend import PanelError
from builder.ui import Btn, Buttons
from builder.util import days_left, esc, fmt_bytes, fmt_date, fmt_money, parse_int, usage_bar

PAGE = 8
BACK_PMENU: Buttons = [[Btn("🔙 فروش پنل", "a:pmenu")]]


class PanelAdminFlows:
    @route("a:pmenu")
    async def panel_menu(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        async with self.db.session() as s:
            masters = int((await s.execute(select(func.count(MasterPanel.id)))).scalar_one())
            active = int(
                (
                    await s.execute(select(func.count(PanelSub.id)).where(PanelSub.status == PanelSubStatus.ACTIVE))
                ).scalar_one()
            )
        rows: Buttons = [
            [Btn("🛰 پنل‌های اصلی", "a:mp"), Btn("📦 پلن‌های پنل", "a:pp")],
            [Btn("🧩 پنل‌های فروخته‌شده", "a:ps")],
            [Btn("🔙 پنل مدیریت", "a:home")],
        ]
        await self.show(
            ctx,
            f"🧩 <b>فروش پنل</b>\n\nپنل‌های اصلی: {masters} | پنل‌های فعال مشتری: {active}\n\n"
            "ابتدا پنل پاسارگارد خودتان را وصل کنید، بعد پلن (حجم/مدت/قیمت) تعریف کنید.",
            rows,
        )

    # ── master panels ─────────────────────────────────────────────────────────
    @route("a:mp")
    async def master_list(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        async with self.db.session() as s:
            masters = (await s.execute(select(MasterPanel).order_by(MasterPanel.id))).scalars().all()
        rows: Buttons = [[Btn(f"{'🟢' if m.enabled else '⚪️'} {m.name}", f"a:mpd:{m.id}")] for m in masters]
        rows += [[Btn("➕ اتصال پنل جدید", "a:mpadd")], *BACK_PMENU]
        await self.show(ctx, "🛰 <b>پنل‌های اصلی</b>\nپنل‌هایی که مشتری‌ها روی آن ادمین می‌گیرند.", rows)

    async def _master_screen(self, ctx, master_id: int, note: str = "") -> None:
        async with self.db.session() as s:
            master = await s.get(MasterPanel, master_id)
            subs = int(
                (
                    await s.execute(
                        select(func.count(PanelSub.id)).where(
                            PanelSub.master_id == master_id, PanelSub.status != PanelSubStatus.DELETED
                        )
                    )
                ).scalar_one()
            )
        if master is None:
            await self.show(ctx, "پنل پیدا نشد.", [[Btn("🔙", "a:mp")]])
            return
        health = {None: "—", True: "✅ سالم", False: "❌ خطا"}[master.last_check_ok]
        lines = [
            f"🛰 <b>{esc(master.name)}</b>",
            f"🌐 <code>{esc(master.base_url)}</code>",
            f"🔐 ورود: {'API Key' if master.auth_type == PanelAuth.API_KEY else 'نام کاربری/رمز'}",
            f"🎭 نقش ادمین مشتری: {master.role_id if master.role_id is not None else '❗️ انتخاب نشده'}",
            f"👥 گروه‌ها: {esc(master.group_ids) or 'همهٔ گروه‌ها'}",
            f"📡 آخرین تست: {health}",
            f"🧩 اشتراک‌ها: {subs}",
            f"وضعیت: {'فعال' if master.enabled else 'غیرفعال'}",
        ]
        if master.role_id is None:
            lines.append("\n⚠️ تا نقش انتخاب نشود، فروش این پنل ممکن نیست.")
        if note:
            lines += ["", note]
        i = master.id
        rows: Buttons = [
            [Btn("🔌 تست اتصال", f"a:mpt:{i}"), Btn("🎭 نقش", f"a:mpr:{i}")],
            [Btn("👥 گروه‌ها", f"a:mpg:{i}"), Btn("🔁 فعال/غیرفعال", f"a:mpe:{i}")],
            [Btn("🗑 حذف", f"a:mpx:{i}")],
            [Btn("🔙 پنل‌های اصلی", "a:mp")],
        ]
        await self.show(ctx, "\n".join(lines), rows)

    @route("a:mpd")
    async def master_detail(self, ctx, master_id: str = "") -> None:
        if master_id.isdigit():
            await self._master_screen(ctx, int(master_id))

    @route("a:mpadd")
    async def master_add(self, ctx) -> None:
        self.set_state(ctx.user_id, "mp_name")
        await self.show(ctx, "نام نمایشی پنل را بفرستید (مثلاً «پنل اصلی»):", [[Btn("❌ انصراف", "a:mp")]])

    @on_state("mp_name")
    async def master_got_name(self, ctx, state) -> None:
        name = ctx.text.strip()[:100]
        if not name:
            await ctx.reply("❌ نام نامعتبر است.")
            return
        state.data["name"], state.name = name, "mp_url"
        await ctx.reply("آدرس پنل را بفرستید (مثلاً <code>https://panel.example.com</code>):")

    @on_state("mp_url")
    async def master_got_url(self, ctx, state) -> None:
        url = ctx.text.strip().rstrip("/")
        if not url.startswith(("http://", "https://")) or len(url) > 255:
            await ctx.reply("❌ آدرس باید با http:// یا https:// شروع شود.")
            return
        state.data["url"], state.name = url, "mp_auth"
        await ctx.reply(
            "روش ورود به پنل:",
            [
                [
                    Btn("🔑 API Key", f"a:mpauth:{PanelAuth.API_KEY}"),
                    Btn("👤 نام کاربری/رمز", f"a:mpauth:{PanelAuth.PASSWORD}"),
                ]
            ],
        )

    @route("a:mpauth")
    async def master_pick_auth(self, ctx, auth: str = "") -> None:
        state = self._need_state(ctx, "mp_auth")
        if state is None or auth not in (PanelAuth.API_KEY, PanelAuth.PASSWORD):
            await self._expired_session(ctx)
            return
        state.data["auth"] = auth
        if auth == PanelAuth.API_KEY:
            state.data["username"], state.name = "", "mp_secret"
            await self.show(ctx, "API Key پنل را بفرستید (پیام شما پس از دریافت پاک می‌شود):")
        else:
            state.name = "mp_user"
            await self.show(ctx, "نام کاربری ادمین (sudo) پنل را بفرستید:")

    @on_state("mp_user")
    async def master_got_user(self, ctx, state) -> None:
        user = ctx.text.strip()[:100]
        if not user:
            await ctx.reply("❌ نام کاربری نامعتبر است.")
            return
        state.data["username"], state.name = user, "mp_secret"
        await ctx.reply("رمز عبور را بفرستید (پیام شما پس از دریافت پاک می‌شود):")

    @on_state("mp_secret")
    async def master_got_secret(self, ctx, state) -> None:
        secret = ctx.text.strip()
        await ctx.delete_incoming()
        if not secret:
            await ctx.reply("❌ مقدار خالی است.")
            return
        d = state.data
        async with self.db.session() as s:
            master = MasterPanel(
                name=d["name"],
                base_url=d["url"],
                auth_type=d["auth"],
                username=d["username"],
                secret_enc=self.box.encrypt(secret),
                enabled=True,
            )
            s.add(master)
            await s.commit()
        self.clear_state(ctx.user_id)
        await ctx.reply("⏳ در حال تست اتصال…")
        await self._pick_role(ctx, master.id, intro="✅ پنل ذخیره شد.\n")

    async def _check(self, master_id: int):
        """Contact the panel; records the outcome. Returns (master, check | None, error | None)."""
        async with self.db.session() as s:
            master = await s.get(MasterPanel, master_id)
        try:
            check = await self.panels.backend.check(master)
            error = None
        except PanelError as exc:
            check, error = None, str(exc)
        async with self.db.session() as s:
            row = await s.get(MasterPanel, master_id)
            row.last_check_ok, row.last_check_at = error is None, utcnow()
            await s.commit()
        return master, check, error

    async def _pick_role(self, ctx, master_id: int, intro: str = "") -> None:
        master, check, error = await self._check(master_id)
        if error:
            await self.show(
                ctx,
                f"{intro}❌ اتصال به پنل برقرار نشد:\n<code>{esc(error)}</code>\n\nاطلاعات را در «ویرایش» اصلاح یا پنل را حذف کنید.",
                [[Btn("🔌 تست دوباره", f"a:mpt:{master_id}")], *[[Btn("🔙 پنل‌های اصلی", "a:mp")]]],
            )
            return
        rows: Buttons = [
            [Btn(f"{'✅ ' if master.role_id == rid else ''}{name} (#{rid})", f"a:mprole:{master_id}:{rid}")]
            for rid, name in check.roles
        ]
        rows.append([Btn("🔙 بازگشت", f"a:mpd:{master_id}")])
        await self.show(
            ctx,
            f"{intro}🎭 <b>نقشی</b> را انتخاب کنید که ادمین‌های مشتری روی پنل می‌گیرند "
            "(محدودیت‌های دسترسی از همین نقش می‌آید):",
            rows,
        )

    @route("a:mpr")
    async def master_roles(self, ctx, master_id: str = "") -> None:
        if master_id.isdigit():
            await self._pick_role(ctx, int(master_id))

    @route("a:mprole")
    async def master_set_role(self, ctx, master_id: str = "", role_id: str = "") -> None:
        if not (master_id.isdigit() and role_id.isdigit()):
            return
        async with self.db.session() as s:
            master = await s.get(MasterPanel, int(master_id))
            if master is not None:
                master.role_id = int(role_id)
                await s.commit()
        await self._pick_groups(ctx, int(master_id), intro="✅ نقش ذخیره شد.\n")

    async def _pick_groups(self, ctx, master_id: int, intro: str = "") -> None:
        master, check, error = await self._check(master_id)
        if error:
            await self._master_screen(ctx, master_id, f"❌ {esc(error)}")
            return
        chosen = set(master.group_id_list)
        rows: Buttons = [
            [Btn(f"{'✅' if gid in chosen else '⬜️'} {name}", f"a:mpgrp:{master_id}:{gid}")]
            for gid, name in check.groups
        ]
        rows.append([Btn("✔️ تمام", f"a:mpd:{master_id}")])
        await self.show(
            ctx,
            f"{intro}👥 <b>گروه‌هایی</b> که ربات مشتری برای کاربرانش استفاده می‌کند را انتخاب کنید "
            "(اگر هیچ‌کدام انتخاب نشود، همهٔ گروه‌های پنل):",
            rows,
        )

    @route("a:mpg")
    async def master_groups(self, ctx, master_id: str = "") -> None:
        if master_id.isdigit():
            await self._pick_groups(ctx, int(master_id))

    @route("a:mpgrp")
    async def master_toggle_group(self, ctx, master_id: str = "", group_id: str = "") -> None:
        if not (master_id.isdigit() and group_id.isdigit()):
            return
        async with self.db.session() as s:
            master = await s.get(MasterPanel, int(master_id))
            if master is not None:
                groups = set(master.group_id_list) ^ {int(group_id)}
                master.group_ids = ",".join(str(g) for g in sorted(groups))
                await s.commit()
        await self._pick_groups(ctx, int(master_id))

    @route("a:mpt")
    async def master_test(self, ctx, master_id: str = "") -> None:
        if not master_id.isdigit():
            return
        _, check, error = await self._check(int(master_id))
        note = (
            f"✅ اتصال سالم است ({len(check.roles)} نقش، {len(check.groups)} گروه)."
            if error is None
            else f"❌ {esc(error)}"
        )
        await self._master_screen(ctx, int(master_id), note)

    @route("a:mpe")
    async def master_toggle(self, ctx, master_id: str = "") -> None:
        if not master_id.isdigit():
            return
        async with self.db.session() as s:
            master = await s.get(MasterPanel, int(master_id))
            if master is not None:
                master.enabled = not master.enabled
                await s.commit()
        await self._master_screen(ctx, int(master_id))

    @route("a:mpx")
    async def master_delete_ask(self, ctx, master_id: str = "") -> None:
        await self.show(
            ctx,
            "⚠️ این پنل اصلی از ربات‌ساز حذف می‌شود (ادمین‌های ساخته‌شده روی خود پنل باقی می‌مانند). مطمئنید؟",
            [[Btn("✅ حذف", f"a:mpxok:{master_id}")], [Btn("🔙 خیر", f"a:mpd:{master_id}")]],
        )

    @route("a:mpxok")
    async def master_delete(self, ctx, master_id: str = "") -> None:
        if not master_id.isdigit():
            return
        async with self.db.session() as s:
            live = int(
                (
                    await s.execute(
                        select(func.count(PanelSub.id)).where(
                            PanelSub.master_id == int(master_id), PanelSub.status != PanelSubStatus.DELETED
                        )
                    )
                ).scalar_one()
            )
            if live:
                await self._master_screen(
                    ctx,
                    int(master_id),
                    f"❌ {live} اشتراک روی این پنل هست؛ اول آن‌ها را حذف کنید یا پنل را غیرفعال کنید.",
                )
                return
            master = await s.get(MasterPanel, int(master_id))
            if master is not None:
                for plan in (await s.execute(select(PanelPlan).where(PanelPlan.master_id == master.id))).scalars():
                    plan.active = False
                master.enabled = False
                master.name = f"{master.name} (حذف‌شده)"
                await s.commit()
        await self.master_list(ctx)

    # ── panel plans ───────────────────────────────────────────────────────────
    @route("a:pp")
    async def pplan_list(self, ctx) -> None:
        self.clear_state(ctx.user_id)
        async with self.db.session() as s:
            plans = await billing.list_panel_plans(s, active_only=False)
        rows: Buttons = [
            [
                Btn(
                    f"{'🟢' if p.active else '⚪️'} {p.name} · {p.data_gb}GB"
                    + ("" if p.kind == "addon" else f" · {p.duration_days}d")
                    + f" · {fmt_money(p.price)}",
                    f"a:ppd:{p.id}",
                )
            ]
            for p in plans
        ]
        rows += [[Btn("➕ پلن جدید", "a:ppadd")], *BACK_PMENU]
        await self.show(ctx, "📦 <b>پلن‌های پنل</b>\n(پلن ماهانه یا بستهٔ حجم اضافه)", rows)

    async def _pplan_screen(self, ctx, plan_id: int) -> None:
        async with self.db.session() as s:
            plan = await s.get(PanelPlan, plan_id)
            master = await s.get(MasterPanel, plan.master_id) if plan else None
        if plan is None:
            await self.show(ctx, "پلن پیدا نشد.", [[Btn("🔙", "a:pp")]])
            return
        lines = [
            f"📦 <b>{esc(plan.name)}</b> — {'بستهٔ حجم اضافه' if plan.kind == 'addon' else 'پلن ماهانه'}",
            f"پنل: {esc(master.name) if master else '—'}",
            f"📊 حجم: {plan.data_gb} GB",
        ]
        if plan.kind != "addon":
            lines += [f"📅 مدت: {plan.duration_days} روز", f"👥 سقف کاربر: {plan.max_users or 'نامحدود'}"]
        lines += [f"💵 قیمت: {fmt_money(plan.price)}", f"وضعیت: {'فعال' if plan.active else 'غیرفعال'}"]
        i = plan.id
        rows: Buttons = [
            [Btn("🔁 فعال/غیرفعال", f"a:ppt:{i}")],
            [Btn("✏️ نام", f"a:ppe:{i}:name"), Btn("💵 قیمت", f"a:ppe:{i}:price"), Btn("📊 حجم", f"a:ppe:{i}:gb")],
        ]
        if plan.kind != "addon":
            rows.append([Btn("📅 مدت", f"a:ppe:{i}:days"), Btn("👥 سقف کاربر", f"a:ppe:{i}:users")])
        rows.append([Btn("🔙 پلن‌های پنل", "a:pp")])
        await self.show(ctx, "\n".join(lines), rows)

    @route("a:ppd")
    async def pplan_detail(self, ctx, plan_id: str = "") -> None:
        if plan_id.isdigit():
            await self._pplan_screen(ctx, int(plan_id))

    @route("a:ppt")
    async def pplan_toggle(self, ctx, plan_id: str = "") -> None:
        if plan_id.isdigit():
            async with self.db.session() as s:
                plan = await s.get(PanelPlan, int(plan_id))
                if plan is not None:
                    plan.active = not plan.active
                    await s.commit()
            await self._pplan_screen(ctx, int(plan_id))

    @route("a:ppe")
    async def pplan_edit(self, ctx, plan_id: str = "", field: str = "") -> None:
        if not plan_id.isdigit() or field not in ("name", "price", "gb", "days", "users"):
            return
        self.set_state(ctx.user_id, "pp_edit", plan_id=int(plan_id), field=field)
        prompt = {
            "name": "نام جدید:",
            "price": "قیمت جدید (تومان):",
            "gb": "حجم جدید (GB):",
            "days": "مدت جدید (روز):",
            "users": "سقف کاربر (۰ = نامحدود):",
        }[field]
        await self.show(ctx, prompt, [[Btn("❌ انصراف", f"a:ppd:{plan_id}")]])

    @on_state("pp_edit")
    async def pplan_got_edit(self, ctx, state) -> None:
        field, plan_id = state.data["field"], state.data["plan_id"]
        if field == "name":
            value = ctx.text.strip()[:100] or None
        else:
            value = parse_int(ctx.text, minimum=1 if field in ("gb", "days") else 0, maximum=10_000_000_000)
        if value is None:
            await ctx.reply("❌ مقدار نامعتبر است. دوباره بفرستید.")
            return
        attr = {"name": "name", "price": "price", "gb": "data_gb", "days": "duration_days", "users": "max_users"}[field]
        async with self.db.session() as s:
            plan = await s.get(PanelPlan, plan_id)
            if plan is not None:
                setattr(plan, attr, value)
                await s.commit()
        self.clear_state(ctx.user_id)
        await self._pplan_screen(ctx, plan_id)

    @route("a:ppadd")
    async def pplan_add(self, ctx) -> None:
        async with self.db.session() as s:
            masters = (await s.execute(select(MasterPanel).where(MasterPanel.enabled.is_(True)))).scalars().all()
        if not masters:
            await self.show(ctx, "ابتدا یک پنل اصلی فعال متصل کنید.", [[Btn("🛰 پنل‌های اصلی", "a:mp")], *BACK_PMENU])
            return
        self.set_state(ctx.user_id, "pp_master")
        rows = [[Btn(m.name, f"a:ppm:{m.id}")] for m in masters] + [[Btn("❌ انصراف", "a:pp")]]
        await self.show(ctx, "این پلن روی کدام پنل اصلی فروخته شود؟", rows)

    @route("a:ppm")
    async def pplan_pick_master(self, ctx, master_id: str = "") -> None:
        state = self._need_state(ctx, "pp_master")
        if state is None or not master_id.isdigit():
            await self._expired_session(ctx)
            return
        state.data["master_id"], state.name = int(master_id), "pp_kind"
        await self.show(
            ctx,
            "نوع:",
            [[Btn("📅 پلن ماهانه", "a:ppk:plan"), Btn("➕ بستهٔ حجم اضافه", "a:ppk:addon")]],
        )

    @route("a:ppk")
    async def pplan_pick_kind(self, ctx, kind: str = "") -> None:
        state = self._need_state(ctx, "pp_kind")
        if state is None or kind not in ("plan", "addon"):
            await self._expired_session(ctx)
            return
        state.data["kind"], state.name = kind, "pp_name"
        await self.show(ctx, "نام پلن را بفرستید (مثلاً «۵۰ گیگ»):")

    @on_state("pp_name")
    async def pplan_got_name(self, ctx, state) -> None:
        name = ctx.text.strip()[:100]
        if not name:
            await ctx.reply("❌ نام نامعتبر است.")
            return
        state.data["name"], state.name = name, "pp_gb"
        await ctx.reply("حجم را به گیگابایت بفرستید:")

    @on_state("pp_gb")
    async def pplan_got_gb(self, ctx, state) -> None:
        gb = parse_int(ctx.text, minimum=1, maximum=1_000_000)
        if gb is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        state.data["gb"] = gb
        if state.data["kind"] == "addon":
            state.name = "pp_price"
            await ctx.reply("قیمت را به تومان بفرستید:")
        else:
            state.name = "pp_days"
            await ctx.reply("مدت اعتبار (روز) را بفرستید:")

    @on_state("pp_days")
    async def pplan_got_days(self, ctx, state) -> None:
        days = parse_int(ctx.text, minimum=1, maximum=3650)
        if days is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        state.data["days"], state.name = days, "pp_users"
        await ctx.reply("سقف تعداد کاربر ادمین را بفرستید (۰ = نامحدود):")

    @on_state("pp_users")
    async def pplan_got_users(self, ctx, state) -> None:
        users = parse_int(ctx.text, minimum=0, maximum=1_000_000)
        if users is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        state.data["users"], state.name = users, "pp_price"
        await ctx.reply("قیمت را به تومان بفرستید:")

    @on_state("pp_price")
    async def pplan_got_price(self, ctx, state) -> None:
        price = parse_int(ctx.text, minimum=0, maximum=10_000_000_000)
        if price is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        d = state.data
        async with self.db.session() as s:
            plan = PanelPlan(
                master_id=d["master_id"],
                name=d["name"],
                kind=d["kind"],
                data_gb=d["gb"],
                duration_days=d.get("days", 0),
                max_users=d.get("users", 0),
                price=price,
            )
            s.add(plan)
            await s.commit()
        self.clear_state(ctx.user_id)
        await self._pplan_screen(ctx, plan.id)

    # ── sold panels ───────────────────────────────────────────────────────────
    @route("a:ps")
    async def sub_list(self, ctx, page: str = "0") -> None:
        self.clear_state(ctx.user_id)
        page_no = int(page) if page.isdigit() else 0
        async with self.db.session() as s:
            total = int(
                (
                    await s.execute(
                        select(func.count(PanelSub.id)).where(PanelSub.status.not_in((PanelSubStatus.DELETED,)))
                    )
                ).scalar_one()
            )
            subs = (
                (
                    await s.execute(
                        select(PanelSub)
                        .where(PanelSub.status.not_in((PanelSubStatus.DELETED,)))
                        .order_by(PanelSub.id.desc())
                        .offset(page_no * PAGE)
                        .limit(PAGE)
                    )
                )
                .scalars()
                .all()
            )
        rows: Buttons = [
            [
                Btn(
                    f"{PANEL_STATUS.get(sub.status, sub.status).split()[0]} #{sub.id} {sub.admin_username or '—'} · {sub.owner_id}",
                    f"a:psd:{sub.id}",
                )
            ]
            for sub in subs
        ]
        nav: list[Btn] = []
        if page_no > 0:
            nav.append(Btn("◀️", f"a:ps:{page_no - 1}"))
        if (page_no + 1) * PAGE < total:
            nav.append(Btn("▶️", f"a:ps:{page_no + 1}"))
        if nav:
            rows.append(nav)
        rows += BACK_PMENU
        await self.show(ctx, f"🧩 <b>پنل‌های فروخته‌شده</b> ({total})" if subs else "🧩 هنوز پنلی فروخته نشده است.", rows)

    async def _sub_screen(self, ctx, sub_id: int, note: str = "") -> None:
        await self.panels.refresh_usage(sub_id)
        async with self.db.session() as s:
            sub = await s.get(PanelSub, sub_id)
            master = await s.get(MasterPanel, sub.master_id) if sub else None
            plan = await s.get(PanelPlan, sub.plan_id) if sub and sub.plan_id else None
            bot = await s.get(Tenant, sub.tenant_id) if sub and sub.tenant_id else None
        if sub is None or sub.status == PanelSubStatus.DELETED:
            await self.show(ctx, "پنل پیدا نشد.", [[Btn("🔙", "a:ps")]])
            return
        left = days_left(sub.expires_at, utcnow())
        lines = [
            f"🧩 <b>#{sub.id}</b> <code>{esc(sub.admin_username or '—')}</code>",
            f"مالک: <code>{sub.owner_id}</code>",
            f"پنل اصلی: {esc(master.name) if master else '—'}",
            f"پلن: {esc(plan.name) if plan else '—'}",
            f"وضعیت: {PANEL_STATUS.get(sub.status, sub.status)}",
            f"📊 {fmt_bytes(sub.used_bytes)} از {fmt_bytes(sub.data_limit)}",
            usage_bar(sub.used_bytes, sub.data_limit),
            f"📅 تا {fmt_date(sub.expires_at)}" + (f" ({left} روز)" if left is not None else ""),
            f"🤖 ربات: {esc(self.tenant_label(bot)) if bot else '—'} — {'✅ متصل' if sub.linked else '⏳ متصل نیست'}",
        ]
        if sub.last_error:
            lines.append(f"\n⚠️ آخرین خطا:\n<pre>{esc(sub.last_error[:500])}</pre>")
        if note:
            lines += ["", note]
        i = sub.id
        rows: Buttons = [
            [Btn("⏸ غیرفعال", f"a:pss:{i}"), Btn("▶️ فعال", f"a:psa:{i}")],
            [Btn("➕ روز", f"a:pse:{i}"), Btn("➕ حجم (GB)", f"a:psv:{i}")],
            [Btn("🔐 ریست رمز", f"a:psp:{i}"), Btn("🔗 اتصال مجدد", f"a:psl:{i}")],
            [Btn("🔑 اطلاعات ورود", f"a:psc:{i}"), Btn("👥 مالک", f"a:user:{sub.owner_id}")],
        ]
        if sub.status == PanelSubStatus.ERROR and sub.admin_id is None:
            rows.append([Btn("🔁 تلاش مجدد ساخت", f"a:psretry:{i}:provision:{sub.plan_id or 0}")])
        rows += [[Btn("🗑 حذف", f"a:psdel:{i}")], [Btn("🔙 پنل‌ها", "a:ps")]]
        await self.show(ctx, "\n".join(lines), rows)

    @route("a:psd")
    async def sub_detail(self, ctx, sub_id: str = "") -> None:
        if sub_id.isdigit():
            await self._sub_screen(ctx, int(sub_id))

    async def _sub_op(self, ctx, sub_id: str, operation, ok_note: str) -> None:
        if not sub_id.isdigit():
            return
        result = await operation(int(sub_id))
        await self._sub_screen(ctx, int(sub_id), ok_note if result.ok else f"❌ {esc((result.error or '')[:300])}")

    @route("a:pss")
    async def sub_suspend(self, ctx, sub_id: str = "") -> None:
        await self._sub_op(ctx, sub_id, self.panels.suspend, "✅ غیرفعال شد.")

    @route("a:psa")
    async def sub_activate(self, ctx, sub_id: str = "") -> None:
        await self._sub_op(ctx, sub_id, self.panels.activate, "✅ فعال شد.")

    @route("a:psl")
    async def sub_relink(self, ctx, sub_id: str = "") -> None:
        await self._sub_op(ctx, sub_id, self.panels.link, "✅ ربات وصل شد.")

    @route("a:psp")
    async def sub_reset_password(self, ctx, sub_id: str = "") -> None:
        if not sub_id.isdigit():
            return
        result = await self.panels.reset_password(int(sub_id))
        if result.ok:
            async with self.db.session() as s:
                sub = await s.get(PanelSub, int(sub_id))
            await self.notifier.send(
                sub.owner_id,
                f"🔐 رمز پنل <code>{esc(sub.admin_username)}</code> توسط مدیریت تغییر کرد:\n<code>{esc(result.password)}</code>",
            )
        await self._sub_screen(
            ctx,
            int(sub_id),
            "✅ رمز تغییر کرد و برای مالک ارسال شد." if result.ok else f"❌ {esc((result.error or '')[:300])}",
        )

    @route("a:psc")
    async def sub_credentials(self, ctx, sub_id: str = "") -> None:
        if not sub_id.isdigit():
            return
        async with self.db.session() as s:
            sub = await s.get(PanelSub, int(sub_id))
            master = await s.get(MasterPanel, sub.master_id) if sub else None
        if sub is None or not sub.admin_password_enc:
            await ctx.answer("اطلاعاتی نیست.", alert=True)
            return
        await self.show(
            ctx,
            f"🔑 <code>{esc(master.base_url)}</code>\n👤 <code>{esc(sub.admin_username)}</code>\n"
            f"🔒 <code>{esc(self.box.decrypt(sub.admin_password_enc))}</code>",
            [[Btn("🔙", f"a:psd:{sub.id}")]],
        )

    @route("a:pse")
    async def sub_extend_ask(self, ctx, sub_id: str = "") -> None:
        if sub_id.isdigit():
            self.set_state(ctx.user_id, "ps_days", sub_id=int(sub_id))
            await self.show(ctx, "چند روز به اعتبار اضافه شود؟", [[Btn("❌ انصراف", f"a:psd:{sub_id}")]])

    @on_state("ps_days")
    async def sub_got_days(self, ctx, state) -> None:
        days = parse_int(ctx.text, minimum=1, maximum=3650)
        if days is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        sub_id = state.data["sub_id"]
        self.clear_state(ctx.user_id)
        result = await self.panels.extend(sub_id, days)
        await self._sub_screen(
            ctx, sub_id, f"✅ {days} روز اضافه شد." if result.ok else f"❌ {esc((result.error or '')[:300])}"
        )

    @route("a:psv")
    async def sub_volume_ask(self, ctx, sub_id: str = "") -> None:
        if sub_id.isdigit():
            self.set_state(ctx.user_id, "ps_gb", sub_id=int(sub_id))
            await self.show(ctx, "چند گیگابایت به حجم اضافه شود؟", [[Btn("❌ انصراف", f"a:psd:{sub_id}")]])

    @on_state("ps_gb")
    async def sub_got_gb(self, ctx, state) -> None:
        gb = parse_int(ctx.text, minimum=1, maximum=1_000_000)
        if gb is None:
            await ctx.reply("❌ عدد معتبر بفرستید.")
            return
        sub_id = state.data["sub_id"]
        self.clear_state(ctx.user_id)
        result = await self.panels.grant_volume(sub_id, gb)
        await self._sub_screen(
            ctx, sub_id, f"✅ {gb} GB اضافه شد." if result.ok else f"❌ {esc((result.error or '')[:300])}"
        )

    @route("a:psretry")
    async def sub_retry(self, ctx, sub_id: str = "", action: str = "provision", plan_id: str = "0") -> None:
        if not sub_id.isdigit():
            return
        from builder.services.billing import Fulfillment

        await ctx.answer("⏳ در حال تلاش مجدد…")
        async with self.db.session() as s:
            sub = await s.get(PanelSub, int(sub_id))
        if sub is None:
            return
        fulfillment = Fulfillment(panel_sub_id=sub.id, panel_plan_id=int(plan_id) or sub.plan_id, panel_action=action)
        await self.apply_panel_fulfillment(fulfillment, sub.owner_id)
        await self._sub_screen(ctx, int(sub_id), "🔁 دوباره انجام شد؛ نتیجه برای مالک ارسال شد.")

    @route("a:psdel")
    async def sub_delete_ask(self, ctx, sub_id: str = "") -> None:
        await self.show(
            ctx,
            "⚠️ ادمین این مشتری <b>همراه با همهٔ کاربرانش</b> از پنل اصلی حذف می‌شود و پنل از ربات او برداشته می‌شود. مطمئنید؟",
            [[Btn("✅ حذف", f"a:psdelok:{sub_id}")], [Btn("🔙 خیر", f"a:psd:{sub_id}")]],
        )

    @route("a:psdelok")
    async def sub_delete(self, ctx, sub_id: str = "") -> None:
        if not sub_id.isdigit():
            return
        result = await self.panels.delete(int(sub_id))
        if result.ok:
            await self.sub_list(ctx)
        else:
            await self._sub_screen(ctx, int(sub_id), f"❌ {esc((result.error or '')[:300])}")
