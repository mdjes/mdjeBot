# MdjeBot

[![Python](https://img.shields.io/badge/Python-3.14-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-AGPL--3.0%20%2B%20Commons%20Clause-orange)](LICENSE)
[![Telethon](https://img.shields.io/badge/Telethon-1.44+-0088cc?logo=telegram)](https://github.com/LonamiWebs/Telethon)
[![Docs](https://img.shields.io/badge/Docs-GitHub%20Pages-blue?logo=github)](https://mdjes.github.io/mdjeBot/)

**ربات‌ساز** تلگرام برای ساخت ربات فروش وی‌پی‌ان روی پنل [پاسارگارد](https://github.com/PasarGuard/panel)، توسعه و نگهداری توسط **Mdje**.

شما تعرفهٔ ماهانه را تعیین می‌کنید؛ مشتری تعرفه را می‌خرد، در @BotFather یک ربات می‌سازد و **توکنش** را برای ربات‌ساز می‌فرستد؛ ربات‌ساز همین سورس را با آن توکن به یک **ربات فروش مستقل** برای او تبدیل و اجرا می‌کند (کانتینر، دیتابیس و Redis جدا).

**[مستندات کامل (فارسی)](https://mdjes.github.io/mdjeBot/)**

## نصب سریع (لینوکس)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/mdjes/MdjeBot/main/scripts/mdjebot.sh)
```

اسکریپت `API_ID` و `API_HASH`، **توکن ربات‌ساز** (یک ربات جدید از @BotFather) و **آیدی عددی ادمین** را می‌پرسد و بقیه را خودکار انجام می‌دهد. بعد از نصب: `mdjebot`

## ربات‌ساز چه می‌کند؟

| بخش | امکانات |
|------|---------|
| کاربر | انتخاب تعرفه ← پرداخت (کیف پول / کارت‌به‌کارت) ← ارسال توکن ← ساخت خودکار ربات؛ تست رایگان، تمدید، تغییر ادمین |
| ادمین (داخل تلگرام) | تعرفه‌ها، پرداخت‌ها و رسیدها، ربات‌ها (شروع/توقف/لاگ/دیپلوی)، کاربران، کارت‌ها، کد تخفیف، تنظیمات، ارسال همگانی، به‌روزرسانی همهٔ ربات‌ها |
| اشتراک | ماهانه؛ یادآوری تمدید، توقف خودکار در پایان و حذف پس از مهلت |
| امنیت | توکن‌ها رمزنگاری‌شده، پیام حاوی توکن پاک می‌شود، هر ربات کانتینر و دیتابیس جدا دارد |

راهنمای کامل: [مستندات ربات‌ساز](https://mdjes.github.io/mdjeBot/docs/builder) و کد در [`builder/`](builder/).

## وب‌اپ (Telegram Mini App)

یک پنل کاربری وب برای مشتریان (مشاهده سرویس‌ها، تمدید، شارژ کیف پول) در مسیر [`frontend/`](frontend/) قرار دارد. برای راه‌اندازی و بیلد به [`frontend/README.md`](frontend/README.md) مراجعه کنید؛ در بیلد Docker به‌صورت خودکار ساخته می‌شود.

<p align="center">
  <img src=".github/assets/webapp-user.png" alt="وب‌اپ کاربر" width="100%">
</p>

<p align="center">
  <img src=".github/assets/webapp-admin.png" alt="پنل مدیریت" width="100%">
</p>

## لایسنس

[GNU AGPL-3.0 + Commons Clause](LICENSE)

- استفاده از ربات برای فروش وی‌پی‌ان خودتان **آزاد است**.
- فروش، اجاره یا فروش نصب و هاست خود ربات (با یا بدون تغییر) بدون اجازه کتبی **ممنوع است**.
- نام «MdjeBot» و لوگو آن بدون اجازه قابل استفاده نیست.

## منشأ پروژه

MdjeBot فورکی تغییریافته از پروژهٔ PasarguardBot اثر Amir Kenzo است که تحت همان لایسنس (AGPL-3.0 + Commons Clause) منتشر شده است. اعلان‌های لایسنس و حق نشر اصلی در فایل [LICENSE](LICENSE) حفظ شده و جزئیات تغییرات در [NOTICE](NOTICE) آمده است.
