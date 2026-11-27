"""Nightly backup of every customer bot's database, sent to its owner in Telegram, and one-tap restore.

The backups live in the owner's chat, not on the server: each one is written to a temporary folder, sent and
deleted straight away, so the disk never fills up with old dumps. The file carries a button that puts the bot back
to that state (the restore downloads the very file the button is on).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import select

from builder.db import Database
from builder.models import Tenant, TenantStatus, utcnow
from builder.notify import Notifier
from builder.security import SecretBox
from builder.services.cfg import load_cfg, set_cfg
from builder.services.provisioner import ProvisionError
from builder.settings import Settings
from builder.ui import Btn
from builder.util import esc, fmt_bytes, fmt_date

LAST_DAY_KEY = "backup_last_day"
FORMAT = 1
SQL_NAME = "database.sql"
META_NAME = "meta.json"
# A backup that would not fit one Telegram upload is not sent at all (the limit is 2 GB; stay well below it).
MAX_ARCHIVE_BYTES = 1_500 * 1024 * 1024
MAX_SQL_BYTES = 8 * 1024**3
BACKED_UP = (TenantStatus.RUNNING, TenantStatus.STOPPED, TenantStatus.EXPIRED)  # bots that have a database


class BackupError(RuntimeError):
    pass


class SqlTool(Protocol):
    """Dumps a database to a file and loads a dump back (the real one runs the MariaDB command line client)."""

    async def dump(self, database: str, out_path: Path) -> None: ...

    async def load(self, database: str, user: str, password: str, sql_path: Path) -> None: ...


def _run_client(
    args: list[str], env: dict[str, str], *, stdin_path: Path | None, stdout_path: Path | None, timeout: float
) -> tuple[int, str]:
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL  # noqa: SIM115
    stdout = open(stdout_path, "wb") if stdout_path else subprocess.DEVNULL  # noqa: SIM115
    try:
        done = subprocess.run(
            args, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, env=env, timeout=timeout, check=False
        )
    except FileNotFoundError:
        return 127, f"{args[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f}s"
    finally:
        for handle in (stdin, stdout):
            if hasattr(handle, "close"):
                handle.close()
    return done.returncode, done.stderr.decode(errors="replace").strip()


class MariaDbTool:
    """``mariadb-dump`` / ``mariadb`` against the shared server (the builder image ships the client)."""

    def __init__(self, settings: Settings, *, timeout: float = 1800.0):
        self._s = settings
        self._timeout = timeout

    @staticmethod
    def _binary(*names: str) -> str:
        for name in names:
            found = shutil.which(name)
            if found:
                return found
        raise BackupError(f"{names[0]} is not installed in the builder image")

    def _base(self, binary: str, user: str) -> list[str]:
        return [binary, f"--host={self._s.mariadb_host}", f"--port={self._s.mariadb_port}", f"--user={user}"]

    async def dump(self, database: str, out_path: Path) -> None:
        args = [
            *self._base(self._binary("mariadb-dump", "mysqldump"), "root"),
            "--single-transaction",
            "--routines",
            "--triggers",
            "--hex-blob",
            "--default-character-set=utf8mb4",
            database,
        ]
        env = {**os.environ, "MYSQL_PWD": self._s.mariadb_root_password}
        code, err = await asyncio.to_thread(
            _run_client, args, env, stdin_path=None, stdout_path=out_path, timeout=self._timeout
        )
        size = await asyncio.to_thread(lambda: out_path.stat().st_size if out_path.exists() else 0)
        if code != 0 or size == 0:
            raise BackupError(f"dump failed: {err or 'empty dump'}"[:400])

    async def load(self, database: str, user: str, password: str, sql_path: Path) -> None:
        # The bot's own database user: a dump can only ever touch that one database.
        args = [
            *self._base(self._binary("mariadb", "mysql"), user),
            "--default-character-set=utf8mb4",
            database,
        ]
        env = {**os.environ, "MYSQL_PWD": password}
        code, err = await asyncio.to_thread(
            _run_client, args, env, stdin_path=sql_path, stdout_path=None, timeout=self._timeout
        )
        if code != 0:
            raise BackupError(f"restore failed: {err}"[:400])


@dataclass(frozen=True)
class BackupResult:
    ok: bool
    error: str | None = None
    size: int = 0


@dataclass(frozen=True)
class RestoreResult:
    ok: bool
    error: str | None = None
    rolled_back: bool = False


def _zip_sync(sql_path: Path, meta: dict, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(sql_path, arcname=SQL_NAME)
        archive.writestr(META_NAME, json.dumps(meta, ensure_ascii=False))


def _read_meta_sync(zip_path: Path) -> dict:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            if SQL_NAME not in names or META_NAME not in names:
                raise BackupError("this is not a backup made by the builder")
            meta = json.loads(archive.read(META_NAME).decode("utf-8"))
            if archive.getinfo(SQL_NAME).file_size > MAX_SQL_BYTES:
                raise BackupError("the backup is too large to restore")
    except (zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise BackupError("this is not a valid backup file") from exc
    if not isinstance(meta, dict) or meta.get("format") != FORMAT:
        raise BackupError("this backup was made by a different version")
    return meta


def _extract_sql_sync(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive, archive.open(SQL_NAME) as source, open(dest, "wb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)


def backup_caption(tenant: Tenant, size: int, when: datetime, *, trigger: str) -> str:
    label = f"@{tenant.bot_username}" if tenant.bot_username else f"#{tenant.id}"
    kind = "خودکار شبانه" if trigger == "auto" else "دستی"
    return (
        f"🗄 <b>پشتیبان ربات {esc(label)}</b> ({kind})\n"
        f"🕒 {fmt_date(when)} · 💾 {fmt_bytes(size)}\n\n"
        "این فایل را نگه دارید؛ با دکمهٔ زیر ربات به همین نسخه برمی‌گردد."
    )


def restore_button(tenant_id: int) -> list[list[Btn]]:
    return [[Btn("♻️ بازیابی این نسخه", f"u:rest:{tenant_id}")]]


class BackupService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        box: SecretBox,
        prov,
        notifier: Notifier,
        *,
        tool: SqlTool | None = None,
        busy: set[int] | None = None,
        pause: float = 1.0,
    ):
        self.db, self.settings, self.box, self.prov, self.notifier = db, settings, box, prov, notifier
        self.tool = tool or MariaDbTool(settings)
        self.busy = busy if busy is not None else set()  # tenants being restored: the watchdog leaves them alone
        self.pause = pause

    async def _tenant(self, tenant_id: int) -> Tenant | None:
        async with self.db.session() as session:
            tenant = await session.get(Tenant, tenant_id)
        if tenant is None or tenant.status == TenantStatus.DELETED:
            return None
        return tenant

    # ── backup ────────────────────────────────────────────────────────────────
    async def make_archive(self, tenant: Tenant, work_dir: Path) -> Path:
        """Dump the bot's database and zip it with a small description; the plain dump is removed again."""
        if tenant.db_password_enc is None:
            raise BackupError("the bot has no database yet")
        sql_path = work_dir / SQL_NAME
        await self.tool.dump(tenant.db_name, sql_path)
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        zip_path = work_dir / f"mdjebot-bot{tenant.id}-{stamp}.zip"
        meta = {
            "format": FORMAT,
            "tenant_id": tenant.id,
            "db_name": tenant.db_name,
            "bot_username": tenant.bot_username,
            "created_at": datetime.now(UTC).isoformat(),
        }
        await asyncio.to_thread(_zip_sync, sql_path, meta, zip_path)
        sql_path.unlink(missing_ok=True)
        return zip_path

    async def backup_and_send(
        self, tenant_id: int, *, trigger: str = "auto", now: datetime | None = None
    ) -> BackupResult:
        tenant = await self._tenant(tenant_id)
        if tenant is None or tenant.status not in BACKED_UP or tenant.db_password_enc is None:
            return BackupResult(False, "this bot has nothing to back up yet")
        work_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, "mdjebot-backup-"))
        try:
            zip_path = await self.make_archive(tenant, work_dir)
            size = zip_path.stat().st_size
            if size > MAX_ARCHIVE_BYTES:
                return BackupResult(False, f"the backup is too large to send ({fmt_bytes(size)})", size)
            sent = await self.notifier.send_file(
                tenant.owner_id,
                str(zip_path),
                backup_caption(tenant, size, now or utcnow(), trigger=trigger),
                restore_button(tenant.id),
            )
            if not sent:
                return BackupResult(False, "the owner could not be reached on Telegram", size)
            async with self.db.session() as session:
                row = await session.get(Tenant, tenant.id)
                if row is not None:
                    row.last_backup_at = now or utcnow()
                    await session.commit()
            return BackupResult(True, size=size)
        except (BackupError, ProvisionError, OSError) as exc:
            return BackupResult(False, str(exc))
        finally:
            await asyncio.to_thread(shutil.rmtree, work_dir, True)

    async def run_all(
        self, *, now: datetime | None = None, skip_newer_than: timedelta | None = None
    ) -> dict[str, list]:
        """Back up every bot that has a database.

        Returns ``{"sent": [ids], "failed": [(id, why)], "skipped": [ids]}``. A bot already backed up within
        ``skip_newer_than`` is skipped, which is what lets a run that was cut short (a restart) carry on where it
        stopped instead of sending everybody a second copy.
        """
        now = now or utcnow()
        async with self.db.session() as session:
            rows = (
                await session.execute(
                    select(Tenant.id, Tenant.last_backup_at)
                    .where(Tenant.status.in_(BACKED_UP), Tenant.db_password_enc.is_not(None))
                    .order_by(Tenant.id)
                )
            ).all()
        outcome: dict[str, list] = {"sent": [], "failed": [], "skipped": []}
        for tenant_id, last in rows:
            if skip_newer_than is not None and last is not None and now - last < skip_newer_than:
                outcome["skipped"].append(tenant_id)
                continue
            result = await self.backup_and_send(tenant_id, trigger="auto", now=now)
            if result.ok:
                outcome["sent"].append(tenant_id)
            else:
                outcome["failed"].append((tenant_id, result.error))
            if self.pause:
                await asyncio.sleep(self.pause)  # keep clear of Telegram's flood limits
        return outcome

    # ── restore ───────────────────────────────────────────────────────────────
    async def restore(self, tenant_id: int, zip_path: Path) -> RestoreResult:
        """Put a bot's database back to the state in ``zip_path``; on any failure the previous data is put back."""
        tenant = await self._tenant(tenant_id)
        if tenant is None or tenant.db_password_enc is None:
            return RestoreResult(False, "this bot has no database to restore into")
        if tenant_id in self.busy:
            return RestoreResult(False, "a restore of this bot is already running")
        try:
            meta = await asyncio.to_thread(_read_meta_sync, zip_path)
        except BackupError as exc:
            return RestoreResult(False, str(exc))
        if int(meta.get("tenant_id") or 0) != tenant.id:
            return RestoreResult(False, "this backup belongs to another bot")

        password = self.box.decrypt(tenant.db_password_enc)
        was_running = tenant.status == TenantStatus.RUNNING
        self.busy.add(tenant_id)
        work_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, "mdjebot-restore-"))
        try:
            new_sql, old_sql = work_dir / "new.sql", work_dir / "before-restore.sql"
            await asyncio.to_thread(_extract_sql_sync, zip_path, new_sql)
            if was_running:
                await self.prov.stop(tenant)
            await self.tool.dump(tenant.db_name, old_sql)  # the way back if the restore fails half-way
            rolled_back = False
            error: str | None = None
            try:
                await self.prov.reset_database(tenant, password)
                await self.tool.load(tenant.db_name, tenant.db_user, password, new_sql)
            except (BackupError, ProvisionError) as exc:
                error = str(exc)
                try:
                    await self.prov.reset_database(tenant, password)
                    await self.tool.load(tenant.db_name, tenant.db_user, password, old_sql)
                    rolled_back = True
                except (BackupError, ProvisionError) as back:
                    error = f"{error}; going back also failed: {back}"
            if was_running:
                with contextlib.suppress(ProvisionError):
                    await self.prov.start(tenant)
            if error:
                return RestoreResult(False, error, rolled_back)
            return RestoreResult(True)
        except (BackupError, ProvisionError, OSError) as exc:
            if was_running:
                with contextlib.suppress(ProvisionError):
                    await self.prov.start(tenant)
            return RestoreResult(False, str(exc))
        finally:
            self.busy.discard(tenant_id)
            await asyncio.to_thread(shutil.rmtree, work_dir, True)


