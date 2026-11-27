"""Card-to-card rotation: a card with a daily cap is skipped once the amounts held on it would pass the cap."""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

import pytest

from app.services.payments import card_rotation as rotation


def _cards(*active: bool):
    return [
        SimpleNamespace(id=i + 1, number=f"6037 0000 0000 000{i + 1}", name=f"c{i + 1}", active=flag)
        for i, flag in enumerate(active)
    ]


def _settings(limits=None, random_mode=False):
    return SimpleNamespace(id=1, manual_card_limits=limits or {}, manual_card_random_mode=random_mode)


class FakeRedis:
    """Just the commands card_rotation uses."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)

    async def expire(self, key, seconds):
        return True

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr(rotation, "get_redis", get_redis)
    monkeypatch.setattr(rotation, "tehran_day", lambda now=None: "2026-01-01")  # never straddle midnight
    return fake


# --- the pure choice ---------------------------------------------------------------------------------------


def test_a_full_card_is_skipped_for_the_next_one_with_room() -> None:
    cards = _cards(True, False, False)
    picked = rotation.pick_card(cards, {1: 100, 2: 100}, {1: 80, 2: 10}, amount=50, active_id=1, random_mode=False)
    assert picked.card.id == 2 and not picked.all_full


def test_a_card_without_a_cap_is_always_open() -> None:
    cards = _cards(True, False)
    picked = rotation.pick_card(cards, {1: 100}, {1: 100}, amount=50, active_id=1, random_mode=False)
    assert picked.card.id == 2


def test_the_active_card_is_used_while_it_has_room() -> None:
    cards = _cards(False, True, False)
    picked = rotation.pick_card(cards, {2: 100}, {2: 10}, amount=50, active_id=2, random_mode=False)
    assert picked.card.id == 2


def test_when_every_card_is_full_the_one_with_most_room_is_shown_anyway() -> None:
    cards = _cards(True, False)
    picked = rotation.pick_card(cards, {1: 100, 2: 200}, {1: 100, 2: 150}, amount=80, active_id=1, random_mode=False)
    assert picked.all_full and picked.card.id == 2


def test_the_same_customer_keeps_the_card_already_shown() -> None:
    cards = _cards(True, False)
    picked = rotation.pick_card(cards, {1: 100, 2: 100}, {}, amount=10, active_id=1, random_mode=False, current_id=2)
    assert picked.card.id == 2


def test_random_mode_only_picks_from_cards_with_room() -> None:
    cards = _cards(True, False, False)
    for seed in range(20):
        picked = rotation.pick_card(
            cards, {1: 10}, {1: 10}, amount=5, active_id=1, random_mode=True, rng=random.Random(seed)
        )
        assert picked.card.id in (2, 3)


def test_no_cards_means_no_pick() -> None:
    assert rotation.pick_card([], {}, {}, amount=1, active_id=None, random_mode=False) is None


def test_limits_ignore_nonsense_and_zero() -> None:
    settings = _settings({"1": 100, "2": 0, "x": 5, "3": "abc", "4": "250"})
    assert rotation.card_limits(settings) == {1: 100, 4: 250}
    assert rotation.limit_for(settings, 1) == 100 and rotation.limit_for(settings, 9) == 0


@pytest.mark.parametrize(
    ("typed", "value"),
    [("۵,۰۰۰,۰۰۰", 5_000_000), ("2000000", 2_000_000), ("0", 0), ("abc", None), ("", None)],
)
def test_an_admin_can_type_the_cap_in_any_digits(typed: str, value: int | None) -> None:
    assert rotation.parse_toman(typed) == value


def test_card_numbers_are_masked() -> None:
    assert rotation.masked("6037 9911 2233 4455") == "6037 •••• 4455"


# --- holding amounts on cards ----------------------------------------------------------------------------


def test_without_a_cap_nothing_changes(redis: FakeRedis) -> None:
    cards = _cards(False, True)
    card = asyncio.run(rotation.choose_card(_settings(), cards, user_id=7, amount=50_000))
    assert card.id == 2
    assert not redis.hashes and not redis.values  # no rotation bookkeeping at all
    assert asyncio.run(rotation.choose_card(_settings(), [], user_id=7, amount=1)) is None


def test_the_second_customer_is_sent_to_another_card_once_the_first_is_full(redis: FakeRedis) -> None:
    cards = _cards(True, False)
    settings = _settings({"1": 100_000})
    first = asyncio.run(rotation.choose_card(settings, cards, user_id=1, amount=70_000))
    second = asyncio.run(rotation.choose_card(settings, cards, user_id=2, amount=70_000))
    assert (first.id, second.id) == (1, 2)


def test_a_customer_asking_again_keeps_the_card_and_does_not_count_twice(redis: FakeRedis) -> None:
    cards = _cards(True, False)
    settings = _settings({"1": 100_000})
    for _ in range(3):
        assert asyncio.run(rotation.choose_card(settings, cards, user_id=1, amount=70_000)).id == 1
    assert asyncio.run(rotation.usage_today(cards))[1] == 70_000


def test_changing_the_amount_moves_the_hold(redis: FakeRedis) -> None:
    cards = _cards(True, False)
    settings = _settings({"1": 100_000})
    asyncio.run(rotation.choose_card(settings, cards, user_id=1, amount=60_000))
    asyncio.run(rotation.choose_card(settings, cards, user_id=2, amount=60_000))  # card 1 is too full: card 2
    # Customer 1 now wants less: still on card 1, counted once with the new amount.
    asyncio.run(rotation.choose_card(settings, cards, user_id=1, amount=20_000))
    assert asyncio.run(rotation.usage_today(cards)) == {1: 20_000, 2: 60_000}


def test_a_hold_that_is_never_paid_expires(redis: FakeRedis) -> None:
    cards = _cards(True, False)
    settings = _settings({"1": 100_000})
    asyncio.run(rotation.choose_card(settings, cards, user_id=1, amount=90_000))
    later = rotation.time.time() + rotation.HOLD_SECONDS + 5
    assert asyncio.run(rotation.usage_today(cards, now=later))[1] == 0


def test_a_receipt_makes_the_hold_permanent(redis: FakeRedis) -> None:
    cards = _cards(True, False)
    settings = _settings({"1": 100_000})
    asyncio.run(rotation.choose_card(settings, cards, user_id=1, amount=90_000))
    asyncio.run(rotation.confirm_pick(1))
    later = rotation.time.time() + rotation.HOLD_SECONDS * 3
    assert asyncio.run(rotation.usage_today(cards, now=later))[1] == 90_000
    assert asyncio.run(rotation.choose_card(settings, cards, user_id=2, amount=50_000)).id == 2


def test_looking_without_holding_leaves_no_trace(redis: FakeRedis) -> None:
    cards = _cards(True, False)
    card = asyncio.run(rotation.choose_card(_settings({"1": 100}), cards, user_id=1, amount=0, hold=False))
    assert card.id == 1 and not redis.hashes


def test_a_redis_outage_falls_back_to_the_normal_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_redis():
        return None

    monkeypatch.setattr(rotation, "get_redis", no_redis)
    cards = _cards(True, False)
    assert asyncio.run(rotation.choose_card(_settings({"1": 100}), cards, user_id=1, amount=50)).id == 1


def test_setting_and_clearing_a_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = _settings({"1": 100})
    saved: list[dict] = []

    class Manager:
        async def get_settings(self):
            return stored

        async def update_setting(self, setting_id, **kwargs):
            saved.append(kwargs["manual_card_limits"])
            stored.manual_card_limits = kwargs["manual_card_limits"]

    monkeypatch.setattr(rotation, "SettingsManager", Manager)
    asyncio.run(rotation.set_card_limit(2, 500))
    asyncio.run(rotation.set_card_limit(1, 0))
    assert saved == [{"1": 100, "2": 500}, {"2": 500}]
