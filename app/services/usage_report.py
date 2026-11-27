"""The weekly usage message a customer gets: what they used this week, the busiest day, and a plan if they run short.

Everything here is pure (no network, no database) so the wording and the "are they about to run out?" rules can be
tested on their own; ``app.jobs.services.weekly_usage`` gathers the numbers and sends the messages.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.utils.formatting.traffic import format_size

GB = 1024**3
WEEK_DAYS = 7
NEAR_CAP_FRACTION = 0.15  # under this much of the volume left counts as "close to the cap"
PLAN_HEADROOM = 1.2  # a suggested plan has to cover the projected month with 20% to spare
MAX_SERVICES_LISTED = 5
CALLBACK_RENEW = "TamdidVPN_{code}"
CALLBACK_OPT_OUT = "WeeklyReportOff"

# datetime.weekday(): Monday is 0
WEEKDAYS_FA = {
    0: "دوشنبه",
    1: "سه‌شنبه",
    2: "چهارشنبه",
    3: "پنج‌شنبه",
    4: "جمعه",
    5: "شنبه",
    6: "یک‌شنبه",
}


@dataclass(frozen=True, slots=True)
class WeekUsage:
    total: int = 0
    busiest_day: date | None = None
    busiest_bytes: int = 0
    days: tuple[tuple[date, int], ...] = ()  # bytes per day, to add services together


@dataclass(slots=True)
class ServiceWeek:
    """One service's week: what it used, what it has left, and the plan worth suggesting."""

    code: int
    username: str
    panel_code: int | None
    usage: WeekUsage
    limit: int = 0  # bytes; 0 = unlimited
    used: int = 0  # bytes used in total so far
    days_left: int | None = None
    plan: Any = field(default=None)  # a suggested Plan row, when they are running short

    @property
    def remaining(self) -> int | None:
        return None if self.limit <= 0 else max(0, self.limit - self.used)


def _from_days(per_day: dict[date, int]) -> WeekUsage:
    if not per_day:
        return WeekUsage()
    day, size = max(per_day.items(), key=lambda item: (item[1], item[0]))
    return WeekUsage(
        total=sum(per_day.values()),
        busiest_day=day if size > 0 else None,
        busiest_bytes=size,
        days=tuple(sorted(per_day.items())),
    )


def summarize_week(daily: Iterable[tuple[date, int]], today: date) -> WeekUsage:
    """Total and busiest day over the seven days ending ``today`` (each day counted once)."""
    first = today - timedelta(days=WEEK_DAYS - 1)
    per_day: dict[date, int] = {}
    for day, size in daily:
        if first <= day <= today:
            per_day[day] = per_day.get(day, 0) + max(0, int(size))
    return _from_days(per_day)


def merge_usage(usages: Iterable[WeekUsage]) -> WeekUsage:
    """All of a customer's services together: the busiest day is the busiest across every service."""
    per_day: dict[date, int] = {}
    for usage in usages:
        for day, size in usage.days:
            per_day[day] = per_day.get(day, 0) + size
    return _from_days(per_day)


def needs_more_volume(week: ServiceWeek) -> bool:
    """Close to the cap now, or on course to hit it before the service ends."""
    remaining = week.remaining
    if remaining is None:
        return False
    if remaining <= week.limit * NEAR_CAP_FRACTION:
        return True
    horizon = min(week.days_left if week.days_left is not None else 30, 30)
    return week.usage.total / WEEK_DAYS * horizon > remaining


def suggest_plan(plans: Iterable[Any], week: ServiceWeek) -> Any | None:
    """The smallest volume plan of the service's panel that covers a month at this pace (the biggest if none does)."""
    projected = week.usage.total / WEEK_DAYS * 30 * PLAN_HEADROOM
    candidates = [
        plan
        for plan in plans
        if plan.panel_code == week.panel_code and (plan.plan_type or "volume") == "volume" and plan.storage > 0
    ]
    if not candidates:
        return None
    enough = [plan for plan in candidates if plan.storage * GB >= projected]
    if enough:
        return min(enough, key=lambda plan: (plan.storage, plan.price))
    return max(candidates, key=lambda plan: (plan.storage, -plan.price))


def _gb(size: float) -> str:
    return format_size(int(size), decimal_places=1)


def _plan_line(plan: Any) -> str:
    return f"📦 پلن پیشنهادی: <b>{plan.storage:g} گیگ</b> / {plan.duration} روز — <b>{int(plan.price):,}</b> تومان"


def render_user_report(weeks: list[ServiceWeek]) -> str:
    """The message text for one customer (all their services together)."""
    overall = merge_usage(w.usage for w in weeks)
    lines = ["📊 <b>گزارش هفتگی مصرف شما</b>", ""]
    summary = f"این هفته <b>{_gb(overall.total)}</b> مصرف کردید"
    if overall.busiest_day is not None:
        summary += (
            f"؛ پرمصرف‌ترین روز <b>{WEEKDAYS_FA[overall.busiest_day.weekday()]}</b> بود ({_gb(overall.busiest_bytes)})"
        )
    lines.append(summary + ".")

    for week in sorted(weeks, key=lambda w: -w.usage.total)[:MAX_SERVICES_LISTED]:
        lines += ["", f"🔹 <code>{week.username}</code> — {_gb(week.usage.total)} این هفته"]
        if week.remaining is not None:
            lines.append(f"   باقی‌مانده: {_gb(week.remaining)} از {_gb(week.limit)}")
        if week.days_left is not None:
            lines.append(f"   {week.days_left} روز تا پایان اعتبار")
        if week.plan is not None and needs_more_volume(week):
            lines += ["   ⚠️ با این روند حجم شما زودتر از موعد تمام می‌شود.", f"   {_plan_line(week.plan)}"]
    if len(weeks) > MAX_SERVICES_LISTED:
        lines += ["", f"و {len(weeks) - MAX_SERVICES_LISTED} سرویس دیگر…"]
    return "\n".join(lines)


def renew_target(weeks: list[ServiceWeek]) -> ServiceWeek | None:
    """The service the «renew / upgrade» button should open: the one closest to running out."""
    short = [w for w in weeks if needs_more_volume(w)]
    return max(short, key=lambda w: w.usage.total) if short else None
