"""``app.cli.panel_link``: loaded standalone with the bot's panel/crypto modules replaced by fakes."""

from __future__ import annotations

import io
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from tests.support.modules import load_module_from_path


class FakePanels:
    def __init__(self, existing=()):
        self.rows = list(existing)
        self.added: list[dict] = []
        self.updated: list[tuple] = []
        self.deleted: list[int] = []

    async def get_all_panels(self):
        return self.rows

    async def add_panel(self, **kwargs):
        self.added.append(kwargs)
        self.rows.append(SimpleNamespace(code=kwargs["code"], base_url=kwargs["base_url"], username=kwargs["username"]))

    async def update_panel(self, code, **kwargs):
        self.updated.append((code, kwargs))
        return True

    async def delete_panel(self, code):
        self.deleted.append(code)
        self.rows = [row for row in self.rows if row.code != code]
        return True


@pytest.fixture
def cli(monkeypatch):
    state = SimpleNamespace(
        panels=FakePanels(), secrets_loaded=0, verified=[], groups=[SimpleNamespace(id=1), SimpleNamespace(id=2)]
    )

    def module(name: str, **attrs) -> ModuleType:
        mod = ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    for name in (
        "app",
        "app.db",
        "app.db.crud",
        "app.services",
        "app.services.panels",
        "app.utils",
        "app.utils.security",
    ):
        module(name)
    module("app.db.crud.panels", PanelsManager=lambda: state.panels)

    async def ensure_secrets():
        state.secrets_loaded += 1

    module("app.db.crud.secrets", ensure_secrets=ensure_secrets)

    async def verify_password(base_url, username, password):
        state.verified.append(("password", base_url, username))
        if password == "wrong":
            raise RuntimeError("401 unauthorized")
        return object(), "jwt-token", SimpleNamespace(groups=state.groups)

    async def verify_key(base_url, api_key):
        state.verified.append(("api_key", base_url))
        return object(), SimpleNamespace(groups=state.groups)

    module("app.services.panels.auth", verify_panel_password=verify_password, verify_panel_api_key=verify_key)
    module("app.utils.security.crypto", encrypt_data=lambda value: f"enc({value})")
    state.module = load_module_from_path("panel_link_under_test", "app/cli/panel_link.py")
    return state


async def test_link_adds_the_panel_with_encrypted_password_and_groups(cli):
    result = await cli.module.run(
        {
            "action": "link",
            "name": "Main panel",
            "base_url": "https://panel.example.com/",
            "auth_type": "password",
            "username": "p5_ab12",
            "secret": "S3cret!",
            "group_ids": [2],
            "user_limit": 20,
        }
    )

    assert result == {"ok": True, "panel_code": 1, "created": True}
    assert cli.secrets_loaded == 1  # the crypto key is loaded before anything is encrypted
    [added] = cli.panels.added
    assert added["base_url"] == "https://panel.example.com" and added["username"] == "p5_ab12"
    assert added["password"] == "enc(S3cret!)" and added["cookie"] == "jwt-token"
    assert added["default_group_ids"] == [2] and added["user_limit"] == 20 and added["auth_type"] == "password"
    assert cli.verified == [("password", "https://panel.example.com", "p5_ab12")]


async def test_link_without_groups_uses_every_group_of_the_panel(cli):
    await cli.module.run(
        {"action": "link", "base_url": "https://p.example.com", "username": "u", "secret": "x", "auth_type": "password"}
    )
    assert cli.panels.added[0]["default_group_ids"] == [1, 2]


async def test_link_with_an_api_key_stores_the_key_as_cookie(cli):
    await cli.module.run(
        {"action": "link", "base_url": "https://p.example.com", "auth_type": "api_key", "secret": "pg_key_abc"}
    )
    [added] = cli.panels.added
    assert added["cookie"] == "pg_key_abc" and added["username"] == "-" and added["auth_type"] == "api_key"


async def test_linking_again_updates_instead_of_duplicating(cli):
    payload = {
        "action": "link",
        "base_url": "https://p.example.com",
        "username": "u1",
        "secret": "old",
        "auth_type": "password",
    }
    await cli.module.run(payload)
    again = await cli.module.run({**payload, "secret": "new"})

    assert again == {"ok": True, "panel_code": 1, "created": False}
    assert len(cli.panels.added) == 1
    [(code, changes)] = cli.panels.updated
    assert code == 1 and changes["password"] == "enc(new)" and changes["enable"] is True


async def test_enable_disable_and_unlink_find_the_panel_by_url_and_username(cli):
    await cli.module.run(
        {
            "action": "link",
            "base_url": "https://p.example.com",
            "username": "u1",
            "secret": "x",
            "auth_type": "password",
        }
    )
    target = {"base_url": "https://p.example.com/", "username": "u1"}

    assert (await cli.module.run({"action": "disable", **target}))["ok"]
    assert cli.panels.updated[-1] == (1, {"enable": False})
    assert (await cli.module.run({"action": "enable", **target}))["ok"]
    assert cli.panels.updated[-1] == (1, {"enable": True})
    assert (await cli.module.run({"action": "unlink", **target}))["panel_code"] == 1
    assert cli.panels.deleted == [1]
    assert (await cli.module.run({"action": "unlink", **target}))["ok"]  # already gone is fine
    assert (await cli.module.run({"action": "disable", **target}))["ok"] is False


async def test_bad_requests_are_rejected_before_touching_anything(cli):
    for payload in (
        {"action": "link", "base_url": "ftp://x", "username": "u", "secret": "s"},
        {"action": "link", "base_url": "https://x.example.com", "username": "u", "secret": ""},
        {"action": "link", "base_url": "https://x.example.com", "username": "", "secret": "s", "auth_type": "password"},
        {"action": "explode"},
    ):
        with pytest.raises(cli.module.CliError):
            await cli.module.run(payload)
    assert cli.panels.added == [] and cli.verified == []


async def test_ping_needs_no_database(cli):
    assert await cli.module.run({"action": "ping"}) == {"ok": True}
    assert cli.secrets_loaded == 0


def _run_main(cli, monkeypatch, capsys, payload: str) -> tuple[int, dict]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    code = cli.module.main()
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("RESULT ")
    return code, json.loads(last[len("RESULT ") :])


def test_main_prints_the_result_line_and_exit_code(cli, monkeypatch, capsys):
    code, result = _run_main(
        cli,
        monkeypatch,
        capsys,
        json.dumps(
            {
                "action": "link",
                "base_url": "https://p.example.com",
                "username": "u",
                "secret": "s",
                "auth_type": "password",
            }
        ),
    )
    assert (code, result["ok"], result["panel_code"]) == (0, True, 1)


def test_main_reports_panel_errors_without_a_traceback(cli, monkeypatch, capsys):
    code, result = _run_main(
        cli,
        monkeypatch,
        capsys,
        json.dumps(
            {
                "action": "link",
                "base_url": "https://p.example.com",
                "username": "u",
                "secret": "wrong",
                "auth_type": "password",
            }
        ),
    )
    assert code == 1 and result["ok"] is False and "401" in result["error"]
    assert "wrong" not in json.dumps(result)  # the secret never leaks into the answer


def test_main_rejects_garbage_input(cli, monkeypatch, capsys):
    assert _run_main(cli, monkeypatch, capsys, "not json")[1]["ok"] is False
    assert _run_main(cli, monkeypatch, capsys, "[1, 2]")[1]["error"] == "the request must be a JSON object"
