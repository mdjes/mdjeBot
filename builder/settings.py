"""Builder runtime configuration, read from the environment and the install ``.env`` file."""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


class SettingsError(RuntimeError):
    """A required builder variable is missing or malformed."""


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def derive_secret_key(seed: str) -> str:
    """A Fernet key derived from ``seed`` (used only when BUILDER_SECRET_KEY is not set)."""
    return base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest()).decode("ascii")


def _parse_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError as exc:
            raise SettingsError(f"BUILDER_ADMIN_ID contains a non-numeric value: {part!r}") from exc
    return tuple(ids)


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    admin_ids: tuple[int, ...]
    database_url: str
    secret_key: str
    tenant_image: str
    mariadb_host: str = "mariadb"
    mariadb_port: int = 3306
    mariadb_root_password: str = ""
    redis_url: str = "redis://redis:6379/0"
    docker_bin: str = "docker"
    docker_network: str = "mdjebot"
    config_dir: str = "/opt/mdjebot"
    tenant_port_start: int = 7000
    tenant_port_end: int = 7999
    tenant_internal_port: int = 6160
    session_path: str = "builder_data/BuilderSession"
    log_level: str = "INFO"

    @property
    def tenants_dir(self) -> Path:
        return Path(self.config_dir) / "tenants"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        def need(key: str) -> str:
            value = (env.get(key) or "").strip()
            if not value:
                raise SettingsError(f"{key} is required for the builder")
            return value

        bot_token = need("BUILDER_BOT_TOKEN")
        admin_ids = _parse_ids(need("BUILDER_ADMIN_ID"))
        if not admin_ids:
            raise SettingsError("BUILDER_ADMIN_ID must contain at least one numeric Telegram ID")
        try:
            api_id = int(need("API_ID"))
        except ValueError as exc:
            raise SettingsError("API_ID must be numeric") from exc

        root_password = (env.get("MARIADB_ROOT_PASSWORD") or "").strip()
        host = (env.get("BUILDER_MARIADB_HOST") or "mariadb").strip()
        port = int(env.get("BUILDER_MARIADB_PORT") or 3306)
        database_url = (env.get("BUILDER_DATABASE_URL") or "").strip()
        if not database_url:
            if not root_password:
                raise SettingsError("MARIADB_ROOT_PASSWORD (or BUILDER_DATABASE_URL) is required for the builder")
            database_url = f"mysql+asyncmy://root:{quote(root_password, safe='')}@{host}:{port}/mdjebot_builder"

        tag = (env.get("MDJEBOT_IMAGE_TAG") or "latest").strip() or "latest"
        image = (env.get("BUILDER_TENANT_IMAGE") or f"ghcr.io/mdjes/mdjebot:{tag}").strip()
        secret = (env.get("BUILDER_SECRET_KEY") or "").strip() or derive_secret_key(bot_token)

        return cls(
            api_id=api_id,
            api_hash=need("API_HASH"),
            bot_token=bot_token,
            admin_ids=admin_ids,
            database_url=database_url,
            secret_key=secret,
            tenant_image=image,
            mariadb_host=host,
            mariadb_port=port,
            mariadb_root_password=root_password,
            redis_url=(env.get("BUILDER_TENANT_REDIS_URL") or "redis://redis:6379/0").strip(),
            docker_bin=(env.get("BUILDER_DOCKER_BIN") or "docker").strip(),
            docker_network=(env.get("BUILDER_DOCKER_NETWORK") or "mdjebot").strip(),
            config_dir=(env.get("MDJEBOT_CONFIG_DIR") or "/opt/mdjebot").strip(),
            tenant_port_start=int(env.get("BUILDER_PORT_START") or 7000),
            tenant_port_end=int(env.get("BUILDER_PORT_END") or 7999),
            session_path=(env.get("BUILDER_SESSION_PATH") or "builder_data/BuilderSession").strip(),
            log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
        )


_cached: Settings | None = None


def load_settings(env_file: str | Path = ".env", *, refresh: bool = False) -> Settings:
    """Settings from the process environment, falling back to ``env_file`` for missing keys."""
    global _cached
    if _cached is None or refresh:
        merged = {**_read_env_file(Path(env_file)), **os.environ}
        _cached = Settings.from_env(merged)
    return _cached
