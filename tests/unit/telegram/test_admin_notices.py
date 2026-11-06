"""What the admins hear about new members, free trials and wallet purchases."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.logger import LogType
from app.telegram.shared.utils import notices
from app.telegram.shared.utils.notices import Person


@pytest.fixture
def stamp(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(notices, "_stamp", lambda: "1405/08/15 23:37:18")
    return "1405/08/15 23:37:18"


def test_new_member_notice(stamp) -> None:
    text = notices.new_member_text(Person(7529990101, "Pardis", "pardis_x"))
    assert text == (
        "📢 | یه گل جدید عضو ربات شد :\n\n"
        "نام و نام خانوادگی: Pardis\n"
        "نام کاربری: @pardis_x\n"
        "آیدی عددی: `7529990101`\n\n"
        "به نظرم یه پیام براش بفرست مثلا ( تبلیغی یا خوش آمد گویی ) 😍"
    )


def test_a_member_without_a_name_or_username_still_reads_well() -> None:
    text = notices.new_member_text(Person(5))
    assert "نام و نام خانوادگی: نامشخص" in text and "نام کاربری: ندارد" in text


def test_trial_notice_ends_with_the_time(stamp) -> None:
    text = notices.trial_text(Person(733536855, "Mamad", "uuuu22"))
    assert text.startswith("📢 | کاربر زیر اکانت تست رو گرفت :")
    assert "آیدی عددی: `733536855`" in text
    assert "ترغیبش کن به خرید اکانت" in text
    assert text.endswith(stamp)


def test_wallet_purchase_notice(stamp) -> None:
    person = Person(126980171, "Atabak", "atabak_g", "")
    text = notices.wallet_purchase_text(
        person,
        balance=123_855,
        plan_label="DE-1 | 30 روزه | 41 گیگابایت",
        service_name="Mrd-126980171-62578",
        volume="41 گیگابایت",
        days=30,
        price=45_000,
    )
    assert text.splitlines()[0] == "❗️|💳 خرید جدید ( کیف پول )"
    for line in (
        "مشخصات کاربر",
        "▫️آیدی کاربر: `126980171`",
        "👨‍💼اسم کاربر: Atabak",
        "⚡️ نام کاربری: @atabak_g",
        "موجودی کاربر: 123,855 تومان",
        "مشخصات سرویس",
        "🚦سرور: DE-1 | 30 روزه | 41 گیگابایت",
        "✏️ نام سرویس: `Mrd-126980171-62578`",
        "🔋حجم سرویس: 41 گیگابایت",
        "⏰ مدت سرویس: 30 روز",
        "💰مبلغ پرداختی: 45,000 تومان",
    ):
        assert line in text
    assert text.endswith(stamp) and "کد تخفیف" not in text


def test_a_discount_code_is_shown_when_used(stamp) -> None:
    text = notices.wallet_purchase_text(
        Person(1), balance=0, plan_label="x", service_name="s", volume="1", days=1, price=1, discount_code="OFF20"
    )
    assert "🎫 کد تخفیف: `OFF20`" in text


def test_buttons_message_the_customer_or_open_their_page() -> None:
    rows = notices.contact_buttons(42)
    assert [button.type.data for row in rows for button in row] == [b"sendm_42", b"UserInfo:42"]


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch):
    log = SimpleNamespace(logged=[], dms=[], destination=True, dm_fails=False)

    async def send_log_message(log_type, **kwargs):
        log.logged.append((log_type, kwargs))
        return object() if log.destination else None

    class FakeClient:
        async def send_message(self, entity, text, **kwargs):
            if log.dm_fails:
                raise RuntimeError("blocked")
            log.dms.append((entity, text, kwargs))

    monkeypatch.setattr(notices, "send_log_message", send_log_message)
    monkeypatch.setattr(notices, "Mdje", FakeClient())
    monkeypatch.setattr(notices, "ADMIN_ID", [1, 2])
    return log


def test_a_notice_goes_to_the_log_destination_only_when_there_is_one(sink) -> None:
    asyncio.run(notices.deliver(LogType.USER_REGISTRATION, "hello", 42))
    assert [entry[0] for entry in sink.logged] == [LogType.USER_REGISTRATION]
    assert sink.logged[0][1]["message"] == "hello" and sink.dms == []


def test_without_a_destination_every_admin_is_told(sink) -> None:
    sink.destination = False
    asyncio.run(notices.deliver(LogType.OTHER, "hello", 42))
    assert [dm[0] for dm in sink.dms] == [1, 2]
    assert all(dm[1] == "hello" and dm[2]["buttons"] for dm in sink.dms)


def test_a_failing_notice_never_raises(sink, monkeypatch: pytest.MonkeyPatch) -> None:
    sink.destination = False
    sink.dm_fails = True
    asyncio.run(notices.deliver(LogType.OTHER, "hello", 42))

    async def broken(*args, **kwargs):
        raise RuntimeError("log down")

    monkeypatch.setattr(notices, "send_log_message", broken)
    asyncio.run(notices.deliver(LogType.OTHER, "hello", 42))


def test_person_comes_from_the_profile_and_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    class Crud:
        async def read_user(self, user_id):
            return SimpleNamespace(number="09120000000")

    monkeypatch.setattr(notices, "UserCRUD", Crud)
    sender = SimpleNamespace(first_name="Ali", last_name="Reza", username="ali_r")
    person = asyncio.run(notices.person_of(9, sender))
    assert person == Person(9, "Ali Reza", "ali_r", "09120000000")


def test_person_survives_an_unreadable_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    class Crud:
        async def read_user(self, user_id):
            raise RuntimeError("db down")

    class Client:
        async def get_entity(self, user_id):
            raise RuntimeError("no such peer")

    monkeypatch.setattr(notices, "UserCRUD", Crud)
    monkeypatch.setattr(notices, "Mdje", Client())
    assert asyncio.run(notices.person_of(9)) == Person(9)
