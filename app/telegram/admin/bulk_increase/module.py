"""Package entry point for the admin bulk_increase module."""

from app.telegram.admin.bulk_increase import callbacks, messages
from app.telegram.plugins import PluginContext

MODULE_NAME = "admin.bulk_increase"
MODULE_REQUIRED = True
MODULE_DESCRIPTION = "Admin bulk increase flow"


def setup(mdje: PluginContext) -> None:
    messages.register(mdje)
    callbacks.register(mdje)
