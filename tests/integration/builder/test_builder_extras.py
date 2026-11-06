"""Moving a panel to another bot, buying again, auto-renew, the watchdog and the daily report."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from builder.models import BuilderUser, PanelSub, Tenant, TenantStatus, utcnow
from builder.services import billing, report
from builder.services.cfg import set_cfg
from builder.services.lifecycle import run_expiry_pass
from builder.services.panels.sync import run_panel_sync
from builder.services.provisioner import CmdResult
from builder.services.watchdog import Watchdog
from tests.integration.builder.panel_helpers import (
    cb,
    exec_payloads,
    get_sub,
    get_tenant,
    make_master,
    make_plan,
    make_sub,
    make_tenant,
)
from tests.support.builder_env import make_plan as make_bot_plan


async def _balance(env, user_id: int, amount: int) -> None:
    async with env.db.session() as s:
        s.add(BuilderUser(id=user_id, balance=amount))
        await s.commit()


async def _user_balance(env, user_id: int) -> int:
    async with env.db.session() as s:
        return (await s.get(BuilderUser, user_id)).balance


async def _active_sub(env, **tenant_kwargs):
    master = await make_master(env)
    plan = await make_plan(env, master)
    tenant = await make_tenant(env, **tenant_kwargs)
    sub = await make_sub(env, plan, tenant_id=tenant)
    assert (await env.panels.provision(sub)).ok
    return plan, tenant, sub


# ── moving a panel to another bot ─────────────────────────────────────────────────────────────────
async def test_a_panel_moves_to_another_bot_of_its_owner(env):
    _, _, sub_id = await _active_sub(env)
    second = await make_tenant(env, bot_id=222)
    stranger = await make_tenant(env, owner_id=99, bot_id=333)
    env.runner.stdin.clear()

    assert (await env.panels.move(sub_id, second)).ok
    assert [p["action"] for p in exec_payloads(env)] == ["unlink", "link"]
    sub = await get_sub(env, sub_id)
    assert (sub.tenant_id, sub.linked) == (second, True)

    result = await env.panels.move(sub_id, stranger)
    assert not result.ok and (await get_sub(env, sub_id)).tenant_id == second


async def test_moving_to_a_bot_that_is_not_running_connects_it_when_it_starts(env):
    _, _, sub_id = await _active_sub(env)
    waiting = await make_tenant(env, bot_id=222, status=TenantStatus.EXPIRED)

    result = await env.panels.move(sub_id, waiting)

    assert not result.ok and "not running" in result.error
    sub = await get_sub(env, sub_id)
    assert (sub.tenant_id, sub.linked) == (waiting, False)


# ── buying again ──────────────────────────────────────────────────────────────────────────────────
async def test_buy_again_goes_to_the_summary_and_creates_a_second_panel(app, env):
    plan, tenant, sub_id = await _active_sub(env)
    await _balance(env, 5, 500_000)

    ctx = await cb(app, f"u:prebuy:{sub_id}")
    assert "خلاصهٔ خرید پنل" in ctx.last
    state = app.get_state(5)
    assert (state.data["plan_id"], state.data["tenant_id"]) == (plan, tenant)

    await cb(app, "u:ppay_wallet")
    async with env.db.session() as s:
        subs = (await s.execute(select(PanelSub).where(PanelSub.owner_id == 5))).scalars().all()
    assert len(subs) == 2 and len({sub.admin_username for sub in subs}) == 2
    assert await _user_balance(env, 5) == 300_000


# ── auto-renew ────────────────────────────────────────────────────────────────────────────────────
async def _bot_expiring(env, *, balance: int, auto: bool = True) -> int:
    async with env.db.session() as s:
        plan = make_bot_plan(price=100_000)
        s.add(plan)
        await s.flush()
        tenant = await billing.create_tenant(s, owner_id=5, plan_id=plan.id)
        billing.attach_token(tenant, env.box, token="1:" + "T" * 35, bot_id=1, bot_username="shop_bot", admin_ids=[5])
        tenant.status, tenant.auto_renew = TenantStatus.RUNNING, auto
        tenant.expires_at = utcnow() + timedelta(days=1)
        await s.commit()
        tenant_id = tenant.id
    await _balance(env, 5, balance)
    return tenant_id


async def test_a_bot_with_auto_renew_is_renewed_from_the_wallet(env):
    tid = await _bot_expiring(env, balance=150_000)
    before = (await get_tenant(env, tid)).expires_at

    counts = await run_expiry_pass(env.db, env.ops, env.notifier)

    assert counts["auto_renewed"] == 1
    assert (await get_tenant(env, tid)).expires_at - before == timedelta(days=30)
    assert await _user_balance(env, 5) == 50_000
    assert "تمدید خودکار" in env.notifier.to(5)[0]


async def test_without_enough_balance_the_reminder_says_why_and_nothing_is_charged(env):
    tid = await _bot_expiring(env, balance=10_000)

    counts = await run_expiry_pass(env.db, env.ops, env.notifier)

    assert "auto_renewed" not in counts and counts["reminded"] == 1
    assert "موجودی کیف پول کافی نیست" in env.notifier.to(5)[0]
    assert await _user_balance(env, 5) == 10_000 and (await get_tenant(env, tid)).status == TenantStatus.RUNNING


async def test_a_panel_with_auto_renew_is_renewed_from_the_wallet(env):
    _, _, sub_id = await _active_sub(env)
    await _balance(env, 5, 250_000)
    async with env.db.session() as s:
        sub = await s.get(PanelSub, sub_id)
        sub.auto_renew, sub.expires_at = True, utcnow() + timedelta(days=1)
        await s.commit()

    counts = await run_panel_sync(env.db, env.panels, env.notifier)

    assert counts["auto_renewed"] == 1 and await _user_balance(env, 5) == 50_000
    assert (await get_sub(env, sub_id)).expires_at - utcnow() > timedelta(days=29)


async def test_a_failed_panel_renewal_is_refunded_and_switched_off(env):
    _, _, sub_id = await _active_sub(env)
    await _balance(env, 5, 250_000)
    async with env.db.session() as s:
        sub = await s.get(PanelSub, sub_id)
        sub.auto_renew, sub.expires_at = True, utcnow() + timedelta(days=1)
        await s.commit()
    from builder.services.panels.backend import PanelError

    env.backend.fail["reset"] = PanelError("panel unreachable")

    await run_panel_sync(env.db, env.panels, env.notifier)

    assert await _user_balance(env, 5) == 250_000 and not (await get_sub(env, sub_id)).auto_renew


# ── watchdog ──────────────────────────────────────────────────────────────────────────────────────
async def test_the_watchdog_restarts_a_dead_bot_once_and_tells_the_admins(env):
    tid = await make_tenant(env)
    env.runner.responses["inspect"] = CmdResult(0, "exited\n")
    dog = Watchdog(env.db, env.ops, env.prov, env.notifier, [7])

    first = await dog.run_once()  # one miss: it may still be starting
    assert first["restarted"] == 0 and "start" not in env.runner.verbs()
    second = await dog.run_once()

    assert second["restarted"] == 1 and "start" in env.runner.verbs()
    assert len(env.notifier.to(7)) == 1 and "دوباره راه‌اندازی شد" in env.notifier.to(7)[0]
    assert (await get_tenant(env, tid)).status == TenantStatus.RUNNING


async def test_the_watchdog_alerts_once_when_starting_keeps_failing_and_ignores_stopped_bots(env):
    await make_tenant(env)
    await make_tenant(env, bot_id=222, status=TenantStatus.STOPPED)
    env.runner.responses["inspect"] = CmdResult(0, "exited\n")
    env.runner.responses["start"] = CmdResult(1, "", "no such container")
    dog = Watchdog(env.db, env.ops, env.prov, env.notifier, [7], misses_before_action=1, failures_before_alert=2)

    for _ in range(4):
        await dog.run_once()

    assert len(env.notifier.to(7)) == 1 and "🚨" in env.notifier.to(7)[0]


# ── daily report ──────────────────────────────────────────────────────────────────────────────────
async def test_the_daily_report_goes_out_once_a_day_with_the_numbers(env):
    now = datetime(2026, 11, 7, 20, 0)  # 23:30 in Tehran
    async with env.db.session() as s:
        s.add(BuilderUser(id=1, created_at=now - timedelta(hours=2)))
        s.add(BuilderUser(id=2, created_at=now - timedelta(days=3)))
        await set_cfg(s, "daily_report_enabled", "1")
        await s.commit()
    await _bot_expiring(env, balance=0, auto=False)

    assert await report.run_if_due(env.db, env.notifier, [7], now=now)
    assert not await report.run_if_due(env.db, env.notifier, [7], now=now + timedelta(minutes=30))

    (text,) = env.notifier.to(7)
    assert "کاربر جدید: <b>2</b>" in text  # user 1 plus the one _bot_expiring created today
    assert "ربات فعال: 1" in text and "تا ۳ روز دیگر" in text


async def test_the_daily_report_waits_for_its_hour_and_its_switch(env):
    async with env.db.session() as s:
        assert (await s.execute(select(func.count(Tenant.id)))).scalar_one() == 0
    morning = datetime(2026, 11, 7, 3, 0)  # 06:30 in Tehran
    assert not await report.run_if_due(env.db, env.notifier, [7], now=morning)  # switched off
    async with env.db.session() as s:
        await set_cfg(s, "daily_report_enabled", "1")
        await s.commit()
    assert not await report.run_if_due(env.db, env.notifier, [7], now=morning)  # too early
    assert env.notifier.sent == []
