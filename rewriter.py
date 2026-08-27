"""ساخت کپشن تبلیغاتی/مارکتینگ‌پسند برای پست اینستاگرام آنلاین‌شاپ (سازگار با اوپن‌ای‌آی).

قابلیت‌ها:
- generate_caption: یک کپشن قوی و مشتری‌پسند با یه فراخوانی (نسخه‌ی فعلی، پیش‌فرض)
- پشتیبانی از «زمینه‌ی ثابت فروشگاه» (shop_profile) که هر بار بدون تکرار توضیح رعایت می‌شه
- تشخیص خودکار قیمت اصلی + تخفیف‌خورده تو توضیح کاربر، و خط‌خوردن قیمت اصلی
  تو کپشن نهایی (چون اینستاگرام مارک‌داون نداره، با کاراکتر یونیکد خط‌خورده انجام می‌شه)
- avoid: برای دکمه‌ی «دوباره بساز»، کپشن قبلی رو به AI می‌ده تا تکرارش نکنه
"""
import json
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

PRODUCT_SYSTEM_PROMPT_TEMPLATE = (
    "تو یک کپی‌رایتر تبلیغاتی حرفه‌ای برای یک فروشگاه اینستاگرامی فارسی‌زبان "
    "هستی و تو نوشتن کپشن‌های پرفروش و مشتری‌پسند تخصص داری. بر اساس توضیح خامی "
    "که کاربر درباره‌ی محصول می‌ده (ممکنه شامل اسم، قیمت، جنس، ویژگی‌ها باشه)، "
    "یک کپشن قوی برای پست اینستاگرام بنویس که:\n"
    "- از همون خط اول توجه رو جلب کنه (هوک قوی)\n"
    "- روی مزیت/حس خوبی که محصول به مشتری می‌ده تمرکز کنه، نه فقط توضیح خشک\n"
    "- لحن صمیمی، پرانرژی و متقاعدکننده داشته باشه (نه اغراق‌آمیز یا دروغ)\n"
    "- در پایان یک دعوت‌به‌اقدام (CTA) واضح داشته باشه، مثل «همین الان سفارش "
    "بده»، «برای خرید پیام بده» یا «تعداد محدوده»\n"
    "- حداکثر ۱۰۰ کلمه باشه\n"
    "اگه قیمتی تو توضیح کاربر بود، دقیقاً با همون عدد (بدون تغییر رقم یا فرمت) "
    "تو کپشن بیار. اگه هم قیمت اصلی هم قیمت تخفیف‌خورده تو توضیح بود، هر دو عدد "
    "رو دقیقاً عین همون چیزی که کاربر نوشته بیار (یکی قیمت قبل، یکی قیمت "
    "الان/تخفیف‌خورده) — مثلاً: «قیمت قبل ۱,۲۰۰,۰۰۰ الان ۹۵۰,۰۰۰ تومان». در "
    "انتها ۳-۴ هشتگ مرتبط فارسی اضافه کن.\n"
    "{shop_profile_block}"
    "فقط متن کپشن رو برگردون، بدون هیچ توضیح اضافه، بدون گیومه و بدون ```."
)

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_STRIKETHROUGH_CHAR = "\u0336"  # کاراکتر یونیکدِ ترکیبیِ خط‌خورده؛ چون اینستاگرام مارک‌داون نداره


def _normalize_digits(text: str) -> str:
    for i, digit in enumerate(_PERSIAN_DIGITS):
        text = text.replace(digit, str(i))
    return text


_PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[,،]\d{3})+|\d{4,})(?!\d)")


