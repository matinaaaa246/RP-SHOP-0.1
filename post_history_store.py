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
    بعداً بشه با /scheduled پیداش کرد و در صورت نیاز با deletePost پاکش کرد"""
    when = when or datetime.datetime.now(datetime.timezone.utc)
    entry = {
        "timestamp": when.astimezone(datetime.timezone.utc).isoformat(),
        "caption_preview": caption[:60],
        "image_count": image_count,
        "status": status,
        "image_urls": image_urls or [],
        "buffer_post_id": buffer_post_id,
    }
    with _lock:
        data = _load_all()
        data.setdefault(str(chat_id), []).append(entry)
        _save_all(data)


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
