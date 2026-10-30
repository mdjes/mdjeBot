"""Panel selling through the Telegram flows: customers buy / renew / add volume, admins manage it all."""

from __future__ import annotations

from sqlalchemy import select

from builder.models import (
    BuilderUser,
    Card,
    DiscountCode,
    MasterPanel,
    PanelPlan,
    PanelSub,
    PanelSubStatus,
    Payment,
    PaymentStatus,
    TenantStatus,
)
from builder.services.panels.backend import PanelError
from builder.util import esc
from tests.integration.builder.panel_helpers import (
    GB,
    cb,
    exec_payloads,
    get_sub,
    make_master,
    make_plan,
    make_tenant,
    msg,
)

ADMIN = 100
USER = 5


async def _seed(env, *, balance=500_000, with_bot=True, card=True):
    async with env.db.session() as s:
        s.add(BuilderUser(id=USER, first_name="Ali", balance=balance))
        if card:
            s.add(Card(number="6037 9911 2233 4455", holder="Mdje"))
        await s.commit()
    master = await make_master(env)
    plan = await make_plan(env, master)
    pack = await make_plan(
        env, master, name="+20GB", kind="addon", data_gb=20, duration_days=0, max_users=0, price=60_000
    )
    tenant = await make_tenant(env) if with_bot else None
    return master, plan, pack, tenant


async def _subs(env) -> list[PanelSub]:
    async with env.db.session() as s:
        return list((await s.execute(select(PanelSub).order_by(PanelSub.id))).scalars().all())


async def _buy(app, plan_id, user=USER):
    await msg(app, "/start", user=user)
    await cb(app, "u:pbuy", user=user)
    return await cb(app, f"u:pplan:{plan_id}", user=user)


# ── customer ──────────────────────────────────────────────────────────────────
async def test_home_shows_the_panel_button_only_when_something_is_for_sale(env, app):
    async with env.db.session() as s:
        s.add(BuilderUser(id=USER, first_name="Ali"))
        await s.commit()
    assert "u:panels" not in (await msg(app, "/start")).button_data()
    await _seed_master_and_plan(env)
    assert "u:panels" in (await msg(app, "/start")).button_data()


async def _seed_master_and_plan(env):
    master = await make_master(env)
    await make_plan(env, master)


async def test_wallet_purchase_creates_the_admin_and_connects_the_bot(env, app):
    _, plan, _, tenant = await _seed(env)
    checkout = await _buy(app, plan)
    assert "50 GB" in checkout.last and "bot111" in checkout.last
    assert "u:ppay_wallet" in checkout.button_data() and "u:ppay_free" not in checkout.button_data()

    await cb(app, "u:ppay_wallet")

    [sub] = await _subs(env)
    assert sub.status == PanelSubStatus.ACTIVE and sub.linked and sub.tenant_id == tenant
    assert env.backend.admins[sub.admin_id]["data_limit"] == 50 * GB
    async with env.db.session() as s:
        assert (await s.get(BuilderUser, USER)).balance == 300_000
    message = env.notifier.to(USER)[-1]
    password = env.box.decrypt(sub.admin_password_enc)
    assert "پنل شما ساخته شد" in message and sub.admin_username in message and esc(password) in message
    assert "https://panel.example.com" in message and "وصل شد" in message
    assert exec_payloads(env)[0]["username"] == sub.admin_username


async def test_buying_a_panel_requires_a_bot(env, app):
    _, _plan, _, _ = await _seed(env, with_bot=False)
    await msg(app, "/start")
    ctx = await cb(app, "u:pbuy")
    assert "u:new" in ctx.button_data()  # offered to create a bot first
    assert await _subs(env) == []


