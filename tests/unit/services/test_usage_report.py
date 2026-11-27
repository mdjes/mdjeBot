"""The weekly usage message: totals, the busiest day, when someone is running short, and which plan to suggest."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from app.services import usage_report as report
from app.services.usage_report import GB, ServiceWeek, WeekUsage

FRIDAY = date(2026, 11, 27)  # a Friday


def _daily(*sizes_gb: float, end: date = FRIDAY) -> list[tuple[date, int]]:
    """``sizes_gb`` oldest first, the last one on ``end``."""
    return [(end - timedelta(days=len(sizes_gb) - 1 - i), int(gb * GB)) for i, gb in enumerate(sizes_gb)]


def _week(*sizes_gb: float, limit_gb: float = 50, used_gb: float = 10, days_left: int | None = 20, **kw) -> ServiceWeek:
    return ServiceWeek(
        code=kw.pop("code", 1),
        username=kw.pop("username", "ali_1"),
        panel_code=kw.pop("panel_code", 1),
        usage=report.summarize_week(_daily(*sizes_gb), FRIDAY),
        limit=int(limit_gb * GB),
        used=int(used_gb * GB),
        days_left=days_left,
        **kw,
    )


def _plan(storage: float, price: int = 100_000, duration: int = 30, panel_code: int = 1, plan_type: str = "volume"):
    return SimpleNamespace(storage=storage, price=price, duration=duration, panel_code=panel_code, plan_type=plan_type)


# ── the week ──────────────────────────────────────────────────────────────────────────────────────
def test_the_week_adds_up_and_names_the_busiest_day() -> None:
    usage = report.summarize_week(_daily(1, 1, 5, 1, 1, 1, 2), FRIDAY)  # Sat..Fri, so Mon (the 3rd) is busiest
    assert usage.total == 12 * GB and usage.busiest_bytes == 5 * GB
    assert report.WEEKDAYS_FA[usage.busiest_day.weekday()] == "دوشنبه"


def test_a_saturday_peak_is_called_saturday() -> None:
    usage = report.summarize_week(_daily(4, 1, 1, 1, 1, 1, 1), FRIDAY)
    assert usage.busiest_day == date(2026, 11, 21) and report.WEEKDAYS_FA[usage.busiest_day.weekday()] == "شنبه"


def test_only_the_last_seven_days_count_and_a_day_is_counted_once() -> None:
    extra = [(FRIDAY - timedelta(days=9), 99 * GB), (FRIDAY, 1 * GB), (FRIDAY, 2 * GB)]
    usage = report.summarize_week(extra + _daily(0, 0, 0, 0, 0, 0, 0)[:-1], FRIDAY)
    assert usage.total == 3 * GB and usage.busiest_bytes == 3 * GB


def test_a_quiet_week_has_no_busiest_day() -> None:
    assert report.summarize_week([], FRIDAY) == WeekUsage()
    assert report.summarize_week(_daily(0, 0, 0), FRIDAY).busiest_day is None


def test_services_are_added_together_before_picking_the_busiest_day() -> None:
    a = report.summarize_week(_daily(1, 3, 0), FRIDAY)  # on its own: Thursday is the busiest day
    b = report.summarize_week(_daily(2, 1, 3), FRIDAY)  # on its own: Friday is
    merged = report.merge_usage([a, b])
    assert merged.total == 10 * GB and merged.busiest_bytes == 4 * GB  # Wed 3 GB, Thu 4 GB, Fri 3 GB together
    assert merged.busiest_day == FRIDAY - timedelta(days=1)


# ── running short ─────────────────────────────────────────────────────────────────────────────────
def test_close_to_the_cap_needs_more_volume() -> None:
    assert report.needs_more_volume(_week(1, 1, 1, 1, 1, 1, 1, limit_gb=50, used_gb=45))  # 10% left


def test_a_pace_that_runs_out_before_the_end_needs_more_volume() -> None:
    heavy = _week(3, 3, 3, 3, 3, 3, 3, limit_gb=100, used_gb=40, days_left=25)  # 3 GB/day * 25 days > 60 GB left
    assert report.needs_more_volume(heavy)


def test_a_comfortable_pace_does_not() -> None:
    assert not report.needs_more_volume(_week(1, 0, 1, 0, 1, 0, 1, limit_gb=100, used_gb=20, days_left=20))


def test_unlimited_services_never_run_short() -> None:
    assert not report.needs_more_volume(_week(50, 50, 50, limit_gb=0, used_gb=500))


def test_the_smallest_plan_that_covers_the_month_is_suggested() -> None:
    week = _week(2, 2, 2, 2, 2, 2, 2)  # 2 GB/day: about 60 GB a month, 72 with headroom
    plans = [_plan(30), _plan(100, 250_000), _plan(75, 200_000), _plan(75, 190_000), _plan(500, panel_code=2)]
    chosen = report.suggest_plan(plans, week)
    assert (chosen.storage, chosen.price) == (75, 190_000)  # the cheaper of the two 75 GB plans, not the other panel's


def test_the_biggest_plan_is_suggested_when_none_is_enough_and_none_when_there_are_none() -> None:
    week = _week(20, 20, 20, 20, 20, 20, 20)
    assert report.suggest_plan([_plan(30), _plan(100)], week).storage == 100
    assert report.suggest_plan([_plan(100, panel_code=2), _plan(10, plan_type="fair_usage")], week) is None


# ── the message ───────────────────────────────────────────────────────────────────────────────────
def test_the_message_says_how_much_and_which_day() -> None:
    text = report.render_user_report([_week(1, 1, 1, 1, 1, 1, 6)])
    assert "گزارش هفتگی" in text and "12.0 گیگابایت" in text and "جمعه" in text and "ali_1" in text
    assert "پلن پیشنهادی" not in text


def test_a_customer_running_short_is_offered_the_plan_and_the_renew_target_is_the_heaviest_user() -> None:
    light = _week(1, 0, 0, 0, 0, 0, 0, code=1, username="light", limit_gb=100, used_gb=10)
    heavy = _week(3, 3, 3, 3, 3, 3, 3, code=2, username="heavy", limit_gb=100, used_gb=40, days_left=25)
    heavy.plan = _plan(75, 200_000)

    text = report.render_user_report([light, heavy])

    assert "پلن پیشنهادی" in text and "75 گیگ" in text and "200,000" in text and "زودتر از موعد" in text
    assert report.renew_target([light, heavy]).code == 2
    assert report.renew_target([light]) is None


def test_a_long_list_is_cut_short() -> None:
    weeks = [_week(1, 1, 1, 1, 1, 1, 1, code=i, username=f"u{i}") for i in range(8)]
    assert "و 3 سرویس دیگر" in report.render_user_report(weeks)
