"""The daily report to the builder admins: what happened today and what needs a look."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from builder.db import Database
from builder.models import (
    BuilderUser,
    PanelSub,
    PanelSubStatus,
    Payment,
    PaymentStatus,
    Tenant,
    TenantStatus,
    utcnow,
)
from builder.notify import Notifier
from builder.services.cfg import load_cfg, set_cfg
from builder.util import fmt_money

TEHRAN = ZoneInfo("Asia/Tehran")
LAST_DAY_KEY = "report_last_day"
SOON = timedelta(days=3)


@dataclass(slots=True)
class Report:
    day: str = ""
    new_users: int = 0
    new_bots: int = 0
    card_count: int = 0
    card_total: int = 0
    wallet_count: int = 0
    wallet_total: int = 0
    pending_receipts: int = 0
    running_bots: int = 0
    expired_bots: int = 0
    active_panels: int = 0
    expiring_bots: int = 0
    expiring_panels: int = 0


def day_start(now: datetime) -> datetime:
    """Midnight of the Tehran day containing ``now`` (naive UTC in, naive UTC out)."""
    local = now.replace(tzinfo=UTC).astimezone(TEHRAN)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC).replace(tzinfo=None)


def tehran_day(now: datetime) -> str:
    return now.replace(tzinfo=UTC).astimezone(TEHRAN).strftime("%Y-%m-%d")


async def collect(db: Database, now: datetime) -> Report:
    start = day_start(now)

    async def one(session, stmt) -> int:
        return int((await session.execute(stmt)).scalar_one() or 0)

    def paid(method: str, column):
        return select(column).where(
            Payment.status == PaymentStatus.APPROVED, Payment.method == method, Payment.decided_at >= start
        )

    report = Report(day=tehran_day(now))
    async with db.session() as session:
        report.new_users = await one(session, select(func.count(BuilderUser.id)).where(BuilderUser.created_at >= start))
        report.new_bots = await one(
            session,
            select(func.count(Tenant.id)).where(Tenant.created_at >= start, Tenant.status != TenantStatus.DELETED),
        )
        for method, count_field, total_field in (
            ("card", "card_count", "card_total"),
            ("wallet", "wallet_count", "wallet_total"),
        ):
            where = (Payment.status == PaymentStatus.APPROVED, Payment.method == method, Payment.decided_at >= start)
            setattr(report, count_field, await one(session, select(func.count(Payment.id)).where(*where)))
            setattr(
                report,
                total_field,
                await one(session, select(func.coalesce(func.sum(Payment.amount), 0)).where(*where)),
            )
        report.pending_receipts = await one(
            session,
            select(func.count(Payment.id)).where(Payment.status == PaymentStatus.PENDING, Payment.note == "receipt"),
        )
        report.running_bots = await one(
            session, select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.RUNNING)
        )
        report.expired_bots = await one(
            session, select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.EXPIRED)
        )
        report.expiring_bots = await one(
            session,
            select(func.count(Tenant.id)).where(
                Tenant.status == TenantStatus.RUNNING, Tenant.expires_at.is_not(None), Tenant.expires_at <= now + SOON
            ),
        )
        report.active_panels = await one(
            session, select(func.count(PanelSub.id)).where(PanelSub.status == PanelSubStatus.ACTIVE)
        )
        report.expiring_panels = await one(
            session,
            select(func.count(PanelSub.id)).where(
                PanelSub.status == PanelSubStatus.ACTIVE,
                PanelSub.expires_at.is_not(None),
                PanelSub.expires_at <= now + SOON,
            ),
        )
    return report


def render(report: Report) -> str:
    lines = [
        f"📊 <b>گزارش روزانهٔ ربات‌ساز</b> — {report.day}",
        "",
        f"👥 کاربر جدید: <b>{report.new_users}</b>",
        f"🤖 ربات جدید: <b>{report.new_bots}</b>",
        f"💳 کارت به کارت تأییدشده: <b>{fmt_money(report.card_total)}</b> ({report.card_count} پرداخت)",
        f"👛 خرید از کیف پول: <b>{fmt_money(report.wallet_total)}</b> ({report.wallet_count} خرید)",
        "",
        f"🟢 ربات فعال: {report.running_bots} · ⛔️ منقضی: {report.expired_bots}",
        f"🧩 پنل فعال: {report.active_panels}",
    ]
    attention = []
    if report.pending_receipts:
        attention.append(f"⏳ {report.pending_receipts} رسید منتظر بررسی است")
    if report.expiring_bots:
        attention.append(f"🔔 {report.expiring_bots} ربات تا ۳ روز دیگر منقضی می‌شود")
    if report.expiring_panels:
        attention.append(f"🔔 {report.expiring_panels} پنل تا ۳ روز دیگر منقضی می‌شود")
    return "\n".join([*lines, "", *attention]) if attention else "\n".join(lines)


async def run_if_due(db: Database, notifier: Notifier, admin_ids: list[int], *, now: datetime | None = None) -> bool:
    """Send the report once per Tehran day, from the configured hour on, when it is switched on."""
    now = now or utcnow()
    async with db.session() as session:
        cfg = await load_cfg(session)
    if not cfg.bool("daily_report_enabled"):
        return False
    local_hour = now.replace(tzinfo=UTC).astimezone(TEHRAN).hour
    if local_hour < min(23, max(0, cfg.int("daily_report_hour"))) or cfg.str(LAST_DAY_KEY) == tehran_day(now):
        return False
    async with db.session() as session:
        await set_cfg(session, LAST_DAY_KEY, tehran_day(now))  # first: a failing send must not repeat every pass
        await session.commit()
    text = render(await collect(db, now))
    for admin_id in admin_ids:
        await notifier.send(admin_id, text)
    return True
