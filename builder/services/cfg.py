"""Admin-editable builder settings stored in the ``builder_settings`` table."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from builder.models import Setting

DEFAULTS: dict[str, str] = {
    "maintenance": "0",
    "trial_enabled": "1",
    "trial_days": "3",
    "reminder_days": "3",
    "grace_days": "3",
    "tenant_memory": "384m",
    "tenant_cpus": "0.5",
    "min_topup": "50000",
    "max_bots_per_user": "5",
    "card_payment_enabled": "1",
    "wallet_enabled": "1",
    "public_host": "",
    "welcome_text": "👋 به ربات‌ساز خوش آمدید.\nاینجا می‌توانید ربات فروش وی‌پی‌ان اختصاصی خود را بسازید و مدیریت کنید.",
    "support_text": "برای پشتیبانی با مدیریت در ارتباط باشید.",
    "card_note": "",
}

# (key, label, kind) — kind: bool | int | text. Drives the admin settings screen.
EDITABLE: tuple[tuple[str, str, str], ...] = (
    ("maintenance", "🛠 حالت تعمیر", "bool"),
    ("trial_enabled", "🎁 تست رایگان", "bool"),
    ("trial_days", "⏳ مدت تست (روز)", "int"),
    ("reminder_days", "🔔 یادآوری تمدید (روز قبل)", "int"),
    ("grace_days", "🕒 مهلت پیش از حذف (روز)", "int"),
    ("tenant_memory", "🧠 رم هر ربات (مثل 384m)", "text"),
    ("tenant_cpus", "⚙️ CPU هر ربات (مثل 0.5)", "text"),
    ("min_topup", "💰 حداقل شارژ (تومان)", "int"),
    ("max_bots_per_user", "🤖 سقف ربات هر کاربر", "int"),
    ("card_payment_enabled", "💳 کارت به کارت", "bool"),
    ("wallet_enabled", "👛 کیف پول", "bool"),
    ("public_host", "🌐 دامنه/IP سرور (نمایش به کاربر)", "text"),
    ("welcome_text", "📝 متن خوش‌آمد", "text"),
    ("support_text", "📞 متن پشتیبانی", "text"),
    ("card_note", "ℹ️ توضیح کارت به کارت", "text"),
)
EDITABLE_KEYS = {key: (label, kind) for key, label, kind in EDITABLE}


@dataclass(frozen=True)
class Cfg:
    values: dict[str, str]

    def str(self, key: str) -> str:
        return self.values.get(key, DEFAULTS.get(key, ""))

    def int(self, key: str) -> int:
        try:
            return int(self.str(key).strip())
        except ValueError:
            return int(DEFAULTS.get(key, "0") or 0)

    def bool(self, key: str) -> bool:
        return self.str(key).strip().lower() in ("1", "true", "yes", "on")


async def load_cfg(session: AsyncSession) -> Cfg:
    rows = (await session.execute(select(Setting))).scalars().all()
    merged = dict(DEFAULTS)
    merged.update({row.key: row.value for row in rows})
    return Cfg(merged)


async def set_cfg(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
