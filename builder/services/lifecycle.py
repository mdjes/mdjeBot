"""Subscription lifecycle: renewal reminders, expiry (stop) and cleanup (delete)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from builder.db import Database
from builder.models import Tenant, TenantStatus, utcnow
from builder.notify import Notifier
from builder.services.cfg import load_cfg
from builder.services.ops import TenantOps
from builder.ui import Btn
from builder.util import fmt_date


async def run_expiry_pass(
    db: Database, ops: TenantOps, notifier: Notifier, *, now: datetime | None = None
) -> dict[str, int]:
    """One sweep over all tenants. Safe to run repeatedly; every step is idempotent."""
    now = now or utcnow()
    counts = {"reminded": 0, "expired": 0, "deleted": 0}

    async with db.session() as session:
        cfg = await load_cfg(session)
        reminder_days = max(0, cfg.int("reminder_days"))
        grace_days = max(0, cfg.int("grace_days"))
        tenants = (
            (
                await session.execute(
                    select(Tenant).where(
                        Tenant.status.in_((TenantStatus.RUNNING, TenantStatus.EXPIRED)),
                        Tenant.expires_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    for tenant in tenants:
        expires_at = tenant.expires_at
        renew_button = [[Btn("🔄 تمدید", f"u:renew:{tenant.id}")]]
        label = f"@{tenant.bot_username}" if tenant.bot_username else f"#{tenant.id}"

        if tenant.status == TenantStatus.RUNNING and expires_at <= now:
            result = await ops.stop(tenant.id, status=TenantStatus.EXPIRED)
            if result.ok:
                counts["expired"] += 1
                await notifier.send(
                    tenant.owner_id,
                    f"⛔️ اشتراک ربات {label} در {fmt_date(expires_at)} منقضی شد و ربات متوقف شد.\n"
                    f"تا {grace_days} روز فرصت دارید تمدید کنید، پس از آن ربات و اطلاعاتش حذف می‌شود.",
                    renew_button,
                )
        elif (
            tenant.status == TenantStatus.RUNNING
            and reminder_days
            and expires_at - now <= timedelta(days=reminder_days)
        ):
            if tenant.reminded_for != expires_at:
                sent = await notifier.send(
                    tenant.owner_id,
                    f"🔔 اشتراک ربات {label} تا {fmt_date(expires_at)} معتبر است. برای جلوگیری از توقف، تمدید کنید.",
                    renew_button,
                )
                async with db.session() as session:
                    row = await session.get(Tenant, tenant.id)
                    if row is not None:
                        row.reminded_for = expires_at  # also when delivery failed: do not retry every pass
                        await session.commit()
                counts["reminded"] += int(sent)
        elif tenant.status == TenantStatus.EXPIRED and expires_at + timedelta(days=grace_days) <= now:
            result = await ops.delete(tenant.id)
            if result.ok:
                counts["deleted"] += 1
                await notifier.send(
                    tenant.owner_id, f"🗑 ربات {label} به دلیل عدم تمدید حذف شد. می‌توانید ربات جدید بسازید."
                )
    return counts


async def expire_stale_payments(db: Database, *, older_than_hours: int = 24, now: datetime | None = None) -> int:
    """Reject card payments that never got a receipt, freeing their pending tenant and discount code."""
    from builder.models import Payment, PaymentStatus
    from builder.services import billing

    cutoff = (now or utcnow()) - timedelta(hours=older_than_hours)
    async with db.session() as session:
        stale = (
            (
                await session.execute(
                    select(Payment.id).where(
                        Payment.status == PaymentStatus.PENDING,
                        Payment.created_at <= cutoff,
                        (Payment.note.is_(None)) | (Payment.note != "receipt"),
                    )
                )
            )
            .scalars()
            .all()
        )
        for payment_id in stale:
            await billing.decide_payment(session, payment_id, approve=False, admin_id=0)
        await session.commit()
    return len(stale)
