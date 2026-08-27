"""تبدیل متن فارسیِ آزاد (مثل «فردا ساعت ۹»، «امروز ۵ عصر»، «۲۰۲۶-۰۸-۲۷ ۰۹:۰۰»)
به یک datetime مشخص، تا کاربر مجبور نباشه از یک فرمت ثابت استفاده کنه.

پشتیبانی می‌شه از:
- "الان" / "همین الان"
- "امروز" / "فردا" / "پس‌فردا" (+ "ساعت" اختیاری) + عدد ساعت (+ :دقیقه اختیاری)
  + "صبح"/"ظهر"/"عصر"/"بعدازظهر"/"شب" اختیاری برای تشخیص قبل/بعد از ظهر
- فقط "ساعت H" بدون روز: اگه اون ساعت امروز نگذشته باشه، امروزه؛ وگرنه فردا
- تاریخ کامل به فرمت YYYY-MM-DD HH:MM یا YYYY/MM/DD HH:MM (میلادی)

اگه متن قابل تشخیص نباشه، None برمی‌گردونه (تا بات دوباره از کاربر بپرسه).
"""
import datetime
import re

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    # فallback به UTC+3:30 اگه tzdata در دسترس نبود
    TEHRAN_TZ = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def _normalize_digits(text: str) -> str:
    for i, digit in enumerate(_PERSIAN_DIGITS):
        text = text.replace(digit, str(i))
    return text


_EXPLICIT_DATE_RE = re.compile(
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})"
)
_TIME_RE = re.compile(r"(?:ساعت\s*)?(\d{1,2})(?::(\d{2}))?")


def parse_schedule_time(text: str, now: datetime.datetime | None = None) -> datetime.datetime | None:
    """متن رو پارس می‌کنه و یه datetime آگاه از timezone (به وقت تهران) برمی‌گردونه،
    یا None اگه نتونست بفهمه کاربر چی گفته."""
    text = _normalize_digits(text.strip())
    now = now or datetime.datetime.now(TEHRAN_TZ)

    if re.search(r"همین\s*الان|^الان$|الان\s*بفرست", text):
        return now

    m = _EXPLICIT_DATE_RE.search(text)
    if m:
        year, month, day, hour, minute = map(int, m.groups())
        try:
            return datetime.datetime(year, month, day, hour, minute, tzinfo=TEHRAN_TZ)
        except ValueError:
            return None

    target_day = None
    if "پس‌فردا" in text or "پس فردا" in text or "پسفردا" in text:
        target_day = now.date() + datetime.timedelta(days=2)
    elif "فردا" in text:
        target_day = now.date() + datetime.timedelta(days=1)
    elif "امروز" in text:
        target_day = now.date()

    tm = _TIME_RE.search(text)
    if not tm:
        return None  # روز رو فهمیدیم ولی ساعت رو نه (یا برعکس)؛ بهتره دوباره بپرسیم

    hour = int(tm.group(1))
    minute = int(tm.group(2)) if tm.group(2) else 0
    if hour > 23 or minute > 59:
        return None

    is_pm_hint = bool(re.search(r"عصر|شب|بعدازظهر|بعد از ظهر", text))
    if is_pm_hint and hour < 12:
        hour += 12

    if target_day is None:
        # فقط ساعت گفته شده، بدون روز: اگه نگذشته امروزه، وگرنه فردا
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return candidate

    candidate = datetime.datetime(
        target_day.year, target_day.month, target_day.day, hour, minute, tzinfo=TEHRAN_TZ
    )

    # اگه صریحاً "امروز" گفته شده، ساعت مبهمه (بدون صبح/عصر)، و اون ساعت با
    # تفسیرِ صبح از الان گذشته: قبل از رد کردنش، تفسیرِ عصر رو امتحان کن
    # (مثلاً الان ساعت ۹ شبه و کاربر گفته «امروز ساعت ۹»، منظورش تقریباً
    # قطعاً ۹ شبه، نه ۹ صبحی که ساعت‌ها پیش گذشته)
    is_am_hint = bool(re.search(r"صبح", text))
    if target_day == now.date() and candidate <= now and not is_pm_hint and not is_am_hint and hour < 12:
        pm_candidate = candidate.replace(hour=hour + 12)
        if pm_candidate > now:
            return pm_candidate

    return candidate
