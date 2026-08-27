"""آپلود عکس به هاست عمومی imgbb (به‌جای catbox که آی‌پی‌های VPN رو مسدود می‌کنه).
نیاز به یه API Key رایگان از api.imgbb.com داره."""
import base64
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

IMGBB_API_KEY = os.environ["IMGBB_API_KEY"]
IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"

# چندبار امتحان کنه وقتی خطای شبکه‌ای موقتی (قطعی SSL، تایم‌اوت و غیره) پیش
# میاد، مثل همون مشکلی که تو اتصال به بافر/Publitio پیش میاد.
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def upload_image(image_path: str, timeout: int = 30) -> str:
    """عکس رو آپلود می‌کنه و یه لینک مستقیم و عمومی برمی‌گردونه.
    اگه آپلود شکست بخوره (بعد از چندبار تلاش برای خطاهای شبکه‌ای موقتی)
    RuntimeError پرتاب می‌کنه."""
    with open(image_path, "rb") as f:
        encoded_image = base64.b64encode(f.read())

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                IMGBB_UPLOAD_URL,
                data={"key": IMGBB_API_KEY, "image": encoded_image},
                timeout=timeout,
            )
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            logger.warning(f"تلاش {attempt}/{MAX_RETRIES} برای اتصال به imgbb شکست خورد: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    else:
        raise RuntimeError(
            f"اتصال به imgbb بعد از {MAX_RETRIES} بار تلاش شکست خورد (مشکل شبکه/VPN): {last_error}"
        )

    if not resp.ok:
        raise RuntimeError(f"آپلود عکس شکست خورد (کد {resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"آپلود عکس شکست خورد: {data}")

    return data["data"]["url"]
