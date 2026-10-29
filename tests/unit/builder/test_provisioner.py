from __future__ import annotations

import pytest

from builder.models import Tenant
from builder.services.provisioner import CmdResult, MariaDbAdmin, ProvisionError
from tests.support.builder_env import FakeDbAdmin, FakeRunner, make_provisioner, make_settings


def _tenant(**kw) -> Tenant:
    values = {
        "id": 7,
        "owner_id": 1,
        "bot_id": 9,
        "bot_username": "shop_bot",
        "token_enc": "x",
        "admin_ids": "10,20",
        "host_port": 7001,
    }
    values.update(kw)
    return Tenant(**values)


async def test_deploy_creates_db_env_and_container(tmp_path):
    settings = make_settings(tmp_path)
    runner, db_admin = FakeRunner(), FakeDbAdmin()
    prov = make_provisioner(settings, runner, db_admin)

    await prov.deploy(_tenant(), token="1:tok", db_password="pw123456", memory="384m", cpus="0.5")

    assert db_admin.created == [("mdjebot_t7", "t7", "pw123456")]
    env = (tmp_path / "tenants" / "t7" / ".env").read_text(encoding="utf-8")
    assert "BOT_TOKEN=1:tok" in env
    assert "ADMIN_ID=10,20" in env
    assert "SQLALCHEMY_DATABASE_URL=mysql+asyncmy://t7:pw123456@mariadb:3306/mdjebot_t7" in env
    assert "REDIS_NAMESPACE_PREFIX=mdjebot:t7" in env
    assert "SQLALCHEMY_POOL_SIZE=5" in env

    assert runner.verbs() == ["rm", "run", "inspect"]
    run_args = runner.calls[1]
    assert run_args[run_args.index("--name") + 1] == "mdjebot-t7"
    assert run_args[run_args.index("-p") + 1] == "7001:6160"
    assert run_args[run_args.index("--memory") + 1] == "384m"
    assert run_args[run_args.index("--network") + 1] == "mdjebot"
    assert run_args[-1] == "ghcr.io/mdjes/mdjebot:latest"


async def test_deploy_requires_port(tmp_path):
    prov = make_provisioner(make_settings(tmp_path))
    with pytest.raises(ProvisionError):
        await prov.deploy(_tenant(host_port=None), token="1:t", db_password="pw123456", memory="", cpus="")


async def test_deploy_reports_docker_run_failure(tmp_path):
    runner = FakeRunner(run=CmdResult(125, "", "port is already allocated"))
    prov = make_provisioner(make_settings(tmp_path), runner)
    with pytest.raises(ProvisionError, match="already allocated"):
        await prov.deploy(_tenant(), token="1:t", db_password="pw123456", memory="", cpus="")


async def test_deploy_fails_when_container_exits(tmp_path):
    runner = FakeRunner(inspect=CmdResult(0, "exited\n"), logs=CmdResult(0, "", "Traceback: bad token"))
    prov = make_provisioner(make_settings(tmp_path), runner)
    with pytest.raises(ProvisionError, match="exited"):
        await prov.deploy(_tenant(), token="1:t", db_password="pw123456", memory="", cpus="")


async def test_remove_with_purge_drops_database_and_files(tmp_path):
    settings = make_settings(tmp_path)
    runner, db_admin = FakeRunner(), FakeDbAdmin()
    prov = make_provisioner(settings, runner, db_admin)
    tenant = _tenant()
    await prov.deploy(tenant, token="1:t", db_password="pw123456", memory="", cpus="")

    await prov.remove(tenant, purge=True)

    assert db_admin.dropped == [("mdjebot_t7", "t7")]
    assert not (tmp_path / "tenants" / "t7").exists()
    assert "rm" in runner.verbs()


async def test_start_failure_is_raised(tmp_path):
    prov = make_provisioner(make_settings(tmp_path), FakeRunner(start=CmdResult(1, "", "no such container")))
    with pytest.raises(ProvisionError, match="no such container"):
        await prov.start(_tenant())


async def test_status_missing_when_inspect_fails(tmp_path):
    prov = make_provisioner(make_settings(tmp_path), FakeRunner(inspect=CmdResult(1, "", "No such object")))
    assert await prov.status(_tenant()) == "missing"


@pytest.mark.parametrize(
    ("name", "user", "password"),
    [("db;drop", "t1", "pw123456"), ("db", "t1' OR '1", "pw123456"), ("db", "t1", "pw'; DROP"), ("db", "t1", "short")],
)
async def test_mariadb_admin_rejects_unsafe_identifiers(tmp_path, name, user, password):
    admin = MariaDbAdmin(make_settings(tmp_path))
    with pytest.raises(ProvisionError):
        await admin.create(name, user, password)