def _extract_two_prices(raw_description: str) -> "tuple[str, str] | None":
    """اگه دقیقاً دو عدد متفاوت (که می‌تونن قیمت باشن، حداقل ۴ رقمی) تو توضیح
    پیدا بشه، (قیمت_اصلی_بزرگ‌تر, قیمت_تخفیف‌خورده_کوچیک‌تر) رو به‌صورت رشته‌ی
    فقط-رقمی برمی‌گردونه؛ وگرنه None (یعنی نمی‌تونیم مطمئن باشیم دو قیمته)"""
    normalized = _normalize_digits(raw_description)
    matches = _PRICE_RE.findall(normalized)

    uniq_digits_only = []
    seen = set()
    for m in matches:
        digits_only = m.replace(",", "").replace("،", "")
        if digits_only not in seen:
            seen.add(digits_only)
            uniq_digits_only.append(digits_only)

    if len(uniq_digits_only) != 2:
        return None

    a, b = uniq_digits_only
    if int(a) == int(b):
        return None
    original, discounted = (a, b) if int(a) > int(b) else (b, a)
    return original, discounted


def _strike(text: str) -> str:
    """با گذاشتن کاراکتر یونیکد خط‌خورده بعد از هر حرف، متن رو تو اینستاگرام
    (که مارک‌داون نداره) به‌صورت واقعاً خط‌خورده نشون می‌ده"""
    return "".join(ch + _STRIKETHROUGH_CHAR for ch in text)


def _apply_price_strikethrough(caption: str, original_price_digits: str) -> str:
    """هرجا تو کپشن، عدد قیمت اصلی (چه با کاما نوشته شده باشه چه بدون کاما)
    پیدا بشه، با نسخه‌ی خط‌خورده‌اش جایگزین می‌شه"""
    grouped = f"{int(original_price_digits):,}"
    for variant in {original_price_digits, grouped}:
        if variant in caption:
            caption = caption.replace(variant, _strike(variant))
    return caption


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _call_ai(system_prompt: str, user_message: str, api_key: str, api_url: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.9,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(api_url, json=payload, headers=headers, timeout=45)
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            logger.warning(f"تلاش {attempt}/{MAX_RETRIES} برای اتصال به سرور هوش مصنوعی شکست خورد: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    else:
        raise RuntimeError(
            f"اتصال به سرور هوش مصنوعی بعد از {MAX_RETRIES} بار تلاش شکست خورد (مشکل شبکه/VPN): {last_error}"
        )

    if not resp.ok:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"سرور هوش مصنوعی خطا داد (کد {resp.status_code}): {detail}")

    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def generate_caption(
    raw_description: str,
    api_key: str,
    api_url: str,
    model: str,
    shop_profile: "str | None" = None,
    avoid: "str | None" = None,
) -> str:
    """یک کپشن قوی و مشتری‌پسند برمی‌گردونه (رشته‌ی متنی ساده).

    shop_profile: زمینه‌ی ثابت فروشگاه (برند/جنس/لحن) که باید هر بار رعایت بشه.
    avoid: اگه داده بشه (کپشن قبلیِ ناپسند)، از AI خواسته می‌شه کاملاً متفاوت
    از اون بنویسه (برای دکمه‌ی «دوباره بساز»)."""
    profile_block = (
        f"این هم زمینه‌ی ثابت فروشگاه که باید توش رعایت بشه: {shop_profile}\n"
        if shop_profile
        else ""
    )
    system_prompt = PRODUCT_SYSTEM_PROMPT_TEMPLATE.format(shop_profile_block=profile_block)

    user_message = raw_description
    if avoid:
        user_message += (
            "\n\n(این کپشن قبلاً پسند نشد، دوباره تکرارش نکن و کلمات/جمله‌بندی/"
            "هوکِ کاملاً متفاوتی بساز: " + avoid + ")"
        )

    caption = _call_ai(system_prompt, user_message, api_key, api_url, model).strip()
    # اگه AI به اشتباه کل کپشن رو تو گیومه برگردوند، پاکش می‌کنیم
    if len(caption) >= 2 and caption[0] in "\"'«" and caption[-1] in "\"'»":
        caption = caption[1:-1].strip()

    price_info = _extract_two_prices(raw_description)
    if price_info:
        caption = _apply_price_strikethrough(caption, price_info[0])

    return caption
