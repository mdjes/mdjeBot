"""Plans, wallet, discounts, payments and tenant records. Pure database logic, no I/O.

Callers own the transaction: every function here only adds/updates rows in the given session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from builder.models import (
    BuilderUser,
    DiscountCode,
    MasterPanel,
    PanelPlan,
    PanelSub,
    PanelSubStatus,
    Payment,
    PaymentKind,
    PaymentStatus,
    Plan,
    Tenant,
    TenantStatus,
    utcnow,
)
from builder.security import SecretBox


class InsufficientBalance(Exception):
    pass


class DiscountInvalid(Exception):
    pass


# ── users & wallet ────────────────────────────────────────────────────────────
async def get_or_create_user(
    session: AsyncSession, user_id: int, first_name: str = "", username: str | None = None
) -> BuilderUser:
    user = await session.get(BuilderUser, user_id)
    if user is None:
        user = BuilderUser(id=user_id, first_name=first_name or "", username=username)
        session.add(user)
        await session.flush()
        return user
    if first_name and user.first_name != first_name:
        user.first_name = first_name
    if username != user.username:
        user.username = username
    return user


async def adjust_balance(session: AsyncSession, user_id: int, delta: int) -> bool:
    """Atomically add ``delta`` Toman to a wallet; a debit never takes the balance below zero."""
    stmt = update(BuilderUser).where(BuilderUser.id == user_id)
    if delta < 0:
        stmt = stmt.where(BuilderUser.balance >= -delta)
    result = await session.execute(stmt.values(balance=BuilderUser.balance + delta))
    return result.rowcount == 1


# ── plans & discounts ─────────────────────────────────────────────────────────
async def list_plans(session: AsyncSession, *, active_only: bool = True) -> list[Plan]:
    stmt = select(Plan).order_by(Plan.sort_order, Plan.id)
    if active_only:
        stmt = stmt.where(Plan.active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


def apply_discount(price: int, percent: int) -> int:
    percent = max(0, min(100, percent))
    return max(0, price - (price * percent) // 100)


async def find_discount(session: AsyncSession, code: str, *, now: datetime | None = None) -> DiscountCode | None:
    """The discount if it exists and is currently usable, else ``None``."""
    now = now or utcnow()
    row = await session.get(DiscountCode, code.strip().upper())
    if row is None or not row.active:
        return None
    if row.expires_at is not None and row.expires_at <= now:
        return None
    if row.max_uses and row.used >= row.max_uses:
        return None
    return row


async def consume_discount(session: AsyncSession, code: str) -> bool:
    stmt = (
        update(DiscountCode)
        .where(
            DiscountCode.code == code.strip().upper(),
            DiscountCode.active.is_(True),
            (DiscountCode.max_uses == 0) | (DiscountCode.used < DiscountCode.max_uses),
        )
        .values(used=DiscountCode.used + 1)
    )
    return (await session.execute(stmt)).rowcount == 1


async def release_discount(session: AsyncSession, code: str) -> None:
    await session.execute(
        update(DiscountCode)
        .where(DiscountCode.code == code.strip().upper(), DiscountCode.used > 0)
        .values(used=DiscountCode.used - 1)
    )


# ── tenants ───────────────────────────────────────────────────────────────────
async def create_tenant(
    session: AsyncSession,
    *,
    owner_id: int,
    plan_id: int | None = None,
    is_trial: bool = False,
    pending_days: int = 0,
    status: str = TenantStatus.PENDING,
) -> Tenant:
    """A tenant "slot": created when the user starts buying; the bot token arrives later."""
    tenant = Tenant(
        owner_id=owner_id,
        plan_id=plan_id,
        admin_ids=str(owner_id),
        status=status,
        is_trial=is_trial,
        pending_days=pending_days,
    )
    session.add(tenant)
    await session.flush()
    return tenant


def attach_token(
    tenant: Tenant, box: SecretBox, *, token: str, bot_id: int, bot_username: str, admin_ids: list[int]
) -> None:
    """Store the customer's bot token (encrypted) and admins on a paid slot."""
    tenant.token_enc = box.encrypt(token)
    tenant.bot_id = bot_id
    tenant.bot_username = bot_username
    tenant.admin_ids = ",".join(str(i) for i in admin_ids)


