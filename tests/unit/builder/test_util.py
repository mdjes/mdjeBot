from __future__ import annotations

from datetime import datetime

import pytest

from builder.ui import Btn, chunk, to_telethon_buttons
from builder.util import days_left, esc, fmt_date, fmt_money, normalize_digits, parse_admin_ids, parse_int


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("123456789", [123456789]),
        ("123456789,987654321", [123456789, 987654321]),
        ("۱۲۳۴۵۶۷۸۹ ۹۸۷۶۵۴۳۲۱", [123456789, 987654321]),
        ("123456789،987654321", [123456789, 987654321]),
        ("123456789\n123456789", [123456789]),  # duplicates collapse
    ],
)
def test_parse_admin_ids_accepts(text, expected):
    assert parse_admin_ids(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "12", "-123456789", "1234567890123456", "1,2,3,4,5,6", "123456789,x"])
def test_parse_admin_ids_rejects(text):
    assert parse_admin_ids(text) is None


def test_parse_int_handles_persian_digits_and_separators():
    assert parse_int("۲۰۰٬۰۰۰") == 200_000
    assert parse_int("1,500") == 1500
    assert parse_int("-20") == -20
    assert parse_int("abc") is None
    assert parse_int("5", minimum=10) is None
    assert parse_int("50", maximum=10) is None
    assert normalize_digits("۱٬۲۳۴") == "1234"


def test_formatting_helpers():
    assert fmt_money(1234567) == "1,234,567 تومان"
    assert fmt_date(None) == "—"
    assert fmt_date(datetime(2026, 1, 1, 0, 0)) == "2026-01-01 03:30"  # UTC → Tehran
    assert days_left(datetime(2026, 1, 11), datetime(2026, 1, 1)) == 10
    assert days_left(datetime(2025, 1, 1), datetime(2026, 1, 1)) == 0
    assert esc("<b>&") == "&lt;b&gt;&amp;"


def test_buttons_convert_to_telethon_and_chunk():
    rows = to_telethon_buttons([[Btn("A", "x:y"), Btn("B", url="https://t.me/x")]])
    assert rows[0][0].text == "A" and rows[0][0].to_dict()["type"]["data"] == b"x:y"
    assert rows[0][1].text == "B" and rows[0][1].to_dict()["type"]["url"] == "https://t.me/x"
    assert chunk([Btn(str(i), "d") for i in range(5)], 2) == [
        [Btn("0", "d"), Btn("1", "d")],
        [Btn("2", "d"), Btn("3", "d")],
        [Btn("4", "d")],
    ]
