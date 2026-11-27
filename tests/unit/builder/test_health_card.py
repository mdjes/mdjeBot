"""The health score, the Persian lettering and the pictures drawn from them."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from builder.services import health, health_card, rtl
from builder.services.health import BotHealth, BotStats


def _bot(**kw) -> BotHealth:
    stats = kw.pop("stats", BotStats())
    values = {"tenant_id": 1, "label": "@shop_bot", "status": "running", "age_days": 40}
    values.update(kw)
    return health.score(BotHealth(stats=stats, **values))


HEALTHY = BotStats(users=320, new_users=14, services=9, topups=2_400_000)


# ── the score ─────────────────────────────────────────────────────────────────────────────────────
def test_a_busy_quiet_bot_scores_full_marks_with_no_problems() -> None:
    h = _bot(stats=HEALTHY, errors=1, memory=48.0, cpu=6.0, days_left=12)
    assert (h.score, h.level, h.tags) == (100, "good", [])
    assert h.parts == {"users": 30, "sales": 30, "errors": 20, "resources": 20}


def test_a_bot_with_users_but_no_sales_and_no_growth_is_idle() -> None:
    h = _bot(stats=BotStats(users=40, new_users=0, services=0, topups=0), errors=0, memory=30.0, cpu=1.0)
    assert h.tags == ["stagnant"] and h.level == "warn" and health.needs_attention(h)


def test_a_bot_nobody_ever_joined_is_flagged_differently() -> None:
    assert _bot(stats=BotStats(users=0, new_users=0, services=0, topups=0)).tags == ["no_users"]


def test_a_top_up_without_a_purchase_still_counts_as_activity() -> None:
    h = _bot(stats=BotStats(users=40, new_users=0, services=0, topups=300_000), errors=0, memory=30.0, cpu=1.0)
    assert "stagnant" not in h.tags and h.parts["sales"] == 12


def test_errors_and_resource_pressure_pull_the_score_down_and_are_named() -> None:
    h = _bot(stats=BotStats(users=15, new_users=1, services=1, topups=0), errors=80, memory=97.0, cpu=93.0)
    assert h.level == "bad" and h.score < health.WARN
    assert set(h.tags) == {"errors", "memory", "cpu"}
    assert h.parts["errors"] == 0


def test_a_bot_that_is_down_is_capped_low_whatever_else_it_did() -> None:
    h = _bot(stats=HEALTHY, container="exited", errors=0, memory=10.0, cpu=1.0)
    assert h.score <= 15 and h.level == "bad" and h.tags[0] == "down"


def test_a_new_bot_is_not_judged_on_users_and_sales_yet() -> None:
    h = _bot(stats=BotStats(users=0, new_users=0, services=0, topups=0), age_days=1, errors=0, memory=20.0, cpu=1.0)
    assert h.tags == [] and h.level in ("good", "warn") and h.parts["users"] >= 15 and h.parts["sales"] >= 15


def test_whatever_could_not_be_measured_is_neither_rewarded_nor_punished() -> None:
    h = _bot(stats=BotStats(), errors=None, memory=None, cpu=None)
    assert h.parts == {"users": 15, "sales": 15, "errors": 10, "resources": 10} and h.tags == []


def test_a_subscription_about_to_end_is_flagged() -> None:
    assert "expiring" in _bot(stats=HEALTHY, errors=0, memory=10.0, cpu=1.0, days_left=2).tags


def test_the_owner_is_told_what_is_wrong_and_what_to_do() -> None:
    h = _bot(stats=BotStats(users=40, new_users=0, services=0, topups=0), errors=0, memory=30.0, cpu=1.0)
    text = health.owner_warning(h)
    assert "@shop_bot" in text and "راکد" in text and "تعرفه" in text
    assert "امتیاز" in health.owner_warning(_bot(stats=HEALTHY, errors=0, memory=10.0, cpu=1.0))


# ── Persian lettering ─────────────────────────────────────────────────────────────────────────────
def test_letters_take_their_joined_forms() -> None:
    # س initial, then لا as one ligature (joined from the right), then م on its own after an alef-ending ligature
    assert rtl.shape("سلام") == "".join(chr(c) for c in (0xFEB3, 0xFEFC, 0xFEE1))
    assert rtl.shape("ربات") == "".join(
        chr(c) for c in (0xFEAD, 0xFE91, 0xFE8E, 0xFE95)
    )  # ر isolated, ب ا joined, ت alone
    assert rtl.shape("بب") == "".join(chr(c) for c in (0xFE91, 0xFE90))  # initial + final
    assert rtl.shape("ببب") == "".join(chr(c) for c in (0xFE91, 0xFE92, 0xFE90))  # initial + medial + final


def test_a_right_joining_letter_does_not_connect_to_the_next() -> None:
    assert rtl.shape("در") == "".join(chr(c) for c in (0xFEA9, 0xFEAD))  # both isolated: د never joins forward


def test_words_run_right_to_left_but_numbers_stay_readable() -> None:
    shown = rtl.visual("سلام 12 bot")
    assert shown.startswith("bot 12 ") and shown.endswith(rtl.shape("سلام")[::-1])
    assert rtl.visual("۱۲") == "12"  # Persian digits become the ones every font has
    assert rtl.has_persian("ربات") and not rtl.has_persian("bot 12")


def test_a_zero_width_non_joiner_breaks_the_join_and_vanishes() -> None:
    assert rtl.shape("می‌خواهم")[:2] == rtl.shape("می")


# ── the pictures ──────────────────────────────────────────────────────────────────────────────────
def _png(data: bytes) -> Image.Image:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return Image.open(io.BytesIO(data))


def test_the_card_is_a_png_with_the_level_colour_in_it() -> None:
    sick = _bot(stats=BotStats(users=15, new_users=1, services=1, topups=0), errors=80, memory=97.0, cpu=93.0)
    image = _png(health_card.render_card(sick)).convert("RGB")
    assert image.size == (health_card.WIDTH, health_card.HEIGHT)
    red = health_card.LEVEL_COLOR["bad"].lstrip("#")
    target = tuple(int(red[i : i + 2], 16) for i in (0, 2, 4))
    assert target in {pixel for _, pixel in image.getcolors(maxcolors=1_000_000)}


def test_the_good_card_looks_different_from_the_bad_one() -> None:
    good = health_card.render_card(_bot(stats=HEALTHY, errors=0, memory=10.0, cpu=1.0))
    bad = health_card.render_card(_bot(stats=HEALTHY, container="exited"))
    assert good != bad


def test_the_overview_grows_with_the_bots_and_copes_with_none() -> None:
    empty = _png(health_card.render_overview([]))
    many = _png(health_card.render_overview([_bot(tenant_id=i, label=f"@b{i}", stats=HEALTHY) for i in range(30)]))
    few = _png(health_card.render_overview([_bot(stats=HEALTHY)]))
    assert empty.width == few.width == many.width == health_card.WIDTH
    assert many.height > few.height >= empty.height  # at most twelve rows are drawn, the rest are in the buttons


def test_without_a_persian_font_the_card_is_still_drawn_in_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_card, "persian_available", lambda: False)
    assert _png(health_card.render_card(_bot(stats=HEALTHY, errors=0, memory=10.0, cpu=1.0))).size == (1000, 520)
    assert health_card.FA.keys() == health_card.EN.keys()  # every label exists in both languages