async def find_live_tenant_by_bot(session: AsyncSession, bot_id: int) -> Tenant | None:
    stmt = select(Tenant).where(Tenant.bot_id == bot_id, Tenant.status != TenantStatus.DELETED).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_owner_tenants(session: AsyncSession, owner_id: int) -> int:
    stmt = select(func.count(Tenant.id)).where(Tenant.owner_id == owner_id, Tenant.status != TenantStatus.DELETED)
    return int((await session.execute(stmt)).scalar_one())


async def list_owner_tenants(session: AsyncSession, owner_id: int) -> list[Tenant]:
    stmt = select(Tenant).where(Tenant.owner_id == owner_id, Tenant.status != TenantStatus.DELETED).order_by(Tenant.id)
    return list((await session.execute(stmt)).scalars().all())


async def allocate_port(session: AsyncSession, start: int, end: int) -> int:
    used = set(
        (
            await session.execute(
                select(Tenant.host_port).where(Tenant.host_port.is_not(None), Tenant.status != TenantStatus.DELETED)
            )
        )
        .scalars()
        .all()
    )
    for port in range(start, end + 1):
        if port not in used:
            return port
    raise RuntimeError("no free tenant port left in the configured range")


def extend_tenant(tenant: Tenant, days: int, *, plan_id: int | None = None, now: datetime | None = None) -> datetime:
    """Push the expiry ``days`` into the future (from the current expiry if still valid, else from now)."""
    now = now or utcnow()
    base = tenant.expires_at if tenant.expires_at and tenant.expires_at > now else now
    tenant.expires_at = base + timedelta(days=days)
    tenant.reminded_for = None
    if plan_id is not None:
        tenant.plan_id = plan_id
        tenant.is_trial = False
    return tenant.expires_at


# ── payments ──────────────────────────────────────────────────────────────────
async def create_payment(
    session: AsyncSession,
    *,
    user_id: int,
    kind: str,
    amount: int,
    method: str,
    plan_id: int | None = None,
    tenant_id: int | None = None,
    panel_plan_id: int | None = None,
    panel_sub_id: int | None = None,
    discount_code: str | None = None,
    status: str = PaymentStatus.PENDING,
    note: str | None = None,
) -> Payment:
    payment = Payment(
        user_id=user_id,
        kind=kind,
        amount=amount,
        method=method,
        plan_id=plan_id,
        tenant_id=tenant_id,
        panel_plan_id=panel_plan_id,
        panel_sub_id=panel_sub_id,
        discount_code=discount_code,
        status=status,
        note=note,
    )
    session.add(payment)
    await session.flush()
    return payment


@dataclass(frozen=True)
class Fulfillment:
    """What the caller still has to do after a payment was approved."""

    tenant_id: int | None = None
    needs_deploy: bool = False
    credited: int = 0
    awaiting_token: bool = False  # paid, but the owner has not sent the bot token yet
    panel_sub_id: int | None = None
    panel_plan_id: int | None = None
    panel_action: str | None = None  # provision | renew | volume — done by PanelOps after the payment


async def fulfill_payment(session: AsyncSession, payment: Payment) -> Fulfillment:
    """Apply an approved payment: credit the wallet, or activate/extend the tenant."""
    if payment.kind == PaymentKind.TOPUP:
        await adjust_balance(session, payment.user_id, payment.amount)
        return Fulfillment(credited=payment.amount)

    if payment.kind in PaymentKind.PANEL_KINDS:
        sub = await session.get(PanelSub, payment.panel_sub_id) if payment.panel_sub_id else None
        panel_plan = await session.get(PanelPlan, payment.panel_plan_id) if payment.panel_plan_id else None
        if sub is None or panel_plan is None or sub.status == PanelSubStatus.DELETED:
            await adjust_balance(session, payment.user_id, payment.amount)  # gone: refund instead of losing it
            return Fulfillment(credited=payment.amount)
        action = {
            PaymentKind.PANEL: "provision",
            PaymentKind.PANEL_RENEW: "renew",
            PaymentKind.PANEL_VOLUME: "volume",
        }[payment.kind]
        return Fulfillment(panel_sub_id=sub.id, panel_plan_id=panel_plan.id, panel_action=action)

    tenant = await session.get(Tenant, payment.tenant_id) if payment.tenant_id else None
    plan = await session.get(Plan, payment.plan_id) if payment.plan_id else None
    if tenant is None or plan is None or tenant.status == TenantStatus.DELETED:
        # Paid for something that no longer exists: give the money back instead of losing it.
        await adjust_balance(session, payment.user_id, payment.amount)
        return Fulfillment(credited=payment.amount)

    if tenant.token_enc is None:
        # No bot yet: bank the days; the clock starts once the bot is running (TenantOps.deploy).
        tenant.pending_days += plan.duration_days
        tenant.plan_id = plan.id
        tenant.is_trial = False
        tenant.status = TenantStatus.AWAITING_TOKEN
        return Fulfillment(tenant_id=tenant.id, awaiting_token=True)

    extend_tenant(tenant, plan.duration_days, plan_id=plan.id)
    needs_deploy = tenant.status in (TenantStatus.PENDING, TenantStatus.EXPIRED, TenantStatus.ERROR)
    return Fulfillment(tenant_id=tenant.id, needs_deploy=needs_deploy)


