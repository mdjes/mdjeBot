from __future__ import annotations

import httpx
import pytest

from builder.security import SecretBox
from builder.services.telegram_api import TelegramUnavailable, TokenError, fetch_bot_info, looks_like_token
from builder.settings import Settings, SettingsError, derive_secret_key

TOKEN = "123456789:" + "A" * 35


def _env(**extra):
    env = {
        "API_ID": "12345",
        "API_HASH": "abc",
        "BUILDER_BOT_TOKEN": TOKEN,
        "BUILDER_ADMIN_ID": "10, 20",
        "MARIADB_ROOT_PASSWORD": "p@ss/word",
    }
    env.update(extra)
    return env


def test_settings_from_env_builds_database_url_and_defaults():
    s = Settings.from_env(_env(MDJEBOT_IMAGE_TAG="dev", MDJEBOT_CONFIG_DIR="/srv/mdje"))
    assert s.admin_ids == (10, 20)
    assert s.database_url == "mysql+asyncmy://root:p%40ss%2Fword@mariadb:3306/mdjebot_builder"
    assert s.tenant_image == "ghcr.io/mdjes/mdjebot:dev"
    assert str(s.tenants_dir).replace("\\", "/") == "/srv/mdje/tenants"
    assert s.secret_key == derive_secret_key(TOKEN)


@pytest.mark.parametrize("missing", ["API_ID", "API_HASH", "BUILDER_BOT_TOKEN", "BUILDER_ADMIN_ID"])
def test_settings_require_core_values(missing):
    env = _env()
    del env[missing]
    with pytest.raises(SettingsError):
        Settings.from_env(env)


def test_settings_reject_non_numeric_admin():
    with pytest.raises(SettingsError):
        Settings.from_env(_env(BUILDER_ADMIN_ID="10,abc"))


def test_secretbox_roundtrip_and_wrong_key():
    box = SecretBox(derive_secret_key("one"))
    assert box.decrypt(box.encrypt(TOKEN)) == TOKEN
    with pytest.raises(ValueError):
        SecretBox(derive_secret_key("two")).decrypt(box.encrypt(TOKEN))


@pytest.mark.parametrize(
    ("value", "valid"),
    [(TOKEN, True), (" " + TOKEN + " ", True), ("123:short", False), ("abc", False), ("", False)],
)
def test_looks_like_token(value, valid):
    assert looks_like_token(value) is valid


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_bot_info_ok():
    def handler(request):
        assert request.url.path.endswith("/getMe")
        return httpx.Response(
            200, json={"ok": True, "result": {"id": 42, "is_bot": True, "username": "shop_bot", "first_name": "Shop"}}
        )

    async with _client(handler) as client:
        info = await fetch_bot_info(TOKEN, client=client)
    assert (info.id, info.username, info.name) == (42, "shop_bot", "Shop")


async def test_fetch_bot_info_rejected_and_unreachable():
    async with _client(lambda r: httpx.Response(401, json={"ok": False})) as client:
        with pytest.raises(TokenError):
            await fetch_bot_info(TOKEN, client=client)

    def boom(request):
        raise httpx.ConnectError("no route")

    async with _client(boom) as client:
        with pytest.raises(TelegramUnavailable):
            await fetch_bot_info(TOKEN, client=client)

    with pytest.raises(TokenError):
        await fetch_bot_info("not-a-token")
