"""Redis key builders for Telegram conversation state."""

from __future__ import annotations

import hashlib
from functools import lru_cache

from config import BOT_TOKEN, REDIS_NAMESPACE_PREFIX


@lru_cache(maxsize=1)
def get_redis_namespace() -> str:
    """Return a bot-specific Redis namespace prefix."""
    prefix = REDIS_NAMESPACE_PREFIX.strip()
    if prefix:
        return prefix.rstrip(":")
    token_hash = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:16]
    return f"bot:{token_hash}"


def build_state_key(user_id: int) -> str:
    """Hash key for temporary user conversation state."""
    return f"{get_redis_namespace()}:user:{user_id}"


def build_lock_key(user_id: int, lock_name: str) -> str:
    """String key for a temporary per-user lock."""
    return f"{get_redis_namespace()}:lock:{user_id}:{lock_name}"


def build_cache_key(key: str) -> str:
    """String key for optional short-lived app cache (not source of truth)."""
    return f"{get_redis_namespace()}:cache:{key}"


def build_direct_pay_user_key(user_id: int) -> str:
    """Pending direct-pay purchase for a user (survives clear_user)."""
    return f"{get_redis_namespace()}:direct_pay:user:{user_id}"


def build_direct_pay_claim_key(user_id: int) -> str:
    """NX lock key so only one worker may fulfill a user's direct-pay record."""
    return f"{get_redis_namespace()}:direct_pay:user:{user_id}:claim"


def build_direct_pay_tx_key(transaction_id: int) -> str:
    """Link manual transaction id to a direct-pay user."""
    return f"{get_redis_namespace()}:direct_pay:tx:{transaction_id}"


def build_direct_pay_crypto_key(order_id: int) -> str:
    """Link crypto order id to a direct-pay user."""
    return f"{get_redis_namespace()}:direct_pay:crypto:{order_id}"


def build_card_load_key(card_id: int, day: str) -> str:
    """Hash of what customers hold on one card in one Tehran day (``user id`` -> ``amount:expires``)."""
    return f"{get_redis_namespace()}:card_load:{card_id}:{day}"


def build_card_pick_key(user_id: int) -> str:
    """The card shown to a customer for the payment in progress (``card id:day``)."""
    return f"{get_redis_namespace()}:card_pick:{user_id}"


def build_weekly_report_key(suffix: str) -> str:
    """Redis key of the weekly usage report (``optout``: customers who turned it off, ``sent:<week>``: done)."""
    return f"{get_redis_namespace()}:weekly_report:{suffix}"
