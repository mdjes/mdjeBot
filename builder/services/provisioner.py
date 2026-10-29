"""Creates, runs and removes customer bot containers through the Docker CLI.

The builder container talks to the host Docker daemon (``/var/run/docker.sock``), so the tenant
containers are siblings of the builder, not children. Tenant files live under
``<config_dir>/tenants/t<id>/`` which is mounted at the same path inside the builder, which is what
lets ``docker run -v`` paths resolve identically on the host.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from builder.models import Tenant
from builder.settings import Settings

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_SECRET_RE = re.compile(r"^[A-Za-z0-9]{8,128}$")


class ProvisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CmdResult:
    rc: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0


Runner = Callable[..., Awaitable[CmdResult]]


async def run_command(*args: str, timeout: float = 180.0) -> CmdResult:  # noqa: ASYNC109
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        return CmdResult(127, "", f"{args[0]}: command not found")
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return CmdResult(124, "", f"timed out after {timeout:.0f}s: {' '.join(args[:3])}")
    return CmdResult(proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace"))


class TenantDatabaseAdmin(Protocol):
    async def create(self, name: str, user: str, password: str) -> None: ...

    async def drop(self, name: str, user: str) -> None: ...


class MariaDbAdmin:
    """Creates one database + user per tenant using the MariaDB root account."""

    def __init__(self, settings: Settings):
        self._url = (
            f"mysql+asyncmy://root:{quote(settings.mariadb_root_password, safe='')}"
            f"@{settings.mariadb_host}:{settings.mariadb_port}/"
        )

    @staticmethod
    def _check(name: str, user: str, password: str | None = None) -> None:
        if not _IDENT_RE.match(name) or not _IDENT_RE.match(user):
            raise ProvisionError("unsafe database identifier")
        if password is not None and not _SECRET_RE.match(password):
            raise ProvisionError("unsafe database password")

    async def _run(self, statements: list[str]) -> None:
        engine = create_async_engine(self._url)
        try:
            async with engine.begin() as conn:
                for statement in statements:
                    await conn.execute(text(statement))
        finally:
            await engine.dispose()

    async def create(self, name: str, user: str, password: str) -> None:
        self._check(name, user, password)
        await self._run(
            [
                f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
                f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{password}'",
                f"ALTER USER '{user}'@'%' IDENTIFIED BY '{password}'",
                f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{user}'@'%'",
                "FLUSH PRIVILEGES",
            ]
        )

    async def drop(self, name: str, user: str) -> None:
        self._check(name, user)
        await self._run([f"DROP DATABASE IF EXISTS `{name}`", f"DROP USER IF EXISTS '{user}'@'%'"])


class Provisioner:
    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner = run_command,
        db_admin: TenantDatabaseAdmin | None = None,
        startup_wait: float = 4.0,
    ):
        self.settings = settings
        self._run = runner
        self._db = db_admin or MariaDbAdmin(settings)
        self._startup_wait = startup_wait

    # ── helpers ───────────────────────────────────────────────────────────────
    def tenant_dir(self, tenant: Tenant):
        return self.settings.tenants_dir / f"t{tenant.id}"

    def render_env(self, tenant: Tenant, token: str, db_password: str) -> str:
        s = self.settings
        db_url = f"mysql+asyncmy://{tenant.db_user}:{db_password}@{s.mariadb_host}:{s.mariadb_port}/{tenant.db_name}"
        lines = {
            "API_ID": str(s.api_id),
            "API_HASH": s.api_hash,
            "BOT_TOKEN": token,
            "ADMIN_ID": tenant.admin_ids,
            "BOT_TAG": tenant.bot_username,
            "SQLALCHEMY_DATABASE_URL": db_url,
            "FASTAPI_PORT": str(s.tenant_internal_port),
            # Many bots share one MariaDB server: keep each bot's connection pool small.
            "SQLALCHEMY_POOL_SIZE": "5",
            "SQLALCHEMY_MAX_OVERFLOW": "5",
            "REDIS_URL": s.redis_url,
            "REDIS_NAMESPACE_PREFIX": tenant.redis_prefix,
            "TELETHON_SESSION_PATH": "sessions/MdjeSession",
            "LOG_DIR": "./logs",
            "LOG_TO_FILE": "true",
            "LOG_LEVEL": "INFO",
        }
        return "\n".join(f"{key}={value}" for key, value in lines.items()) + "\n"

    def _write_env(self, tenant: Tenant, token: str, db_password: str) -> None:
        directory = self.tenant_dir(tenant)
        for sub in ("logs", "sessions", "data"):
            (directory / sub).mkdir(parents=True, exist_ok=True)
        env_path = directory / ".env"
        env_path.write_text(self.render_env(tenant, token, db_password), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(env_path, 0o600)

    async def _docker(self, *args: str, timeout: float = 180.0) -> CmdResult:  # noqa: ASYNC109
        return await self._run(self.settings.docker_bin, *args, timeout=timeout)

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def deploy(self, tenant: Tenant, *, token: str, db_password: str, memory: str, cpus: str) -> None:
        """(Re)create the tenant's database, config files and container."""
        if tenant.host_port is None:
            raise ProvisionError("tenant has no host port assigned")
        try:
            await self._db.create(tenant.db_name, tenant.db_user, db_password)
        except ProvisionError:
            raise
        except Exception as exc:
            raise ProvisionError(f"database setup failed: {exc}") from exc
        self._write_env(tenant, token, db_password)

        await self._docker("rm", "-f", tenant.container_name)
        directory = self.tenant_dir(tenant)
        args = [
            "run",
            "-d",
            "--name",
            tenant.container_name,
            "--restart",
            "unless-stopped",
            "--network",
            self.settings.docker_network,
            "--label",
            "mdjebot.managed=true",
            "--label",
            f"mdjebot.tenant={tenant.id}",
            "-p",
            f"{tenant.host_port}:{self.settings.tenant_internal_port}",
            "-v",
            f"{directory}/.env:/app/.env:ro",
            "-v",
            f"{directory}/logs:/app/logs",
            "-v",
            f"{directory}/sessions:/app/sessions",
            "-v",
            f"{directory}/data:/app/data",
        ]
        if memory:
            args += ["--memory", memory]
        if cpus:
            args += ["--cpus", cpus]
        args.append(self.settings.tenant_image)
        result = await self._docker(*args)
        if not result.ok:
            raise ProvisionError((result.err or result.out).strip() or "docker run failed")

        if self._startup_wait:
            await asyncio.sleep(self._startup_wait)
        state = await self.status(tenant)
        if state != "running":
            tail = await self.logs(tenant, tail=15)
            raise ProvisionError(f"container is {state} after start:\n{tail}".strip())

    async def _simple(self, verb: str, tenant: Tenant) -> None:
        result = await self._docker(verb, tenant.container_name)
        if not result.ok:
            raise ProvisionError((result.err or result.out).strip() or f"docker {verb} failed")

    async def start(self, tenant: Tenant) -> None:
        await self._simple("start", tenant)

    async def stop(self, tenant: Tenant) -> None:
        await self._simple("stop", tenant)

    async def restart(self, tenant: Tenant) -> None:
        await self._simple("restart", tenant)

    async def remove(self, tenant: Tenant, *, purge: bool = False) -> None:
        await self._docker("rm", "-f", tenant.container_name)
        if purge:
            try:
                await self._db.drop(tenant.db_name, tenant.db_user)
            except Exception as exc:
                raise ProvisionError(f"database cleanup failed: {exc}") from exc
            shutil.rmtree(self.tenant_dir(tenant), ignore_errors=True)

    async def update_admins(self, tenant: Tenant, *, token: str, db_password: str) -> None:
        """Rewrite the env file with the tenant's current admin list and restart the container."""
        self._write_env(tenant, token, db_password)
        await self.restart(tenant)

    async def status(self, tenant: Tenant) -> str:
        result = await self._docker("inspect", "-f", "{{.State.Status}}", tenant.container_name, timeout=30)
        return result.out.strip() if result.ok and result.out.strip() else "missing"

    async def logs(self, tenant: Tenant, tail: int = 60) -> str:
        result = await self._docker("logs", "--tail", str(tail), tenant.container_name, timeout=30)
        return ((result.out or "") + (result.err or "")).strip()

    async def pull_image(self) -> CmdResult:
        return await self._docker("pull", self.settings.tenant_image, timeout=900)

    async def server_summary(self) -> str:
        info = await self._docker(
            "info",
            "--format",
            "Docker {{.ServerVersion}} | containers: {{.ContainersRunning}}/{{.Containers}}"
            " | CPUs: {{.NCPU}} | RAM: {{.MemTotal}}",
            timeout=30,
        )
        return info.out.strip() if info.ok else (info.err.strip() or "docker unavailable")
