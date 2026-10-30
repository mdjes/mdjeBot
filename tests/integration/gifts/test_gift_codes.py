"""Gift codes on a real database: creating, redeeming once per user, limits and the on/off switch."""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import BigInteger, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.db.base import AsyncSessionLocal
from app.db.models.gifts import GiftCode, GiftRedemption, GiftSetting
from app.db.models.user import User
from app.services.gifts import codes, settings as gift_settings

TABLES = [User, GiftCode, GiftRedemption, GiftSetting]


@compiles(BigInteger, "sqlite")
def _sqlite_bigint(type_, compiler, **kw) -> str:
    # SQLite only autoincrements an INTEGER primary key, like MySQL does for BIGINT.
    return "INTEGER"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gifts.db'}")

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(User.metadata.create_all, tables=[model.__table__ for model in TABLES])

    asyncio.run(setup())
    # Every module opens its sessions from this one factory, so pointing it at the test engine is enough.
    monkeypatch.setitem(AsyncSessionLocal.kw, "bind", engine)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


def _add_users(maker, **balances: int) -> None:
    async def run():
        async with maker() as session:
            session.add_all([User(id=int(uid.removeprefix("u")), amount=amount) for uid, amount in balances.items()])
            await session.commit()

    asyncio.run(run())


def _balance(maker, user_id: int) -> int:
    async def run():
        async with maker() as session:
            return (await session.execute(select(User.amount).where(User.id == user_id))).scalar()

    return asyncio.run(run())


def test_normalize_code_accepts_what_people_type() -> None:
    assert codes.normalize_code(" ab12-cd34 ") == "AB12-CD34"
    assert codes.normalize_code("ab۱۲_cd٣٤") == "AB12-CD34"
    assert codes.normalize_code(None) == ""


def test_generated_codes_are_grouped_and_readable() -> None:
    code = codes.generate_code()
    groups = code.split("-")
    assert len(groups) == codes.CODE_GROUPS and all(len(group) == codes.GROUP_LENGTH for group in groups)
    assert set(code.replace("-", "")) <= set(codes.ALPHABET)


def test_redeeming_credits_the_wallet_once_per_user(db) -> None:
    _add_users(db, u1=100, u2=0)
    gift = asyncio.run(codes.create_gift(5_000, max_uses=2, created_by=9))

    first = asyncio.run(codes.redeem(1, gift.code.lower()))
    again = asyncio.run(codes.redeem(1, gift.code))
    second = asyncio.run(codes.redeem(2, gift.code))
    third = asyncio.run(codes.redeem(3, gift.code))

    assert (first.status, first.amount, first.balance) == (codes.OK, 5_000, 5_100)
    assert again.status == codes.ALREADY
    assert second.ok and _balance(db, 2) == 5_000
    assert third.status == codes.EXHAUSTED
    assert _balance(db, 1) == 5_100
    assert asyncio.run(codes.get_gift(gift.id)).times_used == 2


def test_redeem_refuses_bad_codes_without_touching_the_wallet(db) -> None:
    _add_users(db, u1=0)
    now = int(time.time())
    expired = asyncio.run(codes.create_gift(100, expires_at=now - 10))
    off = asyncio.run(codes.create_gift(100))
    assert asyncio.run(codes.set_active(off.id, False))
    live = asyncio.run(codes.create_gift(100))

    assert asyncio.run(codes.redeem(1, "NOPE-NOPE-NOPE")).status == codes.NOT_FOUND
    assert asyncio.run(codes.redeem(1, "")).status == codes.NOT_FOUND
    assert asyncio.run(codes.redeem(1, expired.code)).status == codes.EXPIRED
    assert asyncio.run(codes.redeem(1, off.code)).status == codes.INACTIVE
    assert asyncio.run(codes.redeem(404, live.code)).status == codes.NO_USER
    assert _balance(db, 1) == 0
    assert asyncio.run(codes.get_gift(live.id)).times_used == 0


def test_a_wallet_with_no_balance_yet_is_credited(db) -> None:
    async def add_blank_user():
        async with db() as session:
            session.add(User(id=5, amount=None))
            await session.commit()

    asyncio.run(add_blank_user())
    gift = asyncio.run(codes.create_gift(300))
    assert asyncio.run(codes.redeem(5, gift.code)).balance == 300


def test_create_gift_rejects_nonsense_and_duplicate_codes(db) -> None:
    assert asyncio.run(codes.create_gift(0)) is None
    assert asyncio.run(codes.create_gift(10, max_uses=0)) is None
    first = asyncio.run(codes.create_gift(10, code="spring-1"))
    assert first.code == "SPRING-1"
    assert asyncio.run(codes.create_gift(10, code="Spring-1")) is None


def test_deleting_a_gift_removes_its_redemptions(db) -> None:
    _add_users(db, u1=0)
    gift = asyncio.run(codes.create_gift(10))
    assert asyncio.run(codes.redeem(1, gift.code)).ok
    assert asyncio.run(codes.delete_gift(gift.id))
    assert not asyncio.run(codes.delete_gift(gift.id))
    assert asyncio.run(codes.list_gifts()) == []


def test_list_shows_the_newest_first(db) -> None:
    first = asyncio.run(codes.create_gift(1))
    second = asyncio.run(codes.create_gift(2))
    assert [gift.id for gift in asyncio.run(codes.list_gifts())] == [second.id, first.id]
    assert [gift.id for gift in asyncio.run(codes.list_gifts(limit=1))] == [second.id]


def test_the_switch_defaults_to_on_and_toggles(db) -> None:
    assert asyncio.run(gift_settings.get_bool(gift_settings.GIFTS_ENABLED)) is True
    assert asyncio.run(gift_settings.toggle(gift_settings.GIFTS_ENABLED)) is False
    assert asyncio.run(gift_settings.get_bool(gift_settings.GIFTS_ENABLED)) is False
    assert asyncio.run(gift_settings.toggle(gift_settings.GIFTS_ENABLED)) is True
