"""Admin reply/inline keyboard builders."""

from telethon import Button

from app.db.crud.user import UserCRUD
from app.services.reseller.plan_rules import USAGE, is_renewable, rule_for
from app.utils.admin_buttons import DOCS_URL as DOCS_URL

from .admin_menu import MENUS
from .common import glass_inline_button, glass_text_button

Lock_Channels_Menu_Buttons = [
    [glass_text_button("افزودن کانال"), glass_text_button("حذف کانال")],
    [glass_text_button("لیست کانال‌ها")],
    [glass_text_button("🔙 بازگشت به پنل")],
]
# Lock Channels (🔐) Inline Menu (fully glassy)
Lock_Channels_Inline_Menu = [
    [
        glass_inline_button("➕ افزودن کانال", data="lock_add"),
        glass_inline_button("📋 لیست کانال‌ها", data="lock_list:1"),
    ],
    [glass_inline_button("🔙 بازگشت به پنل", data="back_to_panel")],
]


async def create_inline_manageuser(UserID):
    user = await UserCRUD().read_user(UserID)
    buttons = [
        [Button.inline("📂 لیست سرویس‌ها", f"MToUser_listSv:{UserID}")],
        [Button.inline("🏢 نمایندگی‌های کاربر", f"MToUser_resellers:{UserID}")],
        [
            Button.inline("🔍 جستجوی کانفیگ", f"AdminSearchConfig:{UserID}"),
            Button.inline("🗑 حذف گروهی کانفیگ", f"BulkDeleteConfigs:{UserID}"),
        ],
        [Button.inline("رفع مسدودی", f"unbansup_{UserID}"), Button.inline("مسدود سازی", f"bansup_{UserID}")],
        [Button.inline("اطلاعات کاربر", f"UserInfo:{UserID}"), Button.inline("پیام به کاربر", f"sendm_{UserID}")],
    ]
    if not user or not user.number:
        buttons.append([Button.inline("📱 تایید شماره کاربر", f"confirm_phone_{UserID}")])

    buttons.append([Button.inline("ساخت کانفیگ برای کاربر", f"CreateConfigFor:{UserID}")])
    return buttons


def build_admin_reseller_list_buttons(user_id: int, accounts) -> list:
    rows = [
        [Button.inline(f"🏢 {acc.username} · #{acc.code}", data=f"AdminReseller_view:{user_id}:{acc.code}")]
        for acc in accounts
    ]
    rows.append([Button.inline("🔙 بازگشت", data=f"BackToUserManagement:{user_id}")])
    return rows


def build_admin_reseller_account_buttons(user_id: int, account) -> list:
    code = account.code
    rows = [
        [Button.inline("🔑 نمایش رمز ورود", data=f"AdminReseller_creds:{user_id}:{code}")],
        [Button.inline("🔄 تغییر رمز عبور", data=f"AdminReseller_chpwd:{user_id}:{code}")],
    ]
    if account.status in ("paused", "admin_paused", "usage_capped"):
        if account.status != "usage_capped":
            rows.append([Button.inline("▶️ فعال‌سازی پنل", data=f"AdminReseller_resume:{user_id}:{code}")])
    elif account.status in ("active", "suspended"):
        rows.append([Button.inline("⏸ غیرفعال‌سازی پنل", data=f"AdminReseller_pause:{user_id}:{code}")])
    if is_renewable(account.pricing_mode) and account.plan_id:
        rows.append([Button.inline("💎 تمدید با پلن خودش", data=f"AdminReseller_renew:{user_id}:{code}")])
    if account.pricing_mode == USAGE:
        rows.append([Button.inline("📦 محدودیت مصرف", data=f"AdminReseller_usage_cap:{user_id}:{code}")])
    rows.extend(admin_reseller_tool_rows(user_id, account))
    rows.append([Button.inline("🗑 حذف نمایندگی", data=f"AdminReseller_delete:{user_id}:{code}")])
    rows.append([Button.inline("🔙 بازگشت به لیست", data=f"MToUser_resellers:{user_id}")])
    return rows


def admin_reseller_tools(account) -> list[tuple[str, str]]:
    """(action, label) of the free repair tools that apply to ``account``'s plan type."""
    tools: list[tuple[str, str]] = []
    if account.expiration_time:
        tools.append(("days", "📅 روز رایگان"))
    if rule_for(account.pricing_mode).volume != "none":
        tools.append(("volume", "📦 تنظیم حجم"))
    tools.append(("maxusers", "👥 سقف یوزر"))
    tools.append(("chplan", "🔁 تغییر پلن"))
    tools.append(("resync", "🔄 همگام‌سازی با پنل"))
    if account.pricing_mode == USAGE:
        tools.append(("forgive", "🎁 بخشیدن مصرف کسرنشده"))
    return tools


def admin_reseller_tool_rows(user_id: int, account) -> list:
    buttons = [
        Button.inline(label, data=f"AdminReseller_{action}:{user_id}:{account.code}")
        for action, label in admin_reseller_tools(account)
    ]
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def build_admin_reseller_usage_cap_buttons(user_id: int, account_code: int, *, has_cap: bool) -> list:
    rows = [
        [Button.inline("✏️ تنظیم سقف (گیگ)", data=f"AdminReseller_usage_cap_set:{user_id}:{account_code}")],
    ]
    if has_cap:
        rows.append([Button.inline("🗑 حذف سقف مصرف", data=f"AdminReseller_usage_cap_clear:{user_id}:{account_code}")])
    rows.append([Button.inline("🔙 بازگشت", data=f"AdminReseller_view:{user_id}:{account_code}")])
    return rows


def build_admin_reseller_delete_confirm_buttons(user_id: int, account_code: int) -> list:
    return [
        [Button.inline("✅ بله، کامل حذف شود", data=f"AdminReseller_delete_confirm:{user_id}:{account_code}")],
        [Button.inline("❌ انصراف", data=f"AdminReseller_view:{user_id}:{account_code}")],
    ]


def build_admin_reseller_delete_final_buttons(user_id: int, account_code: int) -> list:
    return [
        [Button.inline("🗑 حذف نهایی و غیرقابل بازگشت", data=f"AdminReseller_delete_final:{user_id}:{account_code}")],
        [Button.inline("❌ انصراف", data=f"AdminReseller_view:{user_id}:{account_code}")],
    ]


def build_admin_reseller_chpwd_confirm_buttons(user_id: int, account_code: int) -> list:
    return [
        [Button.inline("✅ بله، رمز عوض شود", data=f"AdminReseller_chpwd_confirm:{user_id}:{account_code}")],
        [Button.inline("❌ انصراف", data=f"AdminReseller_view:{user_id}:{account_code}")],
    ]


# The reply menus below are rebuilt in place from the stored buttons (see ``admin_menu``), so importing
# them by name keeps working after an admin renames a button.
Panel_Admin_Buttons = MENUS["main"]
BT_takhfifList = MENUS["discounts"]
panel_xui_buttons = MENUS["panels"]
panel_back = MENUS["back"]
Home_Back = MENUS["home"]
