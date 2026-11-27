"""Default names for new configs, and the rules applied to the names customers type themselves.

The admin picks a template for the «default name» button (and for shops that name configs on their own):

    {bot}  the bot's name            {id}   the customer's Telegram id      {user}  their @username (else the bot name)
    {seq}  which purchase this is    {n5}   5 random digits (1 to 12)       {r4}    4 random letters/digits (1 to 12)

e.g. ``{bot}_{n5}`` gives ``MdjeBot_48213`` and ``{bot}_{id}_{n5}`` gives ``MdjeBot_989932570_48213``. Panels only
accept letters, digits and underscores, so a dash or a space typed in a template becomes an underscore. Whatever the
template produces is shortened to fit 32 characters (the bot and username parts give way first, the random part is kept)
and replaced by a classic random name if it would not be a valid username.

Everything here is pure; ``load_config`` and ``make_context`` at the bottom read the settings and the database.
"""

from __future__ import annotations

import random
import re
import string
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MIN_LEN, MAX_LEN = 3, 32
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{1,30}[A-Za-z0-9]$")
MAX_TEMPLATE_LEN = 60
FALLBACK_BOT = "VPN"

CASES = ("keep", "lower", "upper")
CASE_LABELS = {"keep": "همان‌طور که هست", "lower": "همه کوچک (abc)", "upper": "همه بزرگ (ABC)"}

# key -> (button label, template). The empty template is the classic 9 random letters and digits.
PRESETS: dict[str, tuple[str, str]] = {
    "random": ("🎲 تصادفی (قدیمی)", ""),
    "bot_digits": ("🤖 اسم ربات + عدد", "{bot}_{n5}"),
    "bot_id_digits": ("🆔 اسم ربات + آیدی + عدد ۵ رقمی", "{bot}_{id}_{n5}"),
    "user_digits": ("👤 یوزرنیم تلگرام + عدد", "{user}_{n4}"),
    "bot_seq": ("🔢 اسم ربات + آیدی + شمارهٔ خرید", "{bot}_{id}_{seq}"),
}

_TOKEN = re.compile(r"\{(\w+)\}")
_RANDOM_TOKEN = re.compile(r"[nr](?:[1-9]|1[0-2])")
_SEPARATORS = re.compile(r"[\s\-.]+")
_NOT_NAME = re.compile(r"[^A-Za-z0-9_]")
_UNDERSCORES = re.compile(r"_+")
_TEMPLATE_CHARS = re.compile(r"^[A-Za-z0-9_\-{}]+$")
_WORD_SPLIT = re.compile(r"[\s,;،]+")
_KNOWN = {"bot", "id", "user", "seq"}


def clean_part(text: str | None) -> str:
    """Letters, digits and underscores only; spaces, dashes and dots become underscores."""
    return _NOT_NAME.sub("", _SEPARATORS.sub("_", str(text or ""))).strip("_")


def _tidy(name: str) -> str:
    return _UNDERSCORES.sub("_", clean_part(name))


def apply_case(name: str, case: str) -> str:
    return name.lower() if case == "lower" else name.upper() if case == "upper" else name


def template_error(template: str) -> str | None:
    """Why an admin's template cannot be used, or ``None`` when it is fine."""
    text = (template or "").strip()
    if not text:
        return "الگو خالی است."
    if len(text) > MAX_TEMPLATE_LEN:
        return f"الگو حداکثر {MAX_TEMPLATE_LEN} کاراکتر می‌تواند باشد."
    if not _TEMPLATE_CHARS.fullmatch(text):
        return "الگو فقط می‌تواند حروف انگلیسی، عدد، _ ، - و بخش‌های داخل { } داشته باشد."
    unknown = [name for name in _TOKEN.findall(text) if name not in _KNOWN and not _RANDOM_TOKEN.fullmatch(name)]
    if unknown:
        return "بخش ناشناخته: " + "، ".join("{" + name + "}" for name in dict.fromkeys(unknown))
    rest = _TOKEN.sub("", text)
    if "{" in rest or "}" in rest:
        return "آکولادها درست بسته نشده‌اند."
    return None


@dataclass(frozen=True, slots=True)
class NamingConfig:
    template: str = ""  # empty: the classic random name
    bot: str = ""  # the bot's name for {bot}; empty uses BOT_TAG
    case: str = "keep"
    prefix_typed: bool = False  # also put the bot name in front of names customers type
    blocked: str = ""  # words a typed name may not contain
    auto: bool = False  # do not ask the customer for a name at all


@dataclass(frozen=True, slots=True)
class NameContext:
    bot: str = FALLBACK_BOT
    user_id: int | None = None
    tg_username: str | None = None
    seq: int = 1