async def decide_payment(
    session: AsyncSession, payment_id: int, *, approve: bool, admin_id: int
) -> tuple[Payment, Fulfillment] | None:
    """Approve or reject a pending payment exactly once. ``None`` if it was already decided."""
    new_status = PaymentStatus.APPROVED if approve else PaymentStatus.REJECTED
    result = await session.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == PaymentStatus.PENDING)
        .values(status=new_status, decided_at=utcnow(), decided_by=admin_id)
    )
    if result.rowcount != 1:
        return None
    payment = await session.get(Payment, payment_id)
    await session.refresh(payment)
    if approve:
        return payment, await fulfill_payment(session, payment)

    if payment.discount_code:
        await release_discount(session, payment.discount_code)
    if payment.kind == PaymentKind.PANEL and payment.panel_sub_id:
        sub = await session.get(PanelSub, payment.panel_sub_id)
        if sub is not None and sub.status == PanelSubStatus.PENDING:
            sub.status = PanelSubStatus.DELETED
    if payment.kind == PaymentKind.ORDER and payment.tenant_id:
        tenant = await session.get(Tenant, payment.tenant_id)
        if tenant is not None and tenant.status == TenantStatus.PENDING:
            tenant.status = TenantStatus.DELETED
    return payment, Fulfillment()


async def charge_wallet(
    session: AsyncSession, user_id: int, price: int, discount_code: str | None
) -> tuple[int, str | None]:
    """Apply a discount code and debit the wallet. Raises (caller rolls back) on a bad code or low balance."""
    code = None
    if discount_code:
        discount = await find_discount(session, discount_code)
        if discount is None or not await consume_discount(session, discount.code):
            raise DiscountInvalid
        code = discount.code
        price = apply_discount(price, discount.percent)
    if price > 0 and not await adjust_balance(session, user_id, -price):
        raise InsufficientBalance
    return price, code


async def purchase_with_wallet(
    session: AsyncSession,
    *,
    user_id: int,
    kind: str,
    plan: Plan,
    tenant: Tenant,
    discount_code: str | None = None,
) -> tuple[Payment, Fulfillment]:
    """Pay an order/renewal from the wallet. Raises (caller must roll back) on a bad discount or low balance."""
    price, code = await charge_wallet(session, user_id, plan.price, discount_code)
    payment = await create_payment(
        session,
        user_id=user_id,
        kind=kind,
        amount=price,
        method="wallet",
        plan_id=plan.id,
        tenant_id=tenant.id,
        discount_code=code,
        status=PaymentStatus.APPROVED,
    )
    payment.decided_at = utcnow()
    return payment, await fulfill_payment(session, payment)


async def purchase_panel_with_wallet(
    session: AsyncSession,
    *,
    user_id: int,
    kind: str,
    plan: PanelPlan,
    sub: PanelSub,
    discount_code: str | None = None,
) -> tuple[Payment, Fulfillment]:
    """Pay a panel purchase / renewal / extra volume from the wallet (same rules as for bots)."""
    price, code = await charge_wallet(session, user_id, plan.price, discount_code)
    payment = await create_payment(
        session,
        user_id=user_id,
        kind=kind,
        amount=price,
        method="wallet",
        panel_plan_id=plan.id,
        panel_sub_id=sub.id,
        discount_code=code,
        status=PaymentStatus.APPROVED,
    )
    payment.decided_at = utcnow()
    return payment, await fulfill_payment(session, payment)


