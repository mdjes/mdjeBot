"""Package entry point for admin panel management flow."""

from app.telegram.admin.panels import callbacks, messages
from app.telegram.plugins import PluginContext

MODULE_NAME = "admin.panels"
MODULE_DESCRIPTION = "admin panel management flow"


def setup(mdje: PluginContext) -> None:
    messages.register(mdje)
    callbacks.register(mdje)
