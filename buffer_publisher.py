"""انتشار پست در اینستاگرام از طریق GraphQL API بافر (Buffer)
پشتیبانی از یک عکس یا چند عکس با هم (پست کروسل/چندعکسی).

چون دسترسی مستقیم به Instagram Graph API / فیسبوک از ایران فیلتره، به‌جاش
از بافر که به‌عنوان پارتنر رسمی متا به اینستاگرام وصله استفاده می‌کنیم.

نکته‌ی مهم: بافر عکس رو خودش دانلود نمی‌کنه که ما بفرستیم؛ باید آدرس‌های
عمومی و مستقیم (https, بدون نیاز به لاگین) از عکس‌ها بهش بدیم تا خودش بگیرتشون.
"""
import datetime
import logging
import time

import requests

logger = logging.getLogger(__name__)

BUFFER_API_URL = "https://api.buffer.com"

# چندبار امتحان کنه وقتی خطای شبکه‌ای موقتی (قطعی SSL، تایم‌اوت و غیره) پیش
# میاد — خصوصاً به‌خاطر کیفیت نامنظم اتصال/VPN از ایران به api.buffer.com.
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def _post_with_retry(payload: dict, api_key: str, timeout: int) -> requests.Response:
    """درخواست POST به بافر رو با چندبار تلاش می‌فرسته. فقط خطاهای شبکه‌ای
    (قطعی اتصال، SSL، تایم‌اوت) رو دوباره امتحان می‌کنه؛ اگه بافر خودش
    جواب داد (حتی جواب خطا) دیگه retry نمی‌کنه، چون اون یه خطای واقعی
    منطقیه نه مشکل شبکه."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return requests.post(
                BUFFER_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=timeout,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            logger.warning(f"تلاش {attempt}/{MAX_RETRIES} برای اتصال به بافر شکست خورد: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise RuntimeError(
        f"اتصال به بافر بعد از {MAX_RETRIES} بار تلاش شکست خورد (مشکل شبکه/VPN): {last_error}"
    )

GET_POST_METRICS_QUERY = """
query GetPostMetrics($id: String!) {
  post(input: { id: $id }) {
    id
    metrics {
      type
      name
      value
      unit
    }
    metricsUpdatedAt
  }
}
"""


def get_post_metrics(post_id: str, api_key: str) -> "dict | None":
    """آمارِ رسمیِ بافر (لایک/ویو/ایمپرشن) رو برای یه پست، فقط از روی
    buffer_post_id (بدون نیاز به لینک اینستاگرام)، برمی‌گردونه.

    ⚠️ این یه فیچرِ «آزمایشی»ِ خودِ API بافره — خودشون گفتن ممکنه بدون
    اطلاع قبلی تغییر کنه یا از کار بیفته. برای همین این تابع هیچ‌وقت
    Exception پرتاب نمی‌کنه، فقط اگه هر مشکلی پیش بیاد None برمی‌گردونه —
    یعنی همیشه به‌عنوان fallback استفاده بشه، نه منبع اصلی.

    داده‌هاش هم واقعاً «الان» نیست؛ بافر می‌گه ممکنه تا ۲۴ ساعت طول بکشه
    تا این آمار براش از اینستاگرام برسه. خروجی:
    {"likes": int|None, "views": int|None, "comments": int|None,
     "updated_at": str|None, "source": "buffer"}
    یا None اگه هنوز هیچ آماری موجود نباشه/خطا بدیم."""
    try:
        resp = _post_with_retry(
            {"query": GET_POST_METRICS_QUERY, "variables": {"id": post_id}},
            api_key,
            timeout=20,
        )
        if not resp.ok:
            return None
        data = resp.json()
        if data.get("errors"):
            return None
        post = data.get("data", {}).get("post")
        if not post or not post.get("metrics"):
            return None

        likes = views = comments = None
        for m in post["metrics"]:
            name = (m.get("name") or "").lower()
            value = m.get("value")
            if "like" in name or "reaction" in name:
                likes = value
            elif "view" in name or "play" in name:
                views = value
            elif "impression" in name and views is None:
                views = value  # برای عکس‌ها، اگه ویوی واقعی نبود، ایمپرشن رو جایگزین می‌کنیم
            elif "comment" in name:
                comments = value

        return {
            "likes": likes,
            "views": views,
            "comments": comments,
            "updated_at": post.get("metricsUpdatedAt"),
            "source": "buffer",
        }
    except Exception as e:
        logger.warning(f"گرفتنِ آمارِ آزمایشیِ بافر برای پستِ {post_id} خطا داد: {e}")
        return None


CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess {
      post {
        id
        text
        dueAt
        status
      }
    }
    ... on MutationError {
      message
    }
  }
}
"""


