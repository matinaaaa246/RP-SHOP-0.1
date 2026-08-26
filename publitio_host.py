"""آپلود ویدیو به Publitio + حذف خودکارِ نیم‌ساعت بعد از انتشار
(که سقف فضای اکانت رایگان پر نشه).

مستقیم با REST API خود Publitio کار می‌کنه (بدون نیاز به publitio_python_sdk)
تا وابستگی به کتابخونه‌ای که موقع نصب مشکل داشت نداشته باشیم.

نیاز به این دو مقدار تو .env:
    PUBLITIO_API_KEY=...
    PUBLITIO_API_SECRET=...
(همون Key و Secret ای که از dashboard.publit.io -> API Keys & Tokens ساختی)
"""
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import datetime

import requests

logger = logging.getLogger(__name__)

PUBLITIO_API_KEY = os.environ["PUBLITIO_API_KEY"]
PUBLITIO_API_SECRET = os.environ["PUBLITIO_API_SECRET"]

CREATE_URL = "https://api.publit.io/v1/files/create"
DELETE_URL = "https://api.publit.io/v1/files/delete"

DELAY_AFTER_PUBLISH = datetime.timedelta(minutes=30)
SWEEP_INTERVAL_SECONDS = 60  # هر چند وقت یه‌بار چک کنه چیزی سررسیدش رسیده یا نه

# فایلی که لیست حذف‌های در انتظار رو نگه می‌داره، تا اگه بات ری‌استارت شد
# هم زمان‌بندی‌های حذف از دست نره
_PENDING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "publitio_pending_deletes.json")
_lock = threading.Lock()


def _auth_params() -> dict:
    """پارامترهای احراز هویت Publitio (key + timestamp + nonce + امضا)."""
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(8)
    signature = hashlib.sha1(f"{timestamp}{nonce}{PUBLITIO_API_SECRET}".encode()).hexdigest()
    return {
        "api_key": PUBLITIO_API_KEY,
        "api_timestamp": timestamp,
        "api_nonce": nonce,
        "api_signature": signature,
    }


MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def upload_video(video_path: str, timeout: int = 180) -> dict:
    """ویدیو رو آپلود می‌کنه و {"id": ..., "url_preview": ...} برمی‌گردونه.
    اگه خطای شبکه‌ای موقتی (قطعی SSL، تایم‌اوت و غیره) پیش بیاد، تا ۳ بار
    با فاصله دوباره امتحان می‌کنه؛ اگه بازم نشد یا Publitio خودش خطای واقعی
    برگردوند، RuntimeError پرتاب می‌کنه."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(video_path, "rb") as f:
                data = _auth_params()
                data["type"] = "video"
                resp = requests.post(CREATE_URL, data=data, files={"file": f}, timeout=timeout)
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            logger.warning(f"تلاش {attempt}/{MAX_RETRIES} برای اتصال به Publitio شکست خورد: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    else:
        raise RuntimeError(
            f"اتصال به Publitio بعد از {MAX_RETRIES} بار تلاش شکست خورد (مشکل شبکه/VPN): {last_error}"
        )

    if not resp.ok:
        raise RuntimeError(f"آپلود ویدیو به Publitio شکست خورد (کد {resp.status_code}): {resp.text[:300]}")

    result = resp.json()
    if not result.get("id"):
        raise RuntimeError(f"آپلود ویدیو به Publitio شکست خورد: {result}")

    url_preview = result.get("url_preview") or result.get("url_preview_original") or result.get("url_preview_full")
    return {"id": result["id"], "url_preview": url_preview}


def _delete_now(file_id: str) -> None:
    try:
        resp = requests.delete(f"{DELETE_URL}/{file_id}", params=_auth_params(), timeout=30)
        if resp.ok:
            logger.info(f"ویدیوی Publitio با id={file_id} پاک شد.")
        else:
            logger.warning(f"حذف ویدیوی Publitio (id={file_id}) شکست خورد (کد {resp.status_code}): {resp.text[:300]}")
    except Exception:
        logger.exception(f"خطا در حذف ویدیوی Publitio با id={file_id}")


def _load_pending() -> dict:
    if not os.path.exists(_PENDING_FILE):
        return {}
    try:
        with open(_PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("خطا در خوندن لیست حذف‌های در انتظار Publitio")
        return {}


def _save_pending(pending: dict) -> None:
    with open(_PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


def schedule_delete_at(file_id: str, published_at: datetime.datetime) -> None:
    """نیم‌ساعت بعد از published_at، فایل رو از Publitio پاک می‌کنه.
    زمان‌بندی روی دیسک ذخیره می‌شه تا با ری‌استارت بات از دست نره."""
    delete_at = published_at + DELAY_AFTER_PUBLISH
    with _lock:
        pending = _load_pending()
        pending[str(file_id)] = delete_at.isoformat()
        _save_pending(pending)


def _sweep_loop() -> None:
    while True:
        try:
            with _lock:
                pending = _load_pending()
                now = datetime.datetime.now(datetime.timezone.utc)
                due_ids = []
                remaining = {}
                for file_id, delete_at_str in pending.items():
                    delete_at = datetime.datetime.fromisoformat(delete_at_str)
                    if delete_at.tzinfo is None:
                        delete_at = delete_at.replace(tzinfo=datetime.timezone.utc)
                    if delete_at <= now:
                        due_ids.append(file_id)
                    else:
                        remaining[file_id] = delete_at_str
                if due_ids:
                    _save_pending(remaining)

            for file_id in due_ids:
                _delete_now(file_id)

        except Exception:
            logger.exception("خطا در چرخه‌ی پاک‌سازی خودکار Publitio")

        time.sleep(SWEEP_INTERVAL_SECONDS)


def start_background_cleaner() -> None:
    """یه ترد پس‌زمینه راه می‌ندازه که هر ۶۰ ثانیه چک می‌کنه کدوم ویدیوها
    سررسیدشون رسیده و پاکشون می‌کنه. باید یه‌بار موقع بالا اومدنِ بات صدا زده بشه."""
    thread = threading.Thread(target=_sweep_loop, daemon=True)
    thread.start()
    logger.info("پاک‌کننده‌ی خودکار Publitio راه‌اندازی شد.")
