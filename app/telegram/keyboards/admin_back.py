"""A way back to the admin panel on every admin screen.

The panel menu is an inline keyboard under the welcome message, so each section opens as a new message
without it. Rather than remember a back button in every handler, the messages an admin's own screens send
to the admin get one appended unless they already have a way out.

"Admin's own screens" means the code that runs is an admin module (``app.telegram.admin``): the same
update can also run user screens (an admin browsing the shop), and those are left alone, as are messages
the admin's actions send to other people.
"""

from __future__ import annotations

import sys
from contextvars import ContextVar

from telethon.tl import types
from telethon.tl.tlobject import TLObject

from .common import glass_inline_button

BACK_CALLBACK = "back_to_admin_panel"
BACK_LABEL = "🔙 بازگشت به پنل"

_ADMIN_MODULE = "app.telegram.admin"
_USER_MODULES = ("app.telegram.user", "app.telegram.business")

_admin_chat: ContextVar[int | None] = ContextVar("admin_chat", default=None)


def set_admin_chat(user_id: int | None) -> None:
    """Remember whose update is being handled, so only that admin's own chat gets the row."""
    _admin_chat.set(user_id)


def _target_id(entity) -> int | None:
    if isinstance(entity, int):
        return abs(entity)
    for attr in ("user_id", "id"):
        value = getattr(entity, attr, None)
        if isinstance(value, int):
            return value
    return None


def in_admin_screen(depth: int = 2) -> bool:
    """True when the innermost ``app.telegram`` module on the call stack is an admin one."""
    frame = sys._getframe(depth)
    for _ in range(80):
        if frame is None:
            return False
        name = frame.f_globals.get("__name__", "")
        if name.startswith(_ADMIN_MODULE):
            return True
        if name.startswith(_USER_MODULES):
            return False
        frame = frame.f_back
    return False


def applies_to(entity) -> bool:
    admin = _admin_chat.get()
    return admin is not None and _target_id(entity) == admin and in_admin_screen(3)


def _inline(button) -> types.KeyboardInlineButton | None:
    button = getattr(button, "button", button)
    return button if isinstance(button, types.KeyboardInlineButton) else None


def _leads_back(button: types.KeyboardInlineButton) -> bool:
    data = getattr(button.type, "data", b"") or b""
    return data == BACK_CALLBACK.encode() or data.startswith(b"adm:") or "بازگشت به پنل" in (button.text or "")


def _rows(buttons) -> list[list] | None:
    """Rows of a keyboard given in any of the shapes Telethon accepts, or None when it is not plain data."""
    if isinstance(buttons, types.ReplyInlineMarkup):
        return [list(row.buttons) for row in buttons.rows]
    if isinstance(buttons, types.KeyboardInlineButton):
        return [[buttons]]
    if isinstance(buttons, TLObject):  # a reply keyboard, a hide or a force-reply: no row to add
        return None
    if not isinstance(buttons, (list, tuple)):
        return [[buttons]]
    if buttons and not isinstance(buttons[0], (list, tuple)):
        return [list(buttons)]
    return [list(row) for row in buttons]


def with_back_row(buttons):
    """``buttons`` plus a back-to-panel row, unchanged when it is a reply keyboard or already leads back."""
    if buttons is None:
        return [[glass_inline_button(BACK_LABEL, data=BACK_CALLBACK)]]
    rows = _rows(buttons)
    if rows is None:
        return buttons
    inline = [_inline(button) for row in rows for button in row]
    if any(button is None for button in inline) or any(_leads_back(button) for button in inline):
        return buttons
    return [*rows, [glass_inline_button(BACK_LABEL, data=BACK_CALLBACK)]]


def install_back_row(client) -> None:
    """Wrap ``send_message`` / ``edit_message`` of ``client``. Safe to call twice."""
    if getattr(client, "_admin_back_row", False):
        return
    send, edit = client.send_message, client.edit_message

    def augment(args, kwargs):
        entity = args[0] if args else kwargs.get("entity")
        if applies_to(entity):
            kwargs["buttons"] = with_back_row(kwargs.get("buttons"))

    async def send_message(*args, **kwargs):
        augment(args, kwargs)
        return await send(*args, **kwargs)

    async def edit_message(*args, **kwargs):
        augment(args, kwargs)
        return await edit(*args, **kwargs)

    client.send_message = send_message
    client.edit_message = edit_message
    client._admin_back_row = True
