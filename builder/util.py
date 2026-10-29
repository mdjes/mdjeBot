"""Small formatting and parsing helpers shared by the builder's handlers and jobs."""

from __future__ import annotations

import html
from datetime import datetime, timedelta

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TEHRAN_OFFSET = timedelta(hours=3, minutes=30)

MAX_ADMIN_IDS = 5


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def to_ascii_digits(text: str) -> str:
    """Persian/Arabic-Indic digits to ASCII, so users can type numbers on any keyboard."""
    return text.translate(_DIGITS)


def normalize_digits(text: str) -> str:
    """ASCII digits with thousands separators removed (for amounts like ۲۰۰٬۰۰۰)."""
    return to_ascii_digits(text).replace("٬", "").replace(",", "").replace("،", "")


def parse_int(text: str, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    cleaned = normalize_digits(text).strip().replace(" ", "")
    if not cleaned.lstrip("-").isdigit():
        return None
    value = int(cleaned)
    if minimum is not None and value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def parse_admin_ids(text: str) -> list[int] | None:
    """Telegram user IDs separated by comma/space/newline; ``None`` when anything is invalid."""
    cleaned = to_ascii_digits(text).replace("،", ",").replace("\n", ",").replace(" ", ",")
    parts = [p for p in cleaned.split(",") if p]
    if not parts or len(parts) > MAX_ADMIN_IDS:
        return None
    ids: list[int] = []
    for part in parts:
        if not part.isdigit() or not (5 <= len(part) <= 15) or int(part) <= 0:
            return None
        if int(part) not in ids:
            ids.append(int(part))
    return ids


def fmt_money(amount: int) -> str:
    return f"{amount:,} تومان"


def fmt_date(value: datetime | None) -> str:
    """UTC datetime shown in Tehran time (no DST since 2022)."""
    if value is None:
        return "—"
    return (value + _TEHRAN_OFFSET).strftime("%Y-%m-%d %H:%M")


def days_left(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    return max(0, (value - now).days)
