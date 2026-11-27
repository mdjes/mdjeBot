"""The package lines a card receipt shows, and that they survive the admin's approve / reject."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.db.crud import manual_auto_approve_rules as rules
from app.services.billing import purchase_lines

PENDING = {"amount": 69_000, "payload": {"product_label": "کانفیگ VPN", "volume": "30 گیگ", "custom_days": 30}}


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def counts(self, user_id: int) -> dict:
        return {("manual", "approved"): 1}

    async def get_entity(user_id: int):
        raise RuntimeError("offline")

    monkeypatch.setattr(rules.TransactionCRUD, "count_user_transactions_by_method_status", counts)
    monkeypatch.setattr(rules.Mdje, "get_entity", get_entity)


def test_lines_written_on_a_message_are_found_again() -> None:
    lines = purchase_lines.describe_pending_purchase(PENDING)
    caption = f"💳 #کارت_به_کارت\n👤 user\n💰 **موجودی:** `0` تومان\n{lines}\n🛡️ **مبلغ وارد شده** `69,000` تومان"
    assert purchase_lines.purchase_lines_in(caption) == lines
    assert purchase_lines.purchase_lines_in(None) == ""
    assert purchase_lines.purchase_lines_in("no package here") == ""


def test_pending_order_wins_and_the_message_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def with_order(transaction_id: int):
        return PENDING

    async def without_order(transaction_id: int):
        return None

    async def broken(transaction_id: int):
        raise RuntimeError("redis down")

    old_text = "x\n📦 **حجم انتخابی:** 5 گیگ\ny"
    monkeypatch.setattr(purchase_lines.direct_pay_store, "get_pending_by_transaction_id", with_order)
    assert "30 گیگ" in asyncio.run(purchase_lines.purchase_lines_for_transaction(1, old_text))
    monkeypatch.setattr(purchase_lines.direct_pay_store, "get_pending_by_transaction_id", without_order)
    assert asyncio.run(purchase_lines.purchase_lines_for_transaction(1, old_text)) == "📦 **حجم انتخابی:** 5 گیگ"
    monkeypatch.setattr(purchase_lines.direct_pay_store, "get_pending_by_transaction_id", broken)
    assert asyncio.run(purchase_lines.purchase_lines_for_transaction(1, old_text)) == "📦 **حجم انتخابی:** 5 گیگ"
    assert asyncio.run(purchase_lines.purchase_lines_for_transaction(1)) == ""


def test_the_reviewed_caption_shows_the_package_between_balance_and_amount(offline: None) -> None:
    caption = asyncio.run(
        rules.build_manual_card_log_caption(
            user_id=1,
            amount=69_000,
            header="✅ تراکنش تایید شد.",
            reduser=SimpleNamespace(amount=0, number=None),
            new_balance=69_000,
            total=69_000,
            purchase=purchase_lines.describe_pending_purchase(PENDING),
        )
    )
    lines = caption.splitlines()
    balance = next(i for i, line in enumerate(lines) if line.startswith("💰 **موجودی:**"))
    amount = next(i for i, line in enumerate(lines) if line.startswith("🛡️ **مبلغ وارد شده**"))
    package = [i for i, line in enumerate(lines) if "30 گیگ" in line]
    assert package and balance < package[0] < amount


def test_a_plain_top_up_has_no_package_lines(offline: None) -> None:
    caption = asyncio.run(
        rules.build_manual_card_log_caption(
            user_id=1, amount=1000, header="ok", reduser=SimpleNamespace(amount=5, number=None)
        )
    )
    assert "🛒" not in caption and "📦" not in caption
