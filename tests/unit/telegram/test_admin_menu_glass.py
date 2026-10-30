"""The admin menus are glass by default, renamable, and a press still reaches the plain-label handlers."""

from __future__ import annotations

import asyncio
import contextvars
from types import SimpleNamespace

import pytest
from telethon import Button
from telethon.tl.types import MessageEntityCustomEmoji

from app.db.crud import keyboards as keyboards_crud
from app.models.panel.keyboard import OTHER_SECTION, SECTION_PREFIXES
from app.telegram.keyboards import admin_menu
from app.telegram.keyboards.admin import Panel_Admin_Buttons, panel_back
from app.telegram.keyboards.common import split_button_label
from app.telegram.keyboards.registry import KEYBOARD_BUTTON_DEFAULTS, KEYBOARD_BUTTON_TITLES
from app.telegram.middlewares.admin import AdminContextMiddleware
from app.utils.admin_buttons import ADMIN_ITEMS, MENU_LAYOUTS
from app.utils.text.glass import GLASS_LEFT, GLASS_RIGHT, glass_text


def _texts(menu: list[list]) -> list[str]:
    return [(getattr(button, "button", button)).text for row in menu for button in row]


def _row(text: str = "", style: str | None = None, icon: int | None = None):
    return SimpleNamespace(button_text=text, button_style=style, button_icon=icon)


@pytest.fixture
def menus(monkeypatch: pytest.MonkeyPatch):
    """Fresh admin-menu state; the stored buttons are whatever the test puts in ``stored``."""
    stored: dict[str, SimpleNamespace] = {}

    async def get_buttons_by_keys(self, keys):
        return {key: stored[key] for key in keys if key in stored}

    monkeypatch.setattr(keyboards_crud.KeyboardButtonCRUD, "get_buttons_by_keys", get_buttons_by_keys)
    monkeypatch.setattr(admin_menu, "_built_version", -1)
    yield stored
    # Leave the shared menus as the defaults for the next test.
    stored.clear()
    keyboards_crud.invalidate_keyboard_button_cache()
    asyncio.run(admin_menu.ensure_admin_menus())


def test_every_admin_button_is_listed_for_renaming() -> None:
    assert set(ADMIN_ITEMS) <= set(KEYBOARD_BUTTON_TITLES)
    assert {key: KEYBOARD_BUTTON_DEFAULTS[key] for key in ADMIN_ITEMS} == {
        key: item.label for key, item in ADMIN_ITEMS.items()
    }
    assert all(key.startswith("ad.") for key in ADMIN_ITEMS)
    assert ("ad.", "admin_menu") in SECTION_PREFIXES and OTHER_SECTION != "admin_menu"


def test_layouts_only_use_known_buttons() -> None:
    for rows in MENU_LAYOUTS.values():
        assert all(key in ADMIN_ITEMS for row in rows for key in row)


def test_default_menu_is_glass() -> None:
    labels = _texts(Panel_Admin_Buttons)
    assert labels, "the main admin menu is empty"
    assert all(text.startswith(GLASS_LEFT) and text.endswith(GLASS_RIGHT) for text in labels)
    assert glass_text("💳 تنظیمات درگاه") in labels
    assert _texts(panel_back) == [glass_text("🔙 بازگشت به پنل")]


def test_glass_press_is_turned_back_into_the_plain_label() -> None:
    assert admin_menu.restore_press(glass_text("💳 تنظیمات درگاه")) == "💳 تنظیمات درگاه"
    assert admin_menu.restore_press("  " + glass_text("🏠") + " ") == "🏠"
    assert admin_menu.restore_press("🏠") is None
    assert admin_menu.restore_press("hello") is None
    assert admin_menu.restore_press(None) is None


def test_restore_message_rewrites_text_and_drops_the_parsed_cache() -> None:
    message = SimpleNamespace(message=glass_text("📦 بکاپ ربات"), _text="stale")
    assert admin_menu.restore_message(message)
    assert (message.message, message._text) == ("📦 بکاپ ربات", None)
    other = SimpleNamespace(message="سلام", _text="x")
    assert not admin_menu.restore_message(other)
    assert other.message == "سلام"


