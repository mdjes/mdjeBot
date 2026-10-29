from __future__ import annotations

from dataclasses import dataclass

import pytest

from builder.db import Database
from builder.services.ops import TenantOps
from tests.support.builder_env import (
    FakeDbAdmin,
    FakeNotifier,
    FakeRunner,
    make_box,
    make_provisioner,
    make_settings,
)


@dataclass
class BuilderEnv:
    db: Database
    settings: object
    box: object
    runner: FakeRunner
    db_admin: FakeDbAdmin
    notifier: FakeNotifier
    prov: object
    ops: TenantOps


@pytest.fixture
async def env(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(settings.database_url)
    await db.create_all()
    runner, db_admin = FakeRunner(), FakeDbAdmin()
    prov = make_provisioner(settings, runner, db_admin)
    box = make_box(settings)
    ops = TenantOps(db, settings, box, prov)
    yield BuilderEnv(db, settings, box, runner, db_admin, FakeNotifier(), prov, ops)
    await db.dispose()


@pytest.fixture
async def app(env):
    from builder.app import BuilderApp
    from builder.services.telegram_api import BotInfo, TokenError

    async def check_token(token: str) -> BotInfo:
        if token.startswith("999"):
            raise TokenError("rejected")
        return BotInfo(id=int(token.split(":")[0]), username=f"bot{token.split(':')[0]}", name="Bot")

    return BuilderApp(env.settings, env.db, env.ops, env.notifier, env.box, check_token=check_token)
