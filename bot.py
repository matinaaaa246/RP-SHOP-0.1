import asyncio
import datetime
import logging
import os
import subprocess
import tempfile
from threading import Thread

from flask import Flask
import imageio_ffmpeg
from dotenv import load_dotenv

load_dotenv()

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ReplyKeyboardMarkup,
)
from telegram.error import TimedOut, NetworkError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from rewriter import generate_caption
import buffer_publisher
from buffer_publisher import publish_to_instagram, publish_video_to_instagram
from image_host import upload_image
import publitio_host
from schedule_parser import parse_schedule_time, TEHRAN_TZ
from shop_profile_store import get_profile as get_shop_profile, set_profile as set_shop_profile, all_chat_ids as shop_profile_chat_ids
import post_history_store

MIN_REEL_FPS = 23
TARGET_FPS = 30
MIN_REEL_SECONDS = 3.5
ALBUM_DEBOUNCE_SECONDS = 1.5
MAX_POSTS_PER_DAY = 9

MENU_NEW = "🆕 محصول جدید"
MENU_HISTORY = "📋 تاریخچه"
MENU_SCHEDULED = "🕒 زمان‌بندی‌شده‌ها"
MENU_PRODUCTS = "🗂 محصولات"
MENU_PROFILE = "⚙️ پروفایل فروشگاه"

SHOP_TELEGRAM_BOT_TOKEN = os.environ["SHOP_TELEGRAM_BOT_TOKEN"]
TELEGRAM_PROXY_URL = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
AI_API_KEY = os.environ["AI_API_KEY"]
AI_API_URL = os.environ["AI_API_URL"]
AI_MODEL = os.environ["AI_MODEL"]
BUFFER_API_KEY = os.environ["BUFFER_API_KEY"]
BUFFER_CHANNEL_ID = os.environ["BUFFER_CHANNEL_ID"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        [MENU_NEW],
        [MENU_HISTORY, MENU_SCHEDULED],
        [MENU_PRODUCTS, MENU_PROFILE],
    ],
    resize_keyboard=True,
)

awaiting_product: dict[int, bool] = {}
collecting: dict[int, dict] = {}
pending_posts: dict[str, dict] = {}
awaiting_caption_edit: dict[int, str] = {}
awaiting_schedule_time: dict[int, str] = {}

web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Bot and Server are running!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)

def _log_actual_fps(ffmpeg_exe: str, path: str) -> None:
    try:
        probe = subprocess.run(
            [ffmpeg_exe, "-i", path],
            capture_output=True, text=True, timeout=30,
        )
        for line in probe.stderr.splitlines():
            if "Video:" in line:
                logger.info(f"[دیباگ fps] {line.strip()}")
    except Exception:
        logger.exception("خوندن fps واقعی برای دیباگ شکست خورد.")

def _get_duration_seconds(ffmpeg_exe: str, path: str) -> "float | None":
    try:
        probe = subprocess.run(
            [ffmpeg_exe, "-i", path],
            capture_output=True, text=True, timeout=30,
        )
        for line in probe.stderr.splitlines():
            line = line.strip()
            if line.startswith("Duration:"):
                time_str = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = time_str.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        logger.exception("خوندن مدت‌زمان ویدیو شکست خورد.")
    return None

def _ensure_min_duration(ffmpeg_exe: str, input_path: str) -> str:
    duration = _get_duration_seconds(ffmpeg_exe, input_path)
    if duration is None or duration >= MIN_REEL_SECONDS:
        return input_path

    pad_seconds = MIN_REEL_SECONDS - duration + 0.2
    output_path = input_path.rsplit(".", 1)[0] + "_padded.mp4"
    try:
        subprocess.run(
            [
                ffmpeg_exe, "-y", "-i", input_path,
                "-vf", f"tpad=stop_mode=clone:stop_duration={pad_seconds}",
                "-af", f"apad=pad_dur={pad_seconds}",
                "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                output_path,
            ],
            capture_output=True, text=True, timeout=180, check=True,
        )
        logger.info(f"ویدیو از {duration:.1f} ثانیه به حداقل {MIN_REEL_SECONDS} ثانیه کشیده شد.")
        return output_path
    except Exception:
        logger.exception("کشیدن ویدیو به حداقل مدت‌زمان شکست خورد؛ ویدیوی اصلی بدون تغییر آپلود می‌شه.")
        return input_path

