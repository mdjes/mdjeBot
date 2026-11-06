"""Admin screens get a way back to the panel; user screens and other people's messages do not."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon import Button
from telethon.tl import types

from app.telegram.keyboards import admin_back
from app.telegram.keyboards.admin_back import BACK_CALLBACK, install_back_row, set_admin_chat, with_back_row


def _run_in(module: str, func, *args):
    """Call ``func`` from a function whose module is ``module`` (what the scoping looks at)."""
    namespace = {"__name__": module}
    exec("async def call(func, *args):\n    return await func(*args)\n", namespace)
    return namespace["call"](func, *args)


def _data(buttons) -> list[bytes]:
    rows = buttons.rows if isinstance(buttons, types.ReplyInlineMarkup) else buttons
    out = []
    for row in rows:
        for button in getattr(row, "buttons", row):
            inner = getattr(button, "button", button)
            out.append(getattr(inner.type, "data", b""))
    return out


@pytest.fixture(autouse=True)
def _no_admin_left_behind():
    yield
    set_admin_chat(None)


# --- the row itself ------------------------------------------------------------------------------------


def test_a_button_less_message_gets_just_the_row() -> None:
    assert _data(with_back_row(None)) == [BACK_CALLBACK.encode()]


def test_the_row_goes_below_existing_inline_buttons() -> None:
    rows = with_back_row(
        [[Button.inline("a", data="x:1"), Button.inline("b", data="x:2")], [Button.inline("c", data="x:3")]]
    )
    assert [len(row) for row in rows] == [2, 1, 1]
    assert _data(rows)[-1] == BACK_CALLBACK.encode()


@pytest.mark.parametrize(
    "shape",
    [
        lambda b: [b],
        lambda b: [[b]],
        lambda b: b,
        lambda b: types.ReplyInlineMarkup([types.KeyboardInlineButtonRow([b])]),
    ],
)
def test_every_shape_telethon_accepts_is_understood(shape) -> None:
    result = with_back_row(shape(Button.inline("a", data="x:1")))
    assert _data(result) == [b"x:1", BACK_CALLBACK.encode()]


@pytest.mark.parametrize("data", [BACK_CALLBACK, "adm:ad.home", "adm:ad.stats"])
def test_a_screen_that_already_leads_back_is_left_alone(data: str) -> None:
    keyboard = [[Button.inline("a", data="x:1")], [Button.inline("out", data=data)]]
    assert with_back_row(keyboard) is keyboard


def test_a_back_button_with_its_own_wording_counts() -> None:
    keyboard = [[Button.inline("🔙 بازگشت به پنل", data="custom")]]
    assert with_back_row(keyboard) is keyboard


def test_reply_keyboards_and_force_reply_are_untouched() -> None:
    for keyboard in (
        [[Button.text("hello")]],
        Button.clear(),
        types.ReplyKeyboardMarkup(rows=[]),
        types.ReplyKeyboardForceReply(),
    ):
        assert with_back_row(keyboard) is keyboard


def test_inline_and_reply_buttons_mixed_are_untouched() -> None:
    keyboard = [[Button.inline("a", data="x"), Button.text("b")]]
    assert with_back_row(keyboard) is keyboard


# --- when it applies -----------------------------------------------------------------------------------


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []
        self.edited: list[tuple[tuple, dict]] = []

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))

    async def edit_message(self, *args, **kwargs):
        self.edited.append((args, kwargs))


def _client() -> FakeClient:
    client = FakeClient()
    install_back_row(client)
    return client


def test_an_admin_screen_gets_the_row_on_send_and_edit() -> None:
    client = _client()
    set_admin_chat(7)

    async def screen():
        await client.send_message(7, "stats", buttons=[[Button.inline("a", data="x")]])
        await client.send_message(entity=types.InputPeerUser(user_id=7, access_hash=0), message="no buttons")
        await client.edit_message(7, 5, "edited", buttons=None)

    asyncio.run(_run_in("app.telegram.admin.info_bot.callbacks", screen))

    assert _data(client.sent[0][1]["buttons"]) == [b"x", BACK_CALLBACK.encode()]
    assert _data(client.sent[1][1]["buttons"]) == [BACK_CALLBACK.encode()]
    assert _data(client.edited[0][1]["buttons"]) == [BACK_CALLBACK.encode()]


def test_a_user_screen_run_for_an_admin_is_left_alone() -> None:
    client = _client()
    set_admin_chat(7)

    async def screen():
        await client.send_message(7, "my services", buttons=[[Button.inline("a", data="x")]])

    asyncio.run(_run_in("app.telegram.user.services.messages", screen))
    assert _data(client.sent[0][1]["buttons"]) == [b"x"]


def test_the_innermost_screen_decides_when_a_user_screen_is_called_from_an_admin_one() -> None:
    client = _client()
    set_admin_chat(7)

    async def user_screen():
        await _run_in(
            "app.telegram.user.start.messages",
            lambda: client.send_message(7, "home", buttons=[[Button.inline("a", data="x")]]),
        )

    asyncio.run(_run_in("app.telegram.admin.menu_press", user_screen))
    assert _data(client.sent[0][1]["buttons"]) == [b"x"]


def test_services_called_from_an_admin_screen_count_as_that_screen() -> None:
    client = _client()
    set_admin_chat(7)

    async def service():
        await client.send_message(7, "report")

    async def screen():
        await _run_in("app.services.billing.reports", service)

    asyncio.run(_run_in("app.telegram.admin.wallets.callbacks", screen))
    assert _data(client.sent[0][1]["buttons"]) == [BACK_CALLBACK.encode()]


def test_messages_to_other_people_are_left_alone() -> None:
    client = _client()
    set_admin_chat(7)

    async def screen():
        await client.send_message(99, "hello customer")
        await client.send_message(-1001234, "channel", buttons=[[Button.inline("a", data="x")]])

    asyncio.run(_run_in("app.telegram.admin.send2all.callbacks", screen))
    assert "buttons" not in client.sent[0][1]
    assert _data(client.sent[1][1]["buttons"]) == [b"x"]


def test_nothing_changes_outside_an_admin_update() -> None:
    client = _client()

    async def screen():
        await client.send_message(7, "a notice to the admin")

    asyncio.run(_run_in("app.telegram.admin.info_bot.callbacks", screen))
    assert "buttons" not in client.sent[0][1]


def test_installing_twice_wraps_once() -> None:
    client = FakeClient()
    install_back_row(client)
    first = client.send_message
    install_back_row(client)
    assert client.send_message is first


def test_a_rich_message_gets_the_row_the_same_way() -> None:
    from app.services.telegram.rich_message import _with_admin_back

    set_admin_chat(7)

    async def screen():
        return _with_admin_back(7, None), _with_admin_back(99, None)

    mine, others = asyncio.run(_run_in("app.telegram.admin.info_bot.callbacks", screen))
    assert _data(mine) == [BACK_CALLBACK.encode()] and others is None


def test_the_back_target_is_the_one_the_panel_handles() -> None:
    # The callback the row carries is answered by the admin home module.
    assert BACK_CALLBACK == "back_to_admin_panel"
    assert admin_back.BACK_LABEL.startswith("🔙")
    assert SimpleNamespace(data=BACK_CALLBACK)
