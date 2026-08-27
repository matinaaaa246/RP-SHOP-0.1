"""ذخیره‌ی تاریخچه‌ی پست‌های هر فروشگاه (چت) — برای دستور /history، آرشیو
محصولات (/products)، پیشنهاد بهترین ساعت پست، و گزارش‌های خودکار (هفتگی/
یادآوری بی‌فعالیتی). مثل shop_profile_store.py، یه فایل JSON ساده رو دیسک؛
چیزی که بین اجراهای مختلف بات هم باقی می‌مونه."""
import collections
import datetime
import json
import os
import threading

_STORE_PATH = os.path.join(os.path.dirname(__file__), "post_history.json")
_lock = threading.Lock()


def _load_all() -> dict:
    if not os.path.exists(_STORE_PATH):
        return {}
    with open(_STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_all(data: dict) -> None:
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_post(
    chat_id: int,
    caption: str,
    image_count: int,
    status: str,
    when: "datetime.datetime | None" = None,
    image_urls: "list[str] | None" = None,
    buffer_post_id: "str | None" = None,
) -> None:
    """یه پست تازه‌منتشرشده/زمان‌بندی‌شده رو تو تاریخچه ثبت می‌کنه.
    status: "published" (همون‌موقع منتشر شد) یا "scheduled" (برای بعداً زمان‌بندی شد)
    image_urls: آدرس عکس(ها)ی پست، برای نمایش تصویر کوچیک تو آرشیو (/products)
    buffer_post_id: شناسه‌ی پست تو بافر (id برگشتی از createPost)، برای اینکه
    بعداً بشه با /scheduled پیداش کرد و در صورت نیاز با deletePost پاکش کرد

    instagram_url و stats اینجا ست نمی‌شن؛ چون در لحظه‌ی ساخته‌شدنِ پست تو
    بافر، لینک واقعیِ پستِ اینستاگرام هنوز وجود نداره (بافر فقط id خودش رو
    برمی‌گردونه). این دو تا بعداً با attach_instagram_link_by_buffer_id و
    save_stats پر می‌شن."""
    when = when or datetime.datetime.now(datetime.timezone.utc)
    entry = {
        "timestamp": when.astimezone(datetime.timezone.utc).isoformat(),
        "caption_preview": caption[:60],
        "image_count": image_count,
        "status": status,
        "image_urls": image_urls or [],
        "buffer_post_id": buffer_post_id,
        "instagram_url": None,
        "stats": None,  # {"likes":.., "views":.., "comments":.., "checked_at":..}
    }
    with _lock:
        data = _load_all()
        data.setdefault(str(chat_id), []).append(entry)
        _save_all(data)


def attach_instagram_link_by_buffer_id(chat_id: int, buffer_post_id: str, url: str) -> bool:
    """دقیقاً همون رکوردی که این buffer_post_id رو داره لینک‌دار می‌کنه — هم
    برای اتصالِ خودکارِ لینک بعد از انتشار (جایی که دقیقاً می‌دونیم دنبال
    کدوم پستیم)، هم برای اتصالِ لینکِ دستی بعد از تاییدِ کاربر (به کمکِ
    get_latest_unlinked). اگه رکورد پیدا نشد یا از قبل لینک داشت، False."""
    with _lock:
        data = _load_all()
        entries = data.get(str(chat_id), [])
        for e in entries:
            if e.get("buffer_post_id") == buffer_post_id:
                if e.get("instagram_url"):
                    return False
                e["instagram_url"] = url
                _save_all(data)
                return True
        return False


def get_latest_unlinked(chat_id: int) -> "dict | None":
    """جدیدترین رکوردی که buffer_post_id داره ولی هنوز instagram_url نداره رو
    برمی‌گردونه، بدون اینکه چیزی رو تغییر بده — برای نشون‌دادنِ پیش‌نمایشِ
    کپشن قبل از اینکه کاربر تایید کنه لینکِ دستی‌ای که فرستاده واقعاً مالِ
    همین پسته (تا لینکِ اشتباهی/نامربوط به‌اشتباه به یه پستِ دیگه وصل نشه).
    اگه هیچ رکورد بی‌لینکی نبود None برمی‌گردونه."""
    with _lock:
        data = _load_all()
    entries = data.get(str(chat_id), [])
    for e in reversed(entries):  # جدیدترین اول
        if e.get("buffer_post_id") and not e.get("instagram_url"):
            return e
    return None


def get_unlinked_entries(chat_id: int) -> list:
    """برخلاف get_latest_unlinked (که فقط جدیدترین رو می‌ده)، همه‌ی
    رکوردهایی که buffer_post_id دارن ولی هنوز instagram_url ندارن رو
    برمی‌گردونه (قدیمی‌ترین اول). برای این ساخته شده که موقعِ روشن‌شدنِ بات،
    بشه هر پستی که تایمرِ خودکارِ پیدا‌کردنِ لینکش (که فقط تو حافظه بود و با
    خاموش/روشن‌شدنِ بات از بین رفته) رو دوباره تو صف گذاشت."""
    with _lock:
        data = _load_all()
    entries = data.get(str(chat_id), [])
    return [e for e in entries if e.get("buffer_post_id") and not e.get("instagram_url")]


def save_stats(chat_id: int, buffer_post_id: str, stats: dict) -> None:
    """آمار (لایک/ویو/کامنت) یه پست رو، شناخته‌شده با buffer_post_id (نه
    instagram_url — چون بعضی وقت‌ها آمار از fallback بافر میاد و اصلاً
    لینکی در کار نیست)، تو تاریخچه ذخیره می‌کنه. برای کش کردن — که هر بار
    /history زدن دوباره نریم سراغ اینستاگرام/بافر و ریسک ۴۲۹/قفل‌شدنِ
    اکانت رو بالا نبریم."""
    entry_stats = dict(stats)
    entry_stats.setdefault("checked_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
    with _lock:
        data = _load_all()
        entries = data.get(str(chat_id), [])
        for e in entries:
            if e.get("buffer_post_id") == buffer_post_id:
                e["stats"] = entry_stats
                _save_all(data)
                return


def get_history(chat_id: int, limit: int = 10) -> list:
    """آخرین N پستِ ثبت‌شده‌ی این چت رو برمی‌گردونه (جدیدترین اول)"""
    with _lock:
        data = _load_all()
    entries = data.get(str(chat_id), [])
    return list(reversed(entries))[:limit]


def count_since(chat_id: int, since: datetime.datetime) -> int:
    """تعداد پست‌های ثبت‌شده‌ی این چت از یه زمان مشخص به بعد رو می‌شمره"""
    with _lock:
        data = _load_all()
    entries = data.get(str(chat_id), [])
    count = 0
    for entry in entries:
        ts = datetime.datetime.fromisoformat(entry["timestamp"])
        if ts >= since:
            count += 1
    return count


def count_on_date(chat_id: int, date: datetime.date, tz: datetime.tzinfo) -> int:
    """تعداد پست‌های منتشرشده/زمان‌بندی‌شده‌ی این چت که تاریخ‌شون (به وقت tz)
    دقیقاً برابر date هست رو می‌شمره. برای محدودیت «حداکثر ۹ پست در روز» استفاده
    می‌شه؛ منظور از «روز» تاریخِ زمانِ انتشار/زمان‌بندیِ پست (dueAt) است، نه
    زمانی که کاربر دستور رو داده."""
    with _lock:
        data = _load_all()
    entries = data.get(str(chat_id), [])
    count = 0
    for entry in entries:
        ts = datetime.datetime.fromisoformat(entry["timestamp"]).astimezone(tz)
        if ts.date() == date:
            count += 1
    return count


def most_common_hour(chat_id: int, tz: datetime.tzinfo) -> "int | None":
    """پرتکرارترین ساعتِ (به وقت tz) پست‌های قبلیِ این چت رو برمی‌گردونه (برای
    پیشنهاد «بهترین ساعت پست»)؛ اگه هیچ تاریخچه‌ای نبود، None برمی‌گردونه."""
    with _lock:
        data = _load_all()
    entries = data.get(str(chat_id), [])
    if not entries:
        return None
    hours = [
        datetime.datetime.fromisoformat(entry["timestamp"]).astimezone(tz).hour
        for entry in entries
    ]
    return collections.Counter(hours).most_common(1)[0][0]


def get_scheduled(chat_id: int) -> list:
    """پست‌های زمان‌بندی‌شده‌ی این چت که هنوز زمان انتشارشون نرسیده رو
    برمی‌گردونه (جدیدترین/نزدیک‌ترین اول)، فقط اونایی که buffer_post_id
    دارن (پست‌های قدیمی‌تر از قبل این فیچر ممکنه نداشته باشن و قابل پاک‌کردن
    از اینجا نیستن). برای دستور /scheduled استفاده می‌شه."""
    now = datetime.datetime.now(datetime.timezone.utc)
    with _lock:
        data = _load_all()
    entries = data.get(str(chat_id), [])
    scheduled = [
        e
        for e in entries
        if e["status"] == "scheduled"
        and e.get("buffer_post_id")
        and datetime.datetime.fromisoformat(e["timestamp"]) > now
    ]
    return list(reversed(scheduled))


def remove_by_buffer_id(chat_id: int, buffer_post_id: str) -> bool:
    """رکوردِ پستی با این buffer_post_id رو از تاریخچه‌ی این چت حذف می‌کنه
    (بعد از اینکه با deletePost تو بافر پاک شد). اگه پیدا نشد False برمی‌گردونه"""
    with _lock:
        data = _load_all()
        entries = data.get(str(chat_id), [])
        for i, e in enumerate(entries):
            if e.get("buffer_post_id") == buffer_post_id:
                entries.pop(i)
                _save_all(data)
                return True
        return False


def all_chat_ids() -> list:
    """همه‌ی chat_idهایی که حداقل یه پست تو تاریخچه‌شون ثبت شده"""
    with _lock:
        data = _load_all()
    return [int(k) for k in data.keys()]
