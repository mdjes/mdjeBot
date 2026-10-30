"""Package entry point for the admin gift-code module."""

from telethon import events

from app.telegram.admin.gifts import handlers
from app.telegram.plugins import PluginContext

MODULE_NAME = "admin.gifts"
MODULE_DESCRIPTION = "Admin gift-code management"


def setup(mdje: PluginContext) -> None:
    mdje.add_event_handler(handlers.on_menu_press, events.NewMessage(incoming=True, func=handlers.is_menu_press))
    mdje.add_event_handler(handlers.on_callback, events.CallbackQuery(func=handlers.is_gift_callback))
    mdje.add_event_handler(handlers.on_form_text, events.NewMessage(incoming=True, func=handlers.is_form_answer))