def _ensure_reel_fps(input_path: str) -> str:
    output_path = input_path.rsplit(".", 1)[0] + "_fixedfps.mp4"
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [
                ffmpeg_exe, "-y", "-i", input_path,
                "-vsync", "cfr", "-r", str(TARGET_FPS),
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "aac",
                "-avoid_negative_ts", "make_zero",
                "-fflags", "+genpts",
                "-movflags", "+faststart",
                output_path,
            ],
            capture_output=True, text=True, timeout=180, check=True,
        )
        logger.info(f"ویدیو با fps ثابت {TARGET_FPS} ری‌انکود شد.")
        _log_actual_fps(ffmpeg_exe, output_path)
        return _ensure_min_duration(ffmpeg_exe, output_path)
    except Exception:
        logger.exception("ری‌انکود ویدیو برای اصلاح fps شکست خورد؛ ویدیوی اصلی بدون تغییر آپلود می‌شه.")
        return input_path

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 خوش اومدی!\n\n"
        f"{MENU_NEW} — شروع یه پست تازه (عکس + توضیح)\n"
        f"{MENU_HISTORY} — آخرین پست‌های منتشرشده/زمان‌بندی‌شده\n"
        f"{MENU_SCHEDULED} — پست‌هایی که هنوز منتشر نشدن (با امکان پاک‌کردن)\n"
        f"{MENU_PRODUCTS} — آرشیو محصولات قبلی با عکس\n"
        f"{MENU_PROFILE} — دیدن/تنظیم زمینه‌ی ثابت فروشگاه\n\n"
        "از دکمه‌های پایین صفحه استفاده کن، یا اگه راحت‌تری همون دستورهای "
        "متنی (/new، /history، ...) رو تایپ کن.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    awaiting_product[chat_id] = True
    collecting.pop(chat_id, None)
    await update.message.reply_text(
        "📸 عکس(ها) یا 🎥 یه ویدیو بفرست (عکس‌ها رو می‌تونی چندتا با هم به‌صورت "
        "آلبوم بفرستی؛ ویدیو فقط یه‌دونه)، و یه توضیح کوتاه هم بذار (اسم محصول، "
        "قیمت، جنس، ویژگی مهم).\n\n"
        "مثال: «کیف چرم قهوه‌ای، قیمت ۸۵۰,۰۰۰ تومان، جنس چرم طبیعی»",
        reply_markup=MAIN_MENU_KEYBOARD,
    )

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if context.args:
        profile_text = " ".join(context.args)
        set_shop_profile(chat_id, profile_text)
        await update.message.reply_text(
            f"✅ زمینه‌ی ثابت فروشگاه ذخیره شد:\n\n{profile_text}\n\n"
            "از این به بعد تو همه‌ی کپشن‌های جدید همینو رعایت می‌کنه، بدون "
            "این‌که لازم باشه دوباره توضیح بدی."
        )
        return

    current = get_shop_profile(chat_id)
    if current:
        await update.message.reply_text(
            f"زمینه‌ی فعلی فروشگاه:\n\n{current}\n\n"
            "برای عوض‌کردنش: /profile <متن جدید>"
        )
    else:
        await update.message.reply_text(
            "هنوز زمینه‌ای برای فروشگاه ثبت نشده. یه‌بار این‌جوری بفرست تا AI "
            "هر دفعه، بدون این‌که دوباره توضیح بدی، همینو رعایت کنه:\n\n"
            "/profile کیف و کفش چرم دست‌دوز، لحن گرم و صمیمی، همیشه رو کیفیت "
            "جنس تاکید کن"
        )

