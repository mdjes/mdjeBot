"""Every button of the admin reply menus, as plain data.

Kept free of imports from the rest of the app so both the keyboard registry (which lists the buttons for
renaming) and the database seeding can read it without an import cycle.

``label`` is the plain text the admin handlers match on. What the admin *sees* may differ (glass brackets, a
new name), and the admin middleware translates a press back to ``label`` before any handler runs.
"""

from __future__ import annotations

from dataclasses import dataclass

DOCS_URL = "https://mdjes.github.io/mdjeBot/"

KEY_PREFIX = "ad."


@dataclass(frozen=True, slots=True)
class AdminItem:
    key: str
    label: str
    title: str
    # A web-view button opens this address instead of sending its label as a message.
    url: str = ""


def _items(*rows: AdminItem) -> dict[str, AdminItem]:
    return {item.key: item for item in rows}


ADMIN_ITEMS: dict[str, AdminItem] = _items(
    AdminItem("ad.gateway", "💳 تنظیمات درگاه", "منوی ادمین · تنظیمات درگاه"),
    AdminItem("ad.stats", "👥 آمار گیری", "منوی ادمین · آمار گیری"),
    AdminItem("ad.panels", "📚 منوی پنل ها", "منوی ادمین · منوی پنل ها"),
    AdminItem("ad.settings", "⚙️ تنظیمات ربات", "منوی ادمین · تنظیمات ربات"),
    AdminItem("ad.discounts", "🎟 کدتخفیف", "منوی ادمین · کد تخفیف"),
    AdminItem("ad.make_plan", "🗞 ساخت پلن", "منوی ادمین · ساخت پلن"),
    AdminItem("ad.reseller_plan", "🏢 پلن نمایندگی", "منوی ادمین · پلن نمایندگی"),
    AdminItem("ad.gifts", "🎁 کدهای هدیه", "منوی ادمین · کدهای هدیه"),
    AdminItem("ad.manage_user", "👤 مدیریت کاربر", "منوی ادمین · مدیریت کاربر"),
    AdminItem("ad.broadcast", "📮 ارسال همگانی", "منوی ادمین · ارسال همگانی"),
    AdminItem("ad.forward", "📥 فوروارد همگانی", "منوی ادمین · فوروارد همگانی"),
    AdminItem("ad.deduct", "➖ کسر موجودی", "منوی ادمین · کسر موجودی"),
    AdminItem("ad.add_balance", "➕ افزودن موجودی", "منوی ادمین · افزودن موجودی"),
    AdminItem("ad.group_charge", "💰 شارژ گروهی", "منوی ادمین · شارژ گروهی"),
    AdminItem("ad.reset_test", "🔄 ریست دریافت تست", "منوی ادمین · ریست دریافت تست"),
    AdminItem("ad.bulk_increase", "📈 افزایش حجم و زمان همگانی", "منوی ادمین · افزایش حجم و زمان همگانی"),
    AdminItem("ad.lock_channels", "🔐 قفل چنل ها", "منوی ادمین · قفل چنل ها"),
    AdminItem("ad.logs", "📝 مدیریت لاگ‌ها", "منوی ادمین · مدیریت لاگ‌ها"),
    AdminItem("ad.backup", "📦 بکاپ ربات", "منوی ادمین · بکاپ ربات"),
    AdminItem("ad.migration", "🧬 مایگریشن از ربات دیگر", "منوی ادمین · مایگریشن"),
    AdminItem("ad.texts", "📝 متن‌های ربات", "منوی ادمین · متن‌های ربات"),
    AdminItem("ad.keyboards", "⌨️ مدیریت دکمه‌های کیبورد", "منوی ادمین · مدیریت دکمه‌ها"),
    AdminItem("ad.referral", "🎁 سیستم دعوت دوستان", "منوی ادمین · دعوت دوستان"),
    AdminItem("ad.links", "🔗 لینک های آماده", "منوی ادمین · لینک های آماده"),
    AdminItem("ad.docs", "📚 مستندات ربات", "منوی ادمین · مستندات", url=DOCS_URL),
    AdminItem("ad.web_panel", "🖥 پنل تحت وب", "منوی ادمین · پنل تحت وب"),
    AdminItem("ad.home", "🏠", "منوی ادمین · بازگشت به خانه"),
    AdminItem("ad.back_panel", "🔙 بازگشت به پنل", "منوی ادمین · بازگشت به پنل"),
    AdminItem("ad.discount_list", "🎛 لیست کدتخفیف", "زیرمنوی کدتخفیف · لیست"),
    AdminItem("ad.discount_make", "🪄 ساخت کدتخفیف", "زیرمنوی کدتخفیف · ساخت"),
    AdminItem("ad.panel_status", "📉 وضعیت پنل ها", "زیرمنوی پنل ها · وضعیت"),
    AdminItem("ad.panel_add", "▫️ افزودن پنل جدید", "زیرمنوی پنل ها · افزودن"),
)

# Rows of each reply menu, by key. The web-panel button is dropped at build time when no web app is configured.
MENU_LAYOUTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "main": (
        ("ad.gateway", "ad.stats"),
        ("ad.panels", "ad.settings"),
        ("ad.discounts", "ad.make_plan"),
        ("ad.reseller_plan", "ad.gifts"),
        ("ad.manage_user", "ad.broadcast"),
        ("ad.forward",),
        ("ad.deduct", "ad.add_balance"),
        ("ad.group_charge", "ad.reset_test"),
        ("ad.bulk_increase", "ad.lock_channels"),
        ("ad.logs", "ad.backup"),
        ("ad.migration",),
        ("ad.texts", "ad.keyboards"),
        ("ad.referral", "ad.links"),
        ("ad.docs", "ad.web_panel"),
        ("ad.home",),
    ),
    "discounts": (
        ("ad.discount_list", "ad.discount_make"),
        ("ad.back_panel",),
    ),
    "panels": (
        ("ad.panel_status", "ad.panel_add"),
        ("ad.back_panel",),
    ),
    "back": (("ad.back_panel",),),
    "home": (("ad.home",),),
}


def manager_groups() -> tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]:
    """Sections of the in-bot keyboard manager page for admin buttons: (heading, rows)."""
    subs = (
        ("ad.discount_list", "ad.discount_make"),
        ("ad.panel_status", "ad.panel_add"),
        ("ad.back_panel",),
    )
    return (("منوی اصلی مدیر", MENU_LAYOUTS["main"]), ("زیرمنوها", subs))
