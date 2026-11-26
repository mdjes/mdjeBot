"""Every button of the admin inline menus, as plain data.

Kept free of imports from the rest of the app so both the keyboard registry (which lists the buttons for
renaming) and the database seeding can read it without an import cycle.

``label`` is the plain text the admin handlers match on. What the admin *sees* may differ (a new name), and
the admin middleware translates a press back to ``label`` before any handler runs.
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
    AdminItem("ad.trial", "🎁 تست رایگان", "منوی ادمین · تست رایگان"),
    AdminItem("ad.grp_users", "👤 کاربران و موجودی", "منوی ادمین · گروه کاربران و موجودی"),
    AdminItem("ad.grp_sales", "🛍 فروش و محصولات", "منوی ادمین · گروه فروش و محصولات"),
    AdminItem("ad.grp_promo", "🎟 تخفیف و هدیه", "منوی ادمین · گروه تخفیف و هدیه"),
    AdminItem("ad.grp_comms", "📣 ارتباط با کاربران", "منوی ادمین · گروه ارتباط با کاربران"),
    AdminItem("ad.grp_look", "🎨 ظاهر ربات", "منوی ادمین · گروه ظاهر ربات"),
    AdminItem("ad.grp_tools", "⚙️ تنظیمات و ابزارها", "منوی ادمین · گروه تنظیمات و ابزارها"),
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
        ("ad.stats", "ad.trial"),
        ("ad.grp_users", "ad.grp_sales"),
        ("ad.gateway", "ad.grp_promo"),
        ("ad.grp_comms", "ad.grp_look"),
        ("ad.grp_tools",),
        ("ad.docs", "ad.web_panel"),
        ("ad.home",),
    ),
    "grp_users": (
        ("ad.manage_user",),
        ("ad.add_balance", "ad.deduct"),
        ("ad.group_charge", "ad.bulk_increase"),
        ("ad.reset_test",),
        ("ad.back_panel",),
    ),
    "grp_sales": (
        ("ad.panels",),
        ("ad.make_plan", "ad.reseller_plan"),
        ("ad.back_panel",),
    ),
    "grp_promo": (
        ("ad.discounts", "ad.gifts"),
        ("ad.referral",),
        ("ad.back_panel",),
    ),
    "grp_comms": (
        ("ad.broadcast", "ad.forward"),
        ("ad.lock_channels", "ad.links"),
        ("ad.back_panel",),
    ),
    "grp_look": (
        ("ad.texts", "ad.keyboards"),
        ("ad.back_panel",),
    ),
    "grp_tools": (
        ("ad.settings", "ad.logs"),
        ("ad.backup", "ad.migration"),
        ("ad.back_panel",),
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


# The group buttons of the main menu, by key, and what each group says about itself when opened.
GROUP_MENUS: dict[str, str] = {
    "ad.grp_users": "grp_users",
    "ad.grp_sales": "grp_sales",
    "ad.grp_promo": "grp_promo",
    "ad.grp_comms": "grp_comms",
    "ad.grp_look": "grp_look",
    "ad.grp_tools": "grp_tools",
}

GROUP_INTROS: dict[str, str] = {
    "grp_users": (
        "**👤 کاربران و موجودی**\n━━━━━━━━━━━━━━━\n"
        "▫️ **مدیریت کاربر**: آیدی کاربر را بدهید؛ سرویس‌ها، مسدود کردن، پیام و ساخت کانفیگ\n"
        "▫️ **افزودن / کسر موجودی**: برای یک کاربر\n"
        "▫️ **شارژ گروهی / افزایش همگانی**: برای همهٔ کاربران یا سرویس‌ها\n"
        "▫️ **ریست دریافت تست**: اجازهٔ تست دوباره"
    ),
    "grp_sales": (
        "**🛍 فروش و محصولات**\n━━━━━━━━━━━━━━━\n"
        "▫️ **منوی پنل‌ها**: افزودن پنل، وضعیت و تنظیمات هر پنل\n"
        "▫️ **ساخت پلن**: پلن‌هایی که کاربر می‌خرد\n"
        "▫️ **پلن نمایندگی**: فروش پنل نمایندگی"
    ),
    "grp_promo": (
        "**🎟 تخفیف و هدیه**\n━━━━━━━━━━━━━━━\n"
        "▫️ **کدتخفیف**: درصدی، با سقف استفاده و انقضا\n"
        "▫️ **کدهای هدیه**: شارژ کیف پول با کد\n"
        "▫️ **سیستم دعوت دوستان**: پاداش دعوت"
    ),
    "grp_comms": (
        "**📣 ارتباط با کاربران**\n━━━━━━━━━━━━━━━\n"
        "▫️ **ارسال / فوروارد همگانی**: پیام به همهٔ کاربران\n"
        "▫️ **قفل چنل‌ها**: عضویت اجباری در کانال\n"
        "▫️ **لینک‌های آماده**: لینک‌های ربات برای تبلیغ"
    ),
    "grp_look": (
        "**🎨 ظاهر ربات**\n━━━━━━━━━━━━━━━\n"
        "▫️ **متن‌های ربات**: ویرایش پیام‌ها\n"
        "▫️ **مدیریت دکمه‌ها**: نام، رنگ و ایموجی دکمه‌ها"
    ),
    "grp_tools": (
        "**⚙️ تنظیمات و ابزارها**\n━━━━━━━━━━━━━━━\n"
        "▫️ **تنظیمات ربات**: روشن و خاموش کردن امکانات\n"
        "▫️ **مدیریت لاگ‌ها**: کانال گزارش‌ها\n"
        "▫️ **بکاپ ربات** و **مایگریشن** از ربات دیگر"
    ),
}


def manager_groups() -> tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]:
    """Sections of the in-bot keyboard manager page for admin buttons: (heading, rows)."""
    sections = [("منوی اصلی مدیر", MENU_LAYOUTS["main"])]
    for key, menu in GROUP_MENUS.items():
        rows = tuple(row for row in MENU_LAYOUTS[menu] if row != ("ad.back_panel",))
        sections.append((ADMIN_ITEMS[key].label, rows))
    subs = (
        ("ad.discount_list", "ad.discount_make"),
        ("ad.panel_status", "ad.panel_add"),
        ("ad.back_panel",),
    )
    return (*sections, ("زیرمنوها", subs))