async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not awaiting_product.get(chat_id):
        await update.message.reply_text("اول دستور /new رو بزن، بعد عکس بفرست.")
        return

    if chat_id in collecting and collecting[chat_id].get("video_file_id"):
        await update.message.reply_text(
            "❌ این پست از قبل یه ویدیو داره؛ نمی‌شه عکس هم بهش اضافه کرد. "
            "دوباره /new بزن و این‌بار فقط عکس(ها) رو بفرست."
        )
        return

    if chat_id not in collecting:
        collecting[chat_id] = {"file_ids": [], "captions": [], "video_file_id": None}

    collecting[chat_id]["file_ids"].append(update.message.photo[-1].file_id)
    if update.message.caption:
        collecting[chat_id]["captions"].append(update.message.caption)

    current_jobs = context.job_queue.get_jobs_by_name(f"finalize_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_once(
        finalize_submission,
        ALBUM_DEBOUNCE_SECONDS,
        chat_id=chat_id,
        name=f"finalize_{chat_id}",
        data={"chat_id": chat_id},
    )

async def video_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not awaiting_product.get(chat_id):
        await update.message.reply_text("اول دستور /new رو بزن، بعد ویدیو بفرست.")
        return

    if chat_id in collecting and collecting[chat_id]["file_ids"]:
        await update.message.reply_text(
            "❌ این پست از قبل عکس داره؛ نمی‌شه ویدیو هم بهش اضافه کرد. "
            "دوباره /new بزن و این‌بار فقط ویدیو رو بفرست."
        )
        return

    if chat_id not in collecting:
        collecting[chat_id] = {"file_ids": [], "captions": [], "video_file_id": None}

    video_like = update.message.video or update.message.animation
    collecting[chat_id]["video_file_id"] = video_like.file_id
    if update.message.caption:
        collecting[chat_id]["captions"].append(update.message.caption)

    current_jobs = context.job_queue.get_jobs_by_name(f"finalize_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_once(
        finalize_submission,
        ALBUM_DEBOUNCE_SECONDS,
        chat_id=chat_id,
        name=f"finalize_{chat_id}",
        data={"chat_id": chat_id},
    )

async def _download_with_retry(tg_file, tmp_path: str, attempts: int = 4):
    for attempt in range(1, attempts + 1):
        try:
            await tg_file.download_to_drive(tmp_path)
            return
        except (TimedOut, NetworkError):
            if attempt == attempts:
                raise
            wait = 2 * attempt
            logger.warning(
                f"تایم‌اوت هنگام دانلود عکس، تلاش {attempt} از {attempts}؛ "
                f"{wait} ثانیه صبر و تلاش دوباره..."
            )
            await asyncio.sleep(wait)