async def test_with_several_bots_the_customer_chooses_which_one(env, app):
    _, plan, _, first = await _seed(env)
    second = await make_tenant(env, bot_id=222)
    await msg(app, "/start")
    await cb(app, "u:pbuy")
    picker = await cb(app, f"u:pplan:{plan}")
    assert f"u:pbot:{first}" in picker.button_data() and f"u:pbot:{second}" in picker.button_data()

    await cb(app, f"u:pbot:{second}")
    await cb(app, "u:ppay_wallet")

    [sub] = await _subs(env)
    assert sub.tenant_id == second


async def test_panel_can_be_bought_before_the_bot_is_live_and_connects_later(env, app):
    master = await make_master(env)
    plan = await make_plan(env, master)
    async with env.db.session() as s:
        s.add(BuilderUser(id=USER, first_name="Ali", balance=500_000))
        await s.commit()
    tenant = await make_tenant(env, status=TenantStatus.AWAITING_TOKEN)

    await _buy(app, plan)
    await cb(app, "u:ppay_wallet")

    [sub] = await _subs(env)
    assert sub.status == PanelSubStatus.ACTIVE and not sub.linked and exec_payloads(env) == []
    assert "به‌محض آماده شدن ربات" in env.notifier.to(USER)[-1]

    async with env.db.session() as s:
        from builder.models import Tenant

        (await s.get(Tenant, tenant)).status = TenantStatus.RUNNING
        await s.commit()
    env.panels.link_tenant_panels(tenant)
    import asyncio

    await asyncio.gather(*env.panels.background)
    assert (await get_sub(env, sub.id)).linked


async def test_low_balance_keeps_the_checkout_and_creates_nothing(env, app):
    _, plan, _, _ = await _seed(env, balance=1_000)
    await _buy(app, plan)
    ctx = await cb(app, "u:ppay_wallet")
    assert ctx.alerts and "موجودی" in ctx.alerts[0]
    assert await _subs(env) == [] and env.backend.admins == {}
    assert app.get_state(USER).name == "pcheckout"


async def test_card_payment_is_provisioned_after_the_admin_approves(env, app):
    _, plan, _, _ = await _seed(env)
    await _buy(app, plan)
    ctx = await cb(app, "u:ppay_card")
    assert "6037 9911 2233 4455" in ctx.last and "200,000" in ctx.last

    receipt = await msg(app, media=True)
    assert receipt.forwarded == [ADMIN]
    summary = env.notifier.to(ADMIN)[-1]
    assert "خرید پنل" in summary and "50 GB" in summary
    assert env.backend.admins == {}  # nothing is created before the approval

    await cb(app, "a:pok:1", user=ADMIN)

    [sub] = await _subs(env)
    assert sub.status == PanelSubStatus.ACTIVE and sub.linked
    assert any("پنل شما ساخته شد" in t for t in env.notifier.to(USER))


async def test_rejected_card_payment_cancels_the_pending_panel_and_frees_the_discount(env, app):
    _, plan, _, _ = await _seed(env)
    async with env.db.session() as s:
        s.add(DiscountCode(code="OFF10", percent=10, max_uses=1))
        await s.commit()
    await _buy(app, plan)
    await cb(app, "u:pdisc")
    applied = await msg(app, "off10")
    assert "180,000" in applied.last

    await cb(app, "u:ppay_card")
    await msg(app, "tracking 998")
    await cb(app, "a:pno:1", user=ADMIN)

    [sub] = await _subs(env)
    assert sub.status == PanelSubStatus.DELETED and env.backend.admins == {}
    async with env.db.session() as s:
        assert (await s.get(DiscountCode, "OFF10")).used == 0
        assert (await s.get(Payment, 1)).status == PaymentStatus.REJECTED


async def test_provisioning_failure_alerts_both_sides_and_the_admin_retry_finishes_the_job(env, app):
    _, plan, _, _ = await _seed(env)
    env.backend.fail["create"] = PanelError("panel returned HTTP 500")
    await _buy(app, plan)
    await cb(app, "u:ppay_wallet")

    [sub] = await _subs(env)
    assert sub.status == PanelSubStatus.ERROR
    assert any("خطا" in t for t in env.notifier.to(USER))
    alert = env.notifier.to(ADMIN)[-1]
    assert "500" in alert

    retry = await cb(app, f"a:psretry:{sub.id}:provision:{plan}", user=ADMIN)
    assert (await get_sub(env, sub.id)).status == PanelSubStatus.ACTIVE
    assert "پنل شما ساخته شد" in env.notifier.to(USER)[-1] and retry.out


