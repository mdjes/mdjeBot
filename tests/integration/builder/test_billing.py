from __future__ import annotations

from datetime import timedelta

import pytest

from builder.models import DiscountCode, PaymentKind, PaymentStatus, Tenant, TenantStatus, utcnow
from builder.services import billing
from tests.support.builder_env import make_plan


async def _user_and_plan(session, balance=0, price=100_000):
    user = await billing.get_or_create_user(session, 5, "Ali", "ali")
    user.balance = balance
    plan = make_plan(price=price)
    session.add(plan)
    await session.flush()
    return user, plan


async def _tenant(env, session, plan, **kw):
    return await billing.create_tenant(
        session,
        env.box,
        owner_id=5,
        bot_id=kw.pop("bot_id", 777),
        bot_username="shop_bot",
        token="1:" + "T" * 35,
        admin_ids=[5],
        plan_id=plan.id,
        **kw,
    )


async def test_adjust_balance_never_goes_negative(env):
    async with env.db.session() as s:
        user, _ = await _user_and_plan(s, balance=1000)
        assert await billing.adjust_balance(s, user.id, -1500) is False
        assert await billing.adjust_balance(s, user.id, -400) is True
        assert await billing.adjust_balance(s, user.id, 250) is True
        await s.refresh(user)
        assert user.balance == 850


def test_apply_discount_bounds():
    assert billing.apply_discount(100_000, 25) == 75_000
    assert billing.apply_discount(100_000, 100) == 0
    assert billing.apply_discount(100_000, 250) == 0
    assert billing.apply_discount(100_000, -5) == 100_000


async def test_discount_validity_and_limits(env):
    async with env.db.session() as s:
        s.add(DiscountCode(code="OFF10", percent=10, max_uses=1))
        s.add(DiscountCode(code="OLD", percent=10, expires_at=utcnow() - timedelta(days=1)))
        s.add(DiscountCode(code="OFF", percent=10, active=False))
        await s.flush()
        assert await billing.find_discount(s, "off10") is not None
        assert await billing.find_discount(s, "OLD") is None
        assert await billing.find_discount(s, "OFF") is None
        assert await billing.find_discount(s, "NOPE") is None

        assert await billing.consume_discount(s, "OFF10") is True
        assert await billing.consume_discount(s, "OFF10") is False  # max_uses reached
        assert await billing.find_discount(s, "OFF10") is None
        await billing.release_discount(s, "OFF10")
        assert await billing.find_discount(s, "OFF10") is not None


async def test_wallet_purchase_activates_order_and_charges_discounted_price(env):
    async with env.db.session() as s:
        user, plan = await _user_and_plan(s, balance=120_000)
        s.add(DiscountCode(code="HALF", percent=50))
        tenant = await _tenant(env, s, plan)
        payment, result = await billing.purchase_with_wallet(
            s, user_id=user.id, kind=PaymentKind.ORDER, plan=plan, tenant=tenant, discount_code="half"
        )
        await s.commit()
        await s.refresh(user)

        assert payment.amount == 50_000 and payment.status == PaymentStatus.APPROVED
        assert user.balance == 70_000
        assert result.needs_deploy and result.tenant_id == tenant.id
        assert tenant.expires_at > utcnow() + timedelta(days=29)


async def test_wallet_purchase_insufficient_balance_raises_and_keeps_discount_unused(env):
    async with env.db.session() as s:
        user, plan = await _user_and_plan(s, balance=10)
        s.add(DiscountCode(code="HALF", percent=50, max_uses=1))
        await s.commit()
        tenant = await _tenant(env, s, plan)
        with pytest.raises(billing.InsufficientBalance):
            await billing.purchase_with_wallet(
                s, user_id=user.id, kind=PaymentKind.ORDER, plan=plan, tenant=tenant, discount_code="HALF"
            )
        await s.rollback()
        # the whole attempt was rolled back: no tenant, discount untouched
        assert await billing.count_owner_tenants(s, 5) == 0
        assert (await s.get(DiscountCode, "HALF")).used == 0


async def test_wallet_purchase_rejects_unknown_discount(env):
    async with env.db.session() as s:
        user, plan = await _user_and_plan(s, balance=1_000_000)
        tenant = await _tenant(env, s, plan)
        with pytest.raises(billing.DiscountInvalid):
            await billing.purchase_with_wallet(
                s, user_id=user.id, kind=PaymentKind.ORDER, plan=plan, tenant=tenant, discount_code="NOPE"
            )