async def finalize_submission(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    data = collecting.pop(chat_id, None)
    awaiting_product[chat_id] = False

    if not data or (not data["file_ids"] and not data.get("video_file_id")):
        return

    if not data["captions"]:
        await context.bot.send_message(
            chat_id,
            "❌ هیچ توضیحی همراه عکس(ها)/ویدیو نبود. دوباره /new بزن و این‌بار "
            "یه کپشن کوتاه هم روش بذار.",
        )
        return

    raw_description = data["captions"][0]
    is_video = bool(data.get("video_file_id"))
    await context.bot.send_message(
        chat_id,
        "⏳ در حال آپلود ویدیو و ساخت کپشن..." if is_video else "⏳ در حال آپلود عکس‌ها و ساخت کپشن...",
    )

    try:
        public_image_urls: list[str] = []
        public_video_url = None
        publitio_file_id = None

        if is_video:
            tg_file = await context.bot.get_file(data["video_file_id"])
            tmp_path = os.path.join(tempfile.gettempdir(), f"product_{chat_id}_video.mp4")
            await _download_with_retry(tg_file, tmp_path)
            tmp_path = _ensure_reel_fps(tmp_path)
            upload_result = publitio_host.upload_video(tmp_path)
            public_video_url = upload_result["url_preview"]
            logger.info(f"[دیباگ] لینک نهایی ویدیو که به بافر داده می‌شه: {public_video_url}")
            publitio_file_id = upload_result["id"]
        else:
            for i, file_id in enumerate(data["file_ids"]):
                tg_file = await context.bot.get_file(file_id)
                tmp_path = os.path.join(tempfile.gettempdir(), f"product_{chat_id}_{i}.jpg")
                await _download_with_retry(tg_file, tmp_path)
                public_image_urls.append(upload_image(tmp_path))

        shop_profile = get_shop_profile(chat_id)
        caption = generate_caption(
            raw_description=raw_description,
            api_key=AI_API_KEY,
            api_url=AI_API_URL,
            model=AI_MODEL,
            shop_profile=shop_profile,
        )

        await _send_caption_result(
            context, chat_id, public_image_urls, public_video_url, publitio_file_id, raw_description, caption
        )

    except Exception as e:
        logger.exception("خطا در پردازش ارسال محصول")
        await context.bot.send_message(chat_id, f"❌ خطا: {e}")

async def _send_caption_result(
    context,
    chat_id,
    image_urls: list[str],
    video_url: "str | None",
    publitio_file_id: "str | None",
    raw_description: str,
    caption: str,
):
    media_type = "video" if video_url else "image"

    if media_type == "video":
        await context.bot.send_video(chat_id=chat_id, video=video_url)
    elif len(image_urls) == 1:
        await context.bot.send_photo(chat_id=chat_id, photo=image_urls[0])
    else:
        media = [InputMediaPhoto(url) for url in image_urls]
        await context.bot.send_media_group(chat_id=chat_id, media=media)

    caption_message = await context.bot.send_message(chat_id, f"📝 پیشنهاد کپشن:\n\n{caption}")
    post_id = str(caption_message.message_id)
    pending_posts[post_id] = {
        "chat_id": chat_id,
        "media_type": media_type,
        "image_urls": image_urls,
        "video_url": video_url,
        "publitio_file_id": publitio_file_id,
        "raw_description": raw_description,
        "caption": caption,
    }
    await _send_decision_keyboard(context, chat_id, post_id)

async def _send_decision_keyboard(
    context, chat_id, post_id: str, prompt: str = "برای تایید، بازسازی، ویرایش یا لغو این پست:"
):
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تایید", callback_data=f"approve:{post_id}")],
            [
                InlineKeyboardButton("🔁 دوباره بساز (AI)", callback_data=f"regen_final:{post_id}"),
                InlineKeyboardButton("✏️ ویرایش دستی", callback_data=f"edit:{post_id}"),
            ],
            [InlineKeyboardButton("❌ لغو", callback_data=f"reject:{post_id}")],
        ]
    )
    await context.bot.send_message(chat_id, prompt, reply_markup=keyboard)

async def _send_timing_keyboard(context, chat_id, post_id: str):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 همین الان بفرست", callback_data=f"send_now:{post_id}"),
        InlineKeyboardButton("🕒 زمان‌بندی کن", callback_data=f"schedule:{post_id}"),
    ]])
    await context.bot.send_message(chat_id, "چه زمانی منتشر بشه؟", reply_markup=keyboard)

