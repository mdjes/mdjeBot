"""Admin handlers of the gift-code menu: list, create, switch on/off, delete."""

from __future__ import annotations

import contextlib
import time

from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from app.services.gifts import codes, settings as gift_settings
from app.telegram.admin.gifts import texts
from app.telegram.shared.reseller_plan_guides import parse_number
from app.telegram.state import clear_user, get_data, get_step, set_data, set_step
from config import ADMIN_ID

MENU_LABEL = "🎁 کدهای هدیه"
PANEL_STEP = "panel"
AMOUNT_KEY = "gift_amount"
USES_KEY = "gift_uses"
RECENT = 10

_CANCEL = [[Button.inline("❌ انصراف", data="gift.menu")]]


async def _menu() -> tuple[str, list]:
    return texts.menu(
        await codes.list_gifts(RECENT),
        await gift_settings.get_bool(gift_settings.GIFTS_ENABLED),
        int(time.time()),
    )


async def _edit(event, text: str, buttons) -> None:
    with contextlib.suppress(MessageNotModifiedError):
        await event.edit(text, buttons=buttons)


# --- entry and callbacks ------------------------------------------------------------------------------------


async def is_menu_press(event) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    return (event.message.message or "").strip() == MENU_LABEL and await get_step(event.sender_id) == PANEL_STEP


async def on_menu_press(event) -> None:
    text, buttons = await _menu()
    await event.respond(text, buttons=buttons)
    raise events.StopPropagation


def is_gift_callback(event) -> bool:
    return event.sender_id in ADMIN_ID and (event.data or b"").startswith(b"gift.") and event.data != b"gift.open"


async def on_callback(event) -> None:
    data = event.data.decode()
    action, _, arg = data.partition(":")
    now = int(time.time())

    if action == "gift.menu":
        await clear_user(event.sender_id)
        await set_step(event.sender_id, PANEL_STEP)
        await _edit(event, *await _menu())
    elif action == "gift.switch":
        await gift_settings.toggle(gift_settings.GIFTS_ENABLED)
        await _edit(event, *await _menu())
    elif action == "gift.new":
        await set_step(event.sender_id, texts.STEP_AMOUNT)
        await _edit(event, texts.ASK_AMOUNT, _CANCEL)
    elif action in {"gift.view", "gift.toggle", "gift.del"} and arg.isdigit():
        await _gift_action(event, action, int(arg), now)
    await event.answer()
    raise events.StopPropagation


async def _gift_action(event, action: str, gift_id: int, now: int) -> None:
    if action == "gift.toggle":
        gift = await codes.get_gift(gift_id)
        if gift is not None:
            await codes.set_active(gift_id, not gift.active)
    elif action == "gift.del":
        await codes.delete_gift(gift_id)
        await _edit(event, *await _menu())
        return
    gift = await codes.get_gift(gift_id)
    if gift is None:
        await _edit(event, *await _menu())
        return
    await _edit(event, *texts.detail(gift, now))


# --- the creation form --------------------------------------------------------------------------------------

_FORM_STEPS = (texts.STEP_AMOUNT, texts.STEP_USES, texts.STEP_DAYS)


async def is_form_answer(event) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private or not (event.message.message or "").strip():
        return False
    return await get_step(event.sender_id) in _FORM_STEPS


async def on_form_text(event) -> None:
    admin_id = event.sender_id
    step = await get_step(admin_id)
    value, error = parse_number(event.message.message, allow_zero=step == texts.STEP_DAYS)
    if error or value is None:
        await event.respond(texts.BAD_NUMBER, buttons=_CANCEL)
        raise events.StopPropagation
    value = int(value)

    if step == texts.STEP_AMOUNT:
        await set_data(admin_id, AMOUNT_KEY, value)
        await set_step(admin_id, texts.STEP_USES)
        await event.respond(texts.ASK_USES, buttons=_CANCEL)
    elif step == texts.STEP_USES:
        await set_data(admin_id, USES_KEY, value)
        await set_step(admin_id, texts.STEP_DAYS)
        await event.respond(texts.ASK_DAYS, buttons=_CANCEL)
    else:
        amount = int(await get_data(admin_id, AMOUNT_KEY) or 0)
        uses = int(await get_data(admin_id, USES_KEY) or 0)
        expires_at = int(time.time()) + value * 86400 if value else None
        gift = await codes.create_gift(amount, max_uses=uses, expires_at=expires_at, created_by=admin_id)
        await clear_user(admin_id)
        await set_step(admin_id, PANEL_STEP)
        if gift is None:
            await event.respond(texts.BAD_NUMBER)
        else:
            await event.respond(texts.created(gift), buttons=[[Button.inline("🎁 کدهای هدیه", data="gift.menu")]])
    raise events.StopPropagation
