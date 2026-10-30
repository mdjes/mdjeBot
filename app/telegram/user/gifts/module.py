"""Package entry point for the user gift-code module."""

from telethon import events

from app.telegram.plugins import PluginContext
from app.telegram.state import get_step
from app.telegram.user.gifts import flows

MODULE_NAME = "user.gifts"
MODULE_DESCRIPTION = "Redeem gift codes for wallet credit"


async def _awaiting_code(event) -> bool:
    return bool(event.is_private and await get_step(event.sender_id) == flows.STEP_GIFT)


async def _command(event) -> None:
    await flows.begin(event, event.pattern_match.group(1))
    raise events.StopPropagation


async def _button(event) -> None:
    await event.answer()
    await flows.begin(event)


def setup(mdje: PluginContext) -> None:
    mdje.add_event_handler(
        _command,
        events.NewMessage(
            incoming=True,
            pattern=r"(?i)^/gift(?:@\w+)?(?:\s+(\S+))?\s*$",
            func=lambda event: event.is_private,
        ),
    )
    mdje.add_event_handler(_button, events.CallbackQuery(data=flows.CB_OPEN))
    mdje.add_event_handler(flows.on_cancel, events.CallbackQuery(data=flows.CB_CANCEL))
    mdje.add_event_handler(flows.on_code_text, events.NewMessage(incoming=True, func=_awaiting_code))
