"""
ربات دریافت پروفایل
- تشخیص چهره انسانی واقعی با MediaPipe (رد می‌کنه انیمیشن/نقاشی)
"""

import asyncio
import logging
import os
import re
import sqlite3
import tempfile
import urllib.request
from datetime import datetime
from io import BytesIO

import cv2
import numpy as np
from telethon import TelegramClient, events, Button
from telethon.errors import (
    FloodWaitError, PhoneCodeExpiredError, PhoneCodeInvalidError,
    PhoneNumberInvalidError, SessionPasswordNeededError
)
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.functions.messages import (
    ImportChatInviteRequest, CheckChatInviteRequest,
    SendMultiMediaRequest
)
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.types import (
    ChannelParticipantsSearch,
    InputMediaPhoto,
    InputSingleMedia
)

# ═══ MediaPipe ═══
try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False
    logger_temp = logging.getLogger(__name__)
    logger_temp.warning("MediaPipe نصب نیست، از YuNet استفاده می‌شه")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==================== تنظیمات ====================
BOT_TOKEN = os.environ.get("TOKEN")
if not BOT_TOKEN:
    raise ValueError("TOKEN environment variable is not set!")

ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "8055930343,7803165903")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

if not ADMIN_IDS:
    raise ValueError("ADMIN_IDS environment variable is not set!")

CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1004421441914"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
BOT_API_ID = int(os.environ.get("BOT_API_ID", "2040"))
BOT_API_HASH = os.environ.get("BOT_API_HASH", "b18441a1ff607e10a989891a5462e627")

# ═══════════════ تنظیمات تشخیص ═══════════════
MAX_PHOTOS_TOTAL = 10
FACE_CONFIDENCE = 0.6
MIN_FACE_SIZE = 40
MAX_FACES_PER_IMAGE = 3
MIN_FACE_RATIO = 0.5
MAX_FACE_RATIO = 1.8
MIN_SHARPNESS = 100
MIN_SKIN_RATIO = 0.20
MEDIAPIPE_CONFIDENCE = 0.7

# ═══════════════ ویدیو ═══════════════
VIDEO_MAX_SIZE_MB = 20
VIDEO_FRAME_SKIP = 30
VIDEO_MAX_FRAMES = 10
MAX_CAPTION_LENGTH = 1000


# ==================== State ====================
user_states = {}
user_temp = {}
pending_channel_sends = {}


# ==================== MediaPipe Setup ====================
_mp_face_detector = None


def init_mediapipe():
    global _mp_face_detector
    if not MEDIAPIPE_AVAILABLE:
        return None
    if _mp_face_detector is None:
        try:
            _mp_face_detector = mp.solutions.face_detection.FaceDetection(
                model_selection=1,  # 1 = full range (دورتر)
                min_detection_confidence=MEDIAPIPE_CONFIDENCE
            )
            logger.info("✅ MediaPipe آماده شد")
        except Exception as e:
            logger.error(f"MediaPipe init error: {e}")
            return None
    return _mp_face_detector


def has_human_face_mediapipe(img_bgr):
    """
    تشخیص چهره انسانی واقعی با MediaPipe
    این مدل روی چهره‌های واقعی انسان آموزش دیده و انیمیشن رو رد می‌کنه
    """
    detector = init_mediapipe()
    if detector is None:
        return None  # نمی‌تونیم تشخیص بدیم

    try:
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        results = detector.process(rgb)

        if not results.detections:
            return False

        # اگه چهره‌ای پیدا شد، چک کن اعتماد بالا باشه
        for detection in results.detections:
            score = detection.score[0] if detection.score else 0
            if score >= MEDIAPIPE_CONFIDENCE:
                return True

        return False
    except Exception as e:
        logger.debug(f"MediaPipe error: {e}")
        return None


# ==================== YuNet (Fallback) ====================
_yunet = None


def _download_yunet_model():
    model_path = "face_detection_yunet_2023mar.onnx"
    if os.path.exists(model_path):
        return model_path
    url = (
        "https://huggingface.co/opencv/face_detection_yunet/"
        "resolve/main/face_detection_yunet_2023mar.onnx"
    )
    try:
        urllib.request.urlretrieve(url, model_path)
        logger.info("مدل YuNet دانلود شد")
    except Exception as e:
        logger.error(f"دانلود YuNet ناموفق: {e}")
    return model_path


def init_yunet():
    global _yunet
    if _yunet is None:
        model_path = _download_yunet_model()
        if not os.path.exists(model_path):
            return None
        _yunet = cv2.FaceDetectorYN.create(
            model=model_path,
            config="",
            input_size=(320, 320),
            score_threshold=FACE_CONFIDENCE,
            nms_threshold=0.3,
            top_k=5000
        )
    return _yunet


def has_human_face_yunet(img_bgr):
    """Fallback با YuNet"""
    detector = init_yunet()
    if detector is None:
        return False

    try:
        h, w = img_bgr.shape[:2]
        img_area = h * w

        detect_img = img_bgr
        if w > 1000:
            scale = 1000 / w
            detect_img = cv2.resize(img_bgr, (1000, int(h * scale)))

        dh, dw = detect_img.shape[:2]
        detector.setInputSize((dw, dh))
        _, faces = detector.detect(detect_img)

        if faces is None or len(faces) == 0:
            return False

        ratio_w = w / dw
        ratio_h = h / dh

        for face in faces[:MAX_FACES_PER_IMAGE]:
            score = face[-1]
            x, y, fw, fh = face[0:4]

            real_fw = fw * ratio_w
            real_fh = fh * ratio_h

            if score < FACE_CONFIDENCE:
                continue
            if real_fw < MIN_FACE_SIZE or real_fh < MIN_FACE_SIZE:
                continue

            ratio = real_fw / real_fh if real_fh > 0 else 0
            if ratio < MIN_FACE_RATIO or ratio > MAX_FACE_RATIO:
                continue

            x1 = max(0, int(x * ratio_w))
            y1 = max(0, int(y * ratio_h))
            x2 = min(w, int((x + fw) * ratio_w))
            y2 = min(h, int((y + fh) * ratio_h))

            if x2 <= x1 or y2 <= y1:
                continue

            face_crop = img_bgr[y1:y2, x1:x2]
            if face_crop.size == 0:
                continue

            try:
                gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
                sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
                if sharpness < MIN_SHARPNESS:
                    continue
            except:
                continue

            try:
                hsv = cv2.cvtColor(face_crop, cv2.COLOR_BGR2HSV)
                ycrcb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2YCrCb)

                lower_hsv1 = np.array([0, 30, 80], dtype=np.uint8)
                upper_hsv1 = np.array([25, 255, 255], dtype=np.uint8)
                mask1 = cv2.inRange(hsv, lower_hsv1, upper_hsv1)

                lower_ycrcb = np.array([0, 135, 85], dtype=np.uint8)
                upper_ycrcb = np.array([255, 180, 135], dtype=np.uint8)
                mask3 = cv2.inRange(ycrcb, lower_ycrcb, upper_ycrcb)

                mask = cv2.bitwise_or(mask1, mask3)
                skin_ratio = np.sum(mask > 0) / mask.size

                if skin_ratio < MIN_SKIN_RATIO:
                    continue
            except:
                continue

            return True

        return False

    except Exception as e:
        logger.exception(f"has_human_face_yunet error: {e}")
        return False