async def run_if_due(
    db: Database, service: BackupService, notifier: Notifier, admin_ids: list[int], *, now: datetime | None = None
) -> dict[str, list] | None:
    """Back every bot up once per Tehran day, from the configured hour on, when it is switched on."""
    from builder.services.report import TEHRAN, tehran_day

    now = now or utcnow()
    async with db.session() as session:
        cfg = await load_cfg(session)
    if not cfg.bool("backup_enabled"):
        return None
    local_hour = now.replace(tzinfo=UTC).astimezone(TEHRAN).hour
    if local_hour < min(23, max(0, cfg.int("backup_hour"))) or cfg.str(LAST_DAY_KEY) == tehran_day(now):
        return None
    outcome = await service.run_all(now=now, skip_newer_than=timedelta(hours=20))
    async with db.session() as session:
        await set_cfg(session, LAST_DAY_KEY, tehran_day(now))  # after the run: a cut-off run resumes on the next pass
        await session.commit()
    if outcome["failed"]:
        lines = [f"⚠️ <b>پشتیبان‌گیری شبانه</b>: {len(outcome['sent'])} ارسال شد، {len(outcome['failed'])} ناموفق."]
        lines += [f"• ربات #{tenant_id}: {esc(str(why)[:160])}" for tenant_id, why in outcome["failed"][:15]]
        for admin_id in admin_ids:
            await notifier.send(admin_id, "\n".join(lines))
    return outcome