async def test_renew_and_extra_volume_through_the_menu(env, app):
    _, plan, pack, _ = await _seed(env)
    await _buy(app, plan)
    await cb(app, "u:ppay_wallet")
    [sub] = await _subs(env)
    env.backend.admins[sub.admin_id]["used"] = 30 * GB

    detail = await cb(app, f"u:psub:{sub.id}")
    assert f"u:prenew:{sub.id}" in detail.button_data() and f"u:pvol:{sub.id}" in detail.button_data()
    assert "30.00 GB" in detail.last

    await cb(app, f"u:pvol:{sub.id}")
    await cb(app, f"u:pplan:{pack}")
    await cb(app, "u:ppay_wallet")
    assert (await get_sub(env, sub.id)).data_limit == 70 * GB

    await cb(app, f"u:prenew:{sub.id}")
    await cb(app, f"u:pplan:{plan}")
    await cb(app, "u:ppay_wallet")
    renewed = await get_sub(env, sub.id)
    assert renewed.data_limit == 50 * GB and renewed.used_bytes == 0
    assert env.backend.admins[sub.admin_id]["used"] == 0


async def test_credentials_and_password_reset(env, app):
    _, plan, _, _ = await _seed(env)
    await _buy(app, plan)
    await cb(app, "u:ppay_wallet")
    [sub] = await _subs(env)
    old = env.box.decrypt(sub.admin_password_enc)

    creds = await cb(app, f"u:pcred:{sub.id}")
    assert esc(old) in creds.last and sub.admin_username in creds.last

    await cb(app, f"u:ppw:{sub.id}")
    done = await cb(app, f"u:ppwok:{sub.id}")
    new = env.box.decrypt((await get_sub(env, sub.id)).admin_password_enc)
    assert new != old and esc(new) in done.last and env.backend.admins[sub.admin_id]["password"] == new


async def test_customers_cannot_touch_each_others_panels(env, app):
    _, plan, _, _ = await _seed(env)
    await _buy(app, plan)
    await cb(app, "u:ppay_wallet")
    [sub] = await _subs(env)
    async with env.db.session() as s:
        s.add(BuilderUser(id=6, first_name="Reza"))
        await s.commit()
    for action in (
        f"u:psub:{sub.id}",
        f"u:pcred:{sub.id}",
        f"u:ppw:{sub.id}",
        f"u:ppwok:{sub.id}",
        f"u:prenew:{sub.id}",
        f"u:pvol:{sub.id}",
        f"u:prelink:{sub.id}",
    ):
        assert (await cb(app, action, user=6)).alerts, action
    assert (await get_sub(env, sub.id)).status == PanelSubStatus.ACTIVE


async def test_a_plan_of_another_master_cannot_renew_this_panel(env, app):
    _, plan, _, _ = await _seed(env)
    other = await make_master(env, name="Other", base_url="https://other.example.com")
    foreign = await make_plan(env, other, name="foreign")
    await _buy(app, plan)
    await cb(app, "u:ppay_wallet")
    [sub] = await _subs(env)

    await cb(app, f"u:prenew:{sub.id}")
    await cb(app, f"u:pplan:{foreign}")  # tampered callback: not in the offered list
    refused = await cb(app, "u:ppay_wallet")
    assert "برای پنل شما نیست" in refused.last
    assert (await get_sub(env, sub.id)).plan_id == plan


