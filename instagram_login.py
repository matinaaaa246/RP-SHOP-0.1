"""فقط یه‌بار، دستی، رو سرور اجراش کن:

    python instagram_login.py

با یوزرنیم/پسورد یه اکانت اینستاگرامِ فرعی (نه اکانت اصلیِ شاپ!) لاگین
می‌کنه و فایل سشن (ig_session) رو می‌سازه. بعد از این، خودِ بات هیچ‌وقت
لاگین نمی‌کنه، فقط همین فایل سشن رو لود می‌کنه — چون لاگین کردنِ مکرر و
خودکار همونیه که باعث چالش امنیتی/قفل شدن اکانت می‌شه.

اگه اکانت 2FA داشته باشه، خودش موقع اجرا کد رو می‌پرسه.
اگه اینستاگرام «چالش امنیتی» (checkpoint) بخواد، یه لینک می‌ده که باید تو
مرورگر بازش کنی و تاییدش کنی، بعد دوباره این اسکریپت رو اجرا کن.

فایل ig_session که ساخته می‌شه شامل کوکیِ سشنه، نه پسورد؛ ولی بازم یه
فایل حساسه — تو .gitignore بذارش و جایی که پابلیک نیست نگهش دار."""
import getpass

import instaloader

from instagram_stats import SESSION_FILE

L = instaloader.Instaloader()
username = input("یوزرنیم اکانتِ فرعیِ اینستاگرام: ").strip()
password = getpass.getpass("پسورد: ")

L.login(username, password)  # اگه 2FA فعال باشه، خودش کد ازت می‌خواد
L.save_session_to_file(filename=SESSION_FILE)
print(f"✅ لاگین موفق بود؛ سشن تو «{SESSION_FILE}» ذخیره شد.")
print("این username رو تو .env به اسم IG_SCRAPER_USERNAME ذخیره کن.")
