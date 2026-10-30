"""The admin panel's menus: glassy by default, renamable, with animated-emoji icons.

The admin handlers match a press against the *plain* label ("💳 تنظیمات درگاه"). What the admin sees can
differ from it (glass brackets, a new name from the keyboard manager), so every press is translated back
to the plain label by ``restore_press`` before any handler runs. Labels, colour and icon live in the same
``keyboard_buttons`` table as the home menu, under the ``ad.*`` keys.

The menus are module-level lists that dozens of call sites import by name, so a rebuild rewrites them in
place instead of binding new lists.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from functools import wraps

from telethon import Button

from app.db.crud.keyboards import KeyboardButtonCRUD, keyboard_cache_version
from app.utils.admin_buttons import ADMIN_ITEMS, MENU_LAYOUTS, AdminItem
from app.utils.text.glass import GLASS_STYLE, glass_text, unglass_text
from config import WEBAPP_URL

from .common import build_telegram_button_style, styled_simple_webview_button

WEB_PANEL_URL = f"{WEBAPP_URL.rstrip('/')}?panel=1" if WEBAPP_URL.startswith("https://") else ""

_TELEGRAM_STYLES = ("primary", "success", "danger")

# A step with this prefix is a short admin form; pressing any admin menu button ends it (back to the panel).
CANCELLABLE_STEP_PREFIX = "form_"
PLAIN_LABELS = frozenset(item.label for item in ADMIN_ITEMS.values() if not item.url)


def _visible_keys(menu: str) -> list[list[str]]:
    rows = []
    for row in MENU_LAYOUTS[menu]:
        keys = [key for key in row if key != "ad.web_panel" or WEB_PANEL_URL]
        if keys:
            rows.append(keys)
    return rows


def _button(item: AdminItem, text: str, style: str | None = None, icon: int | None = None):
    if item.url:
        return styled_simple_webview_button(text, item.url, build_telegram_button_style(style, icon))
    # A web-view opened from the reply keyboard gets no Telegram sign-in, so the web panel is a plain
    # button here; pressing it answers with an inline web-view button.
    return Button.text(text, resize=True, style=style if style in _TELEGRAM_STYLES else None, icon=icon)


def _rows_for(menu: str, resolve) -> list[list]:
    rows = []
    for keys in _visible_keys(menu):
        row = []
        for key in keys:
            item = ADMIN_ITEMS[key]
            text, style, icon = resolve(item)
            row.append(_button(item, text, style, icon))
        rows.append(row)
    return rows


def _default(item: AdminItem) -> tuple[str, str | None, int | None]:
    return glass_text(item.label), None, None


def _from_row(item: AdminItem, row) -> tuple[str, str | None, int | None]:
    """(text, telegram style, icon) for one button given its stored row, or the glass default."""
    text = ((row.button_text if row else "") or "").strip() or item.label
    stored_style = row.button_style if row else None
    # Glass is the default look; an explicit colour (or "none") shows the stored text as it is.
    if stored_style is None or stored_style == GLASS_STYLE:
        text = glass_text(unglass_text(text))
    return text, stored_style, (row.button_icon if row else None)


MENUS: dict[str, list[list]] = {name: _rows_for(name, _default) for name in MENU_LAYOUTS}

# What a press may carry -> the plain label the handlers expect.
_current: dict[str, str] = {}
_history: dict[str, str] = {}
_built_version = -1


def _remember(text: str, label: str) -> None:
    if text and text != label:
        _current[text] = label
        _history[text] = label


def _seed_defaults() -> None:
    for item in ADMIN_ITEMS.values():
        _remember(glass_text(item.label), item.label)


_seed_defaults()


async def ensure_admin_menus() -> None:
    """Rebuild the menus from the stored buttons when they changed since the last build."""
    global _built_version
    version = keyboard_cache_version()
    if version == _built_version:
        return
    stored = await KeyboardButtonCRUD().get_buttons_by_keys(list(ADMIN_ITEMS))
    _current.clear()
    _seed_defaults()

    def resolve(item: AdminItem):
        text, style, icon = _from_row(item, stored.get(item.key))
        _remember(text, item.label)
        return text, style, icon

    for name in MENUS:
        MENUS[name][:] = _rows_for(name, resolve)
    _built_version = version


def restore_press(text: str | None) -> str | None:
    """The plain label for a pressed admin-menu button, or None when the text is not one."""
    text = (text or "").strip()
    if not text:
        return None
    return _current.get(text) or _history.get(text)


def menu_press(text: str | None) -> str | None:
    """The plain label when ``text`` is a press of an admin-menu button (as drawn now or as plain text)."""
    plain = restore_press(text)
    if plain is not None:
        return plain
    text = (text or "").strip()
    return text if text in PLAIN_LABELS else None


def restore_message(message) -> bool:
    """Rewrite an incoming press to the plain label in place. True when it was rewritten."""
    plain = restore_press(getattr(message, "message", None))
    if plain is None:
        return False
    message.message = plain
    # Telethon caches the parsed text; drop it so ``.text`` follows the rewrite.
    with contextlib.suppress(AttributeError):
        message._text = None
    return True


# --- inline buttons -----------------------------------------------------------------------------------

_admin_view: ContextVar[bool] = ContextVar("admin_view", default=False)


def enter_admin_view() -> None:
    """Mark the update being handled as an admin's: its inline buttons are drawn glassy."""
    _admin_view.set(True)


def _glassy(factory):
    @wraps(factory)
    def wrapper(text, *args, **kwargs):
        if _admin_view.get() and isinstance(text, str) and text.strip():
            text = glass_text(unglass_text(text))
        return factory(text, *args, **kwargs)

    wrapper._glassy = True
    return wrapper


def install_inline_glass() -> None:
    """Make ``Button.inline`` / ``Button.url`` glassy inside an admin's update. Safe to call twice."""
    for name in ("inline", "url"):
        factory = getattr(Button, name)
        if not getattr(factory, "_glassy", False):
            setattr(Button, name, staticmethod(_glassy(factory)))
