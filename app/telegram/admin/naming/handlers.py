"""The admin's «default config name» screen."""

from __future__ import annotations

import contextlib

from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from app.services.users import naming
from app.telegram.admin.naming import service
from app.telegram.state import clear_user, get_step, set_step
from config import ADMIN_ID

MENU_LABEL = "🏷 اسم پیشفرض کانفیگ"
PANEL_STEP = "panel"
STEP_TEMPLATE = "form_cname_template"
STEP_BOT = "form_cname_bot"
STEP_BLOCKED = "form_cname_blocked"
FORM_STEPS = (STEP_TEMPLATE, STEP_BOT, STEP_BLOCKED)

DIVIDER = "━━━━━━━━━━━━━━━"
_CANCEL = [[Button.inline("❌ انصراف", data="cname.menu")]]
_ON, _OFF = "✅ روشن", "⛔️ خاموش"

PLACEHOLDER_HELP = (
    "`{bot}` اسم ربات · `{id}` آیدی عددی مشتری · `{user}` یوزرنیم تلگرام او · `{seq}` شمارهٔ خرید\n"
    "`{n5}` ۵ رقم تصادفی · `{r4}` ۴ حرف/عدد تصادفی (عدد بین ۱ تا ۱۲)"
)


def render(state: service.NamingState, samples: list[str]) -> tuple[str, list]:
    cfg = state.cfg
    pattern = f"`{cfg.template}`" if cfg.template else "تصادفی (۹ حرف و عدد)"
    blocked = ", ".join(naming.blocked_words(cfg.blocked)) or "ندارد"
    text = (
        f"**🏷 اسم پیشفرض کانفیگ**\n{DIVIDER}\n"
        "اسمی که دکمهٔ «🎲 اسم پیشفرض» برای مشتری و ادمین می‌سازد (و در حالت خودکار، خود ربات انتخاب می‌کند).\n\n"
        f"🧩 الگو: {pattern}\n"
        f"👀 نمونه: {' ، '.join(f'`{name}`' for name in samples)}\n\n"
        f"🤖 اسم ربات ({{bot}}): `{state.bot}`\n"
        f"🔠 حروف: {naming.CASE_LABELS[cfg.case]}\n"
        f"🏷 پیشوند اسم ربات برای نام‌هایی که مشتری تایپ می‌کند: {_ON if cfg.prefix_typed else _OFF}\n"
        f"⛔️ کلمات ممنوعه در نام تایپی: {blocked}\n"
        f"⚡️ نام‌گذاری خودکار (مشتری اسمی نمی‌دهد): {_ON if cfg.auto else _OFF}\n\n"
        f"💡 بخش‌های الگو:\n{PLACEHOLDER_HELP}\n"
        "نام کانفیگ فقط حروف انگلیسی، عدد و _ می‌پذیرد؛ خط تیره خودکار به _ تبدیل می‌شود."
    )
    rows = []
    keys = list(naming.PRESETS)
    for i in range(0, len(keys), 2):
        rows.append(
            [
                Button.inline(
                    f"{'✅ ' if state.preset == key else ''}{naming.PRESETS[key][0]}", data=f"cname.preset:{key}"
                )
                for key in keys[i : i + 2]
            ]
        )
    rows += [
        [Button.inline(f"✏️ الگوی دلخواه{' ✅' if state.preset is None else ''}", data="cname.tpl")],
        [
            Button.inline("🤖 اسم ربات", data="cname.bot"),
            Button.inline(f"🔠 حروف: {naming.CASE_LABELS[cfg.case].split(' ')[0]}", data="cname.case"),
        ],
        [Button.inline(f"🏷 پیشوند نام تایپی: {'✅' if cfg.prefix_typed else '⛔️'}", data="cname.prefix")],
        [Button.inline("⛔️ کلمات ممنوعه", data="cname.blocked")],
        [Button.inline(f"⚡️ نام‌گذاری خودکار: {'✅' if cfg.auto else '⛔️'}", data="cname.auto")],
    ]
    return text, rows


