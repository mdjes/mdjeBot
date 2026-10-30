"""The user's gift-code flow: asking for a code, redeeming it, and not mistaking other messages for codes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon import events

from app.i18n import t
from app.services.gifts import codes
from app.telegram.user.gifts import flows


class FakeEvent:
    def __init__(self, text: str = "", sender_id: int = 7) -> None:
        self.sender_id = sender_id
        self.message = SimpleNamespace(message=text)
        self.replies: list[tuple[str, object]] = []

    async def respond(self, text, buttons=None):
        self.replies.append((text, buttons))


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch):
    """In-memory stand-ins for the Redis state and the database reads the flow makes."""
    log = SimpleNamespace(step=None, data={}, cleared=0, enabled=True, outcome=None)

    async def set_step(user_id, step, ttl=None):
        log.step = step

    async def clear_user(user_id):
        log.step, log.data, log.cleared = None, {}, log.cleared + 1

    async def get_data(user_id, key):
        return log.data.get(key)

    async def set_data_many(user_id, values, ttl=None):
        log.data.update(values)

    async def get_bool(key):
        return log.enabled

    async def redeem(user_id, code):
        return log.outcome

    async def lang(user_id):
        return "fa"

    async def menu_labels():
        return {"🛍 خرید سرویس"}

    monkeypatch.setattr(flows, "set_step", set_step)
    monkeypatch.setattr(flows, "clear_user", clear_user)
    monkeypatch.setattr(flows, "get_data", get_data)
    monkeypatch.setattr(flows, "set_data_many", set_data_many)
    monkeypatch.setattr(flows.gift_settings, "get_bool", get_bool)
    monkeypatch.setattr(flows.codes, "redeem", redeem)
    monkeypatch.setattr(flows, "_lang", lang)
    monkeypatch.setattr(flows, "_menu_labels", menu_labels)
    return log


def test_asking_for_a_code_waits_for_the_next_message(state) -> None:
    event = FakeEvent()
    asyncio.run(flows.begin(event))
    assert state.step == flows.STEP_GIFT
    text, buttons = event.replies[0]
    assert text == t("gifts.prompt", "fa")
    assert buttons[0][0].text.startswith("「") and buttons[0][0].type.data == flows.CB_CANCEL


def test_a_switched_off_feature_does_not_wait_for_a_code(state) -> None:
    state.enabled = False
    event = FakeEvent()
    asyncio.run(flows.begin(event))
    assert state.step is None
    assert event.replies[0][0] == t("gifts.disabled", "fa")


def test_a_code_sent_with_the_command_is_redeemed_at_once(state) -> None:
    state.outcome = codes.Redemption(codes.OK, amount=5_000, balance=12_000)
    event = FakeEvent()
    asyncio.run(flows.begin(event, "ABCD-1234"))
    assert event.replies[0][0] == t("gifts.ok", "fa", amount="5,000", balance="12,000")
    assert state.step is None and state.cleared == 1


@pytest.mark.parametrize(
    ("status", "key"),
    [
        (codes.EXPIRED, "expired"),
        (codes.EXHAUSTED, "exhausted"),
        (codes.INACTIVE, "inactive"),
        (codes.ALREADY, "already"),
        (codes.NO_USER, "no_user"),
    ],
)
def test_a_refused_code_ends_the_wait_with_the_reason(state, status: str, key: str) -> None:
    state.outcome = codes.Redemption(status)
    state.step = flows.STEP_GIFT
    event = FakeEvent()
    asyncio.run(flows.redeem(event, 7, "fa", "X"))
    assert event.replies[0][0] == t(f"gifts.{key}", "fa")
    assert state.step is None


def test_a_wrong_code_may_be_retyped_until_the_limit(state) -> None:
    state.outcome = codes.Redemption(codes.NOT_FOUND)
    state.step = flows.STEP_GIFT
    for attempt in range(1, flows.MAX_MISSES):
        event = FakeEvent()
        asyncio.run(flows.redeem(event, 7, "fa", "WRONG"))
        assert event.replies[0][0] == t("gifts.not_found", "fa")
        assert state.step == flows.STEP_GIFT and state.data[flows.MISSES_KEY] == attempt

    last = FakeEvent()
    asyncio.run(flows.redeem(last, 7, "fa", "WRONG"))
    assert last.replies[0][0] == t("gifts.too_many", "fa")
    assert state.step is None


def test_the_awaited_message_is_redeemed_and_stops_other_handlers(state) -> None:
    state.outcome = codes.Redemption(codes.OK, amount=100, balance=100)
    event = FakeEvent(" abcd-1234 ")
    with pytest.raises(events.StopPropagation):
        asyncio.run(flows.on_code_text(event))
    assert event.replies[0][0].startswith(t("gifts.ok", "fa", amount="100", balance="100")[:12])


@pytest.mark.parametrize("text", ["/start", "/cancel", "", "「 🛍 خرید سرویس」", "🛍 خرید سرویس"])
def test_commands_and_menu_presses_end_the_wait_without_being_codes(state, text: str) -> None:
    state.step = flows.STEP_GIFT
    event = FakeEvent(text)
    asyncio.run(flows.on_code_text(event))  # no StopPropagation: the other handlers still see the message
    assert state.step is None and event.replies == []
