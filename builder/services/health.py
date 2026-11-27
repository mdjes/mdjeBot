"""The health card of every customer bot: a 0-100 score from users, 7-day sales, errors and resource use.

Admins see at a glance which bots are idle or in trouble (``collect_all`` returns the worst first) and can warn
the owner with one tap (``owner_warning``). The score is the sum of four parts, each of which the card draws:

* users (30)     how many members the bot has, and how many joined this week
* sales (30)     services sold in the last 7 days (a top-up without a purchase still earns a little)
* errors (20)    ERROR lines in the bot's log over the last 24 hours
* resources (20) memory (of the limit the bot runs with) and CPU right now

A bot that is down is capped low whatever else it did, and a bot younger than three days is not judged on users
and sales yet.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from builder.db import Database
from builder.models import Tenant, TenantStatus, utcnow
from builder.services.provisioner import ProvisionError
from builder.settings import Settings
from builder.util import esc

WEEK = 7 * 24 * 3600
NEW_BOT_DAYS = 3
GOOD, WARN = 70, 40  # score thresholds of the green / yellow band

# tag -> (label, advice for the owner). The label is also drawn on the card.
TAGS: dict[str, tuple[str, str]] = {
    "down": ("ربات روشن نیست", "ربات در حال حاضر پاسخ نمی‌دهد؛ مدیریت در حال بررسی است و لازم نیست کاری انجام دهید."),
    "stagnant": (
        "راکد",
        "هفتهٔ گذشته نه کاربر جدیدی آمده و نه فروشی ثبت شده. تعرفه‌ها و قیمت‌ها را مرور کنید، "
        "لینک ربات را در کانال و گروه‌ها منتشر کنید و یک پیام تبلیغی برای کاربران قبلی بفرستید.",
    ),
    "no_users": ("بدون کاربر", "هنوز کاربری وارد ربات نشده؛ لینک ربات را منتشر کنید."),
    "errors": (
        "خطای زیاد",
        "ربات در ۲۴ ساعت اخیر خطاهای زیادی ثبت کرده. اتصال پنل و درگاه پرداخت را بررسی کنید؛ "
        "اگر ادامه داشت به پشتیبانی خبر بدهید.",
    ),
    "memory": (
        "رم تقریباً پر",
        "مصرف رم ربات به سقفش رسیده؛ برای جلوگیری از توقف، از مدیریت بخواهید سقف رم را بالا ببرد.",
    ),
    "cpu": ("فشار پردازنده", "پردازندهٔ ربات زیر فشار است؛ اگر کاربران زیادی دارید با مدیریت هماهنگ کنید."),
    "expiring": ("اشتراک رو به پایان", "اشتراک ربات تا چند روز دیگر تمام می‌شود؛ برای جلوگیری از توقف تمدید کنید."),
}


@dataclass(slots=True)
class BotStats:
    """What the bot's own database says (``None`` = could not be read, e.g. an older schema)."""

    users: int | None = None
    new_users: int | None = None
    services: int | None = None  # services sold in the last 7 days
    topups: int | None = None  # toman of approved top-ups in the last 7 days


@dataclass(slots=True)
class BotHealth:
    tenant_id: int
    label: str
    status: str
    container: str = "running"
    age_days: int = 0
    days_left: int | None = None
    last_backup: datetime | None = None
    stats: BotStats = field(default_factory=BotStats)
    errors: int | None = None
    cpu: float | None = None
    memory: float | None = None
    score: int = 0
    level: str = "good"
    parts: dict[str, int] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @property
    def users(self) -> int | None:
        return self.stats.users


def score(h: BotHealth) -> BotHealth:
    """Fill in ``parts``, ``score``, ``level`` and ``tags`` from the measurements."""
    s = h.stats
    young = h.age_days < NEW_BOT_DAYS

    if s.users is None:
        users_part = 15  # unknown: neither rewarded nor punished
    else:
        growth = min(10, s.new_users) if s.new_users is not None else 5
        users_part = round(min(20.0, s.users * 20 / 50) + growth)
    sales_part = 15 if s.services is None else max(min(30, s.services * 6), 12 if (s.topups or 0) > 0 else 0)
    if young:
        users_part, sales_part = max(users_part, 15), max(sales_part, 15)

    errors_part = 10 if h.errors is None else round(20 - min(20, h.errors / 2))
    memory_part = 6 if h.memory is None else round(12 if h.memory <= 80 else max(0.0, 12 - (h.memory - 80) * 0.6))
    cpu_part = 4 if h.cpu is None else round(8 if h.cpu <= 60 else max(0.0, 8 - (h.cpu - 60) * 0.2))

    h.parts = {"users": users_part, "sales": sales_part, "errors": errors_part, "resources": memory_part + cpu_part}
    total = sum(h.parts.values())
    if h.container != "running":
        total = min(total, 15)
    h.score = max(0, min(100, total))
    h.level = "good" if h.score >= GOOD else "warn" if h.score >= WARN else "bad"

    tags: list[str] = []
    if h.container != "running":
        tags.append("down")
    elif not young and s.users is not None and (s.services, s.new_users) == (0, 0) and not s.topups:
        tags.append("stagnant" if s.users > 0 else "no_users")
    if h.errors is not None and h.errors >= 20:
        tags.append("errors")
    if h.memory is not None and h.memory >= 90:
        tags.append("memory")
    if h.cpu is not None and h.cpu >= 90:
        tags.append("cpu")
    if h.days_left is not None and h.days_left <= 3:
        tags.append("expiring")
    h.tags = tags
    return h


