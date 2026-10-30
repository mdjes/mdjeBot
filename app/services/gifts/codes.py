"""Gift codes: a code credits a fixed amount to the wallet of each user who redeems it, once per user."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.base import AsyncSessionLocal as Session
from app.db.models.gifts import GiftCode, GiftRedemption
from app.db.models.user import User

# No 0/O/1/I/L: codes get read out and retyped.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_GROUPS = 3
GROUP_LENGTH = 4

OK = "ok"
NOT_FOUND = "not_found"
INACTIVE = "inactive"
EXPIRED = "expired"
EXHAUSTED = "exhausted"
ALREADY = "already_redeemed"
NO_USER = "no_user"

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


@dataclass(frozen=True, slots=True)
class Redemption:
    status: str
    amount: int = 0
    balance: int = 0

    @property
    def ok(self) -> bool:
        return self.status == OK


def normalize_code(raw: str | None) -> str:
    """Upper case, ASCII digits, no spaces or look-alike dashes."""
    text = (raw or "").translate(_DIGITS).upper()
    for dash in "–—‑−_":
        text = text.replace(dash, "-")
    return "".join(text.split())


def generate_code() -> str:
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(GROUP_LENGTH)) for _ in range(CODE_GROUPS))


async def create_gift(
    amount: int,
    *,
    max_uses: int = 1,
    expires_at: int | None = None,
    created_by: int | None = None,
    code: str | None = None,
) -> GiftCode | None:
    """Create a gift code; None when ``amount``/``max_uses`` are not positive or a chosen code is taken."""
    if amount <= 0 or max_uses <= 0:
        return None
    chosen = normalize_code(code) if code else ""
    for _ in range(1 if chosen else 5):
        gift = GiftCode(
            code=chosen or generate_code(),
            amount=int(amount),
            max_uses=int(max_uses),
            times_used=0,
            expires_at=expires_at,
            active=True,
            created_by=created_by,
            created_at=int(time.time()),
        )
        async with Session() as session:
            session.add(gift)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                continue
            await session.refresh(gift)
            return gift
    return None


async def redeem(user_id: int, raw_code: str, *, now: int | None = None) -> Redemption:
    """Credit the gift to ``user_id`` in one transaction, or say why not."""
    code = normalize_code(raw_code)
    now = int(now if now is not None else time.time())
    async with Session() as session:
        stmt = select(GiftCode).where(GiftCode.code == code)
        if session.bind is not None and session.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        gift = (await session.execute(stmt)).scalars().first()
        if gift is None:
            return Redemption(NOT_FOUND)
        if not gift.active:
            return Redemption(INACTIVE)
        if gift.expires_at is not None and gift.expires_at <= now:
            return Redemption(EXPIRED)
        if gift.times_used >= gift.max_uses:
            return Redemption(EXHAUSTED)
        if (await session.get(User, user_id)) is None:
            return Redemption(NO_USER)
        taken = await session.execute(
            select(GiftRedemption.id).where(GiftRedemption.gift_id == gift.id, GiftRedemption.user_id == user_id)
        )
        if taken.first() is not None:
            return Redemption(ALREADY)

        amount = int(gift.amount)
        session.add(GiftRedemption(gift_id=gift.id, user_id=user_id, amount=amount, created_at=now))
        gift.times_used += 1
        await session.execute(
            update(User).where(User.id == user_id).values(amount=func.coalesce(User.amount, 0) + amount)
        )
        try:
            await session.commit()
        except IntegrityError:
            # A parallel redemption by the same user won the unique constraint.
            await session.rollback()
            return Redemption(ALREADY)
        balance = (await session.execute(select(User.amount).where(User.id == user_id))).scalar() or 0
        return Redemption(OK, amount=amount, balance=int(balance))


async def list_gifts(limit: int = 20) -> list[GiftCode]:
    async with Session() as session:
        result = await session.execute(select(GiftCode).order_by(GiftCode.id.desc()).limit(limit))
        return list(result.scalars().all())


async def get_gift(gift_id: int) -> GiftCode | None:
    async with Session() as session:
        return await session.get(GiftCode, gift_id)


async def set_active(gift_id: int, active: bool) -> bool:
    async with Session() as session:
        gift = await session.get(GiftCode, gift_id)
        if gift is None:
            return False
        gift.active = active
        await session.commit()
        return True


async def delete_gift(gift_id: int) -> bool:
    async with Session() as session:
        gift = await session.get(GiftCode, gift_id)
        if gift is None:
            return False
        await session.execute(GiftRedemption.__table__.delete().where(GiftRedemption.gift_id == gift_id))
        await session.delete(gift)
        await session.commit()
        return True
