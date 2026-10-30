"""The admin gift-code menu: who can open it, the creation form, and the keyboard it draws."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon import events

from app.db.models.gifts import GiftCode
from app.telegram.admin.gifts import handlers, texts
from app.telegram.keyboards.registry import KEYBOARD_BUTTON_TITLES
from app.utils.admin_buttons import ADMIN_ITEMS, MENU_LAYOUTS

ADMIN = 1


class FakeEvent:
    def __init__(self, text: str = "", sender_id: int = ADMIN, data: bytes | None = None, private: bool = True):
        self.sender_id = sender_id
        self.is_private = private
        self.message = SimpleNamespace(message=text)
        self.data = data
        self.replies: list[tuple[str, object]] = []

    async def respond(self, text, buttons=None):
        self.replies.append((text, buttons))


def _gift(**overrides) -> GiftCode:
    values = {
        "id": 3,
        "code": "ABCD-EFGH-JKMN",
        "amount": 5_000,
        "max_uses": 10,
        "times_used": 2,
        "expires_at": None,
        "active": True,
    }
    return GiftCode(**(values | overrides))


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(handlers, "ADMIN_ID", [ADMIN])
    log = SimpleNamespace(step=None, data={}, created=None)

    async def get_step(user_id):
        return log.step

    async def set_step(user_id, step, ttl=None):
        log.step = step

    async def set_data(user_id, key, value, ttl=None):
        log.data[key] = value

    async def get_data(user_id, key):
        return log.data.get(key)

    async def clear_user(user_id):
        log.step, log.data = None, {}

    async def create_gift(amount, **kwargs):
        log.created = (amount, kwargs)
        return _gift(amount=amount, max_uses=kwargs["max_uses"], expires_at=kwargs["expires_at"])

    for name, fn in {
        "get_step": get_step,
        "set_step": set_step,
        "set_data": set_data,
        "get_data": get_data,
        "clear_user": clear_user,
    }.items():
        monkeypatch.setattr(handlers, name, fn)
    monkeypatch.setattr(handlers.codes, "create_gift", create_gift)
    return log


def test_the_menu_button_is_part_of_the_admin_menu() -> None:
    assert ADMIN_ITEMS["ad.gifts"].label == handlers.MENU_LABEL
    assert "ad.gifts" in {key for row in MENU_LAYOUTS["main"] for key in row}
    assert "ad.gifts" in KEYBOARD_BUTTON_TITLES


def test_only_an_admin_at_the_panel_opens_the_menu(admin) -> None:
    admin.step = handlers.PANEL_STEP
    assert asyncio.run(handlers.is_menu_press(FakeEvent(handlers.MENU_LABEL)))
    assert not asyncio.run(handlers.is_menu_press(FakeEvent(handlers.MENU_LABEL, sender_id=99)))
    assert not asyncio.run(handlers.is_menu_press(FakeEvent(handlers.MENU_LABEL, private=False)))
    assert not asyncio.run(handlers.is_menu_press(FakeEvent("something else")))
    admin.step = "MToUser"
    assert not asyncio.run(handlers.is_menu_press(FakeEvent(handlers.MENU_LABEL)))


def test_only_admin_gift_callbacks_are_taken(admin) -> None:
    assert handlers.is_gift_callback(FakeEvent(data=b"gift.menu"))
    assert handlers.is_gift_callback(FakeEvent(data=b"gift.view:3"))
    assert not handlers.is_gift_callback(FakeEvent(data=b"gift.open"))  # the user's button
    assert not handlers.is_gift_callback(FakeEvent(data=b"other"))
    assert not handlers.is_gift_callback(FakeEvent(data=None))
    assert not handlers.is_gift_callback(FakeEvent(data=b"gift.menu", sender_id=99))


def _answer(text: str) -> FakeEvent:
    event = FakeEvent(text)
    with pytest.raises(events.StopPropagation):
        asyncio.run(handlers.on_form_text(event))
    return event


def test_the_form_asks_amount_uses_and_days_then_creates_the_gift(admin) -> None:
    admin.step = texts.STEP_AMOUNT
    assert _answer("۵,۰۰۰").replies[0][0] == texts.ASK_USES
    assert admin.step == texts.STEP_USES and admin.data[handlers.AMOUNT_KEY] == 5_000

    assert _answer("10").replies[0][0] == texts.ASK_DAYS
    assert admin.step == texts.STEP_DAYS

    done = _answer("30")
    amount, kwargs = admin.created
    assert (amount, kwargs["max_uses"], kwargs["created_by"]) == (5_000, 10, ADMIN)
    assert kwargs["expires_at"] is not None
    assert "ABCD-EFGH-JKMN" in done.replies[0][0] or "5,000" in done.replies[0][0]
    assert admin.step == handlers.PANEL_STEP


def test_zero_days_means_no_expiry(admin) -> None:
    admin.step = texts.STEP_DAYS
    admin.data = {handlers.AMOUNT_KEY: 100, handlers.USES_KEY: 1}
    _answer("0")
    assert admin.created[1]["expires_at"] is None


@pytest.mark.parametrize("step", [texts.STEP_AMOUNT, texts.STEP_USES])
@pytest.mark.parametrize("text", ["abc", "0", "-5", ""])
def test_bad_numbers_keep_the_form_where_it_is(admin, step: str, text: str) -> None:
    admin.step = step
    event = _answer(text)
    assert event.replies[0][0] == texts.BAD_NUMBER
    assert admin.step == step and admin.created is None


def test_the_form_only_listens_to_admins_in_its_steps(admin) -> None:
    admin.step = texts.STEP_AMOUNT
    assert asyncio.run(handlers.is_form_answer(FakeEvent("5")))
    assert not asyncio.run(handlers.is_form_answer(FakeEvent("5", sender_id=99)))
    assert not asyncio.run(handlers.is_form_answer(FakeEvent("  ")))
    admin.step = handlers.PANEL_STEP
    assert not asyncio.run(handlers.is_form_answer(FakeEvent("5")))


def test_gift_states_and_menu_buttons() -> None:
    now = 1_000
    assert texts.state(_gift(), now) == "✅ فعال"
    assert texts.state(_gift(active=False), now) == "⛔️ غیرفعال"
    assert texts.state(_gift(expires_at=now), now) == "⌛️ منقضی"
    assert texts.state(_gift(times_used=10), now) == "✔️ تمام‌شده"

    text, buttons = texts.menu([_gift()], True, now)
    assert "ABCD-EFGH-JKMN" in text and "2/10" in text
    data = [button.type.data for row in buttons for button in row]
    assert b"gift.new" in data and b"gift.switch" in data and b"gift.view:3" in data
    assert texts.menu([], False, now)[0].count("هنوز کدی نساخته‌اید.") == 1

    detail_text, detail_buttons = texts.detail(_gift(), now)
    assert "5,000" in detail_text
    detail_data = [button.type.data for row in detail_buttons for button in row]
    assert b"gift.toggle:3" in detail_data and b"gift.del:3" in detail_data