def has_human_face(img_bgr):
    """
    تشخیص ترکیبی:
    1. اول MediaPipe (دقیق‌تر برای چهره واقعی)
    2. اگه MediaPipe نبود، YuNet
    """
    # MediaPipe اول
    if MEDIAPIPE_AVAILABLE:
        result = has_human_face_mediapipe(img_bgr)
        if result is not None:
            return result

    # Fallback به YuNet
    return has_human_face_yunet(img_bgr)


# ==================== دیتابیس ====================
USE_POSTGRES = False
_pool = None


async def init_db():
    global USE_POSTGRES, _pool

    if DATABASE_URL:
        try:
            import asyncpg
            url = DATABASE_URL
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            _pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
            USE_POSTGRES = True
            async with _pool.acquire() as conn:
                await conn.execute('''CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY, owner_id BIGINT, phone TEXT,
                    api_id INTEGER, api_hash TEXT, session_string TEXT,
                    account_name TEXT, username TEXT, created_date TEXT,
                    is_active INTEGER DEFAULT 1
                )''')
                await conn.execute('''CREATE TABLE IF NOT EXISTS templates (
                    id SERIAL PRIMARY KEY, owner_id BIGINT, title TEXT,
                    text TEXT, created_date TEXT
                )''')
                await conn.execute('''CREATE TABLE IF NOT EXISTS settings (
                    owner_id BIGINT PRIMARY KEY, current_account_id INTEGER,
                    current_template_id INTEGER
                )''')
            logger.info("PostgreSQL آماده شد")
            return
        except Exception as e:
            logger.error(f"PostgreSQL error: {e}")
            USE_POSTGRES = False

    conn = sqlite3.connect("private_bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, phone TEXT,
        api_id INTEGER, api_hash TEXT, session_string TEXT, account_name TEXT,
        username TEXT, created_date TEXT, is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, title TEXT,
        text TEXT, created_date TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        owner_id INTEGER PRIMARY KEY, current_account_id INTEGER,
        current_template_id INTEGER
    )''')
    conn.commit()
    conn.close()
    logger.info("SQLite آماده شد")


def _sqlite_exec(query, params=None, fetch=False, fetch_one=False):
    conn = sqlite3.connect("private_bot.db")
    c = conn.cursor()
    try:
        if params:
            c.execute(query, params)
        else:
            c.execute(query)
        if fetch_one:
            r = c.fetchone()
        elif fetch:
            r = c.fetchall()
        else:
            if 'RETURNING' in query.upper():
                r = c.fetchone()
                if r:
                    r = r[0]
            else:
                conn.commit()
                r = c.lastrowid
        conn.commit()
        return r
    finally:
        conn.close()


async def db_exec(query, params=None, fetch=False, fetch_one=False):
    if USE_POSTGRES and _pool:
        q = query
        if params:
            for i in range(len(params), 0, -1):
                q = q.replace('?', f'${i}', 1)
        async with _pool.acquire() as conn:
            if fetch_one:
                return await conn.fetchrow(q, *params if params else [])
            elif fetch:
                return await conn.fetch(q, *params if params else [])
            else:
                if 'RETURNING' in q.upper():
                    return await conn.fetchval(q, *params if params else [])
                await conn.execute(q, *params if params else [])
                return None
    else:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sqlite_exec, query, params, fetch, fetch_one)


# ==================== DB Helpers ====================
async def db_add_account(owner_id, phone, api_id, api_hash, session_str, name, username):
    return await db_exec(
        '''INSERT INTO accounts (owner_id, phone, api_id, api_hash, session_string, account_name, username, created_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id''',
        (int(owner_id), phone, api_id, api_hash, session_str, name, username, datetime.now().isoformat())
    )


async def db_get_accounts(owner_id):
    rows = await db_exec(
        'SELECT * FROM accounts WHERE owner_id = ? AND is_active = 1 ORDER BY id DESC',
        (int(owner_id),), fetch=True
    )
    return rows or []


async def db_get_account(acc_id):
    return await db_exec('SELECT * FROM accounts WHERE id = ?', (int(acc_id),), fetch_one=True)


async def db_delete_account(acc_id):
    await db_exec('UPDATE accounts SET is_active = 0 WHERE id = ?', (int(acc_id),))


async def db_add_template(owner_id, title, text):
    return await db_exec(
        'INSERT INTO templates (owner_id, title, text, created_date) VALUES (?, ?, ?, ?) RETURNING id',
        (int(owner_id), title, text, datetime.now().isoformat())
    )


async def db_get_templates(owner_id):
    rows = await db_exec(
        'SELECT * FROM templates WHERE owner_id = ? ORDER BY id DESC',
        (int(owner_id),), fetch=True
    )
    return rows or []


async def db_get_template(tid):
    return await db_exec('SELECT * FROM templates WHERE id = ?', (int(tid),), fetch_one=True)


async def db_delete_template(tid):
    await db_exec('DELETE FROM templates WHERE id = ?', (int(tid),))


async def db_get_settings(owner_id):
    row = await db_exec(
        'SELECT * FROM settings WHERE owner_id = ?', (int(owner_id),), fetch_one=True
    )
    if not row:
        await db_exec('INSERT INTO settings (owner_id) VALUES (?)', (int(owner_id),))
        row = await db_exec(
            'SELECT * FROM settings WHERE owner_id = ?', (int(owner_id),), fetch_one=True
        )
    return row


async def db_update_setting(owner_id, **kwargs):
    await db_get_settings(owner_id)
    for key, val in kwargs.items():
        await db_exec(f'UPDATE settings SET {key} = ? WHERE owner_id = ?', (val, int(owner_id)))


def is_owner(user_id):
    return user_id in ADMIN_IDS


# ==================== دکمه‌ها ====================
def btn(text, data, style=None):
    try:
        if style in ("primary", "success", "danger"):
            return Button.inline(text, data, style=style)
        return Button.inline(text, data)
    except (TypeError, ValueError):
        return Button.inline(text, data)


def main_menu_kb():
    return [
        [
            btn("مدیریت حساب‌های کاربری", b"menu_accounts", "primary"),
            btn("مدیریت قالب‌های متنی", b"menu_templates", "primary")
        ],
        [btn("دریافت پروفایل و چهره", b"menu_get_profile", "success")],
        [
            btn("پردازش چندگانه شناسه‌ها", b"menu_multi", "primary"),
            btn("پردازش گروه", b"menu_group", "primary")
        ]
    ]


def back_kb(target=b"menu_main"):
    return [[btn("بازگشت به منوی اصلی", target, "danger")]]


async def main_menu_text(owner_id):
    s = await db_get_settings(owner_id)
    acc_name = "تعیین نشده"
    tmpl_name = "تعیین نشده"
    if s and s[1]:
        a = await db_get_account(s[1])
        if a:
            acc_name = a[6]
    if s and s[2]:
        t = await db_get_template(s[2])
        if t:
            tmpl_name = t[2]
    return (
        "🌟 **پنل مدیریت پیشرفته** 🌟\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "به پنل مدیریت خصوصی خوش آمدید.\n"
        "این پنل به منظور مدیریت حساب‌های کاربری تلگرام و استخراج\n"
        "چهره‌های انسانی از پروفایل‌های کاربران طراحی شده است.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **وضعیت فعلی سیستم**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 حساب فعال: **{acc_name}**\n"
        f"📝 قالب فعال: **{tmpl_name}**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب فرمایید:"
    )


# ==================== صفحه عددی ====================
def get_number_keypad_kb(current_code=""):
    display = current_code if current_code else "─────"
    return [
        [btn(f"کد وارد شده: {display}", b"code_noop", "primary")],
        [
            btn("۱", b"code_1", "primary"),
            btn("۲", b"code_2", "primary"),
            btn("۳", b"code_3", "primary")
        ],
        [
            btn("۴", b"code_4", "primary"),
            btn("۵", b"code_5", "primary"),
            btn("۶", b"code_6", "primary")
        ],
        [
            btn("۷", b"code_7", "primary"),
            btn("۸", b"code_8", "primary"),
            btn("۹", b"code_9", "primary")
        ],
        [
            btn("⬅️ حذف", b"code_del", "danger"),
            btn("۰", b"code_0", "primary"),
            btn("✅ تایید", b"code_submit", "success")
        ],
        [btn("❌ لغو عملیات", b"code_cancel", "danger")]
    ]


# ==================== ربات ====================
bot = TelegramClient(StringSession(), BOT_API_ID, BOT_API_HASH)


# ==================== ارسال آلبومی ====================
async def send_photo_album(client, chat_id, photo_bytes_list, caption=None):
    if not photo_bytes_list:
        return 0

    photo_bytes_list = photo_bytes_list[:10]

    try:
        uploaded = []
        for idx, data in enumerate(photo_bytes_list):
            try:
                f = await client.upload_file(BytesIO(data), file_name=f"p_{idx}.jpg")
                uploaded.append(f)
            except Exception as e:
                logger.warning(f"upload error {idx}: {e}")

        if not uploaded:
            return 0

        if len(uploaded) == 1:
            await client.send_file(chat_id, uploaded[0], caption=caption, force_document=False)
            return 1

        try:
            await client.send_file(chat_id, uploaded, caption=caption, force_document=False)
            return len(uploaded)
        except Exception as e:
            logger.warning(f"send_file album fail: {e}")
    except Exception as e:
        logger.warning(f"method 1 fail: {e}")

    try:
        uploaded = []
        for idx, data in enumerate(photo_bytes_list):
            try:
                f = await client.upload_file(BytesIO(data), file_name=f"p_{idx}.jpg")
                uploaded.append(f)
            except:
                pass

        if not uploaded:
            return 0

        if len(uploaded) == 1:
            await client.send_file(chat_id, uploaded[0], caption=caption, force_document=False)
            return 1

        media_list = []
        for idx, f in enumerate(uploaded):
            media = InputMediaPhoto(file=f, caption=caption if idx == 0 else None)
            media_list.append(InputSingleMedia(media=media))

        await client(SendMultiMediaRequest(peer=chat_id, multi_media=media_list))
        return len(uploaded)
    except Exception as e:
        logger.exception(f"method 2 fail: {e}")

    sent = 0
    for idx, data in enumerate(photo_bytes_list):
        try:
            await client.send_file(
                chat_id, BytesIO(data),
                caption=caption if idx == 0 else None,
                force_document=False
            )
            sent += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"single {idx}: {e}")
    return sent


# ==================== Resolve Entity ====================
async def resolve_entity(client, raw):
    raw = raw.strip()
    if "t.me/" in raw:
        raw = raw.split("t.me/")[-1].split("/")[0].split("?")[0]
    if raw.startswith('@'):
        raw = raw[1:]
    if raw.isdigit():
        try:
            return await client.get_entity(int(raw))
        except:
            pass
    try:
        return await client.get_entity(raw)
    except:
        pass
    try:
        return await client.get_entity(f"@{raw}")
    except:
        pass
    try:
        from telethon.tl.functions.contacts import ResolveUsernameRequest
        result = await client(ResolveUsernameRequest(raw))
        if result and result.users:
            return result.users[0]
    except:
        pass
    return None


# ==================== استخراج عکس‌ها ====================
async def extract_photos_with_faces(client, target, status_msg=None, max_photos=MAX_PHOTOS_TOTAL):
    import time
    start_time = time.time()
    TIMEOUT = 90

    target_id = target.id
    first_name = target.first_name or ""
    last_name = target.last_name or ""
    full_name = (first_name + " " + last_name).strip() or "بدون نام"
    username = target.username or ""
    user_id_str = str(target.id)

    try:
        photos = await client(GetUserPhotosRequest(user_id=target_id, offset=0, max_id=0, limit=200))
        photo_list = photos.photos if hasattr(photos, 'photos') else []
    except Exception as e:
        logger.warning(f"get photos error: {e}")
        photo_list = []

    total = len(photo_list)

    if status_msg and total > 0:
        try:
            await status_msg.edit(
                f"🔍 **در حال بررسی فایل‌ها**\n\n"
                f"👤 نام کاربر: **{full_name}**\n"
                f"📁 تعداد فایل: **{total}**\n\n"
                f"⏳ لطفاً شکیبا باشید..."
            )
        except:
            pass

    all_photos = []
    seen_hashes = set()

    for idx, photo in enumerate(photo_list):
        if time.time() - start_time > TIMEOUT:
            logger.warning(f"timeout for {full_name}")
            break

        if len(all_photos) >= max_photos:
            break

        try:
            is_video = hasattr(photo, 'video_sizes') and photo.video_sizes

            if is_video:
                file_size = getattr(photo, 'size', 0) or 0
                if file_size > VIDEO_MAX_SIZE_MB * 1024 * 1024:
                    continue

                with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
                    tmp_path = tmp.name

                try:
                    await client.download_media(photo, tmp_path)
                    frames = extract_frames_from_video(tmp_path, skip=VIDEO_FRAME_SKIP)
                    for frame in frames:
                        if len(all_photos) >= max_photos:
                            break
                        if has_human_face(frame):
                            ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                            if not ok:
                                continue
                            fb = buf.tobytes()
                            h = hash(fb[:100])
                            if h not in seen_hashes:
                                seen_hashes.add(h)
                                all_photos.append(fb)
                finally:
                    try:
                        os.remove(tmp_path)
                    except:
                        pass
            else:
                buf = BytesIO()
                await client.download_media(photo, buf)
                original_bytes = buf.getvalue()
                arr = np.frombuffer(original_bytes, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    continue
                if has_human_face(img):
                    h = hash(original_bytes[:100])
                    if h not in seen_hashes:
                        seen_hashes.add(h)
                        all_photos.append(original_bytes)
                        logger.info(f"✅ photo {idx}: face accepted")

            if status_msg and (idx + 1) % 3 == 0:
                try:
                    await status_msg.edit(
                        f"🔍 **در حال بررسی فایل‌ها**\n\n"
                        f"👤 نام کاربر: **{full_name}**\n"
                        f"📁 پیشرفت: **{idx+1}/{total}**\n"
                        f"🖼 چهره‌های یافت شده: **{len(all_photos)}**\n\n"
                        f"⏳ لطفاً شکیبا باشید..."
                    )
                except:
                    pass
        except Exception as e:
            logger.warning(f"file {idx} error: {e}")
            continue

    return all_photos, total, full_name, username, user_id_str


def extract_frames_from_video(video_path, skip=30):
    frames = []
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        count = 0
        while len(frames) < VIDEO_MAX_FRAMES:
            ret, frame = cap.read()
            if not ret:
                break
            if count % skip == 0:
                frames.append(frame)
            count += 1
        cap.release()
        return frames
    except:
        return []


# ==================== /start ====================
@bot.on(events.NewMessage(pattern='/start'))
async def handle_start(event):
    if not is_owner(event.sender_id):
        return
    text = await main_menu_text(event.sender_id)
    await event.respond(text, buttons=main_menu_kb())


@bot.on(events.NewMessage(pattern='/cancel'))
async def handle_cancel(event):
    if not is_owner(event.sender_id):
        return
    uid = event.sender_id
    user_states.pop(uid, None)
    user_temp.pop(uid, None)
    await bot.send_message(uid, "❌ عملیات لغو شد.", buttons=Button.clear())
    await event.respond("بازگشت به منوی اصلی:", buttons=back_kb())


# ==================== Callback Handler ====================
@bot.on(events.CallbackQuery)
async def handle_callback(event):
    if not is_owner(event.sender_id):
        return

    data = event.data.decode('utf-8')
    uid = event.sender_id

    try:
        if data.startswith("code_"):
            if data == "code_noop":
                await event.answer()
                return
            if data == "code_cancel":
                user_states.pop(uid, None)
                user_temp.pop(uid, None)
                await event.edit("❌ عملیات لغو شد.", buttons=back_kb())
                return
            if data == "code_del":
                current = user_temp.get(uid, {}).get('input_code', '')
                current = current[:-1]
                user_temp[uid]['input_code'] = current
                display = current if current else "─────"
                await event.edit(
                    f"🔐 **ورود کد تایید**\n\n"
                    f"لطفاً کد پنج رقمی را وارد فرمایید.\n\n"
                    f"کد فعلی: `{display}`",
                    buttons=get_number_keypad_kb(current)
                )
                return
            if data == "code_submit":
                current = user_temp.get(uid, {}).get('input_code', '')
                if len(current) < 5:
                    await event.answer(f"کد باید ۵ رقم باشد", alert=True)
                    return
                await submit_code(event, uid, current)
                return
            if data[5:].isdigit():
                digit = data[5:]
                current = user_temp.get(uid, {}).get('input_code', '')
                if len(current) >= 5:
                    await event.answer("کد کامل شده است", alert=True)
                    return
                current += digit
                user_temp[uid]['input_code'] = current
                display = current if current else "─────"
                await event.edit(
                    f"🔐 **ورود کد تایید**\n\n"
                    f"کد فعلی: `{display}`",
                    buttons=get_number_keypad_kb(current)
                )
                return

        if data.startswith("send_channel_"):
            key = data.replace("send_channel_", "")
            payload = pending_channel_sends.get(key)
            if not payload:
                await event.answer("منقضی شده است", alert=True)
                return
            await event.answer("در حال ارسال...")
            try:
                sent = await send_photo_album(bot, CHANNEL_ID, payload['faces'], caption=payload['caption'])
                await event.edit(
                    f"✅ **ارسال به کانال با موفقیت انجام شد**\n\n"
                    f"📊 تعداد عکس‌های ارسال شده: **{sent}**",
                    buttons=back_kb()
                )
            except Exception as e:
                await event.edit(
                    f"❌ **خطا در ارسال به کانال**\n\n"
                    f"`{str(e)[:100]}`",
                    buttons=back_kb()
                )
            pending_channel_sends.pop(key, None)
            return

        if data.startswith("cancel_channel_"):
            key = data.replace("cancel_channel_", "")
            pending_channel_sends.pop(key, None)
            await event.edit("❌ ارسال به کانال لغو شد.", buttons=back_kb())
            return

        if data == "menu_main":
            text = await main_menu_text(uid)
            await event.edit(text, buttons=main_menu_kb())

        elif data == "menu_accounts":
            await show_accounts_menu(event, uid)

        elif data == "acc_add":
            user_states[uid] = "WAIT_PHONE"
            user_temp[uid] = {}
            await event.edit(
                "➕ **افزودن حساب کاربری جدید**\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "**مرحله ۱ از ۴**\n\n"
                "لطفاً شماره تلفن همراه خود را با کد کشور ارسال فرمایید.\n\n"
                "**نمونه:** `+989123456789`\n\n"
                "برای لغو عملیات، دستور /cancel را ارسال فرمایید.",
                buttons=back_kb(b"menu_accounts")
            )
            await bot.send_message(
                uid,
                "📱 **ارسال شماره تلفن**\n\n"
                "لطفاً برای ارسال شماره خود، از دکمه شیشه‌ای زیر استفاده فرمایید.",
                buttons=Button.request_phone("📱 ارسال شماره من", resize=True)
            )

        elif data.startswith("acc_view_"):
            acc_id = int(data.split("_")[2])
            await show_account_detail(event, uid, acc_id)

        elif data.startswith("acc_use_"):
            acc_id = int(data.split("_")[2])
            await db_update_setting(uid, current_account_id=acc_id)
            await show_account_detail(event, uid, acc_id)

        elif data.startswith("acc_del_"):
            acc_id = int(data.split("_")[2])
            await db_delete_account(acc_id)
            s = await db_get_settings(uid)
            if s and s[1] == acc_id:
                await db_update_setting(uid, current_account_id=None)
            await show_accounts_menu(event, uid)

        elif data == "menu_templates":
            await show_templates_menu(event, uid)

        elif data == "tmpl_add":
            user_states[uid] = "WAIT_NEW_TEMPLATE"
            user_temp[uid] = {}
            await event.edit(
                "📝 **ایجاد قالب متنی جدید**\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً عنوان قالب را ارسال فرمایید.\n\n"
                "**نمونه:** `چهره‌های زیبا`\n\n"
                "برای لغو عملیات، دستور /cancel را ارسال فرمایید.",
                buttons=back_kb(b"menu_templates")
            )

        elif data.startswith("tmpl_view_"):
            tid = int(data.split("_")[2])
            await show_template_detail(event, uid, tid)

        elif data.startswith("tmpl_use_"):
            tid = int(data.split("_")[2])
            await db_update_setting(uid, current_template_id=tid)
            await show_template_detail(event, uid, tid)

        elif data.startswith("tmpl_del_"):
            tid = int(data.split("_")[2])
            await db_delete_template(tid)
            s = await db_get_settings(uid)
            if s and s[2] == tid:
                await db_update_setting(uid, current_template_id=None)
            await show_templates_menu(event, uid)

        elif data == "menu_get_profile":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit(
                    "⚠️ **حساب کاربری فعالی وجود ندارد.**\n\n"
                    "لطفاً ابتدا از بخش «مدیریت حساب‌های کاربری»\n"
                    "یک حساب کاربری انتخاب فرمایید.",
                    buttons=back_kb()
                )
                return
            user_states[uid] = "WAIT_TARGET_INPUT"
            await event.edit(
                "🔍 **دریافت پروفایل و چهره**\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً شناسه یا شناسه‌های کاربر مورد نظر را ارسال فرمایید.\n\n"
                "**انواع شناسه:**\n"
                "• شناسه عددی: `123456789`\n"
                "• نام کاربری: `@username`\n"
                "• پیوند: `https://t.me/username`\n\n"
                "**نکته:** می‌توانید چند شناسه را به صورت همزمان\n"
                "و در چند خط ارسال فرمایید.\n\n"
                "**نمونه:**\n"
                "`@user1 @user2 @user3`",
                buttons=back_kb()
            )

        elif data == "menu_multi":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("⚠️ **حساب کاربری فعالی وجود ندارد.**", buttons=back_kb())
                return
            user_states[uid] = "WAIT_MULTI_IDS"
            await event.edit(
                "📋 **پردازش چندگانه شناسه‌ها**\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً هر شناسه را در یک خط جداگانه ارسال فرمایید.\n\n"
                "**نمونه:**\n"
                "`123456789`\n"
                "`@username1`\n"
                "`@username2`",
                buttons=back_kb()
            )

        elif data == "menu_group":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("⚠️ **حساب کاربری فعالی وجود ندارد.**", buttons=back_kb())
                return
            user_states[uid] = "WAIT_GROUP_LINK"
            await event.edit(
                "👥 **پردازش گروه**\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً پیوند یا نام کاربری گروه مورد نظر را ارسال فرمایید.\n\n"
                "**نمونه:**\n"
                "`https://t.me/groupname`\n"
                "`@groupname`",
                buttons=back_kb()
            )

    except Exception as e:
        logger.exception(f"callback error: {e}")


async def submit_code(event, uid, code):
    client = user_temp[uid].get('client')
    phone = user_temp[uid]['phone']
    phone_code_hash = user_temp[uid].get('phone_code_hash')

    await event.edit("⏳ **در حال بررسی کد تایید...**")

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        await finalize_account(event, uid, client)
    except SessionPasswordNeededError:
        user_states[uid] = "WAIT_PASSWORD"
        await event.edit(
            "🔐 **رمز عبور دو مرحله‌ای**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "حساب کاربری مورد نظر دارای رمز عبور دو مرحله‌ای است.\n\n"
            "لطفاً رمز عبور خود را ارسال فرمایید."
        )
    except PhoneCodeInvalidError:
        user_temp[uid]['input_code'] = ''
        await event.edit(
            "❌ **کد وارد شده اشتباه است.**\n\n"
            "لطفاً مجدداً تلاش فرمایید:",
            buttons=get_number_keypad_kb()
        )
    except PhoneCodeExpiredError:
        await event.edit("⏰ **کد منقضی شده است.**\n\nلطفاً /cancel را ارسال کرده و مجدداً تلاش فرمایید.")
        user_states.pop(uid, None)
        user_temp.pop(uid, None)
    except Exception as e:
        await event.edit(f"❌ **خطا:** `{str(e)[:200]}`")
        user_states.pop(uid, None)
        user_temp.pop(uid, None)


# ==================== نمایش اکانت‌ها ====================
async def show_accounts_menu(event, uid):
    accounts = await db_get_accounts(uid)
    if not accounts:
        text = (
            "📁 **مدیریت حساب‌های کاربری**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "هیچ حساب کاربری ثبت نشده است.\n\n"
            "برای افزودن حساب جدید، از دکمه زیر استفاده فرمایید."
        )
    else:
        lines = [
            "📁 **مدیریت حساب‌های کاربری**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"تعداد حساب‌های ثبت شده: **{len(accounts)}**\n\n"
        ]
        for i, a in enumerate(accounts, 1):
            lines.append(f"**{i}.** {a[6]}\n     شماره: `{a[2]}`\n")
        text = "\n".join(lines)

    kb = []
    for a in accounts:
        kb.append([btn(f"👤 {a[6]} | {a[2][-4:]}", f"acc_view_{a[0]}".encode(), "primary")])
    kb.append([btn("➕ افزودن حساب کاربری جدید", b"acc_add", "success")])
    kb.append([btn("🔙 بازگشت به منوی اصلی", b"menu_main", "danger")])
    await event.edit(text, buttons=kb)


async def show_account_detail(event, uid, acc_id):
    a = await db_get_account(acc_id)
    if not a:
        await event.answer("حساب کاربری یافت نشد", alert=True)
        return
    s = await db_get_settings(uid)
    is_current = s and s[1] == acc_id
    status_line = "✅ **این حساب کاربری فعال است.**" if is_current else "❌ **این حساب کاربری فعال نیست.**"

    text = (
        "ℹ️ **اطلاعات حساب کاربری**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **نام:** {a[6]}\n"
        f"🆔 **نام کاربری:** `@{a[7] or 'ندارد'}`\n"
        f"📱 **شماره تلفن:** `{a[2]}`\n"
        f"🔑 **شناسه API:** `{a[3]}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{status_line}"
    )

    kb = []
    if not is_current:
        kb.append([btn("✅ فعال‌سازی این حساب", f"acc_use_{acc_id}".encode(), "success")])
    kb.append([btn("🗑 حذف حساب کاربری", f"acc_del_{acc_id}".encode(), "danger")])
    kb.append([btn("🔙 بازگشت به لیست", b"menu_accounts", "primary")])
    await event.edit(text, buttons=kb)


async def show_templates_menu(event, uid):
    templates = await db_get_templates(uid)
    if not templates:
        text = (
            "📝 **مدیریت قالب‌های متنی**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "هیچ قالبی ثبت نشده است.\n\n"
            "برای ایجاد قالب جدید، از دکمه زیر استفاده فرمایید."
        )
    else:
        lines = [
            "📝 **مدیریت قالب‌های متنی**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"تعداد قالب‌ها: **{len(templates)}**\n\n"
        ]
        for i, t in enumerate(templates, 1):
            preview = t[3][:60].replace('\n', ' ')
            lines.append(f"**{i}.** {t[2]}\n     _{preview}..._\n")
        text = "\n".join(lines)

    kb = []
    for t in templates:
        kb.append([btn(f"📄 {t[2]}", f"tmpl_view_{t[0]}".encode(), "primary")])
    kb.append([btn("➕ ایجاد قالب جدید", b"tmpl_add", "success")])
    kb.append([btn("🔙 بازگشت به منوی اصلی", b"menu_main", "danger")])
    await event.edit(text, buttons=kb)


async def show_template_detail(event, uid, tid):
    t = await db_get_template(tid)
    if not t:
        await event.answer("قالب یافت نشد", alert=True)
        return
    s = await db_get_settings(uid)
    is_current = s and s[2] == tid
    status_line = "✅ **این قالب فعال است.**" if is_current else "❌ **این قالب فعال نیست.**"

    text = (
        "📄 **اطلاعات قالب متنی**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 **عنوان:** {t[2]}\n\n"
        "**متن قالب:**\n"
        f"`{t[3]}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "**متغیرهای قابل استفاده:**\n"
        "• `{name}` — نام کامل\n"
        "• `{username}` — نام کاربری\n"
        "• `{id}` — شناسه عددی\n"
        "• `{first_name}` — نام کوچک\n"
        "• `{last_name}` — نام خانوادگی\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{status_line}"
    )

    kb = []
    if not is_current:
        kb.append([btn("✅ فعال‌سازی این قالب", f"tmpl_use_{tid}".encode(), "success")])
    kb.append([btn("🗑 حذف قالب", f"tmpl_del_{tid}".encode(), "danger")])
    kb.append([btn("🔙 بازگشت به لیست", b"menu_templates", "primary")])
    await event.edit(text, buttons=kb)


# ==================== هندلر پیام‌ها ====================
@bot.on(events.NewMessage)
async def handle_message(event):
    if not is_owner(event.sender_id):
        return

    uid = event.sender_id
    state = user_states.get(uid)

    if state == "WAIT_PHONE" and event.message.contact:
        phone = event.message.contact.phone_number
        if not phone.startswith('+'):
            phone = '+' + phone
        user_temp[uid]['phone'] = phone
        user_states[uid] = "WAIT_API_ID"
        await bot.send_message(uid, "✅", buttons=Button.clear())
        await event.respond(
            "✅ **شماره تلفن دریافت شد.**\n\n"
            "**مرحله ۲ از ۴**\n\n"
            "لطفاً شناسه API خود را ارسال فرمایید.\n\n"
            "(این مقدار یک عدد است که از سایت my.telegram.org دریافت می‌شود.)"
        )
        return

    if not event.text:
        return
    if event.text.startswith('/'):
        return
    if not state:
        return

    text = event.text.strip()

    try:
        if state == "WAIT_PHONE":
            if not re.match(r'^\+?[0-9]{10,15}$', text):
                await event.respond("❌ **شماره تلفن وارد شده نامعتبر است.**")
                return
            user_temp[uid]['phone'] = text
            user_states[uid] = "WAIT_API_ID"
            await bot.send_message(uid, "✅", buttons=Button.clear())
            await event.respond("✅ **شماره دریافت شد.**\n\n**مرحله ۲ از ۴**\n\nشناسه API:")

        elif state == "WAIT_API_ID":
            if not text.isdigit():
                await event.respond("❌ **شناسه API باید یک عدد باشد.**")
                return
            user_temp[uid]['api_id'] = int(text)
            user_states[uid] = "WAIT_API_HASH"
            await event.respond("✅ **شناسه API دریافت شد.**\n\n**مرحله ۳ از ۴**\n\nهش API:")

        elif state == "WAIT_API_HASH":
            if len(text) < 20:
                await event.respond("❌ **هش API نامعتبر است.**")
                return
            user_temp[uid]['api_hash'] = text
            user_states[uid] = "WAIT_CODE_BUTTONS"
            user_temp[uid]['input_code'] = ''

            phone = user_temp[uid]['phone']
            api_id = user_temp[uid]['api_id']
            api_hash = user_temp[uid]['api_hash']

            msg = await event.respond("⏳ **در حال ارسال کد تایید...**")

            try:
                client = TelegramClient(StringSession(), api_id, api_hash)
                await client.connect()
                sent = await client.send_code_request(phone)
                user_temp[uid]['client'] = client
                user_temp[uid]['phone_code_hash'] = sent.phone_code_hash

                await msg.edit(
                    f"✅ **کد تایید ارسال شد.**\n\n"
                    f"**مرحله ۴ از ۴**\n\n"
                    f"کد پنج رقمی به شماره `{phone}` ارسال گردید.\n\n"
                    f"لطفاً کد را با استفاده از دکمه‌های زیر وارد فرمایید:",
                    buttons=get_number_keypad_kb()
                )
            except PhoneNumberInvalidError:
                await msg.edit("❌ **شماره تلفن نامعتبر است.**")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)
            except FloodWaitError as e:
                await msg.edit(f"⏳ **محدودیت:** {e.seconds} ثانیه")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)
            except Exception as e:
                await msg.edit(f"❌ **خطا:** `{str(e)[:200]}`")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)

        elif state == "WAIT_PASSWORD":
            password = text.strip()
            client = user_temp[uid].get('client')
            try:
                await client.sign_in(password=password)
                await finalize_account(event, uid, client)
            except Exception as e:
                await event.respond(f"❌ **رمز عبور اشتباه است.**\n\n`{str(e)[:200]}`")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)

        elif state == "WAIT_NEW_TEMPLATE":
            user_temp[uid]['tmpl_title'] = text
            user_states[uid] = "WAIT_TEMPLATE_TEXT"
            await event.respond(
                "📝 **متن قالب را ارسال فرمایید.**\n\n"
                "**متغیرهای قابل استفاده:**\n"
                "• `{name}` — نام کامل\n"
                "• `{username}` — نام کاربری\n"
                "• `{id}` — شناسه عددی\n"
                "• `{first_name}` — نام کوچک\n"
                "• `{last_name}` — نام خانوادگی"
            )

        elif state == "WAIT_TEMPLATE_TEXT":
            title = user_temp[uid].get('tmpl_title', 'بدون عنوان')
            tid = await db_add_template(uid, title, text)
            await db_update_setting(uid, current_template_id=tid)
            await event.respond(
                f"✅ **قالب «{title}» با موفقیت ایجاد و فعال شد.**",
                buttons=back_kb(b"menu_templates")
            )
            user_states.pop(uid, None)
            user_temp.pop(uid, None)

        elif state == "WAIT_TARGET_INPUT":
            user_states.pop(uid, None)
            targets = []
            for line in text.split('\n'):
                for part in line.split(','):
                    for item in part.split():
                        item = item.strip()
                        if item:
                            targets.append(item)
            if not targets:
                await event.respond("❌ **هیچ شناسه‌ای ارسال نشد.**")
                return
            if len(targets) == 1:
                asyncio.create_task(process_single_target(event, uid, targets[0]))
            else:
                asyncio.create_task(process_multi_targets(event, uid, targets))

        elif state == "WAIT_MULTI_IDS":
            user_states.pop(uid, None)
            targets = []
            for line in text.split('\n'):
                for part in line.split(','):
                    for item in part.split():
                        item = item.strip()
                        if item:
                            targets.append(item)
            if not targets:
                await event.respond("❌ **هیچ شناسه‌ای ارسال نشد.**")
                return
            asyncio.create_task(process_multi_targets(event, uid, targets))

        elif state == "WAIT_GROUP_LINK":
            user_states.pop(uid, None)
            asyncio.create_task(process_group(event, uid, text))

    except Exception as e:
        logger.exception(f"message handler error: {e}")


async def finalize_account(event, uid, client):
    try:
        me = await client.get_me()
        session_str = client.session.save()
        phone = user_temp[uid]['phone']
        api_id = user_temp[uid]['api_id']
        api_hash = user_temp[uid]['api_hash']
        acc_name = me.first_name or "بدون نام"
        username = me.username or ""

        acc_id = await db_add_account(uid, phone, api_id, api_hash,
                                      session_str, acc_name, username)
        await db_update_setting(uid, current_account_id=acc_id)
        await client.disconnect()

        await event.respond(
            f"✅ **حساب کاربری با موفقیت اضافه و فعال شد.**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **نام:** {acc_name}\n"
            f"🆔 **نام کاربری:** `@{username or 'ندارد'}`\n"
            f"📱 **شماره تلفن:** `{phone}`",
            buttons=[[btn("📁 مدیریت حساب‌های کاربری", b"menu_accounts", "success")]]
        )
    except Exception as e:
        await event.respond(f"❌ **خطا:** `{str(e)[:200]}`")
    user_states.pop(uid, None)
    user_temp.pop(uid, None)


# ==================== پردازش کاربر ====================
async def process_single_target(event, uid, target_raw):
    client = None
    try:
        s = await db_get_settings(uid)
        if not s or not s[1]:
            await event.respond("⚠️ **حساب کاربری فعالی وجود ندارد.**")
            return
        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await event.respond("⚠️ **نشست منقضی شده است.**")
            return

        target = await resolve_entity(client, target_raw)
        if not target:
            await event.respond(f"❌ **کاربر `{target_raw}` یافت نشد.**")
            return

        template_text = ""
        if s[2]:
            t = await db_get_template(s[2])
            if t:
                template_text = t[3]

        status = await event.respond("🔍 **در حال بررسی پروفایل...**")

        photos, total, full_name, username, user_id_str = await extract_photos_with_faces(
            client, target, status
        )

        if not photos:
            await status.edit(
                f"❌ **هیچ عکسی با چهره انسانی از {full_name} یافت نشد.**\n\n"
                f"**دلایل احتمالی:**\n"
                f"• چهره واضح نیست\n"
                f"• چهره بسیار کوچک است\n"
                f"• کیفیت تصویر پایین است"
            )
            return

        footer = template_text
        if footer:
            footer = footer.replace("{name}", full_name)
            footer = footer.replace("{username}", f"@{username}" if username else "")
            footer = footer.replace("{first_name}", target.first_name or "")
            footer = footer.replace("{last_name}", target.last_name or "")
            footer = footer.replace("{id}", user_id_str)
        else:
            footer = f"👤 {full_name}"
            if username:
                footer += f"\n🆔 @{username}"

        if len(footer) > MAX_CAPTION_LENGTH:
            footer = footer[:MAX_CAPTION_LENGTH - 3] + "..."

        await status.edit(f"📤 **در حال ارسال {len(photos)} عکس...**")

        sent_owner = await send_photo_album(bot, uid, photos, caption=footer)

        if sent_owner == 0:
            await status.edit("❌ **خطا در ارسال.**")
            return

        try:
            await status.delete()
        except:
            pass

        import uuid
        send_key = str(uuid.uuid4())[:12]
        pending_channel_sends[send_key] = {'faces': photos, 'caption': footer}

        if len(pending_channel_sends) > 50:
            keys = list(pending_channel_sends.keys())[:20]
            for k in keys:
                pending_channel_sends.pop(k, None)

        await event.respond(
            f"✅ **عملیات با موفقیت انجام شد.**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **نام:** {full_name}\n"
            f"🆔 **نام کاربری:** `@{username or 'ندارد'}`\n"
            f"📊 **تعداد کل:** {total}\n"
            f"🖼 **چهره‌ها:** {len(photos)}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"❓ **آیا مایل به ارسال به کانال هستید؟**",
            buttons=[
                [btn("📡 ارسال به کانال", f"send_channel_{send_key}".encode(), "success")],
                [btn("❌ لغو", f"cancel_channel_{send_key}".encode(), "danger")]
            ]
        )

    except Exception as e:
        logger.exception(f"process error: {e}")
        try:
            await event.respond(f"❌ **خطا:** `{str(e)[:200]}`")
        except:
            pass
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def process_multi_targets(event, uid, targets):
    client = None
    try:
        s = await db_get_settings(uid)
        if not s or not s[1]:
            await event.respond("⚠️ **حساب کاربری فعالی وجود ندارد.**")
            return
        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await event.respond("⚠️ **نشست منقضی شده است.**")
            return

        template_text = ""
        if s[2]:
            t = await db_get_template(s[2])
            if t:
                template_text = t[3]

        total = len(targets)
        success = 0
        failed = 0
        status = await event.respond(
            f"⏳ **در حال پردازش {total} شناسه...**\n\n"
            f"لطفاً شکیبا باشید."
        )

        for idx, raw in enumerate(targets, 1):
            try:
                await status.edit(
                    f"⏳ **در حال پردازش...**\n\n"
                    f"📊 پیشرفت: **{idx}/{total}**\n"
                    f"✅ موفق: **{success}**\n"
                    f"❌ ناموفق: **{failed}**"
                )
            except:
                pass

            target = await resolve_entity(client, raw)
            if not target:
                failed += 1
                continue

            try:
                photos, cnt, full_name, username, user_id_str = await asyncio.wait_for(
                    extract_photos_with_faces(client, target, None),
                    timeout=120
                )
                if not photos:
                    failed += 1
                    logger.info(f"❌ {raw}: no photos with face")
                    continue

                footer = template_text
                if footer:
                    footer = footer.replace("{name}", full_name)
                    footer = footer.replace("{username}", f"@{username}" if username else "")
                    footer = footer.replace("{first_name}", target.first_name or "")
                    footer = footer.replace("{last_name}", target.last_name or "")
                    footer = footer.replace("{id}", user_id_str)

                if len(footer) > MAX_CAPTION_LENGTH:
                    footer = footer[:MAX_CAPTION_LENGTH - 3] + "..."

                await send_photo_album(bot, uid, photos, caption=footer or None)

                success += 1
            except asyncio.TimeoutError:
                logger.warning(f"⏰ timeout for {raw}")
                failed += 1
            except Exception as e:
                logger.exception(f"❌ {raw}: {e}")
                failed += 1

            await asyncio.sleep(1)

        await status.edit(
            f"✅ **عملیات با موفقیت به پایان رسید.**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 **تعداد کل:** {total}\n"
            f"✅ **موفق:** {success}\n"
            f"❌ **ناموفق:** {failed}",
            buttons=back_kb()
        )

    except Exception as e:
        logger.exception(f"multi error: {e}")
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def process_group(event, uid, link):
    client = None
    try:
        s = await db_get_settings(uid)
        if not s or not s[1]:
            await event.respond("⚠️ **حساب کاربری فعالی وجود ندارد.**")
            return
        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await event.respond("⚠️ **نشست منقضی شده است.**")
            return

        entity = None
        try:
            if '+' in link:
                hash_part = link.split('+')[-1].split('/')[0]
                try:
                    result = await client(ImportChatInviteRequest(hash_part))
                    entity = result.chats[0]
                except:
                    result = await client(CheckChatInviteRequest(hash_part))
                    entity = result.chat
            else:
                name = link.split('/')[-1]
                if name.startswith('@'):
                    name = name[1:]
                entity = await client.get_entity(name)
        except Exception as e:
            await event.respond(f"❌ **گروه یافت نشد.**")
            return

        group_title = getattr(entity, 'title', 'بدون نام')
        status = await event.respond(f"👥 **در حال بررسی گروه**\n\n📛 **{group_title}**\n⏳ دریافت اعضا...")

        participants = []
        offset = 0
        while True:
            try:
                result = await client(GetParticipantsRequest(
                    channel=entity, filter=ChannelParticipantsSearch(''),
                    offset=offset, limit=100, hash=0
                ))
                if not result.users:
                    break
                participants.extend(result.users)
                offset += len(result.users)
                if len(result.users) < 100 or len(participants) >= 50:
                    break
                await asyncio.sleep(0.5)
            except:
                break

        total = len(participants)
        if total == 0:
            await status.edit("❌ **هیچ عضوی یافت نشد.**")
            return

        await status.edit(f"👥 **پردازش گروه**\n\n📛 **{group_title}**\n📊 **{total}** عضو\n⏳ پردازش...")

        template_text = ""
        if s[2]:
            t = await db_get_template(s[2])
            if t:
                template_text = t[3]

        success = 0
        for idx, user in enumerate(participants, 1):
            if user.bot or user.deleted:
                continue
            try:
                await status.edit(
                    f"⏳ **پردازش گروه**\n\n"
                    f"📛 **{group_title}**\n"
                    f"📊 **{idx}/{total}**\n"
                    f"✅ **{success}**"
                )
            except:
                pass

            try:
                photos, cnt, full_name, username, user_id_str = await extract_photos_with_faces(
                    client, user, None
                )
                if not photos:
                    continue

                footer = template_text
                if footer:
                    footer = footer.replace("{name}", full_name)
                    footer = footer.replace("{username}", f"@{username}" if username else "")
                    footer = footer.replace("{first_name}", user.first_name or "")
                    footer = footer.replace("{last_name}", user.last_name or "")
                    footer = footer.replace("{id}", user_id_str)

                if len(footer) > MAX_CAPTION_LENGTH:
                    footer = footer[:MAX_CAPTION_LENGTH - 3] + "..."

                await send_photo_album(bot, uid, photos, caption=footer or None)

                success += 1
            except Exception as e:
                logger.warning(f"group target error: {e}")

            await asyncio.sleep(1)

        await status.edit(
            f"✅ **عملیات به پایان رسید.**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📛 **{group_title}**\n"
            f"📊 **کل:** {total}\n"
            f"✅ **پردازش شده:** {success}",
            buttons=back_kb()
        )

    except Exception as e:
        logger.exception(f"group error: {e}")
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


# ==================== Main ====================
async def main():
    await init_db()
    init_yunet()
    init_mediapipe()

    logger.info(f"مالکان: {ADMIN_IDS}")
    logger.info(f"کانال: {CHANNEL_ID}")
    logger.info(f"MediaPipe: {'✅ فعال' if MEDIAPIPE_AVAILABLE else '❌ غیرفعال'}")
    logger.info(f"تنظیمات: confidence={FACE_CONFIDENCE}, size={MIN_FACE_SIZE}, "
                f"sharpness={MIN_SHARPNESS}, skin={MIN_SKIN_RATIO}")

    max_retries = 10
    connected = False

    for attempt in range(max_retries):
        try:
            await bot.start(bot_token=BOT_TOKEN)
            me = await bot.get_me()
            logger.info(f"✅ ربات @{me.username} راه‌اندازی شد")
            connected = True
            break
        except FloodWaitError as e:
            wait_time = e.seconds + 30
            logger.warning(f"FloodWait: {wait_time}s")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.exception(f"start error: {e}")
            await asyncio.sleep(60)

    if not connected:
        logger.error("راه‌اندازی نشد.")
        return

    logger.info("ربات آنلاین است.")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("متوقف شد.")
