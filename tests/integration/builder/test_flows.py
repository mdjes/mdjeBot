"""End-to-end conversation flows through BuilderApp with fake Telegram, Docker and MariaDB."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from builder.models import (
    BuilderUser,
    Card,
    DiscountCode,
    Payment,
    PaymentStatus,
    Plan,
    Tenant,
    TenantStatus,
)
from builder.services.cfg import set_cfg
from tests.support.builder_env import FakeCtx

ADMIN = 100
USER = 5
TOKEN_A = "111111111:" + "A" * 35
TOKEN_B = "222222222:" + "B" * 35


async def seed(env, *, balance=0, price=100_000, card=True):
    async with env.db.session() as s:
        s.add(BuilderUser(id=USER, first_name="Ali", balance=balance))
        s.add(Plan(name="ماهانه", price=price, duration_days=30))
        if card:
            s.add(Card(number="6037 9911 2233 4455", holder="Mdje"))
        await s.commit()


async def cb(app, data, user=USER):
    ctx = FakeCtx(user, data=data)
    await app.on_callback(ctx)
    return ctx


async def msg(app, text="", user=USER, media=False):
    ctx = FakeCtx(user, text=text, media=media)
    await app.on_message(ctx)
    return ctx


async def reach_checkout(app, token=TOKEN_A):
    """/start → new bot → token → 'I am the admin' → first plan."""
    await msg(app, "/start")
    await cb(app, "u:new")
    ctx = await msg(app, token)
    assert ctx.deleted, "the message containing the token must be deleted"
    await cb(app, "u:new_self")
    return await cb(app, "u:plan:1")


async def tenants(env) -> list[Tenant]:
    async with env.db.session() as s:
        return list((await s.execute(select(Tenant).order_by(Tenant.id))).scalars().all())


async def test_wallet_purchase_end_to_end(env, app):
    await seed(env, balance=150_000)
    checkout = await reach_checkout(app)
    assert "u:pay_wallet" in checkout.button_data() and "u:pay_card" in checkout.button_data()

    await cb(app, "u:pay_wallet")

    [tenant] = await tenants(env)
    assert tenant.status == TenantStatus.RUNNING and tenant.bot_username == "bot111111111"
    assert tenant.admin_ids == str(USER)
    async with env.db.session() as s:
        assert (await s.get(BuilderUser, USER)).balance == 50_000
    ready = env.notifier.to(USER)[-1]
    assert "آماده است" in ready and "bot111111111" in ready
    assert "run" in env.runner.verbs()


async def test_wallet_purchase_with_low_balance_keeps_state_and_creates_nothing(env, app):
    await seed(env, balance=1_000)
    await reach_checkout(app)
    ctx = await cb(app, "u:pay_wallet")
    assert ctx.alerts and "موجودی" in ctx.alerts[0]
    assert await tenants(env) == []
    assert app.get_state(USER).name == "checkout"  # the user can still pick card payment


async def test_card_payment_approved_by_admin_deploys_and_notifies(env, app):
    await seed(env)
    await reach_checkout(app)
    ctx = await cb(app, "u:pay_card")
    assert "6037 9911 2233 4455" in ctx.last and "100,000" in ctx.last
    assert app.get_state(USER).name == "receipt"

    receipt = await msg(app, media=True)
    assert receipt.forwarded == [ADMIN]
    admin_msg = env.notifier.to(ADMIN)[-1]
    assert "پرداخت #1" in admin_msg

    decision = await cb(app, "a:pok:1", user=ADMIN)
    assert "تایید شد" in decision.last

    [tenant] = await tenants(env)
    assert tenant.status == TenantStatus.RUNNING and tenant.expires_at is not None
    assert any("پرداخت #1 شما تایید شد" in t for t in env.notifier.to(USER))
    assert any("آماده است" in t for t in env.notifier.to(USER))

    again = await cb(app, "a:pok:1", user=ADMIN)  # double click on the same button
    assert again.alerts and len(await tenants(env)) == 1


async def test_card_payment_rejected_frees_discount_and_cancels_pending_bot(env, app):
    await seed(env)
    async with env.db.session() as s:
        s.add(DiscountCode(code="OFF10", percent=10, max_uses=1))
        await s.commit()
    await reach_checkout(app)
    await cb(app, "u:disc")
    applied = await msg(app, "off10")
    assert "90,000" in applied.last

    await cb(app, "u:pay_card")
    await msg(app, "tracking 12345")
    async with env.db.session() as s:
        assert (await s.get(DiscountCode, "OFF10")).used == 1

    await cb(app, "a:pno:1", user=ADMIN)

    [tenant] = await tenants(env)
    assert tenant.status == TenantStatus.DELETED
    async with env.db.session() as s:
        assert (await s.get(DiscountCode, "OFF10")).used == 0
        assert (await s.get(Payment, 1)).status == PaymentStatus.REJECTED
    assert any("تایید نشد" in t for t in env.notifier.to(USER))


async def test_user_can_retry_same_bot_after_abandoned_order(env, app):
    await seed(env)
    await reach_checkout(app)
    await cb(app, "u:pay_card")  # waits for a receipt that never comes
    await reach_checkout(app)  # same token again must not be refused as a duplicate
    ctx = await cb(app, "u:pay_card")
    assert "6037" in ctx.last
    live = [t for t in await tenants(env) if t.status != TenantStatus.DELETED]
    assert len(live) == 1


async def test_token_validation_errors(env, app):
    await seed(env)
    await msg(app, "/start")
    await cb(app, "u:new")

    assert "قالب توکن" in (await msg(app, "not-a-token")).last
    assert "تأیید نشد" in (await msg(app, "999999999:" + "C" * 35)).last
    assert (await msg(app, "1:" + "D" * 35)).last  # builder's own token (id 1) is refused
    own = await msg(app, env.settings.bot_token if ":" in env.settings.bot_token else "1:" + "E" * 35)
    assert "ربات‌ساز" in own.last or "قالب" in own.last


async def test_duplicate_bot_from_another_user_is_refused(env, app):
    await seed(env, balance=500_000)
    await reach_checkout(app)
    await cb(app, "u:pay_wallet")

    async with env.db.session() as s:
        s.add(BuilderUser(id=6, first_name="Reza"))
        await s.commit()
    await msg(app, "/start", user=6)
    await cb(app, "u:new", user=6)
    refused = await msg(app, TOKEN_A, user=6)
    assert "قبلاً" in refused.last


async def test_admin_id_validation(env, app):
    await seed(env)
    await msg(app, "/start")
    await cb(app, "u:new")
    await msg(app, TOKEN_A)
    assert "معتبر نیست" in (await msg(app, "abc")).last
    assert "معتبر نیست" in (await msg(app, "12")).last
    picked = await msg(app, "۱۲۳۴۵۶۷۸, 987654321")  # Persian digits are accepted
    assert "تعرفه" in picked.last


async def test_trial_flow_runs_once(env, app):
    await seed(env)
    await msg(app, "/start")
    await cb(app, "u:trial")
    await msg(app, TOKEN_A)
    await cb(app, "u:new_self")

    [tenant] = await tenants(env)
    assert tenant.is_trial and tenant.status == TenantStatus.RUNNING and tenant.plan_id is None
    async with env.db.session() as s:
        assert (await s.get(BuilderUser, USER)).trial_used is True

    ctx = await cb(app, "u:trial")
    assert "در دسترس نیست" in ctx.last


async def test_renew_extends_running_bot_without_redeploy(env, app):
    await seed(env, balance=300_000)
    await reach_checkout(app)
    await cb(app, "u:pay_wallet")
    [before] = await tenants(env)
    env.runner.calls.clear()

    await cb(app, f"u:renew:{before.id}")
    await cb(app, "u:plan:1")
    await cb(app, "u:pay_wallet")

    [after] = await tenants(env)
    assert after.expires_at > before.expires_at and (after.expires_at - before.expires_at).days >= 29
    assert "run" not in env.runner.verbs()


async def test_renew_of_expired_bot_redeploys(env, app):
    await seed(env, balance=300_000)
    await reach_checkout(app)
    await cb(app, "u:pay_wallet")
    await env.ops.stop(1, status=TenantStatus.EXPIRED)
    env.runner.calls.clear()

    await cb(app, "u:renew:1")
    await cb(app, "u:plan:1")
    await cb(app, "u:pay_wallet")

    [tenant] = await tenants(env)
    assert tenant.status == TenantStatus.RUNNING and "run" in env.runner.verbs()


async def test_user_cannot_touch_someone_elses_bot(env, app):
    await seed(env, balance=500_000)
    await reach_checkout(app)
    await cb(app, "u:pay_wallet")

    async with env.db.session() as s:
        s.add(BuilderUser(id=6, first_name="Reza"))
        await s.commit()
    for action in ("u:bot:1", "u:renew:1", "u:chadm:1", "u:del:1", "u:delok:1"):
        ctx = await cb(app, action, user=6)
        assert ctx.alerts, action
    assert (await tenants(env))[0].status == TenantStatus.RUNNING


async def test_owner_deletes_bot_and_changes_admins(env, app):
    await seed(env, balance=500_000)
    await reach_checkout(app)
    await cb(app, "u:pay_wallet")

    await cb(app, "u:chadm:1")
    changed = await msg(app, "555555555,666666666")
    assert "به‌روزرسانی شد" in changed.last
    assert (await tenants(env))[0].admin_ids == "555555555,666666666"

    await cb(app, "u:delok:1")
    assert (await tenants(env))[0].status == TenantStatus.DELETED


async def test_topup_flow_credits_wallet_after_approval(env, app):
    await seed(env)
    await cb(app, "u:topup")
    assert "حداقل" in (await msg(app, "100")).last  # below the minimum
    ctx = await msg(app, "۲۰۰٬۰۰۰")
    assert "200,000" in ctx.last
    await msg(app, "receipt-123")

    await cb(app, "a:pok:1", user=ADMIN)

    async with env.db.session() as s:
        assert (await s.get(BuilderUser, USER)).balance == 200_000
    assert any("اضافه شد" in t for t in env.notifier.to(USER))


async def test_access_control_and_maintenance(env, app):
    await seed(env)
    denied = await cb(app, "a:home", user=USER)
    assert denied.alerts and not denied.out

    async with env.db.session() as s:
        await set_cfg(s, "maintenance", "1")
        await s.commit()
    blocked = await msg(app, "/start")
    assert "بروزرسانی" in blocked.last
    admin_ok = await msg(app, "/admin", user=ADMIN)
    assert "پنل مدیریت" in admin_ok.last

    async with env.db.session() as s:
        await set_cfg(s, "maintenance", "0")
        (await s.get(BuilderUser, USER)).banned = True
        await s.commit()
    assert "مسدود" in (await msg(app, "/start")).last


async def test_admin_manages_plans_cards_codes_and_settings(env, app):
    await seed(env, card=False)

    await cb(app, "a:planadd", user=ADMIN)
    await msg(app, "سه‌ماهه", user=ADMIN)
    await msg(app, "۴۰۰۰۰۰", user=ADMIN)
    done = await msg(app, "90", user=ADMIN)
    assert "سه‌ماهه" in done.last
    async with env.db.session() as s:
        plan = (await s.execute(select(Plan).where(Plan.name == "سه‌ماهه"))).scalar_one()
        assert (plan.price, plan.duration_days) == (400_000, 90)

    await cb(app, f"a:plane:{plan.id}:price", user=ADMIN)
    await msg(app, "350000", user=ADMIN)
    await cb(app, f"a:plant:{plan.id}", user=ADMIN)
    async with env.db.session() as s:
        plan = await s.get(Plan, plan.id)
        assert plan.price == 350_000 and plan.active is False

    await cb(app, "a:cardadd", user=ADMIN)
    assert "معتبر نیست" in (await msg(app, "123", user=ADMIN)).last
    await msg(app, "6037991122334455", user=ADMIN)
    await msg(app, "Mdje", user=ADMIN)
    async with env.db.session() as s:
        card = (await s.execute(select(Card))).scalar_one()
        assert card.number == "6037 9911 2233 4455" and card.holder == "Mdje"

    await cb(app, "a:codeadd", user=ADMIN)
    await msg(app, "off20", user=ADMIN)
    await msg(app, "20", user=ADMIN)
    await msg(app, "5", user=ADMIN)
    await msg(app, "0", user=ADMIN)
    async with env.db.session() as s:
        code = await s.get(DiscountCode, "OFF20")
        assert (code.percent, code.max_uses, code.expires_at) == (20, 5, None)

    await cb(app, "a:set:trial_enabled", user=ADMIN)
    await cb(app, "a:set:trial_days", user=ADMIN)
    await msg(app, "7", user=ADMIN)
    cfg = await app.cfg()
    assert cfg.bool("trial_enabled") is False and cfg.int("trial_days") == 7


async def test_admin_tenant_controls_and_user_management(env, app):
    await seed(env, balance=500_000)
    await reach_checkout(app)
    await cb(app, "u:pay_wallet")

    detail = await cb(app, "a:tenant:1", user=ADMIN)
    assert "bot111111111" in detail.last
    await cb(app, "a:tstop:1", user=ADMIN)
    assert (await tenants(env))[0].status == TenantStatus.STOPPED
    await cb(app, "a:tstart:1", user=ADMIN)
    assert (await tenants(env))[0].status == TenantStatus.RUNNING

    await cb(app, "a:text:1", user=ADMIN)
    await msg(app, "10", user=ADMIN)
    assert (await tenants(env))[0].expires_at is not None

    logs = await cb(app, "a:tlogs:1", user=ADMIN)
    assert "لاگ #1" in logs.last

    await cb(app, "a:users", user=ADMIN)
    profile = await msg(app, str(USER), user=ADMIN)
    assert "Ali" in profile.last
    await cb(app, f"a:ubal:{USER}", user=ADMIN)
    await msg(app, "-100000", user=ADMIN)
    async with env.db.session() as s:
        assert (await s.get(BuilderUser, USER)).balance == 500_000 - 100_000 - 100_000  # purchase + manual debit
    await cb(app, f"a:uban:{USER}", user=ADMIN)
    async with env.db.session() as s:
        assert (await s.get(BuilderUser, USER)).banned is True
    await cb(app, f"a:uban:{ADMIN}", user=ADMIN)  # admins cannot be banned


async def test_broadcast_reaches_unbanned_users(env, app):
    await seed(env)
    async with env.db.session() as s:
        s.add(BuilderUser(id=7, first_name="Banned", banned=True))
        await s.commit()
    await cb(app, "a:bc", user=ADMIN)
    await msg(app, "سلام <همه>", user=ADMIN)
    await cb(app, "a:bcok", user=ADMIN)
    await asyncio.gather(*app._background)

    assert env.notifier.to(USER) == ["سلام &lt;همه&gt;"]
    assert env.notifier.to(7) == []
    assert any("تمام شد" in t for t in env.notifier.to(ADMIN))


async def test_deploy_failure_reports_to_owner_and_admins(env, app):
    from builder.services.provisioner import CmdResult

    await seed(env, balance=500_000)
    env.runner.responses["run"] = CmdResult(125, "", "boom")
    await reach_checkout(app)
    await cb(app, "u:pay_wallet")

    [tenant] = await tenants(env)
    assert tenant.status == TenantStatus.ERROR
    assert any("خطا" in t for t in env.notifier.to(USER))
    assert any("boom" in t for t in env.notifier.to(ADMIN))

    env.runner.responses.pop("run")
    retry = await cb(app, "a:tdeploy:1", user=ADMIN)
    assert (await tenants(env))[0].status == TenantStatus.RUNNING and "دیپلوی شد" in retry.last


async def test_state_expires_and_cancel_resets(env, app):
    from datetime import timedelta

    from builder.models import utcnow

    await seed(env)
    await msg(app, "/start")
    await cb(app, "u:new")
    app.states[USER].updated = utcnow() - timedelta(hours=1)
    stray = await msg(app, TOKEN_A)  # state expired: the token is treated as a stray message
    assert "ساخت ربات جدید" in stray.last or "خوش آمدید" in stray.last
    assert app.get_state(USER) is None

    await cb(app, "u:new")
    await msg(app, "/cancel")
    assert app.get_state(USER) is None
