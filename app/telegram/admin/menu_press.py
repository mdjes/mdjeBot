"""Presses of the admin panel's glass menu: each button is handled as its plain label typed by the admin."""

from telethon import events

from app import Mdje
from app.telegram.keyboards.admin_menu import CALLBACK_PREFIX, item_for_callback
from app.telegram.plugins import PluginContext
from app.telegram.shared.utils.press import dispatch_text
from app.telegram.state import set_step
from config import ADMIN_ID

MODULE_NAME = "admin.menu_press"
MODULE_REQUIRED = True
MODULE_DESCRIPTION = "Admin glass menu buttons"

PANEL_STEP = "panel"


def _is_menu_button(event) -> bool:
    return (event.data or b"").startswith(CALLBACK_PREFIX.encode())


async def on_menu_button(event) -> None:
    if event.sender_id not in ADMIN_ID:
        await event.answer("⛔️ دسترسی ندارید", alert=True)
        raise events.StopPropagation
    await event.answer()
    item = item_for_callback(event.data)
    if item is not None:
        # The admin handlers act on a press only at the panel; a button of the menu always means "go there".
        await set_step(event.sender_id, PANEL_STEP)
        await dispatch_text(Mdje, event, item.label)
    raise events.StopPropagation


def setup(mdje: PluginContext) -> None:
    mdje.add_event_handler(on_menu_button, events.CallbackQuery(func=_is_menu_button))