def config_from(settings: Any) -> NamingConfig:
    """The naming options of the settings row (the defaults when there is none)."""
    if settings is None:
        return NamingConfig()
    case = str(getattr(settings, "config_name_case", "keep") or "keep")
    return NamingConfig(
        template=str(getattr(settings, "config_name_template", "") or ""),
        bot=str(getattr(settings, "config_name_bot", "") or ""),
        case=case if case in CASES else "keep",
        prefix_typed=bool(getattr(settings, "config_name_prefix_typed", False)),
        blocked=str(getattr(settings, "config_name_blocked", "") or ""),
        auto=bool(getattr(settings, "config_name_auto", False)),
    )


def _render(template: str, ctx: NameContext, rng: Any, limits: dict[str, int]) -> str:
    def part(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "bot":
            return ctx.bot[: limits["bot"]]
        if name == "id":
            return "" if ctx.user_id is None else str(ctx.user_id)
        if name == "user":
            return (clean_part(ctx.tg_username) or ctx.bot)[: limits["user"]]
        if name == "seq":
            return str(ctx.seq)
        if _RANDOM_TOKEN.fullmatch(name):
            alphabet = string.digits if name[0] == "n" else string.ascii_uppercase + string.digits
            return "".join(rng.choices(alphabet, k=int(name[1:])))
        return ""

    return _TOKEN.sub(part, template)


def build_name(
    template: str,
    ctx: NameContext,
    *,
    rng: Any = None,
    widen: int = 0,
    case: str = "keep",
    legacy: Callable[[], str] | None = None,
) -> str:
    """One default name. ``widen`` > 0 adds random digits, for when the plain name was already taken."""
    rng = rng or random
    legacy = legacy or (lambda: "".join(rng.choices(string.ascii_uppercase + string.digits, k=9)))
    if not (template or "").strip():
        name = legacy()
    else:
        limits = {"bot": len(ctx.bot), "user": MAX_LEN}
        name = _tidy(_render(template, ctx, rng, limits))
        while len(name) > MAX_LEN and any(value > 1 for value in limits.values()):
            over = len(name) - MAX_LEN
            limits = {key: max(1, value - over) for key, value in limits.items()}
            name = _tidy(_render(template, ctx, rng, limits))
        name = name[:MAX_LEN].rstrip("_")
    if widen and name:
        suffix = "_" + "".join(rng.choices(string.digits, k=min(3 + widen, 6)))
        name = name[: MAX_LEN - len(suffix)].rstrip("_") + suffix
    name = apply_case(name, case)
    return name if NAME_RE.fullmatch(name) else apply_case(legacy(), case)


def preview(cfg: NamingConfig, ctx: NameContext, count: int = 3, *, rng: Any = None) -> list[str]:
    return [build_name(cfg.template, ctx, rng=rng, case=cfg.case) for _ in range(count)]


# ── names customers type ──────────────────────────────────────────────────────


def blocked_words(text: str | None) -> list[str]:
    """The admin's list of forbidden words (comma, space or line separated), lower-cased, once each."""
    return list(dict.fromkeys(word.lower() for word in _WORD_SPLIT.split(text or "") if word.strip()))


def check_typed(name: str, cfg: NamingConfig, ctx: NameContext) -> tuple[str | None, str | None]:
    """``(final name, None)`` for a typed name, or ``(None, why not)``: case, forbidden words and the bot prefix."""
    name = apply_case(name, cfg.case)
    lowered = name.lower()
    for word in blocked_words(cfg.blocked):
        if word in lowered:
            return None, f"❌ نام کانفیگ نباید شامل «{word}» باشد. نام دیگری بفرستید."
    if cfg.prefix_typed and ctx.bot:
        prefix = apply_case(ctx.bot, cfg.case)
        if not lowered.startswith(prefix.lower() + "_"):
            final = f"{prefix}_{name}"
            if len(final) > MAX_LEN:
                room = MAX_LEN - len(prefix) - 1
                return None, f"❌ پیش از نام شما «{prefix}_» اضافه می‌شود؛ نام شما حداکثر {room} کاراکتر می‌تواند باشد."
            name = final
    return name, None


# ── reading the settings and the database ─────────────────────────────────────


async def load_config() -> NamingConfig:
    from app.db.crud.settings import SettingsManager

    try:
        return config_from(await SettingsManager().get_settings())
    except Exception:
        return NamingConfig()


async def make_context(
    cfg: NamingConfig, *, user_id: int | None = None, tg_username: str | None = None, fallback_bot: str = ""
) -> NameContext:
    """Who the name is for: the bot name, the customer and (only when the template needs it) their purchase count."""
    seq = 1
    if user_id is not None and "{seq}" in cfg.template:
        from app.db.crud.services import ServiceCRUD

        try:
            seq = len(await ServiceCRUD().get_services_reverse(user_id) or []) + 1
        except Exception:
            seq = 1
    bot = clean_part(cfg.bot) or clean_part(fallback_bot) or FALLBACK_BOT
    return NameContext(bot=bot, user_id=user_id, tg_username=tg_username, seq=seq)
