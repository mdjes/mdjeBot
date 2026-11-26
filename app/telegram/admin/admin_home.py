"""Admin home panel: /panel, the admin menu entry and the panel message."""

import contextlib

from telethon import Button, events
from telethon.tl.custom import Message

from app import Mdje
from app.db.crud.keyboards import get_button_text
from app.db.crud.log_channels import LogChannelManager
from app.db.crud.panels import PanelsManager
from app.logger import get_logger
from app.telegram.keyboards.admin import Panel_Admin_Buttons
from app.telegram.keyboards.common import is_keyboard_config_step
from app.telegram.plugins import PluginContext
from app.telegram.shared.url_presets import format_admin_links_message, get_bot_username
from app.telegram.state import get_step, set_step
from app.telegram.state.store import clear_user_conversation, get_app_cache, set_app_cache
from config import ADMIN_ID, LOG_CHANNEL

logger = get_logger(__name__)

MODULE_NAME = "admin.admin_home"
MODULE_REQUIRED = True
MODULE_DESCRIPTION = "Admin home panel shell"

ADD_PANEL_STEPS = frozenset(
    {
        "addPanel_name",
        "AddPanel_url",
        "AddPanel_auth_type",
        "AddPanel_username",
        "AddPanel_password",
        "AddPanel_api_key",
        "AddPanel_select_group",
        "ChangePanelAuth_username",
        "ChangePanelAuth_password",
        "ChangePanelAuth_api_key",
    }
)

# What is still missing before the shop can work, as short steps that say where to click.
_TODO_PANEL = "یک پنل اضافه کنید: 🛍 فروش و محصولات ← منوی پنل‌ها"
_TODO_LOGS = "کانال گزارش‌ها را تنظیم کنید: ⚙️ تنظیمات و ابزارها ← مدیریت لاگ‌ها"

_OFFICIAL_CHANNEL_URL = "https://t.me/sutaw"

_POWERED_BY_NOTICE = f"🤖 این ربات با ربات‌ساز **MdjeBot** ساخته شده است.\n📢 {_OFFICIAL_CHANNEL_URL}"


def _admin_home_message(user_id: int, username: str | None, *, todo: list[str] | None = None) -> str:
    who = f"@{username}" if username else f"`{user_id}`"
    message = f"**🌺 پنل مدیریت** — {who}\n━━━━━━━━━━━━━━━\nیک بخش را انتخاب کنید 👇\n"
    if todo:
        steps = "\n".join(f"{number}. {step}" for number, step in enumerate(todo, 1))
        message += f"\n⚠️ **برای شروع این کارها مانده:**\n{steps}\n"
    return message + f"\n{_POWERED_BY_NOTICE}"


async def _todo() -> list[str]:
    todo = []
    if not await PanelsManager().get_all_panels():
        todo.append(_TODO_PANEL)
    if LOG_CHANNEL is None:
        channels = await LogChannelManager().get_all_log_channels()
        if not any(ch.is_active for ch in channels):
            todo.append(_TODO_LOGS)
    return todo


async def _drop_old_reply_keyboard(user_id: int) -> None:
    """The panel used to be a reply keyboard; remove it once, now that the menu sits under the message."""
    key = f"admin_menu_inline:{user_id}"
    if await get_app_cache(key):
        return
    try:
        stale = await Mdje.send_message(entity=user_id, message="⏳", buttons=Button.clear())
        await stale.delete()
    except Exception as exc:
        logger.debug("Could not clear the old admin keyboard for %s: %s", user_id, exc)
    await set_app_cache(key, "1")


async def send_admin_home(user_id: int, username: str | None = None) -> None:
    await set_step(user_id=user_id, step="panel")
    await _drop_old_reply_keyboard(user_id)

    await Mdje.send_message(
        entity=user_id,
        message=_admin_home_message(user_id, username, todo=await _todo()),
        buttons=Panel_Admin_Buttons,
    )


async def callback_back_to_admin_panel(event: events.CallbackQuery.Event):
    if not event.is_private:
        return
    await event.answer()
    with contextlib.suppress(Exception):
        await event.delete()
    user = await event.get_sender()
    username = user.username if user else None
    await send_admin_home(event.sender_id, username)
    raise events.StopPropagation


def _panel_command_filter(event: Message) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    msg = (event.message.text or "").strip()
    return msg in {"/panel", "🔙 بازگشت به پنل"}


async def message_handler_admin_panel(event: Message):
    user_id = event.sender_id
    if await get_step(user_id) in ADD_PANEL_STEPS:
        await clear_user_conversation(user_id)
    user = await event.get_sender()
    username = user.username if user else None
    await send_admin_home(user_id, username)


async def _admin_menu_entry_filter(event: Message) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    if is_keyboard_config_step(await get_step(event.sender_id)):
        return False
    msg = (event.message.text or "").strip()
    if msg == "🔗 لینک های آماده":
        return True
    menu_text = await get_button_text("bt.menu_admin_panel", "⚙️ پنل مدیریت")
    return msg in {menu_text, "⚙️ پنل مدیریت"}


async def admin_menu_entry_handler(event: Message):
    msg = (event.message.text or "").strip()
    if msg == "🔗 لینک های آماده":
        bot_username = await get_bot_username(Mdje)
        links_message = format_admin_links_message(bot_username)
        buttons = [[Button.inline("🔙 بازگشت به پنل", data="back_to_admin_panel")]]
        await event.respond(links_message, buttons=buttons, parse_mode="md")
        return

    user = await event.get_sender()
    await send_admin_home(event.sender_id, user.username if user else None)


def setup(mdje: PluginContext) -> None:
    mdje.add_event_handler(
        message_handler_admin_panel,
        events.NewMessage(incoming=True, func=_panel_command_filter),
    )
    mdje.add_event_handler(
        admin_menu_entry_handler,
        events.NewMessage(incoming=True, func=_admin_menu_entry_filter),
    )
    mdje.add_event_handler(
        callback_back_to_admin_panel,
        events.CallbackQuery(data="back_to_admin_panel", func=lambda e: e.sender_id in ADMIN_ID),
    )