async def _screen(admin_id: int) -> tuple[str, list]:
    state = await service.load_state()
    return render(state, service.sample_names(state, admin_id))


async def _edit(event, text: str, buttons) -> None:
    with contextlib.suppress(MessageNotModifiedError):
        await event.edit(text, buttons=buttons)


# --- entry ---------------------------------------------------------------------------------------------


async def is_menu_press(event) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private:
        return False
    return (event.message.message or "").strip() == MENU_LABEL and await get_step(event.sender_id) == PANEL_STEP


async def on_menu_press(event) -> None:
    text, buttons = await _screen(event.sender_id)
    await event.respond(text, buttons=buttons)
    raise events.StopPropagation


def is_naming_callback(event) -> bool:
    return event.sender_id in ADMIN_ID and (event.data or b"").startswith(b"cname.")


_PROMPTS = {
    "cname.tpl": (
        STEP_TEMPLATE,
        "✏️ الگو را بفرستید.\nمثال: `{bot}_{n5}` یا `{bot}_{id}_{n5}` یا `VIP_{user}_{n4}`\n\n" + PLACEHOLDER_HELP,
    ),
    "cname.bot": (
        STEP_BOT,
        "🤖 اسم ربات را بفرستید (حروف انگلیسی، عدد و _ ؛ مثل `MdjeBot`).\nبرای استفاده از تگ خود ربات `-` بفرستید.",
    ),
    "cname.blocked": (
        STEP_BLOCKED,
        "⛔️ کلماتی که نباید در نام تایپی مشتری باشند را بفرستید (با ویرگول یا فاصله جدا کنید).\n"
        "مثال: `admin, root, support, test`\nبرای خالی کردن لیست `-` بفرستید.",
    ),
}


async def on_callback(event) -> None:
    action, _, arg = event.data.decode().partition(":")
    admin_id = event.sender_id
    note = ""  # a query can be answered once, so the answer is decided here and sent at the end

    if action == "cname.menu":
        await clear_user(admin_id)
        await set_step(admin_id, PANEL_STEP)
        await _edit(event, *await _screen(admin_id))
    elif action == "cname.preset":
        note = await service.apply_preset(arg)
        await _edit(event, *await _screen(admin_id))
    elif action in _PROMPTS:
        step, prompt = _PROMPTS[action]
        await set_step(admin_id, step)
        await _edit(event, prompt, _CANCEL)
    elif action == "cname.case":
        note = await service.cycle_case()
        await _edit(event, *await _screen(admin_id))
    elif action == "cname.prefix":
        note = "پیشوند روشن شد." if await service.toggle_prefix() else "پیشوند خاموش شد."
        await _edit(event, *await _screen(admin_id))
    elif action == "cname.auto":
        note = (
            "نام‌گذاری خودکار روشن شد؛ مشتری دیگر اسمی نمی‌دهد."
            if await service.toggle_auto()
            else "نام‌گذاری خودکار خاموش شد؛ مشتری دوباره اسم می‌دهد."
        )
        await _edit(event, *await _screen(admin_id))
    await event.answer(note, alert=bool(note))
    raise events.StopPropagation


# --- the three typed answers ---------------------------------------------------------------------------


async def is_form_answer(event) -> bool:
    if event.sender_id not in ADMIN_ID or not event.is_private or not (event.message.message or "").strip():
        return False
    return await get_step(event.sender_id) in FORM_STEPS


async def on_form_text(event) -> None:
    admin_id = event.sender_id
    step = await get_step(admin_id)
    raw = event.message.message
    if step == STEP_TEMPLATE:
        note, error = await service.set_template(raw)
    elif step == STEP_BOT:
        note, error = await service.set_bot_name(raw)
    else:
        note, error = await service.set_blocked(raw)
    if error:
        await event.respond(f"❌ {error}", buttons=_CANCEL)
        raise events.StopPropagation
    await clear_user(admin_id)
    await set_step(admin_id, PANEL_STEP)
    text, buttons = await _screen(admin_id)
    await event.respond(f"✅ {note}\n\n{text}", buttons=buttons)
    raise events.StopPropagation
