"""Default config names: the templates, the 32-character limit, and the rules for names customers type."""

from __future__ import annotations

import random
import re
from types import SimpleNamespace

import pytest

from app.services.users import naming
from app.services.users.naming import NameContext, NamingConfig

CTX = NameContext(bot="MdjeBot", user_id=989932570, tg_username="Ferii550", seq=4)


def _name(template: str, ctx: NameContext = CTX, **kw) -> str:
    kw.setdefault("rng", random.Random(7))
    return naming.build_name(template, ctx, **kw)


# ── the templates ─────────────────────────────────────────────────────────────────────────────────
def test_the_bot_name_and_five_digits() -> None:
    assert re.fullmatch(r"MdjeBot_\d{5}", _name("{bot}_{n5}"))


def test_the_bot_name_the_customers_id_and_five_digits() -> None:
    assert re.fullmatch(r"MdjeBot_989932570_\d{5}", _name("{bot}_{id}_{n5}"))


def test_telegram_username_and_purchase_number() -> None:
    assert _name("{user}_{seq}") == "Ferii550_4"
    assert _name("{user}_{n4}", NameContext(bot="MdjeBot")).startswith("MdjeBot_")  # no @username: the bot name


def test_random_parts_come_in_digits_or_letters_and_digits() -> None:
    assert re.fullmatch(r"[0-9]{8}", _name("{n8}"))
    assert re.fullmatch(r"[A-Z0-9]{6}", _name("{r6}"))
    assert _name("{n5}") != _name("{n5}", rng=random.Random(99))


def test_an_empty_template_is_the_classic_random_name() -> None:
    assert re.fullmatch(r"[A-Z0-9]{9}", _name(""))
    assert _name("", legacy=lambda: "CLASSIC99") == "CLASSIC99"


def test_dashes_spaces_and_dots_become_underscores() -> None:
    assert re.fullmatch(r"MdjeBot_989932570_\d{5}", _name("{bot}-{id}-{n5}"))
    assert _name("A B.C_{seq}") == "A_B_C_4"


def test_letter_case_is_applied() -> None:
    assert _name("{bot}_{seq}", case="lower") == "mdjebot_4"
    assert _name("{bot}_{seq}", case="upper") == "MDJEBOT_4"
    assert _name("{bot}_{seq}", case="keep") == "MdjeBot_4"


# ── fitting 32 characters ─────────────────────────────────────────────────────────────────────────
def test_a_long_bot_name_is_shortened_but_the_id_and_the_random_digits_stay() -> None:
    ctx = NameContext(bot="AVeryLongBotNameForShops", user_id=989932570)
    name = _name("{bot}_{id}_{n5}", ctx)
    assert len(name) <= naming.MAX_LEN and naming.NAME_RE.fullmatch(name)
    assert "989932570" in name and re.search(r"\d{5}$", name)


def test_taken_names_are_widened_with_digits_and_still_fit() -> None:
    plain = _name("{bot}_{id}", CTX)
    wider = _name("{bot}_{id}", CTX, widen=1)
    assert wider.startswith("MdjeBot_989932570_") and wider != plain and len(wider) <= naming.MAX_LEN
    huge = _name("{bot}_{id}_{n9}_{n9}", CTX, widen=3)
    assert len(huge) <= naming.MAX_LEN and naming.NAME_RE.fullmatch(huge)


def test_a_template_that_gives_nothing_usable_falls_back_to_a_random_name() -> None:
    assert naming.NAME_RE.fullmatch(_name("{id}", NameContext(user_id=None), legacy=lambda: "FALLBACK1"))
    assert _name("{id}", NameContext(user_id=None), legacy=lambda: "FALLBACK1") == "FALLBACK1"


@pytest.mark.parametrize("template", [value for _, value in naming.PRESETS.values()])
def test_every_preset_gives_a_valid_name(template: str) -> None:
    for seed in range(30):
        assert naming.NAME_RE.fullmatch(naming.build_name(template, CTX, rng=random.Random(seed), case="keep"))


def test_the_presets_are_valid_templates() -> None:
    assert [naming.template_error(t) for _, t in naming.PRESETS.values() if t] == [None] * 4
    assert naming.PRESETS["bot_digits"][1] == "{bot}_{n5}" and naming.PRESETS["bot_id_digits"][1] == "{bot}_{id}_{n5}"