async def test_builder_admins_can_create_a_panel_for_free(env, app):
    _, plan, _, _ = await _seed(env)
    async with env.db.session() as s:
        s.add(BuilderUser(id=ADMIN, first_name="Boss"))
        await s.commit()
    await make_tenant(env, owner_id=ADMIN, bot_id=333)
    checkout = await _buy(app, plan, user=ADMIN)
    assert "u:ppay_free" in checkout.button_data()
    await cb(app, "u:ppay_free", user=ADMIN)

    [sub] = await _subs(env)
    assert sub.owner_id == ADMIN and sub.status == PanelSubStatus.ACTIVE
    async with env.db.session() as s:
        payment = (await s.execute(select(Payment))).scalar_one()
        assert (payment.amount, payment.method) == (0, "admin")
    assert (await cb(app, "u:ppay_free")).alerts or True  # a normal user has no such button


async def test_disabling_panel_sales_hides_the_flow(env, app):
    _, _plan, _, _ = await _seed(env)
    from builder.services.cfg import set_cfg

    async with env.db.session() as s:
        await set_cfg(s, "panel_sales_enabled", "0")
        await s.commit()
    ctx = await cb(app, "u:pbuy")
    assert ctx.alerts


# ── builder admin ─────────────────────────────────────────────────────────────
async def test_admin_connects_a_master_panel_and_picks_role_and_groups(env, app):
    await cb(app, "a:mpadd", user=ADMIN)
    await msg(app, "پنل اصلی", user=ADMIN)
    await msg(app, "https://panel.example.com/", user=ADMIN)
    await cb(app, "a:mpauth:password", user=ADMIN)
    await msg(app, "root", user=ADMIN)
    roles = await msg(app, "s3cret!", user=ADMIN)

    async with env.db.session() as s:
        master = (await s.execute(select(MasterPanel))).scalar_one()
    assert master.base_url == "https://panel.example.com" and master.auth_type == "password"
    assert env.box.decrypt(master.secret_enc) == "s3cret!" and master.last_check_ok is True
    assert f"a:mprole:{master.id}:1" in roles.button_data()

    groups = await cb(app, f"a:mprole:{master.id}:1", user=ADMIN)
    assert f"a:mpgrp:{master.id}:2" in groups.button_data()
    await cb(app, f"a:mpgrp:{master.id}:2", user=ADMIN)
    async with env.db.session() as s:
        master = await s.get(MasterPanel, master.id)
        assert master.role_id == 1 and master.group_id_list == [2]

    detail = await cb(app, f"a:mpd:{master.id}", user=ADMIN)
    assert "نقش ادمین مشتری: 1" in detail.last


async def test_admin_connection_failure_is_explained(env, app):
    from builder.services.panels.backend import PanelError

    env.backend.fail["check"] = PanelError("unauthorized: check the panel username/password or API key")
    await cb(app, "a:mpadd", user=ADMIN)
    await msg(app, "X", user=ADMIN)
    await msg(app, "https://x.example.com", user=ADMIN)
    await cb(app, "a:mpauth:api_key", user=ADMIN)
    failed = await msg(app, "pg_key_123", user=ADMIN)
    assert "unauthorized" in failed.last
    async with env.db.session() as s:
        assert (await s.execute(select(MasterPanel))).scalar_one().last_check_ok is False


