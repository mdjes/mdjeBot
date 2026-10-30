from __future__ import annotations

import asyncio
from datetime import timedelta

from builder.models import PanelSubStatus, TenantStatus, utcnow
from builder.services.panels.backend import PanelError
from builder.services.panels.sync import run_panel_sync
from builder.services.provisioner import CmdResult
from tests.integration.builder.panel_helpers import (
    GB,
    exec_payloads,
    get_sub,
    make_master,
    make_plan,
    make_sub,
    make_tenant,
)


async def _ready(env, *, tenant_status=TenantStatus.RUNNING):
    master = await make_master(env)
    plan = await make_plan(env, master)
    tenant = await make_tenant(env, status=tenant_status)
    sub = await make_sub(env, plan, tenant_id=tenant)
    return master, plan, tenant, sub


async def test_provision_creates_the_admin_and_connects_the_bot(env):
    _, _, tenant, sub_id = await _ready(env)

    result = await env.panels.provision(sub_id)

    assert result.ok
    sub = await get_sub(env, sub_id)
    assert sub.status == PanelSubStatus.ACTIVE and sub.linked and sub.admin_id in env.backend.admins
    assert sub.data_limit == 50 * GB and 29 <= (sub.expires_at - utcnow()).days <= 30
    admin = env.backend.admins[sub.admin_id]
    assert admin["username"] == sub.admin_username and admin["data_limit"] == 50 * GB and admin["max_users"] == 20
    password = env.box.decrypt(sub.admin_password_enc)
    assert admin["password"] == password and len(password) >= 12

    [request] = exec_payloads(env)
    assert request["action"] == "link" and request["username"] == sub.admin_username
    assert request["secret"] == password and request["base_url"] == "https://panel.example.com"
    assert request["group_ids"] == [1, 2] and request["user_limit"] == 20
    exec_call = next(c for c in env.runner.calls if c[1] == "exec")
    assert f"mdjebot-t{tenant}" in exec_call and "app.cli.panel_link" in exec_call
    assert not any(password in part for call in env.runner.calls for part in call)  # never on the command line


async def test_provision_waits_for_the_bot_and_links_when_it_starts(env):
    _, _, tenant, sub_id = await _ready(env, tenant_status=TenantStatus.AWAITING_TOKEN)

    assert (await env.panels.provision(sub_id)).ok
    sub = await get_sub(env, sub_id)
    assert sub.status == PanelSubStatus.ACTIVE and not sub.linked and exec_payloads(env) == []

    # the owner sends the token, the bot is deployed, then the panel gets connected in the background
    async with env.db.session() as s:
        from builder.models import Tenant

        row = await s.get(Tenant, tenant)
        row.status = TenantStatus.RUNNING
        await s.commit()
    env.panels.link_tenant_panels(tenant)
    await asyncio.gather(*env.panels.background)

    assert (await get_sub(env, sub_id)).linked
    assert len(exec_payloads(env)) == 1


async def test_failed_link_is_recorded_and_retried_by_the_sync_pass(env):
    _, _, _, sub_id = await _ready(env)
    env.runner.responses["exec"] = CmdResult(1, 'RESULT {"ok": false, "error": "tables not ready"}\n')

    assert (await env.panels.provision(sub_id)).ok  # the admin exists; only the connection failed
    sub = await get_sub(env, sub_id)
    assert not sub.linked and "tables not ready" in sub.last_error

    env.runner.responses.pop("exec")
    counts = await run_panel_sync(env.db, env.panels, env.notifier)
    assert counts["linked"] == 1 and (await get_sub(env, sub_id)).linked


async def test_provision_failure_marks_error_and_the_retry_reuses_credentials(env):
    _, _, _, sub_id = await _ready(env)
    env.backend.fail["create"] = PanelError("panel returned HTTP 500")

    failed = await env.panels.provision(sub_id)
    assert not failed.ok and "500" in failed.error
    errored = await get_sub(env, sub_id)
    assert errored.status == PanelSubStatus.ERROR and errored.admin_id is None
    first_password = env.box.decrypt(errored.admin_password_enc)

    assert (await env.panels.provision(sub_id)).ok
    sub = await get_sub(env, sub_id)
    assert sub.status == PanelSubStatus.ACTIVE and sub.admin_username == errored.admin_username
    assert env.backend.admins[sub.admin_id]["password"] == first_password


