"""Texts and keyboards of the admin gift-code menu."""

from __future__ import annotations

from datetime import datetime

from telethon import Button

from app.db.models.gifts import GiftCode
from app.services.billing.payment_stats import TEHRAN_TZ
from app.utils.formatting.dates import Time_Date

DIVIDER = "━━━━━━━━━━━━━━━"

STEP_AMOUNT = "form_gift_amount"
STEP_USES = "form_gift_uses"
STEP_DAYS = "form_gift_days"

ASK_AMOUNT = f"**➕ کد هدیهٔ جدید**\n{DIVIDER}\n💰 مبلغ هر بار استفاده را به تومان بفرستید:"
ASK_USES = "👥 این کد را چند نفر می‌توانند استفاده کنند؟ (یک عدد، مثلاً 1 یا 100)"
ASK_DAYS = "⏳ چند روز اعتبار داشته باشد؟ (۰ = بدون انقضا)"
BAD_NUMBER = "❌ یک عدد معتبر بفرستید."
CANCELLED = "انصراف داده شد."


def on_off(value: bool) -> str:
    return "✅ روشن" if value else "⛔️ خاموش"


def fmt_time(timestamp: int | None) -> str:
    if not timestamp:
        return "بدون انقضا"
    return Time_Date(datetime.fromtimestamp(timestamp, TEHRAN_TZ))


def state(gift: GiftCode, now: int) -> str:
    if not gift.active:
        return "⛔️ غیرفعال"
    if gift.expires_at is not None and gift.expires_at <= now:
        return "⌛️ منقضی"
    if gift.times_used >= gift.max_uses:
        return "✔️ تمام‌شده"
    return "✅ فعال"


def menu(gifts: list[GiftCode], enabled: bool, now: int) -> tuple[str, list]:
    lines = [f"**🎁 کدهای هدیه**\n{DIVIDER}", f"وضعیت قابلیت: {on_off(enabled)}", ""]
    if gifts:
        lines.append("آخرین کدها:")
        lines.extend(
            f"{state(gift, now)} `{gift.code}` · {gift.amount:,} تومان · {gift.times_used}/{gift.max_uses}"
            for gift in gifts
        )
    else:
        lines.append("هنوز کدی نساخته‌اید.")
    lines.extend(["", "💡 کاربر کد را در «افزایش موجودی ← 🎁 کد هدیه» یا با دستور /gift CODE وارد می‌کند."])
    buttons = [
        [Button.inline("➕ ساخت کد جدید", data="gift.new")],
        [Button.inline("⛔️ خاموش کردن قابلیت" if enabled else "✅ روشن کردن قابلیت", data="gift.switch")],
        *[[Button.inline(f"{state(gift, now).split()[0]} {gift.code}", data=f"gift.view:{gift.id}")] for gift in gifts],
        [Button.inline("🔙 بازگشت به پنل", data="back_to_admin_panel")],
    ]
    return "\n".join(lines), buttons


def detail(gift: GiftCode, now: int) -> tuple[str, list]:
    text = (
        f"**🎁 کد هدیه**\n{DIVIDER}\n"
        f"🔑 کد: `{gift.code}`\n"
        f"💰 مبلغ: `{gift.amount:,}` تومان\n"
        f"👥 استفاده: {gift.times_used} از {gift.max_uses}\n"
        f"⏳ انقضا: {fmt_time(gift.expires_at)}\n"
        f"📌 وضعیت: {state(gift, now)}"
    )
    buttons = [
        [Button.inline("⛔️ غیرفعال کردن" if gift.active else "✅ فعال کردن", data=f"gift.toggle:{gift.id}")],
        [Button.inline("🗑 حذف", data=f"gift.del:{gift.id}")],
        [Button.inline("🔙 بازگشت", data="gift.menu")],
    ]
    return text, buttons


def created(gift: GiftCode) -> str:
    return (
        f"**✅ کد هدیه ساخته شد**\n{DIVIDER}\n"
        f"🔑 کد: `{gift.code}`\n"
        f"💰 مبلغ: `{gift.amount:,}` تومان\n"
        f"👥 تعداد استفاده: {gift.max_uses}\n"
        f"⏳ انقضا: {fmt_time(gift.expires_at)}"
    )
