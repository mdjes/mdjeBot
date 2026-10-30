"""The admin panel's inline menus: optionally glass-boxed, renamable, with animated-emoji icons.

The menus are inline ("glass") keyboards. A button carries the callback ``adm:<key>``; pressing it is
handled as if the admin had typed the button's plain label ("💳 تنظیمات درگاه"), which is what the admin
handlers match on (see ``app.telegram.admin.menu_press``), so the name an admin gives a button never
reaches them. Labels, colour and icon live in the same ``keyboard_buttons`` table as the home menu, under
the ``ad.*`` keys.

``restore_press`` still turns a typed or reply-keyboard press of a renamed/bracketed label back into the
plain one, for admins whose chat still shows the old reply keyboard.

The menus are module-level lists that dozens of call sites import by name, so a rebuild rewrites them in
place instead of binding new lists.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from functools import wraps

from telethon import Button

from app.db.crud.keyboards import KeyboardButtonCRUD, keyboard_cache_version
from app.db.crud.settings import SettingsManager
from app.utils.admin_buttons import ADMIN_ITEMS, MENU_LAYOUTS, AdminItem
from app.utils.text.glass import GLASS_STYLE, glass_text, unglass_text
from config import WEBAPP_URL

from .common import build_telegram_button_style, styled_callback_button, styled_url_button, styled_webview_button

WEB_PANEL_URL = f"{WEBAPP_URL.rstrip('/')}?panel=1" if WEBAPP_URL.startswith("https://") else ""

CALLBACK_PREFIX = "adm:"

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


def item_for_callback(data: bytes | str | None) -> AdminItem | None:
    """The menu button a callback ``adm:<key>`` stands for, if it is one that sends a label."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else (data or "")
    item = ADMIN_ITEMS.get(text.removeprefix(CALLBACK_PREFIX)) if text.startswith(CALLBACK_PREFIX) else None
    return item if item is not None and not item.url and item.key != "ad.web_panel" else None


def _button(item: AdminItem, text: str, style: str | None = None, icon: int | None = None):
    telegram_style = build_telegram_button_style(style, icon)
    if item.key == "ad.web_panel":
        # An inline web-view signs the admin in to the web panel by itself.
        return styled_webview_button(text, WEB_PANEL_URL, telegram_style)
    if item.url:
        return styled_url_button(text, item.url, telegram_style)
    return styled_callback_button(text, f"{CALLBACK_PREFIX}{item.key}", telegram_style)


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
    """(text, telegram style, icon) for one button and the global glass setting."""
    text = ((row.button_text if row else "") or "").strip() or item.label
    stored_style = row.button_style if row else None
    if not _admin_glass_enabled:
        text = unglass_text(text)
    elif stored_style is None or stored_style == GLASS_STYLE:
        text = glass_text(unglass_text(text))
    return text, stored_style, (row.button_icon if row else None)


MENUS: dict[str, list[list]] = {name: _rows_for(name, _default) for name in MENU_LAYOUTS}

# What a press may carry -> the plain label the handlers expect.
_current: dict[str, str] = {}
_history: dict[str, str] = {}
_built_version = -1
_built_glass_enabled: bool | None = None
_admin_glass_enabled = True


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
    global _admin_glass_enabled, _built_glass_enabled, _built_version
    settings = await SettingsManager().get_settings()
    glass_enabled = bool(getattr(settings, "admin_menu_glass_mode", True)) if settings else True
    version = keyboard_cache_version()
    if version == _built_version and glass_enabled == _built_glass_enabled:
        return
    _admin_glass_enabled = glass_enabled
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
    _built_glass_enabled = glass_enabled


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
            text = unglass_text(text)
            if _admin_glass_enabled:
                text = glass_text(text)
        return factory(text, *args, **kwargs)

    wrapper._glassy = True
    return wrapper


def install_inline_glass() -> None:
    """Make ``Button.inline`` / ``Button.url`` glassy inside an admin's update. Safe to call twice."""
    for name in ("inline", "url"):
        factory = getattr(Button, name)
        if not getattr(factory, "_glassy", False):
            setattr(Button, name, staticmethod(_glassy(factory)))
