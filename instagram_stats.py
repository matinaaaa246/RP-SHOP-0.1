"""گرفتنِ تعداد لایک/ویو/کامنتِ یه پست اینستاگرام از روی لینکش، با instaloader.

⚠️ نکات مهمِ قبل از استفاده (این‌ها واقعیت فعلیِ اینستاگرام/instaloader
هستن، نه یه احتیاطِ الکی):

- بدون لاگین: بعد از چند تا درخواست (اغلب کمتر از ۱۰ تا) با خطای ۴۲۹
  (Too Many Requests) روبرو می‌شی. یعنی برای استفاده‌ی مکرر (هر بار که
  /history می‌زنی یا چند تا پست رو چک می‌کنی) عملاً لاگین لازمه.
- لاگین کردنِ مستقیم و مکرر داخل بات (هر بار از نو) خیلی زود باعث
  چالش امنیتیِ اینستاگرام (checkpoint) یا قفل موقت اکانت می‌شه. برای همینه
  که این ماژول لاگین نمی‌کنه؛ فقط یه فایل سشن که یه‌بار جدا ساختیم رو لود
  می‌کنه (نگاه کن به instagram_login.py).
- به‌شدت توصیه می‌شه: از یه اکانت اینستاگرامِ فرعی و جدا برای این کار
  استفاده کنی، نه اکانت اصلیِ شاپ. اسکرپ کردن (حتی پیج خودت) خلاف قوانین
  استفاده‌ی اینستاگرامه؛ ریسکش (قفل شدن/چالش امنیتی) باید بیفته رو یه
  اکانت فرعی، نه چیزی که واقعاً بهش نیاز داری.
- پست‌های عکسی اصلاً «ویو» ندارن؛ فقط ویدیو/ریلز ویو داره (views برای
  عکس همیشه None برمی‌گرده).
- اینستاگرام مرتب endpoint هاش رو عوض می‌کنه؛ اگه یهو همه‌چی خطا داد،
  اول instaloader رو آپدیت کن (pip install -U instaloader).
"""
import re

import instaloader

SESSION_FILE = "ig_session"  # با instagram_login.py ساخته می‌شه (یه‌بار)

_L = instaloader.Instaloader(
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    quiet=True,
)

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
_session_loaded_for: "str | None" = None


def extract_shortcode(url: str) -> "str | None":
    """کد کوتاهِ پست رو از لینک اینستاگرام درمیاره (مثلاً از
    instagram.com/p/ABC123/ → ABC123). اگه لینک معتبر نبود None می‌ده."""
    m = _SHORTCODE_RE.search(url.strip())
    return m.group(1) if m else None


def ensure_logged_in(username: str) -> None:
    """فایل سشنِ ذخیره‌شده رو لود می‌کنه (فقط یه‌بار در طول اجرای برنامه).
    اگه فایل سشن نبود، FileNotFoundError می‌ده — یعنی اول باید
    instagram_login.py رو اجرا کنی."""
    global _session_loaded_for
    if _session_loaded_for == username:
        return
    _L.load_session_from_file(username, filename=SESSION_FILE)
    _session_loaded_for = username


def find_latest_post_link(username: str, caption_hint: str = "", max_posts: int = 5) -> "str | None":
    """آخرین پست‌های پروفایلِ شاپ رو نگاه می‌کنه تا لینکِ پستی که خودِ بات
    تازه منتشر کرده رو خودکار پیدا کنه (بدون اینکه کسی دستی از اینستاگرام
    لینک کپی کنه).

    caption_hint: بخشی از کپشنِ همون پستی که فرستادیم. اگه پستِ جدیدِ
    پروفایل کپشنش این تکه رو داشته باشه، مطمئن می‌شیم همونیه که خودمون
    فرستادیم (نه یه پستِ دیگه که صاحبِ شاپ جدا/دستی گذاشته). اگه خالی
    باشه، صرفاً جدیدترین پستِ پروفایل برگردونده می‌شه.
    فقط max_posts پستِ اول چک می‌شه (برای کم‌کردن تعداد درخواست‌ها)."""
    profile = instaloader.Profile.from_username(_L.context, username)
    hint = (caption_hint or "").strip()[:40]
    checked = 0
    for post in profile.get_posts():  # جدیدترین اول
        if not hint or hint in (post.caption or ""):
            return f"https://www.instagram.com/p/{post.shortcode}/"
        checked += 1
        if checked >= max_posts:
            break
    return None


def get_post_stats(url: str) -> dict:
    """لایک/ویو/کامنتِ یه پست رو از روی لینکش برمی‌گردونه.

    خروجی: {"likes": int, "views": int|None, "comments": int,
             "is_video": bool, "shortcode": str}

    اگه لینک معتبر نباشه ValueError، و اگه هنوز لاگین (ensure_logged_in)
    انجام نشده باشه FileNotFoundError پرتاب می‌شه."""
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise ValueError("این یه لینک معتبر پست/ریلز اینستاگرام نیست.")
    post = instaloader.Post.from_shortcode(_L.context, shortcode)
    views = None
    if post.is_video:
        # اینستاگرام این اواخر برای ریلزها اغلب video_view_count رو خالی
        # (None) برمی‌گردونه؛ عددِ ویوی واقعی (همونی که تو اپ نشون داده
        # می‌شه) رو حالا باید از video_play_count گرفت. video_view_count
        # رو هم به‌عنوان fallback نگه می‌داریم برای ویدیوهای غیرِ ریلز که
        # ممکنه هنوز این فیلد رو داشته باشن.
        views = post.video_play_count
        if views is None:
            views = post.video_view_count
    return {
        "likes": post.likes,
        "comments": post.comments,
        "views": views,
        "is_video": post.is_video,
        "shortcode": shortcode,
    }
