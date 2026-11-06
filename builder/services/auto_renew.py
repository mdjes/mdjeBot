"""Pay a renewal from the wallet when a subscription is about to end, for customers who turned that on."""

from __future__ import annotations

from builder.db import Database
from builder.models import PanelPlan, PanelSub, PanelSubStatus, PaymentKind, Plan, Tenant, TenantStatus
from builder.notify import Notifier
from builder.services import billing
from builder.services.panels.ops import PanelOps
from builder.ui import Btn
from builder.util import esc, fmt_date, fmt_money

RENEWED = "renewed"
NO_FUNDS = "no_funds"
SKIPPED = "skipped"  # not on, no plan to renew with, or the plan is no longer sold
FAILED = "failed"  # paid, but doing the work failed; the payment is refunded and auto-renew turned off

NO_FUNDS_NOTE = "\n\n⚠️ تمدید خودکار انجام نشد؛ موجودی کیف پول کافی نیست."


async def renew_tenant(db: Database, ops, notifier: Notifier, tenant_id: int) -> str:
    """Renew a bot with its current plan from the owner's wallet."""
    async with db.session() as session:
        tenant = await session.get(Tenant, tenant_id)
        if (
            tenant is None
            or not tenant.auto_renew
            or tenant.is_trial
            or tenant.plan_id is None
            or tenant.status not in (TenantStatus.RUNNING, TenantStatus.EXPIRED)
        ):
            return SKIPPED
        plan = await session.get(Plan, tenant.plan_id)
        if plan is None or not plan.active:
            return SKIPPED
        try:
            _, fulfillment = await billing.purchase_with_wallet(
                session, user_id=tenant.owner_id, kind=PaymentKind.RENEW, plan=plan, tenant=tenant
            )
        except billing.InsufficientBalance:
            await session.rollback()
            return NO_FUNDS
        await session.commit()
        owner, price, expires = tenant.owner_id, plan.price, tenant.expires_at
        label = f"@{tenant.bot_username}" if tenant.bot_username else f"#{tenant.id}"

    result = await ops.deploy(tenant_id) if fulfillment.needs_deploy else None
    if result is not None and not result.ok:
        await notifier.send(
            owner,
            f"⚠️ تمدید خودکار ربات {esc(label)} پرداخت شد اما راه‌اندازی دوباره با خطا روبه‌رو شد. مدیریت بررسی می‌کند.",
        )
        return RENEWED
    await notifier.send(
        owner,
        f"🔁 تمدید خودکار ربات {esc(label)} انجام شد.\n💸 {fmt_money(price)} از کیف پول کسر شد.\n"
        f"📅 اعتبار تا: {fmt_date(expires)}",
        [[Btn("🤖 ربات‌های من", "u:bots")]],
    )
    return RENEWED


async def renew_panel(db: Database, panels: PanelOps, notifier: Notifier, sub_id: int) -> str:
    """Renew a panel with its current plan from the owner's wallet."""
    async with db.session() as session:
        sub = await session.get(PanelSub, sub_id)
        if (
            sub is None
            or not sub.auto_renew
            or sub.plan_id is None
            or sub.status not in (PanelSubStatus.ACTIVE, PanelSubStatus.LIMITED)
        ):
            return SKIPPED
        plan = await session.get(PanelPlan, sub.plan_id)
        if plan is None or not plan.active or plan.kind != "plan" or plan.master_id != sub.master_id:
            return SKIPPED
        try:
            payment, _ = await billing.purchase_panel_with_wallet(
                session, user_id=sub.owner_id, kind=PaymentKind.PANEL_RENEW, plan=plan, sub=sub
            )
        except billing.InsufficientBalance:
            await session.rollback()
            return NO_FUNDS
        await session.commit()
        owner, price, plan_id, name = sub.owner_id, payment.amount, plan.id, sub.admin_username

    result = await panels.renew(sub_id, plan_id)
    if not result.ok:
        async with db.session() as session:
            await billing.adjust_balance(session, owner, price)
            row = await session.get(PanelSub, sub_id)
            row.auto_renew = False
            await session.commit()
        await notifier.send(
            owner,
            f"⚠️ تمدید خودکار پنل <code>{esc(name)}</code> انجام نشد و {fmt_money(price)} به کیف پول برگشت. "
            "تمدید خودکار این پنل خاموش شد؛ دستی تمدید کنید.",
            [[Btn("🧩 پنل‌های من", "u:panels")]],
        )
        return FAILED
    async with db.session() as session:
        expires = (await session.get(PanelSub, sub_id)).expires_at
    await notifier.send(
        owner,
        f"🔁 تمدید خودکار پنل <code>{esc(name)}</code> انجام شد.\n💸 {fmt_money(price)} از کیف پول کسر شد.\n"
        f"📅 اعتبار تا: {fmt_date(expires)}",
        [[Btn("🧩 پنل‌های من", "u:panels")]],
    )
    return RENEWED
