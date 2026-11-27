"""The "what is being bought" lines of a manual-card receipt, kept on the admin's log message until the end.

The customer picks a package, then pays by card and sends a photo. The admin reviewing that photo has to see
which package it is for, and the message keeps showing it after the admin approves or rejects (the caption is
rebuilt then, and the pending record it came from may already be gone).
"""

from __future__ import annotations

from typing import Any

from app.services.billing import direct_pay_store
from app.utils.formatting.conversions import convert_storage

# Every line this module writes starts with one of these, so a rebuilt caption can carry the old ones over.
_PREFIXES = ("🛒 **خرید:**", "📦 **حجم انتخابی:**", "⏰ **مدت:**", "💳 **مبلغ سفارش:**")


def describe_pending_purchase(pending: dict[str, Any] | None) -> str:
    """What the customer is paying for, as lines for the receipt the admin reviews ('' for a plain top-up)."""
    if not pending:
        return ""
    payload = pending.get("payload") or {}
    label = str(payload.get("product_label") or "").strip()
    volume = str(payload.get("volume") or "").strip()
    if not volume and payload.get("gig"):  # a custom purchase keeps the number, not the wording
        try:
            volume = convert_storage(float(payload["gig"]))
        except TypeError, ValueError:
            volume = ""
    lines = []
    if label:
        lines.append(f"{_PREFIXES[0]} {label}")
    if volume:
        lines.append(f"{_PREFIXES[1]} {volume}")
    if payload.get("custom_days"):
        lines.append(f"{_PREFIXES[2]} {payload['custom_days']} روز")
    if pending.get("amount"):
        lines.append(f"{_PREFIXES[3]} `{int(pending['amount']):,}` تومان")
    return "\n".join(lines)


def purchase_lines_in(text: str | None) -> str:
    """The purchase lines already written on a log message, to put them on its rebuilt version."""
    return "\n".join(line for line in (text or "").splitlines() if line.startswith(_PREFIXES))


async def purchase_lines_for_transaction(transaction_id: int, existing_text: str | None = None) -> str:
    """The purchase lines for a receipt: from the pending order, else what the message already shows.

    Call it before the order is fulfilled or cancelled, because that clears the pending record.
    """
    try:
        pending = await direct_pay_store.get_pending_by_transaction_id(int(transaction_id))
    except Exception:  # Redis hiccup: the message text still has the lines
        pending = None
    return describe_pending_purchase(pending) or purchase_lines_in(existing_text)
