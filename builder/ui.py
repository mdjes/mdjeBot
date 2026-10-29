"""Transport-neutral message buttons, converted to Telethon buttons only at the edge."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Btn:
    text: str
    data: str | None = None
    url: str | None = None


Buttons = list[list[Btn]]


def to_telethon_buttons(rows: Buttons):
    from telethon import Button

    return [
        [
            Button.url(b.text, b.url) if b.url else Button.inline(b.text, data=(b.data or "noop").encode("utf-8"))
            for b in row
        ]
        for row in rows
    ]


def chunk(buttons: list[Btn], per_row: int = 2) -> Buttons:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]