DELETE_POST_MUTATION = """
mutation DeletePost($input: DeletePostInput!) {
  deletePost(input: $input) {
    ... on DeletePostSuccess {
      id
    }
    ... on VoidMutationError {
      message
    }
  }
}
"""


def delete_post(post_id: str, api_key: str) -> None:
    """پست زمان‌بندی‌شده‌ای که هنوز منتشر نشده رو تو بافر پاک می‌کنه (لغو
    زمان‌بندی). اگه بافر نتونه پاکش کنه (شناسه اشتباه، از قبل منتشر شده و
    غیره) یه RuntimeError با متن دقیق خطا پرتاب می‌شه."""
    variables = {"input": {"id": post_id}}

    resp = _post_with_retry(
        {"query": DELETE_POST_MUTATION, "variables": variables}, api_key, timeout=30
    )

    if not resp.ok:
        raise RuntimeError(f"بافر خطای HTTP داد (کد {resp.status_code}): {resp.text}")

    data = resp.json()

    if data.get("errors"):
        raise RuntimeError(f"خطای GraphQL بافر: {data['errors']}")

    result = data.get("data", {}).get("deletePost", {})
    if "message" in result:
        raise RuntimeError(f"بافر نتونست پست رو پاک کنه: {result['message']}")


def publish_video_to_instagram(
    caption: str,
    video_url: str,
    channel_id: str,
    api_key: str,
    delay_seconds: int = 120,
    due_at: "datetime.datetime | None" = None,
) -> dict:
    """پست ویدیویی (Reels) رو با کپشنِ تایید‌شده، از طریق بافر تو پیج
    اینستاگرام منتشر می‌کنه (یا برای انتشار زمان‌بندی می‌کنه).

    delay_seconds پیش‌فرض ۱۲۰ ثانیه‌ست (نه ۳۰ ثانیه مثل عکس)، چون برای
    Reels بافر باید ویدیو رو از آدرسش دانلود/پردازش کنه که چند ده ثانیه
    طول می‌کشه؛ اگه مهلت کم باشه، تا وقتی بافر پردازش رو تموم کنه due_at
    از الان گذشته و خطای "Scheduled time must be in the future" می‌گیریم.

    video_url باید یه آدرس مستقیم و عمومیِ https باشه (نه فایل لوکال روی
    سرور خودمون) — دقیقاً مثل image_urls تو publish_to_instagram، چون
    بافر خودش این آدرس رو fetch می‌کنه. برای این پروژه از Publitio
    (publitio_host.upload_video) به‌عنوان هاستِ موقتِ ویدیو استفاده می‌شه.

    برخلاف publish_to_instagram که کروسل چندعکسی می‌سازه، اینجا همیشه
    دقیقاً یک ویدیو می‌فرسته (چون Reels ترکیبی از ویدیو + عکس رو
    پشتیبانی نمی‌کنه).

    اگه بافر نتونه پست رو بسازه، همون‌طور که publish_to_instagram هم
    انجام می‌ده، یه RuntimeError با متن دقیق خطا پرتاب می‌شه.
    """
    if not video_url:
        raise ValueError("یک آدرس ویدیو برای پست لازمه.")

    if due_at is None:
        due_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=delay_seconds
        )
    elif due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=datetime.timezone.utc)

    due_at_str = due_at.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )

    variables = {
        "input": {
            "text": caption,
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": "customScheduled",
            "dueAt": due_at_str,
            "assets": [{"video": {"url": video_url}}],
            "metadata": {
                "instagram": {"type": "reel", "shouldShareToFeed": True}
            },
        }
    }

    resp = _post_with_retry(
        {"query": CREATE_POST_MUTATION, "variables": variables}, api_key, timeout=60
    )

    if not resp.ok:
        raise RuntimeError(f"بافر خطای HTTP داد (کد {resp.status_code}): {resp.text}")

    data = resp.json()

    if data.get("errors"):
        raise RuntimeError(f"خطای GraphQL بافر: {data['errors']}")

    result = data.get("data", {}).get("createPost", {})
    if "message" in result:
        raise RuntimeError(f"بافر نتونست پست رو بسازه: {result['message']}")

    if "post" not in result:
        raise RuntimeError(f"پاسخ غیرمنتظره از بافر: {data}")

    return result["post"]