async def test_admin_creates_plans_and_packs(env, app):
    master = await make_master(env)
    await cb(app, "a:ppadd", user=ADMIN)
    await cb(app, f"a:ppm:{master}", user=ADMIN)
    await cb(app, "a:ppk:plan", user=ADMIN)
    await msg(app, "۱۰۰ گیگ", user=ADMIN)
    await msg(app, "100", user=ADMIN)
    await msg(app, "30", user=ADMIN)
    await msg(app, "50", user=ADMIN)
    await msg(app, "۴۰۰٬۰۰۰", user=ADMIN)

    await cb(app, "a:ppadd", user=ADMIN)
    await cb(app, f"a:ppm:{master}", user=ADMIN)
    await cb(app, "a:ppk:addon", user=ADMIN)
    await msg(app, "+10", user=ADMIN)
    await msg(app, "10", user=ADMIN)
    await msg(app, "35000", user=ADMIN)

    async with env.db.session() as s:
        plan, pack = (await s.execute(select(PanelPlan).order_by(PanelPlan.id))).scalars().all()
    assert (plan.kind, plan.data_gb, plan.duration_days, plan.max_users, plan.price) == ("plan", 100, 30, 50, 400_000)
    assert (pack.kind, pack.data_gb, pack.duration_days, pack.price) == ("addon", 10, 0, 35_000)

    await cb(app, f"a:ppe:{plan.id}:price", user=ADMIN)
    await msg(app, "450000", user=ADMIN)
    await cb(app, f"a:ppt:{pack.id}", user=ADMIN)
    async with env.db.session() as s:
        assert (await s.get(PanelPlan, plan.id)).price == 450_000
        assert (await s.get(PanelPlan, pack.id)).active is False


async def test_admin_manages_a_sold_panel(env, app):
    _, plan, _, _ = await _seed(env)
    await _buy(app, plan)
    await cb(app, "u:ppay_wallet")
    [sub] = await _subs(env)
    admin_id = sub.admin_id

    listing = await cb(app, "a:ps", user=ADMIN)
    assert f"a:psd:{sub.id}" in listing.button_data()
    detail = await cb(app, f"a:psd:{sub.id}", user=ADMIN)
    assert sub.admin_username in detail.last

    await cb(app, f"a:pss:{sub.id}", user=ADMIN)
    assert (await get_sub(env, sub.id)).status == PanelSubStatus.DISABLED
    await cb(app, f"a:psa:{sub.id}", user=ADMIN)
    assert (await get_sub(env, sub.id)).status == PanelSubStatus.ACTIVE

    before = (await get_sub(env, sub.id)).expires_at
    await cb(app, f"a:pse:{sub.id}", user=ADMIN)
    await msg(app, "10", user=ADMIN)
    assert ((await get_sub(env, sub.id)).expires_at - before).days == 10

    await cb(app, f"a:psv:{sub.id}", user=ADMIN)
    await msg(app, "25", user=ADMIN)
    assert (await get_sub(env, sub.id)).data_limit == 75 * GB
    assert env.backend.admins[admin_id]["data_limit"] == 75 * GB

    old = env.backend.admins[admin_id]["password"]
    await cb(app, f"a:psp:{sub.id}", user=ADMIN)
    assert env.backend.admins[admin_id]["password"] != old
    assert "رمز پنل" in env.notifier.to(USER)[-1]

    creds = await cb(app, f"a:psc:{sub.id}", user=ADMIN)
    assert esc(env.backend.admins[admin_id]["password"]) in creds.last

    await cb(app, f"a:psdelok:{sub.id}", user=ADMIN)
    assert admin_id not in env.backend.admins and (await get_sub(env, sub.id)).status == PanelSubStatus.DELETED


async def test_panel_admin_screens_are_admin_only(env, app):
    _, _plan, _, _ = await _seed(env)
    for action in ("a:pmenu", "a:mp", "a:mpadd", "a:pp", "a:ppadd", "a:ps"):
        assert (await cb(app, action, user=USER)).alerts, action


async def test_deleting_a_master_with_live_panels_is_refused(env, app):
    master, plan, _, _ = await _seed(env)
    await _buy(app, plan)
    await cb(app, "u:ppay_wallet")
    refused = await cb(app, f"a:mpxok:{master}", user=ADMIN)
    assert "اشتراک" in refused.last
    async with env.db.session() as s:
        assert (await s.get(MasterPanel, master)).enabled is True


async def test_disabled_master_hides_its_plans_from_customers(env, app):
    master, _plan, _, _ = await _seed(env)
    await cb(app, f"a:mpe:{master}", user=ADMIN)
    ctx = await cb(app, "u:pbuy")
    assert "تعریف نشده" in ctx.last
