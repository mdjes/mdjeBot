"""The gift feature's on/off switch, kept in the ``gift_settings`` table."""

from __future__ import annotations

from app.db.base import AsyncSessionLocal as Session
from app.db.models.gifts import GiftSetting

GIFTS_ENABLED = "gifts_enabled"

DEFAULTS: dict[str, str] = {GIFTS_ENABLED: "1"}


async def get(key: str) -> str:
    async with Session() as session:
        row = await session.get(GiftSetting, key)
    return row.value if row is not None else DEFAULTS.get(key, "")


async def get_bool(key: str) -> bool:
    return (await get(key)).strip().lower() in {"1", "true", "yes", "on"}


async def set_value(key: str, value: object) -> None:
    text = str(value)
    async with Session() as session:
        row = await session.get(GiftSetting, key)
        if row is None:
            session.add(GiftSetting(key=key, value=text))
        else:
            row.value = text
        await session.commit()


async def toggle(key: str) -> bool:
    """Flip a yes/no setting and return the new value."""
    new_value = not await get_bool(key)
    await set_value(key, "1" if new_value else "0")
    return new_value
