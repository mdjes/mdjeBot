"""Draws the health cards as PNG pictures (one bot, or the whole fleet at a glance).

Pillow is the only dependency. Persian labels use ``rtl.visual`` and need a font that has the Arabic presentation
forms (Vazirmatn, DejaVu Sans, Tahoma, Segoe UI all do); when none is installed the card is drawn with English
labels instead of broken letters. Set ``BUILDER_FONT`` / ``BUILDER_FONT_BOLD`` to use a specific font file.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from builder.models import utcnow
from builder.services.health import BotHealth
from builder.services.rtl import visual
from builder.util import fmt_date

SS = 2  # draw at twice the size and shrink: Pillow does not anti-alias shapes by itself
WIDTH, HEIGHT = 1000, 520

BG = "#0b1220"
PANEL = "#111c33"
TRACK = "#22304d"
TEXT = "#e8edf7"
MUTED = "#8b9bb8"
LEVEL_COLOR = {"good": "#22c55e", "warn": "#f59e0b", "bad": "#ef4444"}

_ASSETS = Path(__file__).resolve().parents[2] / "app" / "assets" / "fonts"
_REGULAR = (
    os.environ.get("BUILDER_FONT", ""),
    str(_ASSETS / "Vazirmatn-Regular.ttf"),
    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Tahoma.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)
_BOLD = (
    os.environ.get("BUILDER_FONT_BOLD", ""),
    str(_ASSETS / "Vazirmatn-Bold.ttf"),
    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
)

FA = {
    "title": "کارت سلامت ربات",
    "overview": "سلامت ربات‌ها",
    "users": "کاربران",
    "sales": "فروش ۷ روز",
    "errors": "خطای ۲۴ ساعت",
    "resources": "منابع سرور",
    "good": "سالم",
    "warn": "نیازمند توجه",
    "bad": "ضعیف",
    "none": "بدون مشکل",
    "left": "روز مانده",
    "bots": "ربات",
    "no_bots": "ربات فعالی نیست",
    "down": "روشن نیست",
    "stagnant": "راکد",
    "no_users": "بدون کاربر",
    "errors_tag": "خطای زیاد",
    "memory": "رم تقریباً پر",
    "cpu": "فشار پردازنده",
    "expiring": "اشتراک رو به پایان",
}
EN = {
    "title": "Bot health",
    "overview": "Bots health",
    "users": "Users",
    "sales": "Sales 7d",
    "errors": "Errors 24h",
    "resources": "Resources",
    "good": "Healthy",
    "warn": "Needs attention",
    "bad": "Poor",
    "none": "No problems",
    "left": "days left",
    "bots": "bots",
    "no_bots": "No active bots",
    "down": "Down",
    "stagnant": "Idle",
    "no_users": "No users",
    "errors_tag": "Many errors",
    "memory": "Memory full",
    "cpu": "CPU busy",
    "expiring": "Expiring",
}
TAG_KEY = {"errors": "errors_tag"}


@lru_cache(maxsize=64)
def _font(size: int, bold: bool) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, bool]:
    """The font at ``size`` and whether it is a real file (so Persian can be drawn with it)."""
    for path in _BOLD if bold else _REGULAR:
        if path and os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size), True
            except OSError:
                continue
    return ImageFont.load_default(size), False


def persian_available() -> bool:
    return _font(20, False)[1]


@dataclass
class _Canvas:
    image: Image.Image
    draw: ImageDraw.ImageDraw
    fa: bool

    @classmethod
    def new(cls, width: int, height: int) -> _Canvas:
        image = Image.new("RGB", (width * SS, height * SS), BG)
        return cls(image, ImageDraw.Draw(image), persian_available())

    @property
    def words(self) -> dict[str, str]:
        return FA if self.fa else EN

    def _box(self, box):
        return [int(v * SS) for v in box]

    def rect(self, box, fill, radius: int = 0) -> None:
        if radius:
            self.draw.rounded_rectangle(self._box(box), radius=radius * SS, fill=fill)
        else:
            self.draw.rectangle(self._box(box), fill=fill)

    def circle(self, cx: float, cy: float, r: float, fill) -> None:
        self.draw.ellipse(self._box((cx - r, cy - r, cx + r, cy + r)), fill=fill)

    def text(self, xy, value: str, *, size: int, fill=TEXT, bold: bool = False, align: str = "left", rtl: bool = False):
        """Draw ``value`` with its top-left (``align="left"``), top-right (``"right"``) or top-center at ``xy``."""
        font, _ = _font(size * SS, bold)
        shown = visual(value) if rtl and self.fa else value
        anchor = {"left": "la", "right": "ra", "center": "ma"}[align]
        self.draw.text((xy[0] * SS, xy[1] * SS), shown, font=font, fill=fill, anchor=anchor)

    def bar(self, box, ratio: float, color, *, from_right: bool = True) -> None:
        x0, y0, x1, y1 = box
        radius = (y1 - y0) / 2
        self.rect(box, TRACK, int(radius))
        width = max(0.0, min(1.0, ratio)) * (x1 - x0)
        if width >= 1:
            filled = (x1 - width, y0, x1, y1) if from_right else (x0, y0, x0 + width, y1)
            self.rect(filled, color, int(radius))

    def gauge(self, cx: float, cy: float, radius: float, ratio: float, color, thickness: int = 26) -> None:
        box = self._box((cx - radius, cy - radius, cx + radius, cy + radius))
        start = 135
        self.draw.arc(box, start, start + 270, fill=TRACK, width=thickness * SS)
        if ratio > 0:
            self.draw.arc(box, start, start + 270 * max(0.02, min(1.0, ratio)), fill=color, width=thickness * SS)

    def png(self, width: int, height: int) -> bytes:
        small = self.image.resize((width, height), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        small.save(out, "PNG", optimize=True)
        return out.getvalue()


def _tag_text(words: dict[str, str], tag: str) -> str:
    return words[TAG_KEY.get(tag, tag)]


def _metric_rows(h: BotHealth, words: dict[str, str]) -> list[tuple[str, str, float]]:
    s = h.stats
    users = "—" if s.users is None else f"{s.users:,}" + (f"  (+{s.new_users})" if s.new_users else "")
    sales = "—" if s.services is None else f"{s.services}" + (f"  ·  {s.topups:,}" if s.topups else "")
    errors = "—" if h.errors is None else f"{h.errors}"
    ram = "—" if h.memory is None else f"RAM {h.memory:.0f}%"
    cpu = "" if h.cpu is None else f"  CPU {h.cpu:.0f}%"
    return [
        (words["users"], users, h.parts["users"] / 30),
        (words["sales"], sales, h.parts["sales"] / 30),
        (words["errors"], errors, h.parts["errors"] / 20),
        (words["resources"], f"{ram}{cpu}", h.parts["resources"] / 20),
    ]


def render_card(h: BotHealth, *, now: datetime | None = None) -> bytes:
    """One bot: the score gauge, the four measurements and what is wrong."""
    c = _Canvas.new(WIDTH, HEIGHT)
    w = c.words
    color = LEVEL_COLOR[h.level]
    c.rect((20, 20, WIDTH - 20, HEIGHT - 20), PANEL, 28)

    c.text((WIDTH - 52, 44), w["title"], size=34, bold=True, align="right", rtl=True)
    c.text((52, 50), h.label if h.label.startswith("@") else f"#{h.tenant_id}", size=28, fill=MUTED)

    cx, cy = 790, 280
    c.gauge(cx, cy, 138, h.score / 100, color)
    c.text((cx, cy - 62), str(h.score), size=96, bold=True, align="center", fill=color)
    c.text((cx, cy + 56), w[h.level], size=30, bold=True, align="center", rtl=True, fill=TEXT)
    c.text((cx, cy + 96), "/ 100", size=22, align="center", fill=MUTED)

    for i, (label, value, ratio) in enumerate(_metric_rows(h, w)):
        y = 118 + i * 82
        c.text((560, y), label, size=28, align="right", rtl=True)
        c.text((60, y + 2), value, size=26, fill=MUTED)
        c.bar(
            (60, y + 44, 560, y + 58), ratio, LEVEL_COLOR["good" if ratio >= 0.7 else "warn" if ratio >= 0.4 else "bad"]
        )

    x = WIDTH - 52
    tags = h.tags or ["none"]
    for tag in tags[:4]:
        label = w["none"] if tag == "none" else _tag_text(w, tag)
        tag_color = (
            LEVEL_COLOR["good"] if tag == "none" else LEVEL_COLOR["bad" if tag in ("down", "errors") else "warn"]
        )
        font, _ = _font(24 * SS, False)
        text_w = c.draw.textlength(visual(label) if c.fa else label, font=font) / SS
        c.circle(x - 10, 458, 8, tag_color)
        c.text((x - 28, 444), label, size=24, align="right", rtl=True)
        x -= int(text_w + 70)
    if h.days_left is not None:
        c.text((60, 436), f"{h.days_left} {w['left']}", size=24, fill=MUTED, rtl=True)
    stamp = fmt_date(now or utcnow())
    c.text((60, 474), stamp, size=18, fill=MUTED)
    return c.png(WIDTH, HEIGHT)


def render_overview(items: list[BotHealth], *, limit: int = 12, now: datetime | None = None) -> bytes:
    """The fleet: the sickest bots first, one bar each, with the counts of healthy / watch / poor bots."""
    shown = items[:limit]
    row = 54
    height = 150 + max(1, len(shown)) * row + 40
    c = _Canvas.new(WIDTH, height)
    w = c.words
    c.rect((20, 20, WIDTH - 20, height - 20), PANEL, 28)
    c.text((WIDTH - 52, 40), w["overview"], size=34, bold=True, align="right", rtl=True)

    counts = {"good": 0, "warn": 0, "bad": 0}
    for h in items:
        counts[h.level] += 1
    x = 52
    for level in ("bad", "warn", "good"):
        c.circle(x + 10, 66, 9, LEVEL_COLOR[level])
        c.text((x + 28, 50), f"{counts[level]}", size=26, bold=True)
        x += 100
    c.text((x + 8, 54), f"{len(items)} {w['bots']}", size=22, fill=MUTED, rtl=True)

    if not shown:
        c.text((WIDTH / 2, 160), w["no_bots"], size=28, align="center", fill=MUTED, rtl=True)
    for i, h in enumerate(shown):
        y = 120 + i * row
        color = LEVEL_COLOR[h.level]
        c.text((WIDTH - 52, y), h.label if h.label.startswith("@") else f"#{h.tenant_id}", size=26, align="right")
        c.bar((330, y + 8, 660, y + 24), h.score / 100, color, from_right=False)
        c.text((296, y - 2), str(h.score), size=28, bold=True, fill=color, align="right")
        if h.tags:  # the worst problem only: the card lists the rest
            tag = h.tags[0]
            c.circle(186, y + 16, 7, LEVEL_COLOR["bad" if tag in ("down", "errors") else "warn"])
            c.text((170, y + 2), _tag_text(w, tag), size=18, fill=MUTED, align="right", rtl=True)
    c.text((60, height - 56), fmt_date(now or utcnow()), size=18, fill=MUTED)
    return c.png(WIDTH, height)
