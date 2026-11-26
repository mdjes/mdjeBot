"""The admin's free-trial screen."""

from __future__ import annotations

import contextlib

from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from app.telegram.admin.trial import service
from app.telegram.state import clear_user, get_step, set_step
from config import ADMIN_ID

MENU_LABEL = "🎁 تست رایگان"
PANEL_STEP = "panel"
STEP_VOLUME = "form_trial_volume"
STEP_DAYS = "form_trial_days"

DIVIDER = "━━━━━━━━━━━━━━━"
_CANCEL = [[Button.inline("❌ انصراف", data="trial.menu")]]


def render(state: service.TrialState) -> tuple[str, list]:
    if not state.on:
        status = "🔴 **خاموش** — دکمهٔ تست در منوی اصلی نیست"
    elif state.visible:
        status = "🟢 **روشن** — دکمهٔ «🎁 دریافت تست» در منوی اصلی ربات نمایش داده می‌شود"
    else:
        status = "🟡 **روشن است ولی دکمه نمایش داده نمی‌شود** — یک پنل فعال برای تست لازم است"
    names = "، ".join(panel.name for panel in state.panels) or "هنوز انتخاب نشده"
    text = (
        f"**🎁 تست رایگان**\n{DIVIDER}\n"
        f"{status}\n\n"
        f"📥 حجم تست: **{service.volume_text(state.volume_gb)}**\n"
        f"⏰ مدت تست: **{state.days} روز**\n"
        f"📱 تأیید شماره قبل از تست: {'✅ روشن' if state.phone_verify else '⛔️ خاموش'}\n"
        f"🧩 پنل‌های ارائه‌دهنده: {names}\n\n"
        "💡 برای حجم عدد گیگابایت بفرستید: `0.5` یعنی ۵۰۰ مگ، `2` یعنی ۲ گیگ (یا `500mb`)."
    )
    buttons = [
        [Button.inline("🔴 خاموش کردن تست" if state.on else "🟢 روشن کردن تست", data="trial.switch")],
        [
            Button.inline(f"📥 حجم: {service.volume_text(state.volume_gb)}", data="trial.vol"),
            Button.inline(f"⏰ مدت: {state.days} روز", data="trial.days"),
        ],
        [Button.inline(f"📱 تأیید شماره: {'✅' if state.phone_verify else '⛔️'}", data="trial.phone")],
    ]
    if len(state.all_panels) > 1:
        buttons.append([Button.inline("🧩 انتخاب پنل‌های تست", data="trial.panels")])
    return text, buttons


async def _screen() -> tuple[str, list]:
    return render(await service.load_state())


async def _edit(event, text: str, buttons) -> None:
    with contextlib.suppress(MessageNotModifiedError):
        await event.edit(text, buttons=buttons)


# --- entry ---------------------------------------------------------------------------------------------


async def is_menu_press(event) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    return (event.message.message or "").strip() == MENU_LABEL and await get_step(event.sender_id) == PANEL_STEP


async def on_menu_press(event) -> None:
    text, buttons = await _screen()
    await event.respond(text, buttons=buttons)
    raise events.StopPropagation


def is_trial_callback(event) -> bool:
    return event.sender_id in ADMIN_ID and (event.data or b"").startswith(b"trial.")


async def _show_panels(event) -> None:
    state = await service.load_state()
    offering = {int(panel.code) for panel in state.panels}
    rows = [
        [Button.inline(f"{'✅' if int(p.code) in offering else '▫️'} {p.name}", data=f"trial.panel:{p.code}")]
        for p in state.all_panels
        if p.enable
    ]
    rows.append([Button.inline("🔙 بازگشت", data="trial.menu")])
    await _edit(event, "🧩 پنلی را که تست می‌دهد (یا نمی‌دهد) انتخاب کنید:", rows)


async def on_callback(event) -> None:
    action, _, arg = event.data.decode().partition(":")
    admin_id = event.sender_id
    note, alert = "", False  # a query can be answered once, so the answer is decided here and sent at the end

    if action == "trial.menu":
        await clear_user(admin_id)
        await set_step(admin_id, PANEL_STEP)
        await _edit(event, *await _screen())
    elif action == "trial.switch":
        note, alert = await service.switch(not (await service.load_state()).on), True
        await _edit(event, *await _screen())
    elif action == "trial.phone":
        await service.toggle_phone_verify()
        await _edit(event, *await _screen())
    elif action == "trial.vol":
        await set_step(admin_id, STEP_VOLUME)
        await _edit(event, "📥 حجم تست را بفرستید (گیگابایت).\nمثال: `0.5` = ۵۰۰ مگ، `2` = ۲ گیگ، `500mb`", _CANCEL)
    elif action == "trial.days":
        await set_step(admin_id, STEP_DAYS)
        await _edit(event, "⏰ مدت تست را به روز بفرستید.\nمثال: `1` یا `3` یا `7`", _CANCEL)
    elif action == "trial.panels":
        await _show_panels(event)
    elif action == "trial.panel" and arg.isdigit():
        note = await service.toggle_panel(int(arg))
        await _show_panels(event)
    await event.answer(note, alert=alert)
    raise events.StopPropagation


# --- the two number forms ------------------------------------------------------------------------------


async def is_form_answer(event) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private or not (event.message.message or "").strip():
        return False
    return await get_step(event.sender_id) in (STEP_VOLUME, STEP_DAYS)


async def on_form_text(event) -> None:
    admin_id = event.sender_id
    step = await get_step(admin_id)
    raw = event.message.message
    if step == STEP_VOLUME:
        value = service.parse_volume_gb(raw)
        if value is None:
            await event.respond("❌ حجم معتبر نیست. مثال: `0.5` یا `2` یا `500mb`", buttons=_CANCEL)
            raise events.StopPropagation
        await service.set_volume(value)
    else:
        value = service.parse_days(raw)
        if value is None:
            await event.respond(f"❌ مدت معتبر نیست؛ یک عدد بین ۱ تا {service.MAX_DAYS} بفرستید.", buttons=_CANCEL)
            raise events.StopPropagation
        await service.set_days(value)
    await clear_user(admin_id)
    await set_step(admin_id, PANEL_STEP)
    text, buttons = await _screen()
    await event.respond("✅ ذخیره شد.\n\n" + text, buttons=buttons)
    raise events.StopPropagation