def test_rename_and_icon_rebuild_the_menu_in_place(menus) -> None:
    menus["ad.backup"] = _row("「 💾 نسخه پشتیبان」", "glass", 5368324170671202286)
    keyboards_crud.invalidate_keyboard_button_cache()
    before = Panel_Admin_Buttons

    asyncio.run(admin_menu.ensure_admin_menus())

    assert Panel_Admin_Buttons is before
    buttons = [getattr(b, "button", b) for row in Panel_Admin_Buttons for b in row]
    renamed = next(b for b in buttons if b.text == "「 💾 نسخه پشتیبان」")
    assert renamed.style.icon == 5368324170671202286
    assert glass_text("📦 بکاپ ربات") in admin_menu._history
    assert admin_menu.restore_press("「 💾 نسخه پشتیبان」") == "📦 بکاپ ربات"


def test_rename_without_brackets_keeps_the_glass_look(menus) -> None:
    menus["ad.stats"] = _row("آمار من", "glass")
    keyboards_crud.invalidate_keyboard_button_cache()
    asyncio.run(admin_menu.ensure_admin_menus())
    assert glass_text("آمار من") in _texts(Panel_Admin_Buttons)
    assert admin_menu.restore_press(glass_text("آمار من")) == "👥 آمار گیری"


def test_an_explicit_colour_shows_the_stored_text_as_it_is(menus) -> None:
    menus["ad.stats"] = _row("آمار من", "success")
    keyboards_crud.invalidate_keyboard_button_cache()
    asyncio.run(admin_menu.ensure_admin_menus())
    buttons = [getattr(b, "button", b) for row in Panel_Admin_Buttons for b in row]
    colored = next(b for b in buttons if b.text == "آمار من")
    assert colored.style.bg_success
    assert admin_menu.restore_press("آمار من") == "👥 آمار گیری"


