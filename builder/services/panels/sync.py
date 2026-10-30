"""Periodic metering of sold panels: usage warnings, volume exhaustion, expiry and bot re-linking."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from builder.db import Database
from builder.models import MasterPanel, PanelSub, PanelSubStatus, Tenant, TenantStatus, utcnow
from builder.notify import Notifier
from builder.services.cfg import load_cfg
from builder.services.panels.backend import PanelError
from builder.services.panels.ops import PanelOps
from builder.ui import Btn
from builder.util import fmt_bytes, fmt_date


def _percent(used: int, limit: int) -> int:
    return int(used * 100 / limit) if limit > 0 else 0


async def run_panel_sync(
    db: Database, ops: PanelOps, notifier: Notifier, *, now: datetime | None = None
) -> dict[str, int]:
    """One sweep. Idempotent: warnings fire once per cycle, state changes only on real transitions."""
    now = now or utcnow()
    counts = {"synced": 0, "warned": 0, "limited": 0, "expired": 0, "linked": 0, "errors": 0}

    async with db.session() as session:
        cfg = await load_cfg(session)
        warn_percent = min(100, max(1, cfg.int("panel_warn_percent")))
        expiry_days = max(0, cfg.int("panel_expiry_warn_days"))
        subs = (
            (
                await session.execute(
                    select(PanelSub).where(
                        PanelSub.status.in_((PanelSubStatus.ACTIVE, PanelSubStatus.LIMITED)),
                        PanelSub.admin_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    for sub in subs:
        async with db.session() as session:
            master = await session.get(MasterPanel, sub.master_id)
            tenant = await session.get(Tenant, sub.tenant_id) if sub.tenant_id else None
        if master is None:
            continue
        label = f"«{sub.admin_username}»"
        renew = [[Btn("🔄 تمدید", f"u:prenew:{sub.id}"), Btn("➕ حجم اضافه", f"u:pvol:{sub.id}")]]

        try:
            info = await ops.backend.get_admin(master, int(sub.admin_id))
        except PanelError:
            counts["errors"] += 1
            continue
        if info is None:
            await ops._fail(sub.id, "the admin no longer exists on the master panel", status=PanelSubStatus.ERROR)
            counts["errors"] += 1
            continue

        used, limit = info.used_bytes, int(sub.data_limit)
        async with db.session() as session:
            row = await session.get(PanelSub, sub.id)
            row.used_bytes = used
            row.last_sync_at = now
            await session.commit()
        counts["synced"] += 1

        expired = bool(sub.expires_at and sub.expires_at <= now)
        exhausted = bool(info.is_limited or (limit > 0 and used >= limit))

        if expired and sub.status in (PanelSubStatus.ACTIVE, PanelSubStatus.LIMITED):
            if (await ops.suspend(sub.id, status=PanelSubStatus.EXPIRED)).ok:
                counts["expired"] += 1
                await notifier.send(
                    sub.owner_id,
                    f"⛔️ مدت پنل {label} در {fmt_date(sub.expires_at)} تمام شد و پنل غیرفعال شد.\nبرای ادامه تمدید کنید.",
                    renew,
                )
            continue

        if exhausted and sub.status == PanelSubStatus.ACTIVE:
            if (await ops.suspend(sub.id, status=PanelSubStatus.LIMITED)).ok:
                counts["limited"] += 1
                await notifier.send(
                    sub.owner_id,
                    f"🚫 حجم پنل {label} تمام شد ({fmt_bytes(used)} از {fmt_bytes(limit)}) و پنل غیرفعال شد.\n"
                    "با خرید حجم اضافه یا تمدید، دوباره فعال می‌شود.",
                    renew,
                )
            continue

        if sub.status == PanelSubStatus.ACTIVE:
            if limit > 0 and _percent(used, limit) >= warn_percent and not sub.warned_usage:
                async with db.session() as session:
                    (await session.get(PanelSub, sub.id)).warned_usage = True
                    await session.commit()
                counts["warned"] += 1
                await notifier.send(
                    sub.owner_id,
                    f"📉 {_percent(used, limit)}٪ از حجم پنل {label} مصرف شده ({fmt_bytes(used)} از {fmt_bytes(limit)}).",
                    renew,
                )
            elif (
                expiry_days
                and sub.expires_at
                and sub.expires_at - now <= timedelta(days=expiry_days)
                and not sub.warned_expiry
            ):
                async with db.session() as session:
                    (await session.get(PanelSub, sub.id)).warned_expiry = True
                    await session.commit()
                counts["warned"] += 1
                await notifier.send(
                    sub.owner_id,
                    f"🔔 پنل {label} تا {fmt_date(sub.expires_at)} معتبر است. برای ادامه تمدید کنید.",
                    renew,
                )

        if (
            not sub.linked
            and tenant is not None
            and tenant.status == TenantStatus.RUNNING
            and (await ops.link(sub.id)).ok
        ):
            counts["linked"] += 1
    return counts
