"""The «don't send me this report» button under the weekly usage message."""

from __future__ import annotations

from telethon import events

from app.jobs.services.weekly_usage import opt_out
from app.services.usage_report import CALLBACK_OPT_OUT
from app.telegram.plugins import PluginContext

MODULE_NAME = "user.weekly_report"
MODULE_DESCRIPTION = "Opt out of the weekly usage report"

OPTED_OUT_TEXT = "🔕 دیگر گزارش هفتگی برای شما ارسال نمی‌شود."
FAILED_TEXT = "ثبت درخواست انجام نشد؛ کمی بعد دوباره تلاش کنید."


async def on_opt_out(event: events.CallbackQuery.Event) -> None:
    saved = await opt_out(event.sender_id)
    await event.answer(OPTED_OUT_TEXT if saved else FAILED_TEXT, alert=True)
    raise events.StopPropagation


def setup(mdje: PluginContext) -> None:
    mdje.add_event_handler(on_opt_out, events.CallbackQuery(data=CALLBACK_OPT_OUT.encode()))
