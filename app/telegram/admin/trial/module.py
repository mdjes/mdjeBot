"""Package entry point for the admin free-trial module."""

from telethon import events

from app.telegram.admin.trial import handlers
from app.telegram.plugins import PluginContext

MODULE_NAME = "admin.trial"
MODULE_DESCRIPTION = "Admin free-trial screen"


def setup(mdje: PluginContext) -> None:
    mdje.add_event_handler(handlers.on_menu_press, events.NewMessage(incoming=True, func=handlers.is_menu_press))
    mdje.add_event_handler(handlers.on_callback, events.CallbackQuery(func=handlers.is_trial_callback))
    mdje.add_event_handler(handlers.on_form_text, events.NewMessage(incoming=True, func=handlers.is_form_answer))
