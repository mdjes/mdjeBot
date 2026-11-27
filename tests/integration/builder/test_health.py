"""Collecting the health of the running bots, and the admin screens built on it."""

from __future__ import annotations

from datetime import timedelta

import pytest

from builder.models import Tenant, TenantStatus, utcnow
from builder.services.health import BotStats, HealthService
from builder.services.provisioner import CmdResult
from tests.integration.builder.panel_helpers import cb, make_tenant


class FakeReader:
    """What each bot's own database would say, by tenant id."""

    def __init__(self, **by_tenant: BotStats):
        self.by_tenant = {int(key.lstrip("t")): value for key, value in by_tenant.items()}
        self.asked: list[int] = []

    async def read(self, tenant, since: int) -> BotStats:
        self.asked.append(tenant.id)
        return self.by_tenant.get(tenant.id, BotStats())


async def _old_bot(env, **kw) -> int:
    tid = await make_tenant(env, **kw)
    async with env.db.session() as s:
        tenant = await s.get(Tenant, tid)
        tenant.db_password_enc = env.box.encrypt("secretpass1")
        tenant.created_at = utcnow() - timedelta(days=60)
        await s.commit()
    return tid


def _stats_output(*rows: tuple[int, str, str]) -> CmdResult:
    return CmdResult(0, "\n".join(f"mdjebot-t{tid}|{cpu}|{mem}" for tid, cpu, mem in rows) + "\n")


@pytest.fixture
def reader() -> FakeReader:
    return FakeReader()


@pytest.fixture
def service(env, reader) -> HealthService:
    return HealthService(env.db, env.prov, reader=reader)


async def test_the_fleet_is_listed_sickest_first_and_only_running_bots_are_in_it(env, service, reader):
    busy = await _old_bot(env, bot_id=111)
    idle = await _old_bot(env, bot_id=222, owner_id=6)
    broken = await _old_bot(env, bot_id=333, owner_id=7)
    await _old_bot(env, bot_id=444, owner_id=8, status=TenantStatus.STOPPED)
    await _old_bot(env, bot_id=555, owner_id=9, status=TenantStatus.EXPIRED)
    reader.by_tenant = {
        busy: BotStats(users=300, new_users=12, services=8, topups=1_000_000),
        idle: BotStats(users=30, new_users=0, services=0, topups=0),
        broken: BotStats(users=10, new_users=0, services=1, topups=0),
    }
    env.runner.responses["stats"] = _stats_output(
        (busy, "4.00%", "40.00%"), (idle, "1.00%", "20.00%"), (broken, "95.00%", "99.00%")
    )
    env.runner.responses["logs"] = CmdResult(0, "", "2026 | ERROR | x\n" * 60)  # every bot logs the same noise

    items = await service.collect_all()

    scores = [h.score for h in items]
    assert scores == sorted(scores)  # the sickest first
    assert {h.tenant_id for h in items} == {busy, idle, broken}  # stopped and expired bots are not part of it
    worst, *_ = items
    assert worst.tenant_id == broken and "memory" in worst.tags and "cpu" in worst.tags and "errors" in worst.tags
    by_id = {h.tenant_id: h for h in items}
    assert by_id[idle].tags[0] == "stagnant" and by_id[busy].memory == 40.0 and by_id[busy].errors == 60


async def test_a_container_that_is_not_running_is_reported_down_without_asking_its_database(env, service, reader):
    tid = await _old_bot(env)
    env.runner.responses["inspect"] = CmdResult(0, "exited\n")

    h = await service.collect_one(tid)

    assert h.container == "exited" and h.tags[0] == "down" and h.score <= 15
    assert reader.asked == [] and h.errors is None


async def test_a_bot_whose_database_cannot_be_read_is_not_punished_for_it(env, service):
    tid = await _old_bot(env)
    env.runner.responses["stats"] = _stats_output((tid, "2.00%", "30.00%"))

    h = await service.collect_one(tid)

    assert h.stats.users is None and h.tags == [] and h.parts["users"] == 15


async def test_unknown_or_deleted_bots_have_no_card(env, service):
    tid = await _old_bot(env)
    async with env.db.session() as s:
        (await s.get(Tenant, tid)).status = TenantStatus.DELETED
        await s.commit()
    assert await service.collect_one(tid) is None and await service.collect_one(9999) is None


# ── the admin screens ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def flows(app, service):
    app.health = service
    return app


async def test_the_admin_sees_the_fleet_as_a_picture_with_buttons_for_the_bots_that_need_a_look(env, flows, reader):
    idle = await _old_bot(env, bot_id=222)
    await _old_bot(env, bot_id=111, owner_id=6)
    reader.by_tenant = {idle: BotStats(users=30, new_users=0, services=0, topups=0)}

    ctx = await cb(flows, "a:health", user=100)

    ((png, caption, buttons),) = ctx.photos
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and "سلامت ربات‌ها" in caption
    assert f"a:hbot:{idle}" in [b.data for row in buttons for b in row]


async def test_one_bot_card_comes_with_warn_restart_and_manage_buttons(env, flows, reader):
    tid = await _old_bot(env)
    reader.by_tenant = {tid: BotStats(users=30, new_users=0, services=0, topups=0)}

    ctx = await cb(flows, f"a:hbot:{tid}", user=100)

    ((png, caption, buttons),) = ctx.photos
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and "راکد" in caption and "امتیاز" in caption
    data = [b.data for row in buttons for b in row]
    assert f"a:hwarn:{tid}" in data and f"a:trestart:{tid}" in data and f"a:tenant:{tid}" in data


async def test_warning_the_owner_sends_them_what_is_wrong(env, flows, reader):
    tid = await _old_bot(env, owner_id=5)
    reader.by_tenant = {tid: BotStats(users=30, new_users=0, services=0, topups=0)}

    ctx = await cb(flows, f"a:hwarn:{tid}", user=100)

    (warning,) = env.notifier.to(5)
    assert "راکد" in warning and "امتیاز" in warning and any("ارسال شد" in a for a in ctx.alerts)


async def test_only_builder_admins_can_open_the_health_screens(env, flows):
    tid = await _old_bot(env)
    for data in ("a:health", f"a:hbot:{tid}", f"a:hwarn:{tid}"):
        ctx = await cb(flows, data, user=5)
        assert ctx.photos == [] and ctx.alerts
    assert env.notifier.sent == []


async def test_the_admin_home_and_tenant_screen_lead_to_the_health_cards(env, flows):
    tid = await _old_bot(env)
    home = await cb(flows, "a:home", user=100)
    assert "a:health" in home.button_data()
    screen = await cb(flows, f"a:tenant:{tid}", user=100)
    assert f"a:hbot:{tid}" in screen.button_data()