def publish_to_instagram(
    caption: str,
    image_urls: list[str],
    channel_id: str,
    api_key: str,
    delay_seconds: int = 30,
    due_at: "datetime.datetime | None" = None,
) -> dict:
    """پست رو با عکس(ها) و کپشنِ تایید‌شده، از طریق بافر تو پیج اینستاگرام
    منتشر می‌کنه (یا برای انتشار زمان‌بندی می‌کنه).

    - اگه due_at داده نشه: تقریبا بلافاصله منتشر می‌شه (چند ثانیه بعد، چون
      بافر مود «همین الان» نداره و حداقل باید یه dueAt در آینده‌ی نزدیک بدیم).
    - اگه due_at داده بشه (یک datetime، با timezone یا بدون آن که در این
      صورت UTC فرض می‌شه): پست برای همون زمان زمان‌بندی می‌شه.

    اگه image_urls بیش از یک آدرس داشته باشه، پست به‌صورت کروسل (چندعکسی)
    ساخته می‌شه؛ اگه فقط یکی باشه، پست تک‌عکسی عادی می‌شه.

    هر آدرس تو image_urls باید مستقیم و عمومیِ https باشه (نه فایل لوکال
    روی سرور خودمون)، چون بافر خودش این آدرس‌ها رو fetch می‌کنه.

    اگه بافر نتونه پست رو بسازه (کلید اشتباه، channel_id غلط، عکس در
    دسترس نبودن و غیره) یه RuntimeError با متن دقیق خطا پرتاب می‌شه.
    """
    if not image_urls:
        raise ValueError("حداقل یک عکس برای پست لازمه.")

    if due_at is None:
        due_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            seconds=delay_seconds
        )
    elif due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=datetime.timezone.utc)

    due_at_str = due_at.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )

    variables = {
        "input": {
            "text": caption,
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": "customScheduled",
            "dueAt": due_at_str,
            "assets": [{"image": {"url": url}} for url in image_urls],
            "metadata": {
                "instagram": {"type": "post", "shouldShareToFeed": True}
            },
        }
    }

    resp = _post_with_retry(
        {"query": CREATE_POST_MUTATION, "variables": variables}, api_key, timeout=30
    )

    if not resp.ok:
        raise RuntimeError(f"بافر خطای HTTP داد (کد {resp.status_code}): {resp.text}")

    data = resp.json()

    if data.get("errors"):
        raise RuntimeError(f"خطای GraphQL بافر: {data['errors']}")

    result = data.get("data", {}).get("createPost", {})
    if "message" in result:
        raise RuntimeError(f"بافر نتونست پست رو بسازه: {result['message']}")

    if "post" not in result:
        raise RuntimeError(f"پاسخ غیرمنتظره از بافر: {data}")

    return result["post"]
