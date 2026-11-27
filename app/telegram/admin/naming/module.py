"""Package entry point for the admin default-config-name module."""

from telethon import events

from app.telegram.admin.naming import handlers
from app.telegram.plugins import PluginContext

MODULE_NAME = "admin.naming"
MODULE_DESCRIPTION = "Admin screen for the default config name"


def setup(mdje: PluginContext) -> None:
    mdje.add_event_handler(handlers.on_menu_press, events.NewMessage(incoming=True, func=handlers.is_menu_press))
    mdje.add_event_handler(handlers.on_callback, events.CallbackQuery(func=handlers.is_naming_callback))
    mdje.add_event_handler(handlers.on_form_text, events.NewMessage(incoming=True, func=handlers.is_form_answer))
