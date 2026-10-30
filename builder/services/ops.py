"""Tenant operations that combine the database, the provisioner and status bookkeeping."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass

from sqlalchemy import select

from builder.db import Database
from builder.models import Tenant, TenantStatus
from builder.security import SecretBox
from builder.services import billing
from builder.services.cfg import load_cfg
from builder.services.provisioner import ProvisionError
from builder.settings import Settings


@dataclass(frozen=True)
class OpResult:
    ok: bool
    error: str | None = None


class TenantOps:
    def __init__(self, db: Database, settings: Settings, box: SecretBox, provisioner):
        self.db = db
        self.settings = settings
        self.box = box
        self.prov = provisioner
        # Port allocation and `docker run` for the same port must not interleave.
        self._deploy_lock = asyncio.Lock()

    async def _load(self, session, tenant_id: int) -> Tenant | None:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None or tenant.status == TenantStatus.DELETED:
            return None
        return tenant

    async def deploy(self, tenant_id: int) -> OpResult:
        """Provision (or re-provision) the container and mark the tenant running, or ``error`` on failure."""
        async with self._deploy_lock:
            async with self.db.session() as session:
                tenant = await self._load(session, tenant_id)
                if tenant is None:
                    return OpResult(False, "tenant not found")
                if tenant.token_enc is None:
                    return OpResult(False, "the owner has not sent the bot token yet")
                cfg = await load_cfg(session)
                if tenant.host_port is None:
                    tenant.host_port = await billing.allocate_port(
                        session, self.settings.tenant_port_start, self.settings.tenant_port_end
                    )
                if tenant.db_password_enc is None:
                    tenant.db_password_enc = self.box.encrypt(secrets.token_hex(16))
                await session.commit()
                token = self.box.decrypt(tenant.token_enc)
                db_password = self.box.decrypt(tenant.db_password_enc)
                memory, cpus = cfg.str("tenant_memory"), cfg.str("tenant_cpus")

            try:
                await self.prov.deploy(tenant, token=token, db_password=db_password, memory=memory, cpus=cpus)
            except Exception as exc:
                await self._set_status(tenant_id, TenantStatus.ERROR, error=str(exc)[:1500])
                return OpResult(False, str(exc))
            await self._mark_running(tenant_id)
            return OpResult(True)

    async def _mark_running(self, tenant_id: int) -> None:
        """Running now: apply the days that were paid for while the bot did not exist yet."""
        async with self.db.session() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None or tenant.status == TenantStatus.DELETED:
                return
            tenant.status = TenantStatus.RUNNING
            tenant.last_error = None
            if tenant.pending_days > 0:
                billing.extend_tenant(tenant, tenant.pending_days)
                tenant.pending_days = 0
            await session.commit()

    async def _set_status(self, tenant_id: int, status: str, *, error: str | None = None) -> None:
        async with self.db.session() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is not None and tenant.status != TenantStatus.DELETED:
                tenant.status = status
                tenant.last_error = error
                await session.commit()

    async def _act(self, tenant_id: int, verb: str, status: str | None) -> OpResult:
        async with self.db.session() as session:
            tenant = await self._load(session, tenant_id)
        if tenant is None:
            return OpResult(False, "tenant not found")
        try:
            await getattr(self.prov, verb)(tenant)
        except ProvisionError as exc:
            return OpResult(False, str(exc))
        if status:
            await self._set_status(tenant_id, status)
        return OpResult(True)

    async def start(self, tenant_id: int) -> OpResult:
        return await self._act(tenant_id, "start", TenantStatus.RUNNING)

    async def stop(self, tenant_id: int, *, status: str = TenantStatus.STOPPED) -> OpResult:
        return await self._act(tenant_id, "stop", status)

    async def restart(self, tenant_id: int) -> OpResult:
        return await self._act(tenant_id, "restart", None)

    async def delete(self, tenant_id: int) -> OpResult:
        """Remove the container, database and files, and mark the tenant deleted."""
        async with self.db.session() as session:
            tenant = await self._load(session, tenant_id)
        if tenant is None:
            return OpResult(False, "tenant not found")
        try:
            await self.prov.remove(tenant, purge=True)
        except ProvisionError as exc:
            return OpResult(False, str(exc))
        await self._set_status(tenant_id, TenantStatus.DELETED)
        return OpResult(True)

    async def set_admins(self, tenant_id: int, admin_ids: list[int]) -> OpResult:
        async with self.db.session() as session:
            tenant = await self._load(session, tenant_id)
            if tenant is None:
                return OpResult(False, "tenant not found")
            tenant.admin_ids = ",".join(str(i) for i in admin_ids)
            await session.commit()
            token = self.box.decrypt(tenant.token_enc)
            password = self.box.decrypt(tenant.db_password_enc) if tenant.db_password_enc else None
        if password is None or tenant.status != TenantStatus.RUNNING:
            return OpResult(True)  # nothing deployed yet; the new list is used on the first deploy
        try:
            await self.prov.update_admins(tenant, token=token, db_password=password)
        except ProvisionError as exc:
            return OpResult(False, str(exc))
        return OpResult(True)

    async def logs(self, tenant_id: int, tail: int = 40) -> str:
        async with self.db.session() as session:
            tenant = await self._load(session, tenant_id)
        if tenant is None:
            return "tenant not found"
        return await self.prov.logs(tenant, tail=tail)

    async def update_all(self) -> tuple[int, int]:
        """Pull the newest image and recreate every running tenant. Returns (ok, failed)."""
        pulled = await self.prov.pull_image()
        if not pulled.ok:
            raise ProvisionError((pulled.err or pulled.out).strip() or "image pull failed")
        async with self.db.session() as session:
            ids = (
                (await session.execute(select(Tenant.id).where(Tenant.status == TenantStatus.RUNNING))).scalars().all()
            )
        ok = failed = 0
        for tenant_id in ids:
            result = await self.deploy(tenant_id)
            ok += result.ok
            failed += not result.ok
        return ok, failed
