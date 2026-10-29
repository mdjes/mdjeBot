"""The slice of a Telegram update the handlers need, so flows can be tested without Telegram."""

from __future__ import annotations

import contextlib
from typing import Protocol

from builder.ui import Buttons, to_telethon_buttons


class Ctx(Protocol):
    user_id: int
    first_name: str
    username: str | None
    text: str  # message text ("" for callbacks)
    data: str  # callback data ("" for messages)
    has_media: bool
    is_callback: bool

    async def reply(self, text: str, buttons: Buttons | None = None) -> None: ...

    async def edit(self, text: str, buttons: Buttons | None = None) -> None: ...

    async def answer(self, text: str = "", alert: bool = False) -> None: ...

    async def forward_to(self, chat_id: int) -> bool: ...

    async def delete_incoming(self) -> None: ...


class TelethonCtx:
    """Adapter over a Telethon ``NewMessage`` or ``CallbackQuery`` event."""

    def __init__(self, event, *, first_name: str = "", username: str | None = None):
        self.event = event
        self.user_id: int = event.sender_id
        self.first_name = first_name
        self.username = username
        from telethon import events

        self.is_callback = isinstance(event, events.CallbackQuery.Event)
        self.data = event.data.decode("utf-8", "replace") if self.is_callback else ""
        self.text = "" if self.is_callback else (event.raw_text or "")
        self.has_media = (not self.is_callback) and bool(getattr(event, "media", None) or getattr(event, "photo", None))
        self._answered = False

    @classmethod
    async def from_event(cls, event) -> TelethonCtx:
        sender = await event.get_sender()
        return cls(
            event,
            first_name=(getattr(sender, "first_name", None) or "") if sender else "",
            username=getattr(sender, "username", None) if sender else None,
        )

    async def reply(self, text: str, buttons: Buttons | None = None) -> None:
        await self.event.client.send_message(
            self.user_id, text, buttons=to_telethon_buttons(buttons) if buttons else None, link_preview=False
        )

    async def edit(self, text: str, buttons: Buttons | None = None) -> None:
        if not self.is_callback:
            await self.reply(text, buttons)
            return
        from telethon.errors import MessageNotModifiedError

        try:
            await self.event.edit(text, buttons=to_telethon_buttons(buttons) if buttons else None, link_preview=False)
        except MessageNotModifiedError:
            pass
        except Exception:
            await self.reply(text, buttons)  # the original message is gone or not editable

    async def answer(self, text: str = "", alert: bool = False) -> None:
        if not self.is_callback or (self._answered and not text):
            return
        self._answered = True
        with contextlib.suppress(Exception):
            await self.event.answer(text or None, alert=alert)

    async def forward_to(self, chat_id: int) -> bool:
        try:
            await self.event.client.forward_messages(chat_id, self.event.message)
        except Exception:
            return False
        return True

    async def delete_incoming(self) -> None:
        if self.is_callback:
            return
        with contextlib.suppress(Exception):
            await self.event.delete()
