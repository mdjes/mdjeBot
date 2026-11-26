"""The free trial in one place: on/off, volume, days and which panels hand it out.

The pieces already existed but lived apart: a global switch in the bot settings, and per panel a switch,
a volume and a number of days. Here one screen reads and writes all of them, so turning the trial on
also makes sure a panel offers it (otherwise the home-menu button silently stays hidden).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.crud.panels import PanelsManager
from app.db.crud.settings import SettingsManager
from app.services.panels.settings import panel_test_duration_days, panel_test_flag, panel_test_volume_gb
from app.services.panels.trials import trial_offered, trial_panels
from app.utils.formatting.conversions import convert_storage

MAX_VOLUME_GB = 1000
MAX_DAYS = 365
DEFAULT_VOLUME_GB = 2.0
DEFAULT_DAYS = 3

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬،", "01234567890123456789.,,")
_VOLUME = re.compile(r"(\d+(?:\.\d+)?)\s*(mb|m|مگ|مگابایت|gb|g|گیگ|گیگابایت)?")
_MEGA = {"mb", "m", "مگ", "مگابایت"}


def parse_volume_gb(text: str | None) -> float | None:
    """A volume in GB from what an admin types: ``0.5`` and ``500mb`` both mean 500 MB, ``2`` means 2 GB."""
    cleaned = (text or "").strip().lower().translate(_DIGITS).replace(",", "")
    match = _VOLUME.fullmatch(cleaned)
    if match is None:
        return None
    value = float(match.group(1))
    if match.group(2) in _MEGA:
        value /= 1000  # the bot counts 1 GB as 1000 MB when it shows a volume
    return round(value, 3) if 0 < value <= MAX_VOLUME_GB else None


def parse_days(text: str | None) -> int | None:
    cleaned = (text or "").strip().translate(_DIGITS)
    days = int(cleaned) if cleaned.isdigit() else 0
    return days if 1 <= days <= MAX_DAYS else None


def volume_text(gb: float) -> str:
    return convert_storage(gb, for_button=True)


@dataclass(slots=True)
class TrialState:
    on: bool
    visible: bool  # the button really shows in the main menu now
    panels: list  # panels handing out the trial
    all_panels: list
    volume_gb: float
    days: int
    phone_verify: bool


async def load_state() -> TrialState:
    setting = await SettingsManager().get_settings()
    offering = await trial_panels(setting)
    all_panels = list(await PanelsManager().get_all_panels())
    reference = offering[0] if offering else next((p for p in all_panels if p.enable), None)
    return TrialState(
        on=bool(setting and setting.test_mode),
        visible=await trial_offered(setting),
        panels=offering,
        all_panels=all_panels,
        volume_gb=panel_test_volume_gb(reference) if reference else DEFAULT_VOLUME_GB,
        days=panel_test_duration_days(reference) if reference else DEFAULT_DAYS,
        phone_verify=bool(getattr(setting, "test_phone_verify", True)),
    )


async def _enable_panel(panel, setting) -> None:
    await PanelsManager().update_panel(panel.code, test_enabled=True)
    await SettingsManager().update_setting(setting.id, test_panel_id=int(panel.code))


async def switch(on: bool) -> str:
    """Turn the trial on or off. Returns a one-line note for the admin."""
    setting = await SettingsManager().get_settings()
    await SettingsManager().update_setting(setting.id, test_mode=on)
    if not on:
        return "تست خاموش شد؛ دکمهٔ دریافت تست از منوی اصلی برداشته شد."
    if await trial_panels(setting):
        return "تست روشن شد."
    panels = [p for p in await PanelsManager().get_all_panels() if p.enable]
    if not panels:
        return "تست روشن شد، اما هنوز پنل فعالی نیست؛ اول یک پنل اضافه کنید."
    await _enable_panel(panels[0], setting)
    return f"تست روشن شد و پنل «{panels[0].name}» برای ارائهٔ تست انتخاب شد."


async def _targets() -> list:
    offering = await trial_panels()
    return offering or [p for p in await PanelsManager().get_all_panels() if p.enable]


async def set_volume(gb: float) -> None:
    for panel in await _targets():
        await PanelsManager().update_panel(panel.code, test_volume_gb=gb)


async def set_days(days: int) -> None:
    for panel in await _targets():
        await PanelsManager().update_panel(panel.code, test_duration_days=days)


async def toggle_phone_verify() -> bool:
    setting = await SettingsManager().get_settings()
    new_value = not bool(getattr(setting, "test_phone_verify", True))
    await SettingsManager().update_setting(setting.id, test_phone_verify=new_value)
    return new_value


async def toggle_panel(code: int) -> str:
    """Flip whether one panel hands out the trial."""
    setting = await SettingsManager().get_settings()
    panel = await PanelsManager().get_panel_by_code(code)
    if panel is None:
        return "پنل پیدا نشد."
    if panel_test_flag(panel):
        await PanelsManager().update_panel(code, test_enabled=False)
        if int(getattr(setting, "test_panel_id", 0) or 0) == int(code):
            await SettingsManager().update_setting(setting.id, test_panel_id=0)
        return f"پنل «{panel.name}» دیگر تست نمی‌دهد."
    await _enable_panel(panel, setting)
    return f"پنل «{panel.name}» تست می‌دهد." + ("" if setting.test_mode else " (تست کلی خاموش است؛ آن را روشن کنید.)")