async def test_provision_adopts_an_admin_left_by_an_interrupted_attempt(env):
    _, _, _, sub_id = await _ready(env)
    env.backend.fail["create"] = PanelError("the panel did not answer in time")
    await env.panels.provision(sub_id)
    username = (await get_sub(env, sub_id)).admin_username
    # the panel actually created it before the timeout
    await env.backend.create_admin(None, username=username, password="x", role_id=1, data_limit=0, max_users=0, note="")

    assert (await env.panels.provision(sub_id)).ok
    sub = await get_sub(env, sub_id)
    assert sub.status == PanelSubStatus.ACTIVE
    assert env.backend.admins[sub.admin_id]["password"] == env.box.decrypt(sub.admin_password_enc)
    assert env.backend.admins[sub.admin_id]["data_limit"] == 50 * GB


async def test_provision_refuses_a_disabled_or_unconfigured_master(env):
    master = await make_master(env, role_id=None)
    plan = await make_plan(env, master)
    sub_id = await make_sub(env, plan)
    result = await env.panels.provision(sub_id)
    assert not result.ok and "role" in result.error and env.backend.admins == {}


async def test_provision_twice_creates_one_admin(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    await env.panels.provision(sub_id)
    assert len(env.backend.admins) == 1


async def test_renew_starts_a_fresh_cycle(env):
    _, plan, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    sub = await get_sub(env, sub_id)
    env.backend.admins[sub.admin_id]["used"] = 40 * GB
    old_expiry = sub.expires_at
    env.runner.stdin.clear()

    assert (await env.panels.renew(sub_id, plan)).ok

    sub = await get_sub(env, sub_id)
    assert sub.used_bytes == 0 and sub.status == PanelSubStatus.ACTIVE
    assert env.backend.admins[sub.admin_id]["used"] == 0 and env.backend.admins[sub.admin_id]["status"] == "active"
    assert (sub.expires_at - old_expiry).days == 30  # stacked on the remaining time
    assert [p["action"] for p in exec_payloads(env)] == ["enable"]


async def test_add_volume_grants_traffic_and_reenables_a_limited_panel(env):
    master, _, _, sub_id = await _ready(env)
    pack = await make_plan(env, master, name="+20GB", kind="addon", data_gb=20, duration_days=0, price=50_000)
    await env.panels.provision(sub_id)
    sub = await get_sub(env, sub_id)
    env.backend.admins[sub.admin_id]["used"] = 50 * GB
    await run_panel_sync(env.db, env.panels, env.notifier)
    assert (await get_sub(env, sub_id)).status == PanelSubStatus.LIMITED
    assert env.backend.admins[sub.admin_id]["status"] == "disabled"

    assert (await env.panels.add_volume(sub_id, pack)).ok

    sub = await get_sub(env, sub_id)
    assert sub.data_limit == 70 * GB and sub.status == PanelSubStatus.ACTIVE
    assert (
        env.backend.admins[sub.admin_id]["data_limit"] == 70 * GB
        and env.backend.admins[sub.admin_id]["status"] == "active"
    )


async def test_suspend_and_activate_respect_expiry_and_volume(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    admin_id = (await get_sub(env, sub_id)).admin_id

    assert (await env.panels.suspend(sub_id)).ok
    assert (await get_sub(env, sub_id)).status == PanelSubStatus.DISABLED
    assert env.backend.admins[admin_id]["status"] == "disabled"
    assert (await env.panels.activate(sub_id)).ok
    assert env.backend.admins[admin_id]["status"] == "active"

    await env.panels.suspend(sub_id)
    async with env.db.session() as s:
        from builder.models import PanelSub

        (await s.get(PanelSub, sub_id)).expires_at = utcnow() - timedelta(days=1)
        await s.commit()
    refused = await env.panels.activate(sub_id)
    assert not refused.ok and "renew" in refused.error


async def test_reset_password_updates_panel_and_relinks_the_bot(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    before = env.box.decrypt((await get_sub(env, sub_id)).admin_password_enc)
    env.runner.stdin.clear()

    result = await env.panels.reset_password(sub_id)

    sub = await get_sub(env, sub_id)
    assert result.ok and result.password and result.password != before
    assert env.backend.admins[sub.admin_id]["password"] == result.password
    assert env.box.decrypt(sub.admin_password_enc) == result.password and sub.linked
    assert exec_payloads(env)[0]["secret"] == result.password


async def test_delete_removes_admin_and_unlinks_the_bot(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    admin_id = (await get_sub(env, sub_id)).admin_id
    env.runner.stdin.clear()

    assert (await env.panels.delete(sub_id)).ok

    assert admin_id not in env.backend.admins
    assert (await get_sub(env, sub_id)).status == PanelSubStatus.DELETED
    assert exec_payloads(env)[0]["action"] == "unlink"


async def test_delete_keeps_the_subscription_when_the_panel_refuses(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    env.backend.fail["remove"] = PanelError("forbidden")
    result = await env.panels.delete(sub_id)
    assert not result.ok
    assert (await get_sub(env, sub_id)).status != PanelSubStatus.DELETED  # never lose track of a live admin


# ── metering ──────────────────────────────────────────────────────────────────
async def test_sync_warns_once_then_limits_the_panel(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    admin_id = (await get_sub(env, sub_id)).admin_id

    env.backend.admins[admin_id]["used"] = 42 * GB  # 84%
    first = await run_panel_sync(env.db, env.panels, env.notifier)
    second = await run_panel_sync(env.db, env.panels, env.notifier)
    assert (first["warned"], second["warned"]) == (1, 0)
    assert "84" in env.notifier.to(5)[-1]

    env.backend.admins[admin_id]["used"] = 50 * GB
    third = await run_panel_sync(env.db, env.panels, env.notifier)
    sub = await get_sub(env, sub_id)
    assert third["limited"] == 1 and sub.status == PanelSubStatus.LIMITED and sub.used_bytes == 50 * GB
    assert env.backend.admins[admin_id]["status"] == "disabled"
    assert "حجم" in env.notifier.to(5)[-1]

    again = await run_panel_sync(env.db, env.panels, env.notifier)  # limited subs are not processed again
    assert again["synced"] == 1 and again["limited"] == 0


async def test_sync_expires_the_panel_and_notifies(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    admin_id = (await get_sub(env, sub_id)).admin_id

    counts = await run_panel_sync(env.db, env.panels, env.notifier, now=utcnow() + timedelta(days=31))

    assert counts["expired"] == 1
    assert (await get_sub(env, sub_id)).status == PanelSubStatus.EXPIRED
    assert env.backend.admins[admin_id]["status"] == "disabled"
    assert "منقضی" in env.notifier.to(5)[-1] or "تمام شد" in env.notifier.to(5)[-1]


async def test_sync_warns_before_expiry_only_once(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    soon = utcnow() + timedelta(days=28)  # 2 days left, default warning window is 3
    first = await run_panel_sync(env.db, env.panels, env.notifier, now=soon)
    second = await run_panel_sync(env.db, env.panels, env.notifier, now=soon)
    assert (first["warned"], second["warned"]) == (1, 0)


async def test_sync_flags_an_admin_that_vanished_from_the_panel(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    env.backend.admins.clear()
    counts = await run_panel_sync(env.db, env.panels, env.notifier)
    sub = await get_sub(env, sub_id)
    assert counts["errors"] == 1 and sub.status == PanelSubStatus.ERROR and "no longer exists" in sub.last_error


async def test_sync_survives_a_panel_outage(env):
    _, _, _, sub_id = await _ready(env)
    await env.panels.provision(sub_id)
    env.backend.fail["get"] = PanelError("the panel did not answer in time")
    counts = await run_panel_sync(env.db, env.panels, env.notifier)
    assert counts["errors"] == 1 and (await get_sub(env, sub_id)).status == PanelSubStatus.ACTIVE
