"""User side of gift codes: ask for a code (button or /gift) and credit the wallet."""

from __future__ import annotations

import contextlib

from telethon import events
from telethon.errors import MessageNotModifiedError

from app.db.crud.keyboards import KeyboardButtonCRUD
from app.db.crud.user import UserCRUD
from app.i18n import normalize_language, t
from app.services.gifts import codes, settings as gift_settings
from app.telegram.keyboards.common import glass_inline_button
from app.telegram.keyboards.home import HOME_BUTTON_KEYS
from app.telegram.keyboards.registry import KEYBOARD_BUTTON_DEFAULTS
from app.telegram.state import clear_user, get_data, set_data_many, set_step
from app.utils.text.glass import unglass_text

STEP_GIFT = "gift_code"
STEP_TTL = 900
MAX_MISSES = 5
MISSES_KEY = "gift_misses"

CB_OPEN = b"gift.open"
CB_CANCEL = b"gift.cancel"

_REPLIES = {
    codes.NOT_FOUND: "not_found",
    codes.EXPIRED: "expired",
    codes.EXHAUSTED: "exhausted",
    codes.INACTIVE: "inactive",
    codes.ALREADY: "already",
    codes.NO_USER: "no_user",
}


async def _lang(user_id: int) -> str:
    user = await UserCRUD().read_user(user_id)
    return normalize_language(getattr(user, "language", None))


def _cancel_rows(lang: str) -> list:
    return [[glass_inline_button(t("gifts.btn_cancel", lang), data=CB_CANCEL.decode())]]


async def _show(event, text: str, buttons=None) -> None:
    """Edit the message under a pressed button, or answer a typed command or code."""
    if isinstance(event, events.CallbackQuery.Event):
        with contextlib.suppress(MessageNotModifiedError):
            await event.edit(text, buttons=buttons)
    else:
        await event.respond(text, buttons=buttons)


async def _menu_labels() -> set[str]:
    """Texts of the home menu buttons, so pressing one while a code is awaited is not taken for a code."""
    stored = await KeyboardButtonCRUD().get_buttons_by_keys(list(HOME_BUTTON_KEYS))
    labels = {unglass_text(row.button_text) for row in stored.values() if row.button_text}
    labels.update(
        unglass_text(KEYBOARD_BUTTON_DEFAULTS[key]) for key in HOME_BUTTON_KEYS if key in KEYBOARD_BUTTON_DEFAULTS
    )
    return labels


async def begin(event, code: str | None = None) -> None:
    """Redeem ``code`` right away when it came with the command, else wait for it."""
    user_id = event.sender_id
    lang = await _lang(user_id)
    if not await gift_settings.get_bool(gift_settings.GIFTS_ENABLED):
        await _show(event, t("gifts.disabled", lang))
        return
    if code:
        await redeem(event, user_id, lang, code)
        return
    await set_step(user_id, STEP_GIFT, ttl=STEP_TTL)
    await _show(event, t("gifts.prompt", lang), _cancel_rows(lang))


async def redeem(event, user_id: int, lang: str, code: str) -> None:
    result = await codes.redeem(user_id, code)
    if result.ok:
        await clear_user(user_id)
        await _show(event, t("gifts.ok", lang, amount=f"{result.amount:,}", balance=f"{result.balance:,}"))
        return
    misses = int(await get_data(user_id, MISSES_KEY) or 0) + 1
    if result.status != codes.NOT_FOUND:
        await clear_user(user_id)
    elif misses >= MAX_MISSES:
        await clear_user(user_id)
        await _show(event, t("gifts.too_many", lang))
        return
    else:
        await set_data_many(user_id, {MISSES_KEY: misses}, ttl=STEP_TTL)
    await _show(event, t(f"gifts.{_REPLIES.get(result.status, 'not_found')}", lang))


async def on_code_text(event) -> None:
    """The next message while a code is awaited: a code, or a command / menu press that ends the wait."""
    user_id = event.sender_id
    text = (event.message.message or "").strip()
    if not text or text.startswith("/") or unglass_text(text) in await _menu_labels():
        await clear_user(user_id)
        return
    await redeem(event, user_id, await _lang(user_id), text)
    raise events.StopPropagation


async def on_cancel(event) -> None:
    user_id = event.sender_id
    lang = await _lang(user_id)
    await clear_user(user_id)
    await event.answer()
    await _show(event, t("gifts.cancelled", lang))