def test_the_preview_gives_the_requested_number_of_names() -> None:
    names = naming.preview(NamingConfig(template="{bot}_{n5}"), CTX, 3, rng=random.Random(1))
    assert len(names) == 3 and len(set(names)) == 3


# ── what an admin may type as a template ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("template", ["{bot}_{n5}", "VIP-{user}-{n4}", "{bot}{id}{r3}", "shop_{seq}", "{n12}"])
def test_good_templates(template: str) -> None:
    assert naming.template_error(template) is None


@pytest.mark.parametrize(
    "template",
    ["", "   ", "{foo}_{n5}", "{n13}", "{n0}", "{bot}_{", "bot}_{n5}", "اسم_{n5}", "a" * 61, "{}", "{bot} {n5}!"],
)
def test_bad_templates_are_explained(template: str) -> None:
    assert naming.template_error(template)


# ── names customers type ──────────────────────────────────────────────────────────────────────────
def test_forbidden_words_are_read_in_any_separator() -> None:
    assert naming.blocked_words("Admin, root;Support  test،TEST\nroot") == ["admin", "root", "support", "test"]
    assert naming.blocked_words(None) == [] and naming.blocked_words(" ") == []


def test_a_typed_name_with_a_forbidden_word_is_refused() -> None:
    cfg = NamingConfig(blocked="admin, root")
    assert naming.check_typed("SuperAdmin1", cfg, CTX) == (
        None,
        "❌ نام کانفیگ نباید شامل «admin» باشد. نام دیگری بفرستید.",
    )
    assert naming.check_typed("Neda_123", cfg, CTX) == ("Neda_123", None)


def test_the_bot_prefix_goes_in_front_of_typed_names_once() -> None:
    cfg = NamingConfig(prefix_typed=True)
    assert naming.check_typed("Neda", cfg, CTX) == ("MdjeBot_Neda", None)
    assert naming.check_typed("mdjebot_Neda", cfg, CTX) == ("mdjebot_Neda", None)  # already there, in any case
    assert naming.check_typed("Neda", NamingConfig(), CTX) == ("Neda", None)  # off


def test_a_typed_name_too_long_for_the_prefix_says_how_long_it_may_be() -> None:
    name, error = naming.check_typed("x" * 30, NamingConfig(prefix_typed=True), CTX)
    assert name is None and "24" in error  # 32 - len("MdjeBot_")


def test_typed_names_follow_the_letter_case() -> None:
    assert naming.check_typed("Neda_123", NamingConfig(case="lower"), CTX) == ("neda_123", None)
    assert naming.check_typed("Neda", NamingConfig(case="upper", prefix_typed=True), CTX) == ("MDJEBOT_NEDA", None)


# ── the options and where they come from ──────────────────────────────────────────────────────────
def test_the_settings_row_is_read_with_safe_defaults() -> None:
    assert naming.config_from(None) == NamingConfig()
    row = SimpleNamespace(
        config_name_template="{bot}_{n5}",
        config_name_bot="Shop",
        config_name_case="weird",
        config_name_prefix_typed=True,
        config_name_blocked="a",
        config_name_auto=True,
    )
    assert naming.config_from(row) == NamingConfig("{bot}_{n5}", "Shop", "keep", True, "a", True)


async def test_the_bot_name_falls_back_from_the_setting_to_the_tag_to_a_default() -> None:
    assert (await naming.make_context(NamingConfig(bot="My Shop!"), fallback_bot="Tag")).bot == "My_Shop"
    assert (await naming.make_context(NamingConfig(), fallback_bot="@my-tag")).bot == "my_tag"
    assert (await naming.make_context(NamingConfig(), fallback_bot="Tag")).bot == "Tag"
    assert (await naming.make_context(NamingConfig())).bot == naming.FALLBACK_BOT


async def test_the_purchase_number_counts_the_customers_services_only_when_the_template_needs_it(monkeypatch) -> None:
    asked: list[int] = []

    class Services:
        async def get_services_reverse(self, user_id):
            asked.append(user_id)
            return [object(), object()]

    import app.db.crud.services as services_module

    monkeypatch.setattr(services_module, "ServiceCRUD", Services)
    assert (await naming.make_context(NamingConfig(template="{bot}_{seq}"), user_id=5)).seq == 3
    assert (await naming.make_context(NamingConfig(template="{bot}_{n5}"), user_id=5)).seq == 1
    assert asked == [5]
