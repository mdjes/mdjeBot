"""Admin screens for the bot health cards: the whole fleet as one picture, one bot as a card, warn the owner."""

from __future__ import annotations

import asyncio

from builder.routing import route
from builder.services import health_card
from builder.services.health import TAGS, BotHealth, needs_attention, owner_warning
from builder.ui import Btn, Buttons
from builder.util import esc

LEVEL_EMOJI = {"good": "🟢", "warn": "🟡", "bad": "🔴"}
LEVEL_TEXT = {"good": "سالم", "warn": "نیازمند توجه", "bad": "ضعیف"}
MAX_LISTED = 8


def card_caption(h: BotHealth) -> str:
    lines = [f"{LEVEL_EMOJI[h.level]} <b>{esc(h.label)}</b> — امتیاز {h.score} ({LEVEL_TEXT[h.level]})"]
    s = h.stats
    if s.users is not None:
        lines.append(f"👥 کاربران: {s.users:,}" + (f" (+{s.new_users} در ۷ روز)" if s.new_users else ""))
    if s.services is not None:
        lines.append(f"🛒 فروش ۷ روز: {s.services} سرویس" + (f" · شارژ {s.topups:,} تومان" if s.topups else ""))
    if h.errors is not None:
        lines.append(f"⚠️ خطا در ۲۴ ساعت: {h.errors}")
    if h.memory is not None:
        lines.append(f"🧠 رم {h.memory:.0f}٪" + (f" · پردازنده {h.cpu:.0f}٪" if h.cpu is not None else ""))
    if h.tags:
        lines += ["", *(f"• {TAGS[tag][0]}" for tag in h.tags)]
    return "\n".join(lines)


class HealthFlows:
    @route("a:health")
    async def health_overview(self, ctx) -> None:
        await ctx.answer("⏳ در حال بررسی ربات‌ها…")
        items = await self.health.collect_all()
        picture = await asyncio.to_thread(health_card.render_overview, items)
        sick = [h for h in items if needs_attention(h)]
        rows: Buttons = [
            [Btn(f"{LEVEL_EMOJI[h.level]} {h.label} · {h.score}", f"a:hbot:{h.tenant_id}")] for h in sick[:MAX_LISTED]
        ]
        rows += [[Btn("🔄 بروزرسانی", "a:health"), Btn("🔙 پنل", "a:home")]]
        counts = {level: sum(1 for h in items if h.level == level) for level in LEVEL_EMOJI}
        caption = f"🩺 <b>سلامت ربات‌ها</b>\n🟢 {counts['good']} · 🟡 {counts['warn']} · 🔴 {counts['bad']}\n" + (
            "\nربات‌های نیازمند توجه را برای دیدن کارتشان بزنید:" if sick else "\nهمه‌چیز مرتب است."
        )
        await ctx.send_photo(picture, caption, rows)

    @route("a:hbot")
    async def health_bot(self, ctx, tenant_id: str = "") -> None:
        if not tenant_id.isdigit():
            return
        await ctx.answer("⏳")
        h = await self.health.collect_one(int(tenant_id))
        if h is None:
            await ctx.answer("ربات پیدا نشد.", alert=True)
            return
        picture = await asyncio.to_thread(health_card.render_card, h)
        i = h.tenant_id
        rows: Buttons = [
            [Btn("📨 تذکر به صاحب ربات", f"a:hwarn:{i}")],
            [Btn("🔁 ریستارت", f"a:trestart:{i}"), Btn("🤖 مدیریت ربات", f"a:tenant:{i}")],
            [Btn("🔙 سلامت ربات‌ها", "a:health")],
        ]
        await ctx.send_photo(picture, card_caption(h), rows)

    @route("a:hwarn")
    async def health_warn_owner(self, ctx, tenant_id: str = "") -> None:
        if not tenant_id.isdigit():
            return
        from builder.models import Tenant

        async with self.db.session() as s:
            tenant = await s.get(Tenant, int(tenant_id))
        h = await self.health.collect_one(int(tenant_id))
        if tenant is None or h is None:
            await ctx.answer("ربات پیدا نشد.", alert=True)
            return
        sent = await self.notifier.send(tenant.owner_id, owner_warning(h))
        await ctx.answer(
            "✅ تذکر برای صاحب ربات ارسال شد." if sent else "❌ پیام به صاحب ربات نرسید (ربات را بلاک کرده؟).",
            alert=True,
        )
