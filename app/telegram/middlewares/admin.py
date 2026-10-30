"""Admin context — admins skip restrictive middlewares via ``applies()`` checks."""

from __future__ import annotations

from app.telegram.keyboards.admin_menu import (
    CANCELLABLE_STEP_PREFIX,
    ensure_admin_menus,
    enter_admin_view,
    menu_press,
    restore_message,
)
from app.telegram.keyboards.common import is_keyboard_config_step
from app.telegram.middlewares.base import BaseMiddleware
from app.telegram.middlewares.context import MiddlewareContext
from app.telegram.state import get_step, set_step


class AdminContextMiddleware(BaseMiddleware):
    """Marks admin updates and keeps the admin menus (glass, renamed buttons) in step with their presses.

    ``ctx.is_admin`` is already on the context. For an admin this also rebuilds the admin menus when a button
    was renamed, draws inline buttons glassy for the update, and turns a press on a glass/renamed menu button
    back into the plain label the admin handlers match on.
    """

    name = "admin_context"
    priority = 10

    async def before(self, ctx: MiddlewareContext) -> None:
        ctx.extras["is_admin"] = ctx.is_admin
        if not ctx.is_admin:
            return
        enter_admin_view()
        await ensure_admin_menus()
        message = getattr(ctx.event, "message", None) if ctx.is_newmessage else None
        if message is None or ctx.user_id is None or menu_press(message.message) is None:
            return
        step = await get_step(ctx.user_id)
        # A name typed into the keyboard manager must reach its handler untouched.
        if is_keyboard_config_step(step):
            return
        restore_message(message)
        if step and step.startswith(CANCELLABLE_STEP_PREFIX):
            await set_step(ctx.user_id, "panel")
