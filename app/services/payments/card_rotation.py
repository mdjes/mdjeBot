"""Card-to-card rotation: a card can have a daily cap, and a customer is shown a card that still has room.

Banks stop a card that receives too much in a day, so a shop with several cards wants to spread the payments.
The admin gives each card a cap in toman per Tehran day (0 = no cap). When a customer is shown the card to pay
to, the amount is *held* on it for a while; the hold becomes permanent once the receipt arrives, and simply
expires if the customer never pays. A card is skipped once the amounts held on it would pass its cap.

Without any cap set nothing here runs: the old behaviour (the active card, or a random one) is untouched.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db.crud.settings import SettingsManager
from app.db.redis import get_redis
from app.logger import get_logger
from app.telegram.state.keys import build_card_load_key, build_card_pick_key

logger = get_logger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")
LIMITS_SETTING = "manual_card_limits"  # {"<card id>": toman per day}, kept in the payment settings
HOLD_SECONDS = 90 * 60  # a shown amount that is not followed by a receipt frees up after this long
_KEY_TTL = 2 * 24 * 3600


_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬،", "01234567890123456789,,")


def parse_toman(text: str | None) -> int | None:
    """An amount typed by the admin ("۵,۰۰۰,۰۰۰" or "5000000"); None when it is not a whole number."""
    cleaned = (text or "").translate(_DIGITS).replace(",", "").replace(" ", "")
    return int(cleaned) if cleaned.isdigit() else None


def tehran_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), TEHRAN).strftime("%Y-%m-%d")


def card_limits(settings: Any) -> dict[int, int]:
    """The daily cap of each card that has one."""
    raw = getattr(settings, LIMITS_SETTING, None) or {}
    limits: dict[int, int] = {}
    for key, value in raw.items():
        try:
            if int(value) > 0:
                limits[int(key)] = int(value)
        except TypeError, ValueError:
            continue
    return limits


def limit_for(settings: Any, card_id: int) -> int:
    return card_limits(settings).get(int(card_id), 0)


async def set_card_limit(card_id: int, amount: int) -> None:
    """Give a card a daily cap in toman (0 or less removes it)."""
    manager = SettingsManager()
    settings = await manager.get_settings()
    if settings is None:
        return
    limits = {str(key): int(value) for key, value in card_limits(settings).items()}
    if amount > 0:
        limits[str(int(card_id))] = int(amount)
    else:
        limits.pop(str(int(card_id)), None)
    await manager.update_setting(settings.id, **{LIMITS_SETTING: limits})


def masked(number: str) -> str:
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    return f"{digits[:4]} •••• {digits[-4:]}" if len(digits) >= 8 else str(number)


@dataclass(slots=True)
class Pick:
    card: Any
    all_full: bool = False  # every capped card is over its limit; this is the one with the most room left


def pick_card(
    cards: list,
    limits: dict[int, int],
    loads: dict[int, int],
    *,
    amount: int,
    active_id: int | None,
    random_mode: bool,
    current_id: int | None = None,
    rng: random.Random | None = None,
) -> Pick | None:
    """The card to show for ``amount``. ``loads`` is what *other* customers hold on each card today."""
    if not cards:
        return None
    ordered = sorted(cards, key=lambda c: c.id)

    def fits(card) -> bool:
        cap = limits.get(card.id, 0)
        return cap == 0 or loads.get(card.id, 0) + amount <= cap

    eligible = [card for card in ordered if fits(card)]
    if not eligible:
        roomiest = max(ordered, key=lambda c: limits.get(c.id, 0) - loads.get(c.id, 0))
        return Pick(roomiest, all_full=True)
    for card in eligible:  # the same customer asking again keeps the card already shown
        if card.id == current_id:
            return Pick(card)
    if random_mode:
        return Pick((rng or random).choice(eligible))
    start = next((i for i, c in enumerate(ordered) if c.id == active_id), 0)
    for card in ordered[start:] + ordered[:start]:  # from the active card on, the first one with room
        if card in eligible:
            return Pick(card)
    return Pick(eligible[0])


# --- who holds what today ------------------------------------------------------------------------------


def _parse(raw: str) -> tuple[int, float]:
    amount, _, expires = str(raw).partition(":")
    return int(amount or 0), float(expires or 0)


async def _held(redis, card_id: int, day: str, now: float, skip_user: int | None = None) -> int:
    total = 0
    for user, raw in (await redis.hgetall(build_card_load_key(card_id, day))).items():
        if skip_user is not None and str(user) == str(skip_user):
            continue
        try:
            amount, expires = _parse(raw)
        except ValueError:
            continue
        if expires == 0 or expires > now:  # 0 = paid, otherwise still within the hold
            total += amount
    return total


async def usage_today(cards: list, now: float | None = None) -> dict[int, int]:
    """Toman held on each card in the current Tehran day (paid, plus holds that have not expired)."""
    now = now if now is not None else time.time()
    redis = await get_redis()
    if redis is None:
        return {}
    day = tehran_day(now)
    try:
        return {card.id: await _held(redis, card.id, day, now) for card in cards}
    except Exception as exc:
        logger.warning("card usage unavailable: %s", exc)
        return {}


async def choose_card(settings: Any, cards: list, *, user_id: int | None, amount: int, hold: bool = True):
    """The card for this customer, or None when no card is registered.

    ``hold=False`` only looks (the Mini App shows a card before the amount is known).
    """
    if not cards:
        return None
    limits = {cid: cap for cid, cap in card_limits(settings).items() if cid in {c.id for c in cards}}
    active = next((c for c in cards if c.active), None)
    random_mode = bool(settings.manual_card_random_mode)
    if not limits:  # no cap anywhere: exactly the behaviour before rotation existed
        return random.choice(cards) if random_mode else active

    redis = await get_redis()
    now = time.time()
    day = tehran_day(now)
    loads: dict[int, int] = {}
    current_id: int | None = None
    if redis is not None:
        try:
            for card in cards:
                loads[card.id] = await _held(redis, card.id, day, now, skip_user=user_id)
            if user_id is not None:
                held = await redis.get(build_card_pick_key(user_id))
                if held and held.partition(":")[2] == day:
                    current_id = int(held.partition(":")[0])
        except Exception as exc:
            logger.warning("card rotation without load data: %s", exc)
            loads, current_id = {}, None

    picked = pick_card(
        cards,
        limits,
        loads,
        amount=amount,
        active_id=active.id if active else None,
        random_mode=random_mode,
        current_id=current_id,
    )
    if picked is None:
        return None
    if picked.all_full:
        logger.warning("every capped card is over its daily limit; showing card %s anyway", picked.card.id)
    if hold and redis is not None and user_id is not None and amount > 0:
        await _hold(redis, picked.card.id, day, user_id, amount, now, previous=current_id)
    return picked.card


async def _hold(redis, card_id: int, day: str, user_id: int, amount: int, now: float, previous: int | None) -> None:
    try:
        if previous is not None and previous != card_id:
            await redis.hdel(build_card_load_key(previous, day), str(user_id))
        key = build_card_load_key(card_id, day)
        await redis.hset(key, str(user_id), f"{int(amount)}:{now + HOLD_SECONDS}")
        await redis.expire(key, _KEY_TTL)
        await redis.set(build_card_pick_key(user_id), f"{card_id}:{day}", ex=HOLD_SECONDS)
    except Exception as exc:
        logger.warning("could not hold card %s for user %s: %s", card_id, user_id, exc)


async def confirm_pick(user_id: int) -> None:
    """The receipt arrived: the amount shown to this customer now counts against the card for good."""
    redis = await get_redis()
    if redis is None:
        return
    try:
        held = await redis.get(build_card_pick_key(user_id))
        if not held:
            return
        card_id, _, day = held.partition(":")
        key = build_card_load_key(int(card_id), day)
        raw = await redis.hget(key, str(user_id))
        if raw:
            amount, _ = _parse(raw)
            await redis.hset(key, str(user_id), f"{amount}:0")
        await redis.delete(build_card_pick_key(user_id))
    except Exception as exc:
        logger.warning("could not confirm the card pick of user %s: %s", user_id, exc)
