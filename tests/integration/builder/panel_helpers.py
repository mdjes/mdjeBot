"""Shared setup for the panel-selling tests (plain helpers, not tests)."""

from __future__ import annotations

import json

from builder.models import MasterPanel, PanelPlan, PanelSub, Tenant, TenantStatus
from builder.services import billing

GB = 1024**3


async def make_master(env, **overrides) -> int:
    values = {
        "name": "Main panel",
        "base_url": "https://panel.example.com",
        "auth_type": "password",
        "username": "root",
        "secret_enc": env.box.encrypt("rootpw"),
        "role_id": 1,
        "group_ids": "1,2",
        "enabled": True,
    }
    values.update(overrides)
    async with env.db.session() as s:
        master = MasterPanel(**values)
        s.add(master)
        await s.commit()
        return master.id


async def make_plan(env, master_id: int, **overrides) -> int:
    values = {
        "master_id": master_id,
        "name": "50GB",
        "kind": "plan",
        "data_gb": 50,
        "duration_days": 30,
        "max_users": 20,
        "price": 200_000,
    }
    values.update(overrides)
    async with env.db.session() as s:
        plan = PanelPlan(**values)
        s.add(plan)
        await s.commit()
        return plan.id


async def make_tenant(env, *, owner_id: int = 5, status: str = TenantStatus.RUNNING, bot_id: int = 111) -> int:
    async with env.db.session() as s:
        tenant = await billing.create_tenant(s, owner_id=owner_id)
        if status != TenantStatus.AWAITING_TOKEN:
            billing.attach_token(
                tenant,
                env.box,
                token=f"{bot_id}:" + "T" * 35,
                bot_id=bot_id,
                bot_username=f"bot{bot_id}",
                admin_ids=[owner_id],
            )
        tenant.status = status
        await s.commit()
        return tenant.id


async def make_sub(env, plan_id: int, *, owner_id: int = 5, tenant_id: int | None = None) -> int:
    async with env.db.session() as s:
        plan = await s.get(PanelPlan, plan_id)
        sub = await billing.create_panel_sub(s, owner_id=owner_id, plan=plan, tenant_id=tenant_id)
        await s.commit()
        return sub.id


async def get_sub(env, sub_id: int) -> PanelSub:
    async with env.db.session() as s:
        return await s.get(PanelSub, sub_id)


async def get_tenant(env, tenant_id: int) -> Tenant:
    async with env.db.session() as s:
        return await s.get(Tenant, tenant_id)


def exec_payloads(env) -> list[dict]:
    """The JSON requests the builder sent to bot containers through ``docker exec``."""
    return [json.loads(raw) for raw in env.runner.stdin]


async def cb(app, data, user=5):
    from tests.support.builder_env import FakeCtx

    ctx = FakeCtx(user, data=data)
    await app.on_callback(ctx)
    return ctx


async def msg(app, text="", user=5, media=False):
    from tests.support.builder_env import FakeCtx

    ctx = FakeCtx(user, text=text, media=media)
    await app.on_message(ctx)
    return ctx