def test_menus_are_not_rebuilt_while_nothing_changed(menus, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    real = keyboards_crud.KeyboardButtonCRUD.get_buttons_by_keys

    async def counting(self, keys):
        calls.append(1)
        return await real(self, keys)

    keyboards_crud.invalidate_keyboard_button_cache()
    asyncio.run(admin_menu.ensure_admin_menus())
    monkeypatch.setattr(keyboards_crud.KeyboardButtonCRUD, "get_buttons_by_keys", counting)
    asyncio.run(admin_menu.ensure_admin_menus())
    assert calls == []
    keyboards_crud.invalidate_keyboard_button_cache()
    asyncio.run(admin_menu.ensure_admin_menus())
    assert calls == [1]


def _ctx(text: str, *, admin: bool = True):
    message = SimpleNamespace(message=text, _text=text)
    event = SimpleNamespace(message=message)
    return SimpleNamespace(
        event=event,
        is_admin=admin,
        is_newmessage=True,
        user_id=7,
        extras={},
    ), message


def test_middleware_restores_an_admins_press(menus, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_step(user_id):
        return "panel"

    monkeypatch.setattr("app.telegram.middlewares.admin.get_step", no_step)
    ctx, message = _ctx(glass_text("🔐 قفل چنل ها"))
    asyncio.run(AdminContextMiddleware().before(ctx))
    assert message.message == "🔐 قفل چنل ها"


def test_middleware_leaves_a_name_typed_in_the_keyboard_manager(menus, monkeypatch: pytest.MonkeyPatch) -> None:
    async def editing(user_id):
        return "edit_keyboard:ad.backup:6"

    monkeypatch.setattr("app.telegram.middlewares.admin.get_step", editing)
    typed = glass_text("📦 بکاپ ربات")
    ctx, message = _ctx(typed)
    asyncio.run(AdminContextMiddleware().before(ctx))
    assert message.message == typed


def test_middleware_ignores_other_users(menus) -> None:
    typed = glass_text("📦 بکاپ ربات")
    ctx, message = _ctx(typed, admin=False)
    asyncio.run(AdminContextMiddleware().before(ctx))
    assert message.message == typed


def test_inline_buttons_are_glass_only_inside_an_admin_update() -> None:
    admin_menu.install_inline_glass()
    admin_menu.install_inline_glass()  # twice must not double-wrap

    assert Button.inline("📊 آمار", data="x").text == "📊 آمار"

    def as_admin():
        admin_menu.enter_admin_view()
        return (
            Button.inline("📊 آمار", data="x").text,
            Button.inline(glass_text("📊 آمار"), data="x").text,
            Button.url("سایت", url="https://example.com").text,
            Button.inline(text="🔙", data="back").text,
            Button.inline(" ", data="blank").text,
        )

    inline, already, url, keyword, blank = contextvars.copy_context().run(as_admin)
    assert inline == already == glass_text("📊 آمار")
    assert url == glass_text("سایت")
    assert keyword == glass_text("🔙")
    assert blank == " "
    # The admin view lived in the copied context only.
    assert Button.inline("📊 آمار", data="x").text == "📊 آمار"


def test_inline_glass_keeps_callback_data() -> None:
    admin_menu.install_inline_glass()

    def as_admin():
        admin_menu.enter_admin_view()
        return Button.inline("x", data="pay:1").type.data

    assert contextvars.copy_context().run(as_admin) == b"pay:1"


def _custom_emoji_message(text: str, *spans: tuple[int, int, int]):
    entities = [MessageEntityCustomEmoji(offset=o, length=n, document_id=d) for o, n, d in spans]
    return SimpleNamespace(message=text, entities=entities)


def test_label_without_animated_emoji_is_unchanged() -> None:
    assert split_button_label(SimpleNamespace(message="  بکاپ  ", entities=None)) == ("بکاپ", None)


def test_animated_emoji_becomes_the_icon_and_leaves_the_name() -> None:
    # "🔥" is two UTF-16 units.
    message = _custom_emoji_message("🔥 بکاپ", (0, 2, 555))
    assert split_button_label(message) == ("بکاپ", 555)


def test_animated_emoji_in_the_middle_and_after_a_bmp_character() -> None:
    message = _custom_emoji_message("ab 🔥 cd", (3, 2, 77))
    assert split_button_label(message) == ("ab cd", 77)
    # Offsets after an astral character still line up.
    after_astral = _custom_emoji_message("😀 x 🔥", (5, 2, 9))
    assert split_button_label(after_astral) == ("😀 x", 9)


def test_only_an_emoji_leaves_an_empty_name() -> None:
    assert split_button_label(_custom_emoji_message("🔥", (0, 2, 5))) == ("", 5)


def test_admin_only_screens_draw_glass_buttons_once() -> None:
    from app.services.telegram.rich_message import rt_glass
    from app.telegram.keyboards.common import admin_callback_button, admin_copy_button, admin_webview_button

    plain = admin_callback_button("📊 آمار", b"x")
    again = admin_callback_button(glass_text("📊 آمار"), b"x")
    assert plain.text == again.text == glass_text("📊 آمار")
    assert plain.type.data == b"x"
    assert admin_copy_button("کپی", "link").text == glass_text("کپی")
    assert admin_webview_button("باز", "https://example.com").text == glass_text("باز")
    assert rt_glass("🔄 بروزرسانی").text == glass_text("🔄 بروزرسانی")
    assert rt_glass(glass_text("🔄 بروزرسانی")).text == glass_text("🔄 بروزرسانی")


def test_settings_menu_navigation_buttons_are_glass() -> None:
    from app.telegram.keyboards import settings as settings_keyboards

    sections = settings_keyboards.SETTINGS_MENU_SECTIONS
    buttons = [
        settings_keyboards._settings_home_button(),
        *(settings_keyboards._settings_section_button(section) for section in sections),
        *settings_keyboards._settings_nav_buttons(sections[1].key),
    ]
    assert len(buttons) >= 3
    assert all(b.text.startswith(GLASS_LEFT) and b.text.endswith(GLASS_RIGHT) for b in buttons)
    rich = settings_keyboards._settings_rich_home_button()
    assert rich.text.text == glass_text("🏠 فهرست بخش‌ها")
