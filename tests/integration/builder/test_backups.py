"""Nightly backups to the owner's chat, and one-tap restore (with the way back when it goes wrong)."""

from __future__ import annotations

import glob
import json
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from builder.models import Tenant, TenantStatus, utcnow
from builder.services import backup
from builder.services.backup import BackupService
from builder.services.cfg import set_cfg
from tests.integration.builder.panel_helpers import cb, get_tenant, make_tenant
from tests.support.builder_env import FakeCtx, FakeSqlTool


async def _bot(env, *, owner_id: int = 5, bot_id: int = 111, status: str = TenantStatus.RUNNING, database: bool = True):
    tid = await make_tenant(env, owner_id=owner_id, bot_id=bot_id, status=status)
    if database:
        async with env.db.session() as s:
            tenant = await s.get(Tenant, tid)
            tenant.db_password_enc = env.box.encrypt("secretpass1")
            await s.commit()
    return tid


@pytest.fixture
def tool() -> FakeSqlTool:
    return FakeSqlTool()


@pytest.fixture
def service(env, tool) -> BackupService:
    return BackupService(env.db, env.settings, env.box, env.prov, env.notifier, tool=tool, busy=env.ops.busy, pause=0)


def _leftovers() -> set[str]:
    return set(glob.glob(str(Path(tempfile.gettempdir()) / "mdjebot-*")))


# ── the nightly run ───────────────────────────────────────────────────────────────────────────────
async def test_every_bot_with_a_database_is_backed_up_to_its_owner_and_nothing_stays_on_disk(
    env, service, tool, tmp_path
):
    a = await _bot(env, owner_id=5, bot_id=111)
    b = await _bot(env, owner_id=6, bot_id=222, status=TenantStatus.STOPPED)
    await _bot(env, owner_id=7, bot_id=333, status=TenantStatus.PENDING, database=False)
    gone = await _bot(env, owner_id=8, bot_id=444)
    async with env.db.session() as s:
        (await s.get(Tenant, gone)).status = TenantStatus.DELETED
        await s.commit()
    tool.state[f"mdjebot_t{a}"] = "-- rows of bot a"
    before = _leftovers()

    outcome = await service.run_all()

    assert outcome == {"sent": [a, b], "failed": [], "skipped": []}
    assert [chat for chat, *_ in env.notifier.files] == [5, 6]
    _chat, name, caption, buttons = env.notifier.files[0]
    assert name.startswith(f"mdjebot-bot{a}-") and name.endswith(".zip")
    assert "پشتیبان ربات" in caption and "@bot111" in caption
    assert buttons[0][0].data == f"u:rest:{a}"
    assert _leftovers() == before  # the dump and the zip were deleted right after sending
    assert (await get_tenant(env, a)).last_backup_at is not None

    archive = tmp_path / "sent.zip"
    archive.write_bytes(env.notifier.file_bytes[0])
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist()) == {"database.sql", "meta.json"}
        assert z.read("database.sql").decode() == "-- rows of bot a"
        meta = json.loads(z.read("meta.json"))
    assert meta["tenant_id"] == a and meta["db_name"] == f"mdjebot_t{a}" and meta["format"] == backup.FORMAT


async def test_one_failing_bot_does_not_stop_the_others_and_admins_are_told(env, service, tool):
    a = await _bot(env, owner_id=5, bot_id=111)
    b = await _bot(env, owner_id=6, bot_id=222)
    tool.fail_dump.add(f"mdjebot_t{a}")
    async with env.db.session() as s:
        await set_cfg(s, "backup_hour", "0")
        await s.commit()

    outcome = await backup.run_if_due(env.db, service, env.notifier, [7], now=utcnow())

    assert outcome["sent"] == [b] and outcome["failed"][0][0] == a
    (alert,) = env.notifier.to(7)
    assert "1 ارسال شد، 1 ناموفق" in alert and f"#{a}" in alert and "access denied" in alert
    assert (await get_tenant(env, a)).last_backup_at is None


async def test_an_owner_who_blocked_the_bot_is_a_failure_not_a_backup(env, service):
    a = await _bot(env)
    env.notifier.deliver = False

    result = await service.backup_and_send(a)

    assert not result.ok and "Telegram" in result.error
    assert (await get_tenant(env, a)).last_backup_at is None


