"""Short messages to the admins about what customers do, written to be read at a glance.

They go to the log destination of their type (a channel or topic set under "مدیریت لاگ‌ها"), and to the
admins' own chats when no destination is set. Each carries buttons to message the customer or open their
page, because the point of hearing about a new customer is to say something to them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import Mdje
from app.db.crud.user import UserCRUD
from app.logger import LogType, get_logger
from app.telegram.keyboards.common import glass_inline_button
from app.telegram.shared.utils.logging import send_log_message
from app.utils.formatting.dates import Time_Date
from config import ADMIN_ID

logger = get_logger(__name__)

NO_USERNAME = "ندارد"
NO_NAME = "نامشخص"


@dataclass(frozen=True, slots=True)
class Person:
    user_id: int
    full_name: str = ""
    username: str = ""
    phone: str = ""

    @property
    def name_line(self) -> str:
        return self.full_name or NO_NAME

    @property
    def username_line(self) -> str:
        return f"@{self.username}" if self.username else NO_USERNAME


async def person_of(user_id: int, sender=None) -> Person:
    """Who the user is: from the Telegram profile (``sender`` when the update carries it) and our database."""
    if sender is None:
        try:
            sender = await Mdje.get_entity(user_id)
        except Exception as exc:
            logger.debug("Could not read the profile of %s: %s", user_id, exc)
    name = " ".join(part for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if part)
    username = getattr(sender, "username", None) or ""
    try:
        record = await UserCRUD().read_user(user_id)
    except Exception as exc:
        logger.debug("Could not read user %s: %s", user_id, exc)
        record = None
    return Person(user_id, name.strip(), username, (getattr(record, "number", None) or "").strip())


def _who(person: Person) -> str:
    return f"نام و نام خانوادگی: {person.name_line}\nنام کاربری: {person.username_line}\nآیدی عددی: `{person.user_id}`"


def _stamp() -> str:
    return Time_Date()["jf"]


def new_member_text(person: Person) -> str:
    return (
        "📢 | یه گل جدید عضو ربات شد :\n\n"
        f"{_who(person)}\n\n"
        "به نظرم یه پیام براش بفرست مثلا ( تبلیغی یا خوش آمد گویی ) 😍"
    )


def trial_text(person: Person) -> str:
    return (
        "📢 | کاربر زیر اکانت تست رو گرفت :\n\n"
        f"{_who(person)}\n\n"
        "به نظرم یه پیام براش بفرست مثلا ( ترغیبش کن به خرید اکانت ) 😍\n\n"
        f"{_stamp()}"
    )


def wallet_purchase_text(
    person: Person,
    *,
    balance: int,
    plan_label: str,
    service_name: str,
    volume: str,
    days: int,
    price: int,
    discount_code: str | None = None,
) -> str:
    discount = f"\n🎫 کد تخفیف: `{discount_code}`" if discount_code else ""
    return (
        "❗️|💳 خرید جدید ( کیف پول )\n\n"
        "مشخصات کاربر\n"
        f"▫️آیدی کاربر: `{person.user_id}`\n"
        f"👨‍💼اسم کاربر: {person.name_line}\n"
        f"⚡️ نام کاربری: {person.username_line}\n"
        f"شماره تماس: {person.phone}\n\n"
        f"موجودی کاربر: {balance:,} تومان\n\n"
        "مشخصات سرویس\n"
        f"🚦سرور: {plan_label}\n"
        f"✏️ نام سرویس: `{service_name}`\n"
        f"🔋حجم سرویس: {volume}\n"
        f"⏰ مدت سرویس: {days} روز\n\n"
        f"💰مبلغ پرداختی: {price:,} تومان{discount}\n\n"
        f"{_stamp()}"
    )


def contact_buttons(user_id: int) -> list:
    return [
        [
            glass_inline_button("✉️ ارسال پیام", data=f"sendm_{user_id}"),
            glass_inline_button("👤 اطلاعات کاربر", data=f"UserInfo:{user_id}"),
        ]
    ]


async def deliver(log_type: LogType, text: str, user_id: int) -> None:
    """Send to the log destination, or to every admin when none is set. Never raises: a notice must not
    break the customer's own request."""
    buttons = contact_buttons(user_id)
    try:
        if await send_log_message(log_type, message=text, buttons=buttons) is not None:
            return
        for admin_id in ADMIN_ID:
            try:
                await Mdje.send_message(admin_id, text, buttons=buttons)
            except Exception as exc:
                logger.debug("Could not tell admin %s: %s", admin_id, exc)
    except Exception as exc:
        logger.warning("Could not send a %s notice: %s", log_type, exc)


async def notify_new_member(user_id: int, sender=None) -> None:
    await deliver(LogType.USER_REGISTRATION, new_member_text(await person_of(user_id, sender)), user_id)


async def notify_trial_taken(user_id: int, sender=None) -> None:
    await deliver(LogType.OTHER, trial_text(await person_of(user_id, sender)), user_id)


async def notify_wallet_purchase(user_id: int, **details) -> None:
    person = await person_of(user_id)
    await deliver(LogType.OTHER, wallet_purchase_text(person, **details), user_id)
