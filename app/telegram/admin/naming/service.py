"""The default config name in one place: the pattern, the bot's name, letter case and the extra naming rules.

Every option lives in the purchase settings (see ``app.services.users.naming``); this module reads and writes
them for the admin screen and checks what the admin types.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.crud.settings import SettingsManager
from app.services.users import naming
from config import BOT_TAG

CLEAR_WORDS = {"-", "0", "پاک", "حذف", "خالی", "none"}
MAX_BOT_LEN = 20
MAX_BLOCKED = 40


@dataclass(slots=True)
class NamingState:
    cfg: naming.NamingConfig
    preset: str | None  # the preset the template matches, None for a custom one
    bot: str  # the name {bot} really gives


def preset_for(template: str) -> str | None:
    for key, (_, preset_template) in naming.PRESETS.items():
        if preset_template == (template or "").strip():
            return key
    return None


async def load_state() -> NamingState:
    cfg = await naming.load_config()
    return NamingState(cfg=cfg, preset=preset_for(cfg.template), bot=naming.clean_part(cfg.bot) or _fallback_bot())


def _fallback_bot() -> str:
    return naming.clean_part(BOT_TAG) or naming.FALLBACK_BOT


async def _save(**values) -> None:
    setting = await SettingsManager().get_settings()
    if setting is not None:
        await SettingsManager().update_setting(setting.id, **values)


def sample_names(state: NamingState, admin_id: int, count: int = 3) -> list[str]:
    """What the pattern gives, for an example customer (the admin's own id, a made-up @username, third purchase)."""
    ctx = naming.NameContext(bot=state.bot, user_id=admin_id, tg_username="ali", seq=3)
    return naming.preview(state.cfg, ctx, count)


async def apply_preset(key: str) -> str:
    if key not in naming.PRESETS:
        return "گزینه نامعتبر است."
    label, template = naming.PRESETS[key]
    await _save(config_name_template=template)
    return f"الگو روی «{label}» تنظیم شد."


async def set_template(text: str | None) -> tuple[str | None, str | None]:
    """Save a custom template. Returns ``(note, error)``."""
    template = (text or "").strip()
    error = naming.template_error(template)
    if error:
        return None, error
    await _save(config_name_template=template)
    return "الگو ذخیره شد.", None


async def set_bot_name(text: str | None) -> tuple[str | None, str | None]:
    """Save the name for ``{bot}`` (letters, digits and underscores; ``-`` goes back to the bot's own tag)."""
    raw = (text or "").strip()
    if raw.lower() in CLEAR_WORDS:
        await _save(config_name_bot="")
        return "اسم ربات پاک شد؛ از تگ خود ربات استفاده می‌شود.", None
    cleaned = naming.clean_part(raw)
    if len(cleaned) < 2 or len(cleaned) > MAX_BOT_LEN:
        return None, f"اسم باید ۲ تا {MAX_BOT_LEN} کاراکتر و فقط حروف انگلیسی، عدد و _ باشد."
    await _save(config_name_bot=cleaned)
    return f"اسم ربات روی «{cleaned}» تنظیم شد.", None


async def cycle_case() -> str:
    cfg = await naming.load_config()
    new = naming.CASES[(naming.CASES.index(cfg.case) + 1) % len(naming.CASES)]
    await _save(config_name_case=new)
    return f"حروف: {naming.CASE_LABELS[new]}"


async def toggle_prefix() -> bool:
    cfg = await naming.load_config()
    await _save(config_name_prefix_typed=not cfg.prefix_typed)
    return not cfg.prefix_typed


async def toggle_auto() -> bool:
    cfg = await naming.load_config()
    await _save(config_name_auto=not cfg.auto)
    return not cfg.auto


async def set_blocked(text: str | None) -> tuple[str | None, str | None]:
    """Save the forbidden words (any separator; ``-`` clears the list)."""
    raw = (text or "").strip()
    if raw.lower() in CLEAR_WORDS:
        await _save(config_name_blocked="")
        return "لیست کلمات ممنوعه پاک شد.", None
    words = [naming.clean_part(word) for word in naming.blocked_words(raw)]
    words = [word for word in dict.fromkeys(words) if word]
    if not words:
        return None, "کلمهٔ معتبری پیدا نشد؛ کلمات انگلیسی را با ویرگول یا فاصله جدا کنید."
    if len(words) > MAX_BLOCKED:
        return None, f"حداکثر {MAX_BLOCKED} کلمه می‌توانید بگذارید."
    await _save(config_name_blocked=", ".join(words))
    return f"{len(words)} کلمه ممنوع شد.", None