async def test_the_nightly_backup_runs_once_a_day_after_its_hour_and_only_when_on(env, service):
    await _bot(env)
    morning = datetime(2026, 11, 7, 0, 30)  # 04:00 in Tehran
    early = datetime(2026, 11, 6, 22, 0)  # 01:30 in Tehran

    assert await backup.run_if_due(env.db, service, env.notifier, [7], now=early) is None  # before the hour
    async with env.db.session() as s:
        await set_cfg(s, "backup_enabled", "0")
        await s.commit()
    assert await backup.run_if_due(env.db, service, env.notifier, [7], now=morning) is None  # switched off
    async with env.db.session() as s:
        await set_cfg(s, "backup_enabled", "1")
        await s.commit()

    first = await backup.run_if_due(env.db, service, env.notifier, [7], now=morning)
    again = await backup.run_if_due(env.db, service, env.notifier, [7], now=morning + timedelta(minutes=20))
    next_day = await backup.run_if_due(env.db, service, env.notifier, [7], now=morning + timedelta(days=1))

    assert len(first["sent"]) == 1 and again is None and len(next_day["sent"]) == 1
    assert len(env.notifier.files) == 2 and env.notifier.sent == []  # no admin message when all went well


# ── restore ───────────────────────────────────────────────────────────────────────────────────────
async def _archive_of(env, service, tool, tid: int, content: str, where: Path) -> Path:
    tool.state[f"mdjebot_t{tid}"] = content
    async with env.db.session() as s:
        tenant = await s.get(Tenant, tid)
    return await service.make_archive(tenant, where)


async def test_a_restore_puts_the_old_data_back_with_the_bots_own_database_user(env, service, tool, tmp_path):
    tid = await _bot(env)
    archive = await _archive_of(env, service, tool, tid, "-- version one", tmp_path)
    tool.state[f"mdjebot_t{tid}"] = "-- version two"
    seen_busy: list[bool] = []
    real_load = tool.load

    async def watching_load(*args, **kwargs):
        seen_busy.append(tid in env.ops.busy)
        await real_load(*args, **kwargs)

    tool.load = watching_load
    env.runner.calls.clear()

    result = await service.restore(tid, archive)

    assert result.ok and tool.state[f"mdjebot_t{tid}"] == "-- version one"
    database, user, _ = tool.loads[-1]
    assert (database, user) == (f"mdjebot_t{tid}", f"t{tid}")  # not root: a dump can only touch this database
    assert seen_busy == [True] and env.ops.busy == set()  # the watchdog is told to leave the bot alone meanwhile
    assert env.runner.verbs() == ["stop", "start"]
    assert (f"mdjebot_t{tid}", f"t{tid}") in env.db_admin.dropped and env.db_admin.created[-1][0] == f"mdjebot_t{tid}"
    assert (await get_tenant(env, tid)).status == TenantStatus.RUNNING


async def test_a_backup_of_another_bot_or_a_stray_file_is_refused(env, service, tool, tmp_path):
    a = await _bot(env, bot_id=111)
    b = await _bot(env, bot_id=222)
    other = await _archive_of(env, service, tool, b, "-- belongs to b", tmp_path)
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("hello.txt", "hi")

    for path, why in ((other, "another bot"), (junk, "valid"), (empty, "not a backup")):
        result = await service.restore(a, path)
        assert not result.ok and why in result.error
    assert tool.loads == [] and env.runner.verbs().count("stop") == 0


async def test_a_failed_restore_puts_the_current_data_back_and_restarts_the_bot(env, service, tool, tmp_path):
    tid = await _bot(env)
    archive = await _archive_of(env, service, tool, tid, "-- broken dump", tmp_path)
    tool.state[f"mdjebot_t{tid}"] = "-- live data"
    tool.fail_load.add("-- broken dump")
    env.runner.calls.clear()

    result = await service.restore(tid, archive)

    assert not result.ok and result.rolled_back and "syntax error" in result.error
    assert tool.state[f"mdjebot_t{tid}"] == "-- live data"
    assert env.runner.verbs() == ["stop", "start"] and env.ops.busy == set()


