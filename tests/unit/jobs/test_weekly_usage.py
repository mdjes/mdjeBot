"""The Friday job: when it is due, who gets the message, and that nobody gets it twice."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.jobs import registry
from app.jobs.services import weekly_usage as job
from app.services.usage_report import GB

IRAN = job.IRAN_TZ


def _at(year, month, day, hour, minute=0) -> datetime:
    return IRAN.localize(datetime(year, month, day, hour, minute))


FRIDAY_EVENING = _at(2026, 11, 27, 19)
FRIDAY_NOON = _at(2026, 11, 27, 12)
SATURDAY = _at(2026, 11, 28, 9)


# ── when ──────────────────────────────────────────────────────────────────────────────────────────
def test_the_week_runs_saturday_to_friday() -> None:
    assert job.week_key(_at(2026, 11, 21, 8)) == "2026-11-21"  # Saturday
    assert job.week_key(FRIDAY_EVENING) == "2026-11-21"
    assert job.week_key(SATURDAY) == "2026-11-28"


def test_it_is_due_on_friday_evening_once_per_week() -> None:
    assert job.is_due(FRIDAY_EVENING, "")
    assert not job.is_due(FRIDAY_NOON, "")  # too early in the day
    assert not job.is_due(_at(2026, 11, 26, 20), "")  # a Thursday
    assert not job.is_due(FRIDAY_EVENING, "2026-11-21")  # this week is already sent
    assert job.is_due(FRIDAY_EVENING + timedelta(days=7), "2026-11-21")  # next week is not


def test_the_job_is_registered_with_the_scheduler() -> None:
    names = [getattr(func, "__name__", "") for func, *_ in _job_defs()]
    assert "send_weekly_usage_reports" in names


def _job_defs():
    import inspect

    source = inspect.getsource(registry.register_all_jobs)
    assert "send_weekly_usage_reports" in source
    return [(job.send_weekly_usage_reports,)]


# ── who ───────────────────────────────────────────────────────────────────────────────────────────
class FakeRedis:
    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}

    async def sismember(self, key, value):
        return value in self.sets.get(key, set())

    async def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    async def expire(self, key, seconds):
        return True


class World:
    """Two panel users, one customer with a heavy service and one who used nothing."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, mode: bool = True, last_week: str = "") -> None:
        self.sent: list[tuple[int, str, list]] = []
        self.saved: dict = {}
        self.redis = FakeRedis()
        self.settings = SimpleNamespace(id=1, weekly_usage_report_mode=mode, weekly_usage_report_last_week=last_week)
        panel = SimpleNamespace(code=1, name="p", base_url="http://panel", cookie="tok")
        services = [
            SimpleNamespace(
                code=1, username="heavy", id=501, panel_userid=11, in_panel=1, is_test=False, expiration_time=0
            ),
            SimpleNamespace(
                code=2, username="idle", id=502, panel_userid=12, in_panel=1, is_test=False, expiration_time=0
            ),
            SimpleNamespace(
                code=3, username="trial", id=503, panel_userid=13, in_panel=1, is_test=True, expiration_time=0
            ),
            SimpleNamespace(
                code=4, username="off", id=504, panel_userid=14, in_panel=1, is_test=False, expiration_time=0
            ),
        ]
        users = {
            11: SimpleNamespace(status="active", data_limit=100 * GB, used_traffic=40 * GB),
            12: SimpleNamespace(status="active", data_limit=100 * GB, used_traffic=1 * GB),
            14: SimpleNamespace(status="disabled", data_limit=100 * GB, used_traffic=5 * GB),
        }
        usage = {11: 3 * GB, 12: 0}

        class Api:
            def __init__(self, base_url):
                pass

            async def get_user_by_id(self, *, user_id, token):
                return users[user_id]

        async def panels(self):
            return [panel]

        async def batch(self, panel_codes, limit=500, offset=0, *, after_code=None):
            return [s for s in services if after_code is None or s.code > after_code][:limit]

        async def daily(panel_, panel_userid, *, days):
            today = FRIDAY_EVENING.date()
            return [(today - timedelta(days=i), usage[panel_userid]) for i in range(7)]

        async def get_settings(self_):
            return self.settings

        async def update_setting(self_, setting_id, **kwargs):
            self.saved.update(kwargs)

        async def plans(self_):
            return [SimpleNamespace(storage=150, price=300_000, duration=30, panel_code=1, plan_type="volume")]

        async def get_redis():
            return self.redis

        async def send_message(user_id, text, **kwargs):
            self.sent.append((user_id, text, kwargs.get("buttons")))

        async def no_sleep(seconds):
            return None

        monkeypatch.setattr(job, "PasarguardAPI", Api)
        monkeypatch.setattr(job.PanelsManager, "get_all_panels", panels)
        monkeypatch.setattr(job.ServiceCRUD, "get_all_services_by_panels_batch", batch)
        monkeypatch.setattr(job, "fetch_daily_usage", daily)
        monkeypatch.setattr(job.SettingsManager, "get_settings", get_settings)
        monkeypatch.setattr(job.SettingsManager, "update_setting", update_setting)
        monkeypatch.setattr(job.PlanManager, "get_all_plans", plans)
        monkeypatch.setattr(job, "get_redis", get_redis)
        monkeypatch.setattr(job.Mdje, "send_message", send_message)
        monkeypatch.setattr(job.asyncio, "sleep", no_sleep)


