"""Outbound messages from background jobs and flows (Telegram in production, a fake in tests)."""

from __future__ import annotations

from typing import Protocol

from builder.ui import Buttons, to_telethon_buttons


class Notifier(Protocol):
    async def send(self, chat_id: int, text: str, buttons: Buttons | None = None) -> bool: ...


class TelethonNotifier:
    def __init__(self, client):
        self._client = client

    async def send(self, chat_id: int, text: str, buttons: Buttons | None = None) -> bool:
        try:
            await self._client.send_message(
                chat_id, text, buttons=to_telethon_buttons(buttons) if buttons else None, link_preview=False
            )
        except Exception:  # user blocked the bot / chat not found: never break a job over a notification
            return False
        return True