async def _publish_post(context, chat_id, post_id: str, status_message, due_at=None):
    post = pending_posts.get(post_id)
    if not post:
        await status_message.edit_text("این پیشنهاد دیگه معتبر نیست.")
        return

    target_date = (due_at or datetime.datetime.now(TEHRAN_TZ)).astimezone(TEHRAN_TZ).date()
    posts_that_day = post_history_store.count_on_date(chat_id, target_date, TEHRAN_TZ)
    if posts_that_day >= MAX_POSTS_PER_DAY:
        date_str = target_date.strftime("%Y-%m-%d")
        await status_message.edit_text(
            f"❌ برای {date_str} از قبل {posts_that_day} پست ثبت شده و به سقف "
            f"روزانه ({MAX_POSTS_PER_DAY} پست) رسیدی. یه روز دیگه رو انتخاب کن:"
        )
        awaiting_caption_edit.pop(chat_id, None)
        awaiting_schedule_time.pop(chat_id, None)
        await _send_timing_keyboard(context, chat_id, post_id)
        return

    is_video = post.get("media_type") == "video"

    try:
        if is_video:
            result = publish_video_to_instagram(
                caption=post["caption"],
                video_url=post["video_url"],
                channel_id=BUFFER_CHANNEL_ID,
                api_key=BUFFER_API_KEY,
                due_at=due_at,
            )
        else:
            result = publish_to_instagram(
                caption=post["caption"],
                image_urls=post["image_urls"],
                channel_id=BUFFER_CHANNEL_ID,
                api_key=BUFFER_API_KEY,
                due_at=due_at,
            )
        logger.info(f"پست فروشگاه با موفقیت تو بافر ساخته شد: {result}")

        if is_video and post.get("publitio_file_id"):
            publitio_host.schedule_delete_at(
                post["publitio_file_id"], due_at or datetime.datetime.now(TEHRAN_TZ)
            )

        post_history_store.log_post(
            chat_id=chat_id,
            caption=post["caption"],
            image_count=len(post["image_urls"]) if not is_video else 1,
            status="scheduled" if due_at else "published",
            when=due_at,
            image_urls=post["image_urls"] if not is_video else [],
            buffer_post_id=result.get("id"),
        )
        if due_at:
            when = due_at.astimezone(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M")
            await status_message.edit_text(f"✅ برای انتشار در {when} زمان‌بندی شد")
        else:
            await status_message.edit_text("✅ تایید شد و برای انتشار به بافر فرستاده شد")
    except Exception as e:
        logger.warning(f"ارسال پست فروشگاه به بافر خطا داد: {e}")
        await status_message.edit_text(f"⚠️ ارسال به بافر خطا داد:\n{e}")
    finally:
        pending_posts.pop(post_id, None)
        awaiting_caption_edit.pop(chat_id, None)
        awaiting_schedule_time.pop(chat_id, None)

async def _handle_regenerate_final(context, chat_id, post_id: str, query):
    post = pending_posts.get(post_id)
    if not post:
        await query.edit_message_text("این پیشنهاد دیگه معتبر نیست.")
        return

    await query.edit_message_text("⏳ در حال ساخت نسخه‌ی جدید کپشن...")
    try:
        shop_profile = get_shop_profile(chat_id)
        new_caption = generate_caption(
            raw_description=post["raw_description"],
            api_key=AI_API_KEY,
            api_url=AI_API_URL,
            model=AI_MODEL,
            shop_profile=shop_profile,
            avoid=post["caption"],
        )
        post["caption"] = new_caption
        await query.edit_message_text(f"📝 کپشن جدید:\n\n{post['caption']}")
        await _send_decision_keyboard(context, chat_id, post_id)
    except Exception as e:
        logger.warning(f"دوباره‌سازی کپشن نهایی خطا داد: {e}")
        await query.edit_message_text(f"⚠️ ساخت نسخه‌ی جدید خطا داد:\n{e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    parts = query.data.split(":")
    action = parts[0]

    if action == "regen_final":
        post_id = parts[1]
        await _handle_regenerate_final(context, chat_id, post_id, query)
        return

    if action == "delete_scheduled":
        buffer_post_id = parts[1]
        try:
            buffer_publisher.delete_post(buffer_post_id, BUFFER_API_KEY)
            post_history_store.remove_by_buffer_id(chat_id, buffer_post_id)
            if query.message.photo:
                await query.edit_message_caption(caption="🗑 این پست از زمان‌بندی پاک شد.")
            else:
                await query.edit_message_text("🗑 این پست از زمان‌بندی پاک شد.")
        except Exception as e:
            logger.warning(f"پاک کردن پست زمان‌بندی‌شده خطا داد: {e}")
            await query.answer(f"⚠️ خطا: {e}", show_alert=True)
        return

    post_id = parts[1]
    post = pending_posts.get(post_id)

    if not post:
        await query.edit_message_text("این پیشنهاد دیگه معتبر نیست.")
        return

    if action == "reject":
        await query.edit_message_text("❌ لغو شد")
        pending_posts.pop(post_id, None)
        awaiting_caption_edit.pop(chat_id, None)
        awaiting_schedule_time.pop(chat_id, None)
        return

    if action == "edit":
        awaiting_caption_edit[chat_id] = post_id
        await query.edit_message_text(
            "✏️ کپشن جدید رو به‌صورت یه پیام متنی بفرست (جایگزین کپشن قبلی می‌شه)."
        )
        return

    if action == "approve":
        await query.edit_message_text(f"کپشن نهایی:\n\n{post['caption']}")
        await _send_timing_keyboard(context, chat_id, post_id)
        return

    if action == "send_now":
        await query.edit_message_text("⏳ در حال ارسال به بافر برای انتشار...")
        await _publish_post(context, chat_id, post_id, query.message)
        return

    if action == "schedule":
        awaiting_schedule_time[chat_id] = post_id
        suggested_hour = post_history_store.most_common_hour(chat_id, TEHRAN_TZ)
        if suggested_hour is not None:
            suggestion_line = (
                f"💡 پیشنهاد: قبلاً بیشتر ساعت {suggested_hour:02d}:۰۰ پست فرستادی، "
                "همون معمولاً بهترین گزینه‌ست.\n\n"
            )
        else:
            suggestion_line = (
                "💡 پیشنهاد: ساعت‌های ۱۹ تا ۲۲ (عصر تا اوایل شب) معمولاً تعامل "
                "بیشتری تو اینستاگرام دارن.\n\n"
            )
        await query.edit_message_text(
            "🕒 چه زمانی بفرستم؟\n\n" + suggestion_line +
            "آزادانه بنویس، مثلاً:\n"
            "«فردا ساعت ۹»، «امروز ساعت ۵ عصر»، «پس‌فردا ۱۰:۳۰» یا "
            "«۲۰۲۶-۰۸-۲۷ ۰۹:۰۰»"
        )
        return

async def text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if text in MENU_HANDLERS:
        awaiting_caption_edit.pop(chat_id, None)
        awaiting_schedule_time.pop(chat_id, None)
        await MENU_HANDLERS[text](update, context)
        return

    if chat_id in awaiting_caption_edit:
        post_id = awaiting_caption_edit.pop(chat_id)
        post = pending_posts.get(post_id)
        if not post:
            await update.message.reply_text("این پیشنهاد دیگه معتبر نیست.")
            return
        post["caption"] = text
        await update.message.reply_text(f"✅ کپشن به‌روز شد:\n\n{text}")
        await _send_decision_keyboard(context, chat_id, post_id)
        return

    if chat_id in awaiting_schedule_time:
        post_id = awaiting_schedule_time[chat_id]
        if post_id not in pending_posts:
            awaiting_schedule_time.pop(chat_id, None)
            await update.message.reply_text("این پیشنهاد دیگه معتبر نیست.")
            return
        due_at = parse_schedule_time(text)
        if due_at is None:
            await update.message.reply_text(
                "❌ متوجه زمان نشدم. یه نمونه‌ی دیگه امتحان کن، مثلاً "
                "«فردا ساعت ۹» یا «۲۰۲۶-۰۸-۲۷ ۰۹:۰۰»."
            )
            return
        awaiting_schedule_time.pop(chat_id, None)
        status_message = await update.message.reply_text("⏳ در حال زمان‌بندی پست...")
        await _publish_post(context, chat_id, post_id, status_message, due_at=due_at)
        return

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    entries = post_history_store.get_history(chat_id, limit=10)
    if not entries:
        await update.message.reply_text("هنوز هیچ پستی از این فروشگاه منتشر/زمان‌بندی نشده.")
        return

    lines = ["📋 آخرین پست‌ها:\n"]
    for e in entries:
        dt = datetime.datetime.fromisoformat(e["timestamp"]).astimezone(TEHRAN_TZ)
        status_label = "🕒 زمان‌بندی‌شده برای" if e["status"] == "scheduled" else "✅ منتشرشده در"
        lines.append(
            f"{status_label} {dt.strftime('%Y-%m-%d %H:%M')} — {e['image_count']} عکس\n"
            f"«{e['caption_preview']}...»"
        )
    await update.message.reply_text("\n\n".join(lines))

async def products_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    entries = post_history_store.get_history(chat_id, limit=10)
    if not entries:
        await update.message.reply_text("هنوز هیچ محصولی تو آرشیو نیست.")
        return

    await update.message.reply_text(f"🗂 آخرین {len(entries)} محصول:")
    for e in entries:
        dt = datetime.datetime.fromisoformat(e["timestamp"]).astimezone(TEHRAN_TZ)
        status_label = "🕒 زمان‌بندی‌شده برای" if e["status"] == "scheduled" else "✅ منتشرشده در"
        caption = (
            f"{status_label} {dt.strftime('%Y-%m-%d %H:%M')}\n«{e['caption_preview']}...»"
        )
        image_urls = e.get("image_urls") or []
        if not image_urls:
            await update.message.reply_text(caption)
        elif len(image_urls) == 1:
            await update.message.reply_photo(photo=image_urls[0], caption=caption)
        else:
            media = [InputMediaPhoto(url) for url in image_urls]
            media[0] = InputMediaPhoto(image_urls[0], caption=caption)
            await update.message.reply_media_group(media=media)

async def scheduled_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    entries = post_history_store.get_scheduled(chat_id)
    if not entries:
        await update.message.reply_text("در حال حاضر پست زمان‌بندی‌شده‌ای نداری.")
        return

    for e in entries:
        dt = datetime.datetime.fromisoformat(e["timestamp"]).astimezone(TEHRAN_TZ)
        caption = f"🕒 زمان‌بندی‌شده برای {dt.strftime('%Y-%m-%d %H:%M')}\n«{e['caption_preview']}...»"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ پاک کردن این پست", callback_data=f"delete_scheduled:{e['buffer_post_id']}")]]
        )
        image_urls = e.get("image_urls") or []
        if image_urls:
            await update.message.reply_photo(photo=image_urls[0], caption=caption, reply_markup=keyboard)
        else:
            await update.message.reply_text(caption, reply_markup=keyboard)

MENU_HANDLERS = {
    MENU_NEW: new_command,
    MENU_HISTORY: history_command,
    MENU_SCHEDULED: scheduled_command,
    MENU_PRODUCTS: products_command,
    MENU_PROFILE: profile_command,
}

async def weekly_report_job(context: ContextTypes.DEFAULT_TYPE):
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    for chat_id in post_history_store.all_chat_ids():
        count = post_history_store.count_since(chat_id, since)
        if count == 0:
            continue
        try:
            await context.bot.send_message(
                chat_id,
                f"📊 گزارش هفتگی: این هفته {count} پست فرستادی. برای دیدن جزئیات، /history رو بزن.",
            )
        except Exception as e:
            logger.warning(f"ارسال گزارش هفتگی به چت {chat_id} خطا داد: {e}")

async def inactivity_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
    known_chat_ids = set(post_history_store.all_chat_ids()) | set(shop_profile_chat_ids())
    for chat_id in known_chat_ids:
        count = post_history_store.count_since(chat_id, since)
        if count > 0:
            continue
        try:
            await context.bot.send_message(
                chat_id,
                "📸 ۲ روزه پست جدیدی نفرستادی! برای ساخت یه پست تازه /new رو بزن.",
            )
        except Exception as e:
            logger.warning(f"ارسال یادآوری بی‌فعالیتی به چت {chat_id} خطا داد: {e}")

def main():
    server_thread = Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    builder = (
        ApplicationBuilder()
        .token(SHOP_TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
    )
    if TELEGRAM_PROXY_URL:
        builder = builder.proxy(TELEGRAM_PROXY_URL).get_updates_proxy(TELEGRAM_PROXY_URL)
        logger.info(f"در حال استفاده از پروکسی برای اتصال به تلگرام: {TELEGRAM_PROXY_URL}")
    
    app = builder.build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", start_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("products", products_command))
    app.add_handler(CommandHandler("scheduled", scheduled_command))
    app.add_handler(MessageHandler(filters.PHOTO, photo_received))
    app.add_handler(MessageHandler(filters.VIDEO | filters.ANIMATION, video_received))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_received))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.job_queue.run_daily(
        weekly_report_job,
        time=datetime.time(hour=10, minute=0, tzinfo=TEHRAN_TZ),
        days=(6,),
        name="weekly_report",
    )
    app.job_queue.run_daily(
        inactivity_reminder_job,
        time=datetime.time(hour=11, minute=0, tzinfo=TEHRAN_TZ),
        name="inactivity_reminder",
    )
    
    publitio_host.start_background_cleaner()
    
    logger.info("بات فروشگاه روشن شد...")
    app.run_polling()

if __name__ == "__main__":
    main()