async def test_when_going_back_fails_too_the_error_says_so_and_the_bot_is_still_started(env, service, tool, tmp_path):
    tid = await _bot(env)
    archive = await _archive_of(env, service, tool, tid, "-- broken dump", tmp_path)
    tool.state[f"mdjebot_t{tid}"] = "-- live data"
    tool.fail_load.update({"-- broken dump", "-- live data"})
    env.runner.calls.clear()

    result = await service.restore(tid, archive)

    assert not result.ok and not result.rolled_back and "going back also failed" in result.error
    assert "start" in env.runner.verbs()


# ── the buttons ───────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def flows(app, service):
    app.backups = service
    return app


async def test_the_owner_can_back_up_now_but_not_every_minute_and_strangers_cannot(env, flows):
    tid = await _bot(env, owner_id=5)

    ctx = await cb(flows, f"u:bkp:{tid}", user=5)
    assert len(env.notifier.files) == 1 and any("ارسال شد" in a for a in ctx.alerts)

    ctx = await cb(flows, f"u:bkp:{tid}", user=5)
    assert len(env.notifier.files) == 1 and any("چند دقیقه" in a for a in ctx.alerts)

    ctx = await cb(flows, f"u:bkp:{tid}", user=99)
    assert len(env.notifier.files) == 1 and ctx.alerts


async def test_the_bot_screen_offers_the_backup_and_shows_when_the_last_one_was(env, flows):
    tid = await _bot(env, owner_id=5)
    ctx = await cb(flows, f"u:bot:{tid}", user=5)
    assert f"u:bkp:{tid}" in ctx.button_data() and "هنوز گرفته نشده" in ctx.last

    await flows.backups.backup_and_send(tid)
    ctx = await cb(flows, f"u:bot:{tid}", user=5)
    assert "هنوز گرفته نشده" not in ctx.last


async def test_restoring_asks_first_then_restores_the_file_under_the_button(env, flows, service, tool, tmp_path):
    tid = await _bot(env, owner_id=5)
    archive = await _archive_of(env, service, tool, tid, "-- version one", tmp_path)
    tool.state[f"mdjebot_t{tid}"] = "-- version two"

    ask = await cb(flows, f"u:rest:{tid}", user=5)
    assert "جایگزین می‌شود" in ask.last
    assert ask.button_data() == [f"u:restok:{tid}", f"u:restno:{tid}"]
    assert tool.loads == []  # nothing happens before the confirmation

    no = await cb(flows, f"u:restno:{tid}", user=5)
    assert no.button_data() == [f"u:rest:{tid}"]

    ctx = FakeCtx(5, data=f"u:restok:{tid}")
    ctx.attachment = archive.read_bytes()
    await flows.on_callback(ctx)

    assert tool.state[f"mdjebot_t{tid}"] == "-- version one"
    assert "به همین نسخه برگشت" in ctx.last
    assert any("بازیابی کرد" in text for text in env.notifier.to(100))  # the builder admins are told


async def test_a_restore_without_a_file_or_by_a_stranger_does_nothing(env, flows, tool):
    tid = await _bot(env, owner_id=5)

    ctx = await cb(flows, f"u:restok:{tid}", user=5)  # no file on that message
    assert ctx.alerts and tool.loads == []

    stranger = FakeCtx(99, data=f"u:restok:{tid}")
    stranger.attachment = b"x"
    await flows.on_callback(stranger)
    assert stranger.alerts and tool.loads == []


async def test_a_run_cut_short_carries_on_without_sending_anyone_a_second_copy(env, service, tool):
    done = await _bot(env, owner_id=5, bot_id=111)
    waiting = await _bot(env, owner_id=6, bot_id=222)
    morning = datetime(2026, 11, 7, 0, 30)
    await service.backup_and_send(done, now=morning)  # sent before the restart; the day was never marked
    env.notifier.files.clear()

    outcome = await backup.run_if_due(env.db, service, env.notifier, [7], now=morning + timedelta(minutes=20))

    assert outcome["sent"] == [waiting] and outcome["skipped"] == [done]
    assert [chat for chat, *_ in env.notifier.files] == [6]
