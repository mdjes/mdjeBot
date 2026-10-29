from __future__ import annotations

from datetime import timedelta

from builder.models import Tenant, TenantStatus, utcnow
from builder.services import billing
from builder.services.cfg import set_cfg
from builder.services.lifecycle import run_expiry_pass
from builder.services.provisioner import CmdResult
from tests.support.builder_env import make_plan


async def _tenant(env, *, status=TenantStatus.PENDING, expires_in_days=None, bot_id=1) -> int:
    async with env.db.session() as s:
        plan = make_plan()
        s.add(plan)
        await s.flush()
        tenant = await billing.create_tenant(
            s,
            env.box,
            owner_id=5,
            bot_id=bot_id,
            bot_username="shop_bot",
            token="1:" + "T" * 35,
            admin_ids=[5, 6],
            plan_id=plan.id,
        )
        tenant.status = status
        if expires_in_days is not None:
            tenant.expires_at = utcnow() + timedelta(days=expires_in_days)
        await s.commit()
        return tenant.id


async def _get(env, tenant_id) -> Tenant:
    async with env.db.session() as s:
        return await s.get(Tenant, tenant_id)


async def test_deploy_assigns_port_password_and_marks_running(env):
    tid = await _tenant(env)
    result = await env.ops.deploy(tid)
    tenant = await _get(env, tid)

    assert result.ok and tenant.status == TenantStatus.RUNNING
    assert tenant.host_port == env.settings.tenant_port_start
    name, user, password = env.db_admin.created[0]
    assert (name, user) == (f"mdjebot_t{tid}", f"t{tid}")
    assert env.box.decrypt(tenant.db_password_enc) == password
    assert "BOT_TOKEN=1:" in (env.settings.tenants_dir / f"t{tid}" / ".env").read_text(encoding="utf-8")


async def test_redeploy_keeps_port_and_database_password(env):
    tid = await _tenant(env)
    await env.ops.deploy(tid)
    first = await _get(env, tid)
    await env.ops.deploy(tid)
    second = await _get(env, tid)
    assert (second.host_port, second.db_password_enc) == (first.host_port, first.db_password_enc)


async def test_two_tenants_get_distinct_ports(env):
    a, b = await _tenant(env, bot_id=1), await _tenant(env, bot_id=2)
    await env.ops.deploy(a)
    await env.ops.deploy(b)
    assert (await _get(env, a)).host_port != (await _get(env, b)).host_port


async def test_failed_deploy_sets_error_status_with_reason(env):
    env.runner.responses["run"] = CmdResult(125, "", "port is already allocated")
    tid = await _tenant(env)
    result = await env.ops.deploy(tid)
    tenant = await _get(env, tid)
    assert not result.ok
    assert tenant.status == TenantStatus.ERROR and "already allocated" in tenant.last_error


async def test_stop_start_delete(env):
    tid = await _tenant(env)
    await env.ops.deploy(tid)

    assert (await env.ops.stop(tid)).ok
    assert (await _get(env, tid)).status == TenantStatus.STOPPED
    assert (await env.ops.start(tid)).ok
    assert (await _get(env, tid)).status == TenantStatus.RUNNING

    assert (await env.ops.delete(tid)).ok
    assert (await _get(env, tid)).status == TenantStatus.DELETED
    assert env.db_admin.dropped == [(f"mdjebot_t{tid}", f"t{tid}")]
    assert not (env.settings.tenants_dir / f"t{tid}").exists()
    assert not (await env.ops.start(tid)).ok  # deleted tenants are gone for good


async def test_set_admins_rewrites_env_and_restarts_running_tenant(env):
    tid = await _tenant(env)
    await env.ops.deploy(tid)
    env.runner.calls.clear()
    assert (await env.ops.set_admins(tid, [9, 8])).ok
    assert "ADMIN_ID=9,8" in (env.settings.tenants_dir / f"t{tid}" / ".env").read_text(encoding="utf-8")
    assert env.runner.verbs() == ["restart"]


async def test_update_all_recreates_only_running_tenants(env):
    running, stopped = await _tenant(env, bot_id=1), await _tenant(env, bot_id=2)
    await env.ops.deploy(running)
    await env.ops.deploy(stopped)
    await env.ops.stop(stopped)
    env.runner.calls.clear()

    ok, failed = await env.ops.update_all()

    assert (ok, failed) == (1, 0)
    assert env.runner.verbs()[0] == "pull"
    assert env.runner.verbs().count("run") == 1


async def test_expiry_pass_reminds_once_then_expires_then_deletes(env):
    tid = await _tenant(env, status=TenantStatus.RUNNING, expires_in_days=2)
    async with env.db.session() as s:
        await set_cfg(s, "reminder_days", "3")
        await set_cfg(s, "grace_days", "3")
        await s.commit()

    first = await run_expiry_pass(env.db, env.ops, env.notifier)
    second = await run_expiry_pass(env.db, env.ops, env.notifier)
    assert (first["reminded"], second["reminded"]) == (1, 0)
    assert len(env.notifier.to(5)) == 1

    later = utcnow() + timedelta(days=3)
    expired = await run_expiry_pass(env.db, env.ops, env.notifier, now=later)
    assert expired["expired"] == 1 and (await _get(env, tid)).status == TenantStatus.EXPIRED
    assert "stop" in env.runner.verbs()

    much_later = utcnow() + timedelta(days=7)
    deleted = await run_expiry_pass(env.db, env.ops, env.notifier, now=much_later)
    assert deleted["deleted"] == 1 and (await _get(env, tid)).status == TenantStatus.DELETED


async def test_expiry_pass_ignores_admin_stopped_and_unexpired(env):
    stopped = await _tenant(env, status=TenantStatus.STOPPED, expires_in_days=-10, bot_id=1)
    fresh = await _tenant(env, status=TenantStatus.RUNNING, expires_in_days=20, bot_id=2)
    counts = await run_expiry_pass(env.db, env.ops, env.notifier)
    assert counts == {"reminded": 0, "expired": 0, "deleted": 0}
    assert (await _get(env, stopped)).status == TenantStatus.STOPPED
    assert (await _get(env, fresh)).status == TenantStatus.RUNNING


async def test_renewal_before_expiry_rearms_reminder(env):
    tid = await _tenant(env, status=TenantStatus.RUNNING, expires_in_days=1)
    await run_expiry_pass(env.db, env.ops, env.notifier)
    async with env.db.session() as s:
        tenant = await s.get(Tenant, tid)
        assert tenant.reminded_for == tenant.expires_at
        billing.extend_tenant(tenant, 1)  # still within the reminder window, but a new expiry
        await s.commit()
    counts = await run_expiry_pass(env.db, env.ops, env.notifier)
    assert counts["reminded"] == 1