async def grant_free(
    session: AsyncSession, *, user_id: int, kind: str, plan: Plan, tenant: Tenant, admin_id: int
) -> tuple[Payment, Fulfillment]:
    """Builder admins can create/renew a bot without paying (recorded as a zero-amount admin payment)."""
    payment = await create_payment(
        session,
        user_id=user_id,
        kind=kind,
        amount=0,
        method="admin",
        plan_id=plan.id,
        tenant_id=tenant.id,
        status=PaymentStatus.APPROVED,
        note=f"free by admin {admin_id}",
    )
    payment.decided_at = utcnow()
    payment.decided_by = admin_id
    return payment, await fulfill_payment(session, payment)


# ── panels ────────────────────────────────────────────────────────────────────
async def list_panel_plans(
    session: AsyncSession, *, kind: str | None = None, active_only: bool = True, master_id: int | None = None
) -> list[PanelPlan]:
    """Plans of enabled master panels (so a disabled panel is never sold)."""
    stmt = (
        select(PanelPlan)
        .join(MasterPanel, MasterPanel.id == PanelPlan.master_id)
        .order_by(PanelPlan.sort_order, PanelPlan.id)
    )
    if active_only:
        stmt = stmt.where(PanelPlan.active.is_(True), MasterPanel.enabled.is_(True))
    if kind:
        stmt = stmt.where(PanelPlan.kind == kind)
    if master_id:
        stmt = stmt.where(PanelPlan.master_id == master_id)
    return list((await session.execute(stmt)).scalars().all())


async def create_panel_sub(session: AsyncSession, *, owner_id: int, plan: PanelPlan, tenant_id: int | None) -> PanelSub:
    """The subscription row exists before payment; the admin on the master panel is created once it is paid."""
    sub = PanelSub(
        owner_id=owner_id,
        tenant_id=tenant_id,
        master_id=plan.master_id,
        plan_id=plan.id,
        status=PanelSubStatus.PENDING,
    )
    session.add(sub)
    await session.flush()
    return sub


async def list_owner_panel_subs(session: AsyncSession, owner_id: int) -> list[PanelSub]:
    stmt = (
        select(PanelSub)
        .where(PanelSub.owner_id == owner_id, PanelSub.status.not_in((PanelSubStatus.DELETED, PanelSubStatus.PENDING)))
        .order_by(PanelSub.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_owner_panel_subs(session: AsyncSession, owner_id: int) -> int:
    stmt = select(func.count(PanelSub.id)).where(
        PanelSub.owner_id == owner_id, PanelSub.status.not_in((PanelSubStatus.DELETED,))
    )
    return int((await session.execute(stmt)).scalar_one())


# ── reporting ─────────────────────────────────────────────────────────────────
async def collect_stats(session: AsyncSession) -> dict[str, int]:
    async def count(stmt) -> int:
        return int((await session.execute(stmt)).scalar_one() or 0)

    stats = {
        "users": await count(select(func.count(BuilderUser.id))),
        "pending_payments": await count(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.PENDING)),
        "wallet_total": await count(select(func.coalesce(func.sum(BuilderUser.balance), 0))),
    }
    for status in TenantStatus.LIVE:
        stats[f"tenants_{status}"] = await count(select(func.count(Tenant.id)).where(Tenant.status == status))
    for status in PanelSubStatus.LIVE:
        stats[f"panels_{status}"] = await count(select(func.count(PanelSub.id)).where(PanelSub.status == status))
    approved = Payment.status == PaymentStatus.APPROVED
    stats["revenue_total"] = await count(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(approved, Payment.kind != PaymentKind.TOPUP)
    )
    stats["topups_total"] = await count(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(approved, Payment.kind == PaymentKind.TOPUP)
    )
    since = utcnow() - timedelta(days=30)
    stats["revenue_30d"] = await count(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            approved, Payment.kind != PaymentKind.TOPUP, Payment.created_at >= since
        )
    )
    return stats
