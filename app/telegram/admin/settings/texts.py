"""Text templates for admin settings."""

API_KEY_LOGIN_MODE_FLOW = {
    "none": "phone_verified",
    "phone_verified": "all",
    "all": "none",
}

KEYBOARD_PAGE_TITLES = {
    1: "📄 **صفحه 1 از 6:** دکمه‌های منوی اصلی",
    2: "📄 **صفحه 2 از 6:** دکمه‌های بخش سرویس‌های من",
    3: "📄 **صفحه 3 از 6:** دکمه‌های افزایش موجودی",
    4: "📄 **صفحه 4 از 6:** دکمه‌های خرید سرویس",
    5: "📄 **صفحه 5 از 6:** دکمه‌های نمایندگی",
    6: "📄 **صفحه 6 از 6:** دکمه‌های منوی مدیر",
}

TEXT_MANAGEMENT_INTRO = "📝 **مدیریت متن‌های ربات**\n\nیکی از بخش‌های زیر را انتخاب کنید:"

KEYBOARD_MANAGEMENT_INTRO = f"⌨️ یکی از دکمه‌های کیبورد را برای ویرایش انتخاب کنید:\n\n{KEYBOARD_PAGE_TITLES[1]}"
