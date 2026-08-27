"""ذخیره‌ی زمینه‌ی ثابت هر فروشگاه (برند/جنس/لحن) که فروشگاه‌دار یه‌بار
تنظیم می‌کنه و AI هر بار، بدون نیاز به توضیح دوباره، همونو رعایت می‌کنه.

یه فایل JSON ساده رو دیسک — برای مقیاس این بات (یه چت یا چندتا چت/فروشگاه
که هر کدوم زمینه‌ی خودشون رو دارن) کافیه و بین اجراهای مختلف بات هم باقی می‌مونه
(برخلاف dict تو حافظه که با ری‌استارت بات پاک می‌شه)."""
import json
import os
import threading

_STORE_PATH = os.path.join(os.path.dirname(__file__), "shop_profiles.json")
_lock = threading.Lock()


def _load_all() -> dict:
    if not os.path.exists(_STORE_PATH):
        return {}
    with open(_STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_all(data: dict) -> None:
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_profile(chat_id: int) -> str | None:
    """زمینه‌ی ثابت ثبت‌شده برای این چت رو برمی‌گردونه، یا None اگه چیزی ثبت نشده"""
    with _lock:
        return _load_all().get(str(chat_id))


def set_profile(chat_id: int, profile_text: str) -> None:
    """زمینه‌ی ثابت این چت رو ذخیره/جایگزین می‌کنه"""
    with _lock:
        data = _load_all()
        data[str(chat_id)] = profile_text
        _save_all(data)


def all_chat_ids() -> list:
    """همه‌ی chat_idهایی که حداقل یه‌بار زمینه‌ی ثابت فروشگاه رو تنظیم کردن"""
    with _lock:
        return [int(k) for k in _load_all().keys()]
