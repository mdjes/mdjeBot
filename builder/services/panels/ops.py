"""Selling panels: an admin account on a master Pasarguard panel, connected to the customer's bot.

Flow after a payment is approved:
  ``provision``  create the admin on the master panel (random password, plan traffic) and connect it
  ``link``       tell the customer's bot container to add that panel (``app.cli.panel_link``)
  ``renew`` / ``add_volume``   start a new monthly cycle / grant extra traffic
  ``suspend`` / ``activate`` / ``reset_password`` / ``delete``   admin tools
Every method returns a ``PanelResult`` instead of raising, so callers can report what happened.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from builder.db import Database
from builder.models import MasterPanel, PanelPlan, PanelSub, PanelSubStatus, Tenant, TenantStatus, utcnow
from builder.security import SecretBox
from builder.services.panels.backend import AdminInfo, PanelBackend, PanelError
from builder.services.panels.credentials import generate_password, generate_username
from builder.services.provisioner import ProvisionError


@dataclass(frozen=True)
class PanelResult:
    ok: bool
    error: str | None = None
    sub_id: int | None = None
    password: str | None = None  # only set by reset_password


class PanelOps:
    def __init__(
        self,
        db: Database,
        box: SecretBox,
        backend: PanelBackend,
        provisioner,
        *,
        link_attempts: int = 6,
        link_delay: float = 10.0,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.db = db
        self.box = box
        self.backend = backend
        self.prov = provisioner
        self.link_attempts = link_attempts
        self.link_delay = link_delay
        self.clock = clock
        self.background: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()

    # ── loading ───────────────────────────────────────────────────────────────
    async def _load(self, session, sub_id: int) -> tuple[PanelSub, MasterPanel, PanelPlan | None] | None:
        sub = await session.get(PanelSub, sub_id)
        if sub is None or sub.status == PanelSubStatus.DELETED:
            return None
        master = await session.get(MasterPanel, sub.master_id)
        plan = await session.get(PanelPlan, sub.plan_id) if sub.plan_id else None
        return (sub, master, plan) if master is not None else None

    async def _fail(self, sub_id: int, message: str, *, status: str | None = None) -> PanelResult:
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            if sub is not None:
                sub.last_error = message[:1500]
                if status:
                    sub.status = status
                await session.commit()
        return PanelResult(False, message, sub_id)

    async def refresh_usage(self, sub_id: int) -> None:
        """Pull the live usage from the master panel (best effort) so a screen never shows stale numbers."""
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            if loaded is None or loaded[0].admin_id is None:
                return
            sub, master, _ = loaded
            admin_id = int(sub.admin_id)
        try:
            info = await self.backend.get_admin(master, admin_id)
        except PanelError:
            return
        if info is None:
            return
        async with self.db.session() as session:
            row = await session.get(PanelSub, sub_id)
            row.used_bytes, row.last_sync_at = info.used_bytes, self.clock()
            await session.commit()

    # ── provisioning ──────────────────────────────────────────────────────────
    async def provision(self, sub_id: int) -> PanelResult:
        """Create the admin on the master panel and connect the customer's bot. Safe to call again after an error."""
        async with self._lock:
            async with self.db.session() as session:
                loaded = await self._load(session, sub_id)
                if loaded is None:
                    return PanelResult(False, "panel subscription not found", sub_id)
                sub, master, plan = loaded
                if sub.status == PanelSubStatus.ACTIVE and sub.admin_id:
                    return PanelResult(True, sub_id=sub_id)
                if plan is None:
                    return PanelResult(False, "the plan no longer exists", sub_id)
                if not master.enabled or master.role_id is None:
                    return PanelResult(False, "the master panel is disabled or has no role configured", sub_id)
                if not sub.admin_username:
                    sub.admin_username = generate_username(sub.owner_id)
                if sub.admin_password_enc:
                    password = self.box.decrypt(sub.admin_password_enc)
                else:
                    password = generate_password(username=sub.admin_username)
                    sub.admin_password_enc = self.box.encrypt(password)
                await session.commit()  # stored before the panel call, so a retry reuses the same credentials
                username, data_limit, max_users = sub.admin_username, plan.data_bytes, plan.max_users

            try:
                info = await self._create_or_adopt(master, username, password, data_limit, max_users, sub.owner_id)
            except PanelError as exc:
                return await self._fail(sub_id, str(exc), status=PanelSubStatus.ERROR)

            async with self.db.session() as session:
                sub = await session.get(PanelSub, sub_id)
                sub.admin_id = info.id
                sub.status = PanelSubStatus.ACTIVE
                sub.data_limit = data_limit
                sub.used_bytes = 0
                sub.expires_at = self.clock() + timedelta(days=max(1, plan.duration_days))
                sub.last_error = None
                sub.warned_usage = sub.warned_expiry = False
                await session.commit()

        await self.link(sub_id)
        return PanelResult(True, sub_id=sub_id)

    async def _create_or_adopt(
        self, master: MasterPanel, username: str, password: str, data_limit: int, max_users: int, owner_id: int
    ) -> AdminInfo:
        try:
            return await self.backend.create_admin(
                master,
                username=username,
                password=password,
                role_id=int(master.role_id),
                data_limit=data_limit,
                max_users=max_users,
                note=f"builder:{owner_id}",
            )
        except PanelError as exc:
            if "conflict" not in str(exc).lower():
                raise
        # An earlier attempt created the admin but the database update was lost: take it over.
        existing = await self.backend.find_admin(master, username)
        if existing is None:
            raise PanelError("the username already exists on the panel")
        return await self.backend.modify_admin(
            master, existing.id, password=password, data_limit=data_limit or None, status="active"
        )

    # ── connecting the customer's bot ─────────────────────────────────────────
    async def link(self, sub_id: int) -> PanelResult:
        """Add the panel to the customer's running bot. Not an error when the bot does not exist yet."""
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            if loaded is None:
                return PanelResult(False, "panel subscription not found", sub_id)
            sub, master, plan = loaded
            tenant = await session.get(Tenant, sub.tenant_id) if sub.tenant_id else None
            if sub.admin_id is None or not sub.admin_password_enc:
                return PanelResult(False, "the panel admin has not been created yet", sub_id)
            if tenant is None or tenant.status == TenantStatus.DELETED:
                return PanelResult(False, "no bot is attached to this panel", sub_id)
            if tenant.status != TenantStatus.RUNNING:
                return PanelResult(False, "the bot is not running yet; it is connected as soon as it starts", sub_id)
            payload = {
                "action": "link",
                "name": master.name,
                "base_url": master.base_url,
                "auth_type": "password",
                "username": sub.admin_username,
                "secret": self.box.decrypt(sub.admin_password_enc),
                "group_ids": master.group_id_list,
                "user_limit": plan.max_users if plan and plan.max_users else None,
            }
        try:
            answer = await self.prov.exec_json(tenant, "app.cli.panel_link", payload)
        except ProvisionError as exc:
            return await self._fail(sub_id, f"connecting the bot failed: {exc}")
        if not answer.get("ok"):
            return await self._fail(sub_id, f"connecting the bot failed: {answer.get('error')}")
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            sub.linked = True
            sub.last_error = None
            await session.commit()
        return PanelResult(True, sub_id=sub_id)

    async def _bot_toggle(self, sub_id: int, action: str) -> None:
        """Enable/disable/unlink the panel inside the customer's bot; failures never block the main operation."""
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            if loaded is None:
                return
            sub, master, _ = loaded
            tenant = await session.get(Tenant, sub.tenant_id) if sub.tenant_id else None
        if tenant is None or tenant.status != TenantStatus.RUNNING or not sub.linked:
            return
        with contextlib.suppress(ProvisionError):
            await self.prov.exec_json(
                tenant,
                "app.cli.panel_link",
                {"action": action, "base_url": master.base_url, "username": sub.admin_username},
            )

    def link_tenant_panels(self, tenant_id: int) -> None:
        """Right after a bot (re)deploy: connect its panels in the background, retrying while the bot starts."""
        task = asyncio.create_task(self._link_tenant_panels(tenant_id))
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def _link_tenant_panels(self, tenant_id: int) -> None:
        async with self.db.session() as session:
            ids = (
                (
                    await session.execute(
                        select(PanelSub.id).where(
                            PanelSub.tenant_id == tenant_id,
                            PanelSub.linked.is_(False),
                            PanelSub.status.in_((PanelSubStatus.ACTIVE, PanelSubStatus.LIMITED)),
                        )
                    )
                )
                .scalars()
                .all()
            )
        for sub_id in ids:
            for attempt in range(self.link_attempts):
                result = await self.link(sub_id)
                if result.ok:
                    break
                if attempt + 1 < self.link_attempts and self.link_delay:
                    await asyncio.sleep(self.link_delay)

    # ── subscription changes ──────────────────────────────────────────────────
    async def renew(self, sub_id: int, plan_id: int) -> PanelResult:
        """A new monthly cycle: fresh traffic, usage reset, expiry pushed out, admin re-enabled."""
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            plan = await session.get(PanelPlan, plan_id)
            if loaded is None or plan is None:
                return PanelResult(False, "panel subscription or plan not found", sub_id)
            sub, master, _ = loaded
            admin_id = sub.admin_id
        if admin_id is None:
            return await self.provision(sub_id)
        try:
            await self.backend.reset_usage(master, admin_id)
            await self.backend.modify_admin(
                master, admin_id, status="active", data_limit=plan.data_bytes, max_users=plan.max_users
            )
        except PanelError as exc:
            return await self._fail(sub_id, str(exc))
        now = self.clock()
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
            sub.expires_at = base + timedelta(days=max(1, plan.duration_days))
            sub.plan_id = plan.id
            sub.data_limit = plan.data_bytes
            sub.used_bytes = 0
            sub.status = PanelSubStatus.ACTIVE
            sub.warned_usage = sub.warned_expiry = False
            sub.last_error = None
            await session.commit()
        await self._bot_toggle(sub_id, "enable")
        return PanelResult(True, sub_id=sub_id)

    async def add_volume(self, sub_id: int, addon_plan_id: int) -> PanelResult:
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            addon = await session.get(PanelPlan, addon_plan_id)
            if loaded is None or addon is None:
                return PanelResult(False, "panel subscription or pack not found", sub_id)
            sub, master, _ = loaded
            admin_id, new_limit = sub.admin_id, int(sub.data_limit) + addon.data_bytes
            reenable = sub.status == PanelSubStatus.LIMITED
        if admin_id is None:
            return PanelResult(False, "the panel admin has not been created yet", sub_id)
        try:
            await self.backend.modify_admin(
                master, admin_id, data_limit=new_limit, status="active" if reenable else None
            )
        except PanelError as exc:
            return await self._fail(sub_id, str(exc))
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            sub.data_limit = new_limit
            sub.warned_usage = False
            if reenable:
                sub.status = PanelSubStatus.ACTIVE
            await session.commit()
        if reenable:
            await self._bot_toggle(sub_id, "enable")
        return PanelResult(True, sub_id=sub_id)

    async def extend(self, sub_id: int, days: int) -> PanelResult:
        """Admin tool: add days without a payment (does not re-enable an expired panel by itself)."""
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            if sub is None or sub.status == PanelSubStatus.DELETED:
                return PanelResult(False, "panel subscription not found", sub_id)
            now = self.clock()
            base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
            sub.expires_at = base + timedelta(days=days)
            sub.warned_expiry = False
            reactivate = sub.status == PanelSubStatus.EXPIRED
            await session.commit()
        return await self.activate(sub_id) if reactivate else PanelResult(True, sub_id=sub_id)

    async def grant_volume(self, sub_id: int, gb: int) -> PanelResult:
        """Admin tool: add traffic without a payment."""
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            if loaded is None:
                return PanelResult(False, "panel subscription not found", sub_id)
            sub, master, _ = loaded
            admin_id, new_limit = sub.admin_id, int(sub.data_limit) + int(gb) * 1024**3
            reenable = sub.status == PanelSubStatus.LIMITED
        if admin_id is None:
            return PanelResult(False, "the panel admin has not been created yet", sub_id)
        try:
            await self.backend.modify_admin(
                master, admin_id, data_limit=new_limit, status="active" if reenable else None
            )
        except PanelError as exc:
            return await self._fail(sub_id, str(exc))
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            sub.data_limit = new_limit
            sub.warned_usage = False
            if reenable:
                sub.status = PanelSubStatus.ACTIVE
            await session.commit()
        if reenable:
            await self._bot_toggle(sub_id, "enable")
        return PanelResult(True, sub_id=sub_id)

    async def suspend(self, sub_id: int, *, status: str = PanelSubStatus.DISABLED) -> PanelResult:
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            if loaded is None:
                return PanelResult(False, "panel subscription not found", sub_id)
            sub, master, _ = loaded
            admin_id = sub.admin_id
        if admin_id is not None:
            try:
                await self.backend.modify_admin(master, admin_id, status="disabled")
            except PanelError as exc:
                return await self._fail(sub_id, str(exc))
        await self._bot_toggle(sub_id, "disable")
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            sub.status = status
            await session.commit()
        return PanelResult(True, sub_id=sub_id)

    async def activate(self, sub_id: int) -> PanelResult:
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            if loaded is None:
                return PanelResult(False, "panel subscription not found", sub_id)
            sub, master, _ = loaded
            admin_id, expired = sub.admin_id, bool(sub.expires_at and sub.expires_at <= self.clock())
            limited = bool(sub.data_limit and sub.used_bytes >= sub.data_limit)
        if admin_id is None:
            return await self.provision(sub_id)
        if expired:
            return PanelResult(False, "the subscription has expired; renew it first", sub_id)
        if limited:
            return PanelResult(False, "the traffic is used up; add volume first", sub_id)
        try:
            await self.backend.modify_admin(master, admin_id, status="active")
        except PanelError as exc:
            return await self._fail(sub_id, str(exc))
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            sub.status = PanelSubStatus.ACTIVE
            await session.commit()
        await self._bot_toggle(sub_id, "enable")
        return PanelResult(True, sub_id=sub_id)

    async def reset_password(self, sub_id: int) -> PanelResult:
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            if loaded is None:
                return PanelResult(False, "panel subscription not found", sub_id)
            sub, master, _ = loaded
            admin_id, username = sub.admin_id, sub.admin_username
        if admin_id is None:
            return PanelResult(False, "the panel admin has not been created yet", sub_id)
        password = generate_password(username=username)
        try:
            await self.backend.modify_admin(master, admin_id, password=password)
        except PanelError as exc:
            return await self._fail(sub_id, str(exc))
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            sub.admin_password_enc = self.box.encrypt(password)
            sub.linked = False  # the bot still stores the old password until it is re-linked
            await session.commit()
        await self.link(sub_id)
        return PanelResult(True, sub_id=sub_id, password=password)

    async def delete(self, sub_id: int) -> PanelResult:
        """Remove the admin from the master panel (with its users) and the panel from the bot."""
        async with self.db.session() as session:
            loaded = await self._load(session, sub_id)
            if loaded is None:
                return PanelResult(False, "panel subscription not found", sub_id)
            sub, master, _ = loaded
            admin_id = sub.admin_id
        await self._bot_toggle(sub_id, "unlink")
        if admin_id is not None:
            try:
                await self.backend.remove_admin(master, admin_id)
            except PanelError as exc:
                return await self._fail(sub_id, str(exc))
        async with self.db.session() as session:
            sub = await session.get(PanelSub, sub_id)
            sub.status = PanelSubStatus.DELETED
            await session.commit()
        return PanelResult(True, sub_id=sub_id)
