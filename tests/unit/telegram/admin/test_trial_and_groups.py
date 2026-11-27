"""The free-trial screen, the grouped admin menu, and the purchase lines on a receipt."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon import events

from app.services.billing.purchase_lines import describe_pending_purchase
from app.telegram.admin import menu_press
from app.telegram.admin.trial import handlers as trial_ui, service as trial
from app.telegram.keyboards.admin_menu import MENUS
from app.utils.admin_buttons import ADMIN_ITEMS, GROUP_INTROS, GROUP_MENUS, MENU_LAYOUTS

# --- the trial: what an admin may type ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "gb"),
    [("0.5", 0.5), ("۰٫۵", 0.5), ("500mb", 0.5), ("500 مگ", 0.5), ("2", 2.0), ("2 GB", 2.0), ("1,5", 1.5)],
)
def test_volume_is_read_the_way_an_admin_writes_it(typed: str, gb: float) -> None:
    assert trial.parse_volume_gb(typed) == gb


@pytest.mark.parametrize("typed", ["", "abc", "0", "-1", "5000", "1.2.3"])
def test_nonsense_volume_is_refused(typed: str) -> None:
    assert trial.parse_volume_gb(typed) is None


def test_days_are_whole_and_bounded() -> None:
    assert trial.parse_days("۳") == 3 and trial.parse_days("365") == 365
    assert all(trial.parse_days(bad) is None for bad in ("0", "366", "1.5", "x", ""))


def test_half_a_gigabyte_is_shown_as_500_megabytes() -> None:
    assert trial.volume_text(0.5) == "500 مگ"


# --- the trial: turning it on makes sure a panel offers it -------------------------------------------------


class FakeSettings:
    def __init__(self, **values) -> None:
        self.row = SimpleNamespace(id=1, test_mode=False, test_panel_id=0, test_phone_verify=True, **values)

    async def get_settings(self):
        return self.row

    async def update_setting(self, setting_id, **fields):
        for key, value in fields.items():
            setattr(self.row, key, value)


class FakePanels:
    def __init__(self, *panels) -> None:
        self.panels = {p.code: p for p in panels}
        self.updates: list[tuple[int, dict]] = []

    async def get_all_panels(self):
        return list(self.panels.values())

    async def get_panel_by_code(self, code):
        return self.panels.get(int(code))

    async def update_panel(self, code, **fields):
        self.updates.append((int(code), fields))
        return True


def _panel(code: int, name: str, enable: bool = True) -> SimpleNamespace:
    return SimpleNamespace(code=code, name=name, enable=enable, test_settings=None)


@pytest.fixture
def shop(monkeypatch: pytest.MonkeyPatch):
    settings, panels = FakeSettings(), FakePanels(_panel(1, "DE-1"), _panel(2, "NL-1"))
    monkeypatch.setattr(trial, "SettingsManager", lambda: settings)
    monkeypatch.setattr(trial, "PanelsManager", lambda: panels)

    async def offering(setting=None):
        return [p for p in panels.panels.values() if (p.test_settings or {}).get("enabled")]

    async def offered(setting=None):
        return bool(settings.row.test_mode and await offering())

    monkeypatch.setattr(trial, "trial_panels", offering)
    monkeypatch.setattr(trial, "trial_offered", offered)
    monkeypatch.setattr(trial, "panel_test_flag", lambda p: bool((p.test_settings or {}).get("enabled")))

    async def update_panel(code, **fields):
        panels.updates.append((int(code), fields))
        if "test_enabled" in fields:
            panels.panels[int(code)].test_settings = {"enabled": fields["test_enabled"]}
        return True

    panels.update_panel = update_panel
    return settings, panels


def test_turning_the_trial_on_also_picks_a_panel_so_the_button_shows(shop) -> None:
    settings, _panels = shop

    note = asyncio.run(trial.switch(True))

    assert settings.row.test_mode and settings.row.test_panel_id == 1
    assert "DE-1" in note and asyncio.run(trial.load_state()).visible


@pytest.mark.usefixtures("shop")
def test_turning_it_off_hides_the_button_and_keeps_the_panel_choice() -> None:
    asyncio.run(trial.switch(True))
    asyncio.run(trial.switch(False))
    state = asyncio.run(trial.load_state())
    assert not state.on and not state.visible and [p.code for p in state.panels] == [1]


def test_volume_and_days_go_to_the_panels_that_give_the_trial(shop) -> None:
    _, panels = shop
    asyncio.run(trial.switch(True))
    panels.updates.clear()

    asyncio.run(trial.set_volume(0.5))
    asyncio.run(trial.set_days(2))

    assert panels.updates == [(1, {"test_volume_gb": 0.5}), (1, {"test_duration_days": 2})]


def test_the_screen_says_when_it_is_on_but_nothing_is_offered() -> None:
    state = trial.TrialState(on=True, visible=False, panels=[], all_panels=[], volume_gb=0.5, days=3, phone_verify=True)
    text, buttons = trial_ui.render(state)
    assert "نمایش داده نمی‌شود" in text and "500 مگ" in text
    assert [b.type.data for row in buttons for b in row][:3] == [b"trial.switch", b"trial.vol", b"trial.days"]


# --- the grouped admin menu --------------------------------------------------------------------------------


def _keys(menu: str) -> set[str]:
    return {key for row in MENU_LAYOUTS[menu] for key in row}


def test_the_main_menu_is_short_and_every_old_button_is_in_a_group() -> None:
    main = _keys("main")
    assert len(main) <= 12
    grouped = set().union(*(_keys(menu) for menu in GROUP_MENUS.values()))
    reachable = main | grouped | _keys("discounts") | _keys("panels") | _keys("back") | _keys("home")
    assert set(ADMIN_ITEMS) <= reachable
    assert set(GROUP_MENUS) <= main and set(GROUP_MENUS.values()) <= set(GROUP_INTROS)


def test_every_group_leads_back_to_the_panel() -> None:
    assert all("ad.back_panel" in _keys(menu) for menu in GROUP_MENUS.values())


class MenuEvent:
    def __init__(self, data: bytes) -> None:
        self.data, self.sender_id, self.edits = data, 7, []

    async def answer(self, *args, **kwargs):
        pass

    async def edit(self, text, buttons=None):
        self.edits.append((text, buttons))


def test_pressing_a_group_opens_it_in_place_without_running_a_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    pressed = []

    async def dispatch(client, event, text):
        pressed.append(text)

    monkeypatch.setattr(menu_press, "ADMIN_ID", [7])
    monkeypatch.setattr(menu_press, "dispatch_text", dispatch)
    event = MenuEvent(b"adm:ad.grp_users")

    with pytest.raises(events.StopPropagation):
        asyncio.run(menu_press.on_menu_button(event))

    text, buttons = event.edits[0]
    assert "کاربران و موجودی" in text and buttons is MENUS["grp_users"] and pressed == []


# --- the receipt -------------------------------------------------------------------------------------------


def test_a_receipt_shows_the_volume_the_customer_chose() -> None:
    pending = {"amount": 80_000, "payload": {"product_label": "کانفیگ VPN", "volume": "41 گیگ", "custom_days": 30}}
    lines = describe_pending_purchase(pending)
    assert "🛒 **خرید:** کانفیگ VPN" in lines and "📦 **حجم انتخابی:** 41 گیگ" in lines
    assert "⏰ **مدت:** 30 روز" in lines and "`80,000` تومان" in lines


def test_a_custom_purchase_without_wording_uses_the_number_and_a_plain_top_up_adds_nothing() -> None:
    assert "📦 **حجم انتخابی:** 50 گیگ" in describe_pending_purchase({"payload": {"gig": "50"}})
    assert describe_pending_purchase(None) == "" and describe_pending_purchase({}) == ""
