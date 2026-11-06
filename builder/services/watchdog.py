"""Bring back customer bots whose container died on its own, and tell the admins."""

from __future__ import annotations

from sqlalchemy import select

from builder.db import Database
from builder.models import Tenant, TenantStatus
from builder.notify import Notifier
from builder.ui import Btn
from builder.util import esc


class Watchdog:
    """A bot the database says is running but whose container is not gets started again.

    A container still starting up looks the same for a moment, so a bot must be seen down on
    ``misses_before_action`` passes in a row before anything is done, and the admins are warned once per
    incident: when it is back, or when starting it keeps failing.
    """

    def __init__(
        self,
        db: Database,
        ops,
        prov,
        notifier: Notifier,
        admin_ids: list[int],
        *,
        misses_before_action: int = 2,
        failures_before_alert: int = 3,
    ):
        self.db, self.ops, self.prov, self.notifier = db, ops, prov, notifier
        self.admin_ids = admin_ids
        self.misses_before_action = misses_before_action
        self.failures_before_alert = failures_before_alert
        self._misses: dict[int, int] = {}
        self._failures: dict[int, int] = {}

    async def _tell_admins(self, text: str, buttons=None) -> None:
        for admin_id in self.admin_ids:
            await self.notifier.send(admin_id, text, buttons)

    async def run_once(self) -> dict[str, int]:
        counts = {"checked": 0, "restarted": 0, "failed": 0}
        async with self.db.session() as session:
            tenants = (
                (await session.execute(select(Tenant).where(Tenant.status == TenantStatus.RUNNING))).scalars().all()
            )
        live = {tenant.id for tenant in tenants}
        for state in (self._misses, self._failures):
            for gone in set(state) - live:
                del state[gone]

        for tenant in tenants:
            counts["checked"] += 1
            if await self.prov.status(tenant) == "running":
                self._misses.pop(tenant.id, None)
                self._failures.pop(tenant.id, None)
                continue
            self._misses[tenant.id] = self._misses.get(tenant.id, 0) + 1
            if self._misses[tenant.id] < self.misses_before_action:
                continue

            label = esc(f"@{tenant.bot_username}" if tenant.bot_username else f"#{tenant.id}")
            result = await self.ops.start(tenant.id)
            if result.ok:
                counts["restarted"] += 1
                self._misses.pop(tenant.id, None)
                failures = self._failures.pop(tenant.id, 0)
                # An incident already reported as stuck is not announced a second time as a plain restart.
                if failures < self.failures_before_alert:
                    await self._tell_admins(f"🩺 ربات {label} متوقف شده بود و دوباره راه‌اندازی شد.")
                else:
                    await self._tell_admins(f"✅ ربات {label} دوباره بالا آمد.")
                continue

            counts["failed"] += 1
            self._failures[tenant.id] = self._failures.get(tenant.id, 0) + 1
            if self._failures[tenant.id] == self.failures_before_alert:
                await self._tell_admins(
                    f"🚨 ربات {label} از کار افتاده و راه‌اندازی خودکار جواب نمی‌دهد.\n"
                    f"<pre>{esc((result.error or '')[:600])}</pre>",
                    [[Btn("🔁 دیپلوی مجدد", f"a:tdeploy:{tenant.id}")]],
                )
        return counts
