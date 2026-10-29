"""Bot token validation through the Telegram Bot API (``getMe``)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,50}$")


class TokenError(ValueError):
    """The token is malformed or rejected by Telegram."""


class TelegramUnavailable(RuntimeError):
    """Telegram could not be reached, so the token could not be checked."""


@dataclass(frozen=True)
class BotInfo:
    id: int
    username: str
    name: str


def looks_like_token(value: str) -> bool:
    return bool(TOKEN_RE.match(value.strip()))


async def fetch_bot_info(token: str, *, client: httpx.AsyncClient | None = None) -> BotInfo:
    """Return the bot behind ``token`` or raise :class:`TokenError` / :class:`TelegramUnavailable`."""
    token = token.strip()
    if not looks_like_token(token):
        raise TokenError("invalid token format")

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        try:
            response = await http.get(f"https://api.telegram.org/bot{token}/getMe")
        except httpx.HTTPError as exc:
            raise TelegramUnavailable(str(exc)) from exc
        if response.status_code in (401, 404):
            raise TokenError("token rejected by Telegram")
        if response.status_code != 200:
            raise TelegramUnavailable(f"unexpected status {response.status_code}")
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        if not payload.get("ok") or not isinstance(result, dict) or not result.get("is_bot", True):
            raise TokenError("token rejected by Telegram")
        return BotInfo(
            id=int(result["id"]),
            username=str(result.get("username") or ""),
            name=str(result.get("first_name") or ""),
        )
    finally:
        if owns_client:
            await http.aclose()
