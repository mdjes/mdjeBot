"""Package entry point for user shop purchase flow."""

from app.telegram.plugins import PluginContext
from app.telegram.user.shop import callbacks, messages

MODULE_NAME = "user.shop"
MODULE_REQUIRED = True
MODULE_DESCRIPTION = "user shop purchase flow"


def setup(mdje: PluginContext) -> None:
    messages.register(mdje)
    callbacks.register(mdje)
