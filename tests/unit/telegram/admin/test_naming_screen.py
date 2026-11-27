"""The admin's default-config-name screen, and the places that use its settings."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import webapp_purchase
from app.services.users import naming
from app.services.users.naming import NamingConfig
from app.telegram.admin.naming import handlers, service
from app.telegram.shared.utils import username as username_utils
from app.telegram.user.shop import helpers as shop_helpers
from app.utils.admin_buttons import ADMIN_ITEMS, MENU_LAYOUTS


class FakeSettings:
    """The purchase settings row, kept in memory."""

    def __init__(self, **values) -> None:
        self.row = SimpleNamespace(
            id=1,
            config_name_template="",
            config_name_bot="",
            config_name_case="keep",
            config_name_prefix_typed=False,
            config_name_blocked="",
            config_name_auto=False,
            **values,
        )

    async def get_settings(self):
        return self.row

    async def update_setting(self, setting_id, **values):
        for key, value in values.items():
            setattr(self.row, key, value)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> FakeSettings:
    fake = FakeSettings()
    monkeypatch.setattr(service, "SettingsManager", lambda: fake)
    monkeypatch.setattr(shop_helpers, "SettingsManager", lambda: fake)
    monkeypatch.setattr("app.db.crud.settings.SettingsManager", lambda: fake)
    monkeypatch.setattr(service, "BOT_TAG", "MdjeBot")
    return fake


# ── the service ───────────────────────────────────────────────────────────────────────────────────
async def test_presets_set_the_template_and_are_recognised_again(settings) -> None:
    assert (await service.load_state()).preset == "random"
    await service.apply_preset("bot_id_digits")
    state = await service.load_state()
    assert settings.row.config_name_template == "{bot}_{id}_{n5}" and state.preset == "bot_id_digits"
    assert "نامعتبر" in await service.apply_preset("nope")


async def test_a_custom_template_is_checked_before_it_is_saved(settings) -> None:
    note, error = await service.set_template("{bot}_{foo}")
    assert note is None and "{foo}" in error and settings.row.config_name_template == ""
    note, error = await service.set_template("VIP-{user}-{n4}")
    assert error is None and settings.row.config_name_template == "VIP-{user}-{n4}"
    assert (await service.load_state()).preset is None  # a custom template matches no preset


async def test_the_bot_name_is_cleaned_and_can_be_cleared(settings) -> None:
    assert (await service.set_bot_name("My Shop"))[0] and settings.row.config_name_bot == "My_Shop"
    assert (await service.set_bot_name("x"))[1]  # too short
    assert (await service.set_bot_name("ربات"))[1]  # no usable characters
    await service.set_bot_name("-")
    assert settings.row.config_name_bot == "" and (await service.load_state()).bot == "MdjeBot"


async def test_the_letter_case_cycles_and_the_switches_flip(settings) -> None:
    assert [await service.cycle_case() for _ in range(3)] == [
        "حروف: " + naming.CASE_LABELS[case] for case in ("lower", "upper", "keep")
    ]
    assert await service.toggle_prefix() is True and await service.toggle_prefix() is False
    assert await service.toggle_auto() is True and settings.row.config_name_auto is True


async def test_forbidden_words_are_saved_cleaned_and_can_be_cleared(settings) -> None:
    await service.set_blocked("Admin، root  test; admin")
    assert settings.row.config_name_blocked == "admin, root, test"  # lower-case, each word once
    assert (await service.set_blocked("،،،"))[1]
    await service.set_blocked("-")
    assert settings.row.config_name_blocked == ""


async def test_the_samples_follow_the_settings(settings) -> None:
    await service.apply_preset("bot_id_digits")
    state = await service.load_state()
    names = service.sample_names(state, admin_id=989932570)
    assert len(names) == 3 and all(name.startswith("MdjeBot_989932570_") for name in names)


# ── the screen ────────────────────────────────────────────────────────────────────────────────────
async def test_the_screen_shows_the_pattern_the_samples_and_every_control(settings) -> None:
    await service.apply_preset("bot_digits")
    text, buttons = handlers.render(await service.load_state(), ["MdjeBot_11111", "MdjeBot_22222"])

    assert "`{bot}_{n5}`" in text and "MdjeBot_11111" in text and "نام‌گذاری خودکار" in text
    data = [b.type.data.decode() for row in buttons for b in row]
    assert {f"cname.preset:{key}" for key in naming.PRESETS} <= set(data)
    assert {"cname.tpl", "cname.bot", "cname.case", "cname.prefix", "cname.blocked", "cname.auto"} <= set(data)
    labels = [b.text for row in buttons for b in row]
    assert any(label.startswith("✅ 🤖") for label in labels)  # the chosen preset is ticked


def test_the_screen_is_in_the_sales_group_of_the_admin_menu() -> None:
    assert ADMIN_ITEMS["ad.naming"].label == handlers.MENU_LABEL
    assert "ad.naming" in {key for row in MENU_LAYOUTS["grp_sales"] for key in row}


# ── the bot, the admin and the Mini App use the settings ──────────────────────────────────────────
class FreePanel:
    code = 1


def _free_after(taken_count: int):
    seen: list[str] = []

    async def is_free(panel, name):
        seen.append(name)
        return len(seen) > taken_count

    return seen, is_free


async def test_the_default_name_follows_the_template(monkeypatch, settings) -> None:
    await service.apply_preset("bot_id_digits")
    seen, is_free = _free_after(0)
    monkeypatch.setattr(username_utils, "is_username_free", is_free)
    monkeypatch.setattr(username_utils, "BOT_TAG", "MdjeBot")

    name = await username_utils.generate_unique_username(FreePanel(), user_id=989932570, tg_username="Ferii550")

    assert name.startswith("MdjeBot_989932570_") and seen == [name]


async def test_taken_names_are_retried_with_more_digits(monkeypatch, settings) -> None:
    await service.set_template("{bot}_{id}")  # fully fixed: only widening can find a free name
    seen, is_free = _free_after(3)
    monkeypatch.setattr(username_utils, "is_username_free", is_free)
    monkeypatch.setattr(username_utils, "BOT_TAG", "MdjeBot")

    name = await username_utils.generate_unique_username(FreePanel(), user_id=5555555)

    assert seen[0] == seen[1] == "MdjeBot_5555555" and len(set(seen)) > 2
    assert name == seen[3] and name.startswith("MdjeBot_5555555_")


async def test_without_settings_the_name_is_the_classic_random_one(monkeypatch) -> None:
    async def no_config():
        return NamingConfig()

    _, is_free = _free_after(0)
    monkeypatch.setattr(naming, "load_config", no_config)
    monkeypatch.setattr(username_utils, "is_username_free", is_free)
    assert len(await username_utils.generate_unique_username(FreePanel())) == 9


async def test_typed_names_follow_the_rules(monkeypatch, settings) -> None:
    await service.set_blocked("admin")
    await service.toggle_prefix()
    monkeypatch.setattr(username_utils, "BOT_TAG", "MdjeBot")

    assert await username_utils.apply_typed_name_rules("Neda") == ("MdjeBot_Neda", None)
    name, error = await username_utils.apply_typed_name_rules("MyAdmin")
    assert name is None and "admin" in error


async def test_the_mini_app_gets_the_same_default_name(monkeypatch, settings) -> None:
    await service.apply_preset("bot_id_digits")

    async def get_panel(code):
        return SimpleNamespace(base_url="http://panel", cookie="t", code=code)

    class Api:
        def __init__(self, base_url):
            pass

        async def get_user_by_username(self, **kwargs):
            import httpx

            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://panel"), response=httpx.Response(404))

    monkeypatch.setattr(webapp_purchase, "PasarguardAPI", Api)
    monkeypatch.setattr(webapp_purchase, "BOT_TAG", "MdjeBot")
    purchase = webapp_purchase.WebAppPurchaseService.__new__(webapp_purchase.WebAppPurchaseService)
    purchase.panels = SimpleNamespace(get_panel_by_code=get_panel)

    result = await purchase.generate_username(1, user_id=42424242)

    assert result["ok"] and result["username"].startswith("MdjeBot_42424242_")


# ── naming without asking ─────────────────────────────────────────────────────────────────────────
class Event:
    sender_id = 777

    async def get_sender(self):
        return SimpleNamespace(username="ali")


async def test_with_auto_naming_on_the_customer_goes_straight_to_the_confirmation(monkeypatch, settings) -> None:
    settings.row.config_name_auto = True
    confirmed: list[tuple[str, bool]] = []

    async def context(user_id):
        return SimpleNamespace(code=1), 1, None

    async def generate(panel, *, user_id=None, tg_username=None):
        assert (user_id, tg_username) == (777, "ali")
        return "MdjeBot_777_12345"

    async def confirm(event, username, *, edit):
        confirmed.append((username, edit))

    monkeypatch.setattr(shop_helpers, "_buy_username_context", context)
    monkeypatch.setattr(shop_helpers, "generate_unique_username", generate)
    monkeypatch.setattr(shop_helpers, "_confirm_buy_username", confirm)

    assert await shop_helpers.auto_name_purchase(Event(), edit=True) is True
    assert confirmed == [("MdjeBot_777_12345", True)]


async def test_with_auto_naming_off_or_the_panel_unreachable_the_customer_is_asked(monkeypatch, settings) -> None:
    async def context(user_id):
        return SimpleNamespace(code=1), 1, None

    async def refuse(panel, **kwargs):
        raise shop_helpers.UsernameLookupError("down")

    async def confirm(event, username, *, edit):
        raise AssertionError("must not confirm")

    monkeypatch.setattr(shop_helpers, "_buy_username_context", context)
    monkeypatch.setattr(shop_helpers, "generate_unique_username", refuse)
    monkeypatch.setattr(shop_helpers, "_confirm_buy_username", confirm)

    assert await shop_helpers.auto_name_purchase(Event(), edit=True) is False  # switched off
    settings.row.config_name_auto = True
    assert await shop_helpers.auto_name_purchase(Event(), edit=True) is False  # the panel could not be asked