async def test_the_heavy_customer_gets_the_report_with_a_plan_and_the_idle_one_gets_nothing(monkeypatch) -> None:
    world = World(monkeypatch)

    counts = await job.send_weekly_usage_reports(now=FRIDAY_EVENING)

    assert counts == {"sent": 1, "skipped": 1, "failed": 0}  # trial and disabled services are not even asked about
    ((user_id, text, buttons),) = world.sent
    assert user_id == 501 and "21.0 گیگابایت" in text and "heavy" in text
    assert "پلن پیشنهادی" in text and "150 گیگ" in text
    assert [b.type.data for row in buttons for b in row] == [b"TamdidVPN_1", b"WeeklyReportOff"]
    assert world.saved == {"weekly_usage_report_last_week": "2026-11-21"}


async def test_it_does_nothing_when_off_not_due_or_already_sent(monkeypatch) -> None:
    off = World(monkeypatch, mode=False)
    assert await job.send_weekly_usage_reports(now=FRIDAY_EVENING) == {"sent": 0, "skipped": 0, "failed": 0}
    early = World(monkeypatch)
    assert (await job.send_weekly_usage_reports(now=FRIDAY_NOON))["sent"] == 0
    done = World(monkeypatch, last_week="2026-11-21")
    assert (await job.send_weekly_usage_reports(now=FRIDAY_EVENING))["sent"] == 0
    assert off.sent == early.sent == done.sent == []


async def test_a_run_that_is_repeated_does_not_message_the_same_customer_twice(monkeypatch) -> None:
    world = World(monkeypatch)
    await job.send_weekly_usage_reports(now=FRIDAY_EVENING)
    world.settings.weekly_usage_report_last_week = ""  # as if the first run was cut off before marking the week

    again = await job.send_weekly_usage_reports(now=FRIDAY_EVENING)

    assert len(world.sent) == 1 and again["sent"] == 0


async def test_a_customer_who_opted_out_is_skipped(monkeypatch) -> None:
    world = World(monkeypatch)
    assert await job.opt_out(501)

    counts = await job.send_weekly_usage_reports(now=FRIDAY_EVENING)

    assert world.sent == [] and counts["sent"] == 0 and await job.is_opted_out(501)
    assert not await job.is_opted_out(502)


async def test_a_forced_run_sends_now_and_does_not_use_up_the_week(monkeypatch) -> None:
    world = World(monkeypatch)

    counts = await job.send_weekly_usage_reports(now=FRIDAY_NOON, force=True)

    assert counts["sent"] == 1 and world.saved == {}


async def test_a_customer_who_blocked_the_bot_counts_as_failed(monkeypatch) -> None:
    world = World(monkeypatch)

    async def refuse(user_id, text, **kwargs):
        raise RuntimeError("chat not found")

    monkeypatch.setattr(job.Mdje, "send_message", refuse)

    counts = await job.send_weekly_usage_reports(now=FRIDAY_EVENING)

    assert counts["failed"] == 1 and counts["sent"] == 0 and world.redis.sets == {}
