"""
این فایل رو رو همون لپ‌تاپ (تو VS Code) اجرا کن، دقیقاً کنار bot.py و بقیه‌ی
فایل‌های پروژه.

قبلش:
۱) تو Firefox لاگینِ کاملِ اکانتِ فرعیِ اینستاگرام (king.827828) باش —
   بدون هیچ هشدار/پیامِ ناتموم.
۲) تو ترمینالِ VS Code این رو بزن:
       pip install browser-cookie3
۳) بعدش بزن:
       python import_cookies.py

اگه موفق بشه، یه فایل به اسم "ig_session" کنارِ همین فایل ساخته می‌شه.
همون فایل رو باید ببری رو سرور.
"""
import browser_cookie3
import instaloader

BROWSER = "firefox"
SESSION_FILE = "ig_session"

cookiejar = browser_cookie3.firefox(domain_name="instagram.com")
L = instaloader.Instaloader()
L.context._session.cookies.update({c.name: c.value for c in cookiejar})

username = L.test_login()
if not username:
    raise SystemExit(
        "لاگین از کوکی‌ها موفق نبود — مطمئن شو تو Chrome کامل و بدون هشدار "
        "لاگینِ اکانتِ فرعیِ اینستاگرام هستی، بعد دوباره این اسکریپت رو اجرا کن."
    )

L.context.username = username
L.save_session_to_file(filename=SESSION_FILE)
print(f"✅ سشن برای {username} ذخیره شد تو «{SESSION_FILE}».")
print("این فایل رو حالا باید ببری رو سرور، کنار بقیه‌ی فایل‌های بات.")
