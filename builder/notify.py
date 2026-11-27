"""Outbound messages from background jobs and flows (Telegram in production, a fake in tests)."""

from __future__ import annotations

import io
from typing import Protocol

from builder.ui import Buttons, to_telethon_buttons


class Notifier(Protocol):
    async def send(self, chat_id: int, text: str, buttons: Buttons | None = None) -> bool: ...

    async def send_file(self, chat_id: int, path: str, caption: str, buttons: Buttons | None = None) -> bool:
        """Send a file from disk as a document (the nightly backups)."""
        ...

    async def send_photo(self, chat_id: int, data: bytes, caption: str, buttons: Buttons | None = None) -> bool:
        """Send a PNG as a photo (the health cards)."""
        ...


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

    async def send_file(self, chat_id: int, path: str, caption: str, buttons: Buttons | None = None) -> bool:
        try:
            await self._client.send_file(
                chat_id,
                path,
                caption=caption,
                buttons=to_telethon_buttons(buttons) if buttons else None,
                force_document=True,
            )
        except Exception:
            return False
        return True

    async def send_photo(self, chat_id: int, data: bytes, caption: str, buttons: Buttons | None = None) -> bool:
        picture = io.BytesIO(data)
        picture.name = "health.png"
        try:
            await self._client.send_file(
                chat_id,
                picture,
                caption=caption,
                buttons=to_telethon_buttons(buttons) if buttons else None,
                force_document=False,
            )
        except Exception:
            return False
        return True