async def test_card_payment_approval_is_applied_exactly_once(env):
    async with env.db.session() as s:
        user, plan = await _user_and_plan(s)
        tenant = await _tenant(env, s, plan)
        payment = await billing.create_payment(
            s,
            user_id=user.id,
            kind=PaymentKind.ORDER,
            amount=plan.price,
            method="card",
            plan_id=plan.id,
            tenant_id=tenant.id,
        )
        await s.commit()

        first = await billing.decide_payment(s, payment.id, approve=True, admin_id=100)
        second = await billing.decide_payment(s, payment.id, approve=True, admin_id=100)
        rejected_after = await billing.decide_payment(s, payment.id, approve=False, admin_id=100)
        await s.commit()

        assert first is not None and first[1].needs_deploy
        assert second is None and rejected_after is None
        await s.refresh(tenant)
        assert tenant.expires_at.date() == (utcnow() + timedelta(days=30)).date()  # extended once, not twice


async def test_rejected_order_cancels_pending_tenant_and_releases_discount(env):
    async with env.db.session() as s:
        user, plan = await _user_and_plan(s)
        s.add(DiscountCode(code="OFF10", percent=10, max_uses=1, used=1))
        tenant = await _tenant(env, s, plan)
        payment = await billing.create_payment(
            s,
            user_id=user.id,
            kind=PaymentKind.ORDER,
            amount=90_000,
            method="card",
            plan_id=plan.id,
            tenant_id=tenant.id,
            discount_code="OFF10",
        )
        await s.commit()

        await billing.decide_payment(s, payment.id, approve=False, admin_id=100)
        await s.commit()

        await s.refresh(tenant)
        assert tenant.status == TenantStatus.DELETED
        assert (await s.get(DiscountCode, "OFF10")).used == 0


async def test_topup_approval_credits_wallet(env):
    async with env.db.session() as s:
        user, _ = await _user_and_plan(s, balance=5)
        payment = await billing.create_payment(
            s, user_id=user.id, kind=PaymentKind.TOPUP, amount=200_000, method="card"
        )
        await s.commit()
        decided = await billing.decide_payment(s, payment.id, approve=True, admin_id=100)
        await s.commit()
        await s.refresh(user)
        assert decided[1].credited == 200_000 and user.balance == 200_005


async def test_payment_for_deleted_tenant_is_refunded_to_wallet(env):
    async with env.db.session() as s:
        user, plan = await _user_and_plan(s, balance=0)
        tenant = await _tenant(env, s, plan)
        tenant.status = TenantStatus.DELETED
        payment = await billing.create_payment(
            s,
            user_id=user.id,
            kind=PaymentKind.RENEW,
            amount=plan.price,
            method="card",
            plan_id=plan.id,
            tenant_id=tenant.id,
        )
        await s.commit()
        await billing.decide_payment(s, payment.id, approve=True, admin_id=100)
        await s.commit()
        await s.refresh(user)
        assert user.balance == plan.price


def test_extend_tenant_stacks_on_valid_expiry_and_restarts_from_now_when_expired():
    now = utcnow()
    live = Tenant(expires_at=now + timedelta(days=10))
    billing.extend_tenant(live, 30, now=now)
    assert live.expires_at == now + timedelta(days=40)

    dead = Tenant(expires_at=now - timedelta(days=5), reminded_for=now)
    billing.extend_tenant(dead, 30, plan_id=3, now=now)
    assert dead.expires_at == now + timedelta(days=30)
    assert dead.reminded_for is None and dead.plan_id == 3


async def test_allocate_port_skips_used_ports(env):
    async with env.db.session() as s:
        _, plan = await _user_and_plan(s)
        a = await _tenant(env, s, plan, bot_id=1)
        b = await _tenant(env, s, plan, bot_id=2)
        a.host_port, b.host_port = 7000, 7001
        await s.flush()
        assert await billing.allocate_port(s, 7000, 7005) == 7002
        b.status = TenantStatus.DELETED
        await s.flush()
        assert await billing.allocate_port(s, 7000, 7005) == 7001  # freed by deletion
        with pytest.raises(RuntimeError):
            await billing.allocate_port(s, 7000, 7000)


async def test_duplicate_bot_detection_ignores_deleted(env):
    async with env.db.session() as s:
        _, plan = await _user_and_plan(s)
        tenant = await _tenant(env, s, plan, bot_id=555)
        assert (await billing.find_live_tenant_by_bot(s, 555)).id == tenant.id
        tenant.status = TenantStatus.DELETED
        await s.flush()
        assert await billing.find_live_tenant_by_bot(s, 555) is None


async def test_collect_stats(env):
    async with env.db.session() as s:
        user, plan = await _user_and_plan(s, balance=40)
        tenant = await _tenant(env, s, plan)
        await billing.create_payment(
            s,
            user_id=user.id,
            kind=PaymentKind.ORDER,
            amount=100,
            method="card",
            tenant_id=tenant.id,
            status=PaymentStatus.APPROVED,
        )
        await billing.create_payment(s, user_id=user.id, kind=PaymentKind.TOPUP, amount=999, method="card")
        await s.commit()
        stats = await billing.collect_stats(s)
    assert stats["users"] == 1 and stats["wallet_total"] == 40
    assert stats["revenue_total"] == 100 and stats["pending_payments"] == 1
    assert stats["tenants_pending"] == 1