def needs_attention(h: BotHealth) -> bool:
    return h.level != "good" or bool(h.tags)


def owner_warning(h: BotHealth) -> str:
    """The message the owner gets when an admin taps «warn the owner»."""
    lines = [f"🩺 <b>وضعیت سلامت ربات {esc(h.label)}</b>", f"امتیاز: <b>{h.score}</b> از ۱۰۰", ""]
    for tag in h.tags:
        label, advice = TAGS[tag]
        lines.append(f"• <b>{label}</b>: {advice}")
    if not h.tags:
        lines.append("مشکل مشخصی دیده نمی‌شود، اما امتیاز ربات پایین است؛ فروش و کاربران جدید را بیشتر کنید.")
    return "\n".join(lines)


# ── reading the bots ──────────────────────────────────────────────────────────


class StatsReader(Protocol):
    async def read(self, tenant: Tenant, since: int) -> BotStats: ...


class MariaDbStats:
    """Counts straight from the bot's own database (the builder holds the server's root account)."""

    def __init__(self, settings: Settings):
        s = settings
        self._url = f"mysql+asyncmy://root:{quote(s.mariadb_root_password, safe='')}@{s.mariadb_host}:{s.mariadb_port}"

    async def read(self, tenant: Tenant, since: int) -> BotStats:
        engine = create_async_engine(f"{self._url}/{tenant.db_name}", pool_pre_ping=False)
        stats = BotStats()
        queries = {
            "users": ("SELECT COUNT(*) FROM `user`", {}),
            "new_users": ("SELECT COUNT(*) FROM `user` WHERE time_s >= :since", {"since": since}),
            "services": ("SELECT COUNT(*) FROM services WHERE createtime >= :since", {"since": since}),
            "topups": (
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'approved' AND created_at >= :since",
                {"since": since},
            ),
        }
        try:
            for name, (sql, params) in queries.items():
                try:
                    async with engine.connect() as conn:
                        setattr(stats, name, int((await conn.execute(text(sql), params)).scalar() or 0))
                except Exception:  # a table or column this version of the bot does not have: leave it unknown
                    continue
        finally:
            await engine.dispose()
        return stats


class HealthService:
    def __init__(self, db: Database, prov, *, reader: StatsReader, concurrency: int = 6):
        self.db, self.prov, self.reader = db, prov, reader
        self._gate = asyncio.Semaphore(concurrency)

    @staticmethod
    def _label(tenant: Tenant) -> str:
        return f"@{tenant.bot_username}" if tenant.bot_username else f"ربات #{tenant.id}"

    async def measure(
        self, tenant: Tenant, docker_stats: dict[str, tuple[float, float]] | None = None, *, now: datetime | None = None
    ) -> BotHealth:
        now = now or utcnow()
        if docker_stats is None:
            docker_stats = await self.prov.container_stats()
        async with self._gate:
            container = await self.prov.status(tenant)
            h = BotHealth(
                tenant_id=tenant.id,
                label=self._label(tenant),
                status=tenant.status,
                container=container,
                age_days=max(0, (now - tenant.created_at).days),
                days_left=None if tenant.expires_at is None else max(0, (tenant.expires_at - now).days),
                last_backup=tenant.last_backup_at,
            )
            cpu_mem = docker_stats.get(tenant.container_name)
            if cpu_mem:
                h.cpu, h.memory = cpu_mem
            if container == "running":
                try:
                    h.errors = await self.prov.recent_errors(tenant)
                except ProvisionError:
                    h.errors = None
                if tenant.db_password_enc is not None:
                    h.stats = await self.reader.read(tenant, int(time.time()) - WEEK)
        return score(h)

    async def collect_all(self, *, now: datetime | None = None) -> list[BotHealth]:
        """Every running (or failed) bot, the sickest first."""
        from sqlalchemy import select

        async with self.db.session() as session:
            tenants = (
                (
                    await session.execute(
                        select(Tenant).where(Tenant.status.in_((TenantStatus.RUNNING, TenantStatus.ERROR)))
                    )
                )
                .scalars()
                .all()
            )
        docker_stats = await self.prov.container_stats()
        results = await asyncio.gather(*(self.measure(t, docker_stats, now=now) for t in tenants))
        return sorted(results, key=lambda h: (h.score, h.tenant_id))

    async def collect_one(self, tenant_id: int) -> BotHealth | None:
        async with self.db.session() as session:
            tenant = await session.get(Tenant, tenant_id)
        if tenant is None or tenant.status == TenantStatus.DELETED:
            return None
        return await self.measure(tenant)
