"""
ربات دریافت پروفایل
- عکس‌های دارای چهره با کیفیت اصلی
- Reply Keyboard برای شماره
- ارسال آلبومی کامل
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

# ==================== تنظیمات تشخیص ====================
MAX_PHOTOS_TOTAL = 10        # حداکثر عکس (تا 10 برای آلبوم تلگرام)
FACE_CONFIDENCE = 0.65
MIN_FACE_SIZE = 40
MAX_FACES_PER_IMAGE = 3
VIDEO_MAX_SIZE_MB = 20
VIDEO_FRAME_SKIP = 30
MIN_FACE_RATIO = 0.5
MAX_FACE_RATIO = 1.8


# ==================== State ====================
user_states = {}
user_temp = {}
pending_channel_sends = {}


# ==================== YuNet ====================
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


def has_human_face(img_bgr):
    """چک می‌کنه آیا عکس حاوی چهره انسانی هست"""
    detector = init_yunet()
    if detector is None:
        return False

    try:
        h, w = img_bgr.shape[:2]

        # resize فقط برای تشخیص سریع‌تر
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

        valid = 0
        for face in faces[:MAX_FACES_PER_IMAGE]:
            score = face[-1]
            fw = face[2] * ratio_w
            fh = face[3] * ratio_h

            if score < FACE_CONFIDENCE:
                continue
            if fw < MIN_FACE_SIZE or fh < MIN_FACE_SIZE:
                continue

            ratio = fw / fh if fh > 0 else 0
            if ratio < MIN_FACE_RATIO or ratio > MAX_FACE_RATIO:
                continue

            valid += 1

        return valid > 0

    except Exception as e:
        logger.exception(f"has_human_face error: {e}")
        return False


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
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS accounts (
                        id SERIAL PRIMARY KEY,
                        owner_id BIGINT,
                        phone TEXT,
                        api_id INTEGER,
                        api_hash TEXT,
                        session_string TEXT,
                        account_name TEXT,
                        username TEXT,
                        created_date TEXT,
                        is_active INTEGER DEFAULT 1
                    )
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS templates (
                        id SERIAL PRIMARY KEY,
                        owner_id BIGINT,
                        title TEXT,
                        text TEXT,
                        created_date TEXT
                    )
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        owner_id BIGINT PRIMARY KEY,
                        current_account_id INTEGER,
                        current_template_id INTEGER
                    )
                ''')
            logger.info("PostgreSQL آماده شد")
            return
        except Exception as e:
            logger.error(f"PostgreSQL error: {e}")
            USE_POSTGRES = False

    conn = sqlite3.connect("private_bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER, phone TEXT, api_id INTEGER, api_hash TEXT,
        session_string TEXT, account_name TEXT, username TEXT,
        created_date TEXT, is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER, title TEXT, text TEXT, created_date TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        owner_id INTEGER PRIMARY KEY,
        current_account_id INTEGER, current_template_id INTEGER
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
        'SELECT * FROM settings WHERE owner_id = ?',
        (int(owner_id),), fetch_one=True
    )
    if not row:
        await db_exec('INSERT INTO settings (owner_id) VALUES (?)', (int(owner_id),))
        row = await db_exec(
            'SELECT * FROM settings WHERE owner_id = ?',
            (int(owner_id),), fetch_one=True
        )
    return row


async def db_update_setting(owner_id, **kwargs):
    await db_get_settings(owner_id)
    for key, val in kwargs.items():
        await db_exec(
            f'UPDATE settings SET {key} = ? WHERE owner_id = ?',
            (val, int(owner_id))
        )


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
            btn("مدیریت اکانت‌ها 📁", b"menu_accounts", "primary"),
            btn("مدیریت قالب‌ها 📝", b"menu_templates", "primary")
        ],
        [btn("دریافت پروفایل 🔍", b"menu_get_profile", "success")],
        [
            btn("چند شناسه همزمان 📋", b"menu_multi", "primary"),
            btn("گروه 👥", b"menu_group", "primary")
        ]
    ]


def back_kb(target=b"menu_main"):
    return [[btn("بازگشت 🔙", target, "danger")]]


async def main_menu_text(owner_id):
    s = await db_get_settings(owner_id)
    acc_name = "انتخاب نشده"
    tmpl_name = "انتخاب نشده"

    if s and s[1]:
        a = await db_get_account(s[1])
        if a:
            acc_name = a[6]
    if s and s[2]:
        t = await db_get_template(s[2])
        if t:
            tmpl_name = t[2]

    return (
        "پنل مدیریت خصوصی\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"اکانت فعال: {acc_name}\n"
        f"قالب فعال: {tmpl_name}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "لطفاً از گزینه‌های زیر انتخاب فرمایید:"
    )


# ==================== ربات ====================
bot = TelegramClient(StringSession(), BOT_API_ID, BOT_API_HASH)


# ==================== ارسال آلبومی (اصلاح‌شده) ====================
async def send_photo_album(client, chat_id, photo_bytes_list, caption=None):
    """
    ارسال عکس‌ها به صورت آلبوم
    - روش ۱: send_file با لیست فایل‌ها (ساده‌تر، معمولاً کار می‌کنه)
    - روش ۲: SendMultiMediaRequest (fallback)
    """
    if not photo_bytes_list:
        return 0

    photo_bytes_list = photo_bytes_list[:10]
    total = len(photo_bytes_list)

    logger.info(f"ارسال {total} عکس به {chat_id}")

    # ═══════ روش ۱: send_file با لیست ═══════
    try:
        # آپلود همه
        uploaded = []
        for idx, data in enumerate(photo_bytes_list):
            try:
                f = await client.upload_file(
                    BytesIO(data),
                    file_name=f"photo_{idx}.jpg"
                )
                uploaded.append(f)
            except Exception as e:
                logger.warning(f"upload error {idx}: {e}")

        if not uploaded:
            return 0

        if len(uploaded) == 1:
            await client.send_file(
                chat_id, uploaded[0],
                caption=caption,
                force_document=False
            )
            logger.info(f"تک عکس ارسال شد")
            return 1

        # آلبوم با لیست
        try:
            await client.send_file(
                chat_id,
                uploaded,
                caption=caption,
                force_document=False
            )
            logger.info(f"آلبوم {len(uploaded)} عکسی ارسال شد (روش send_file)")
            return len(uploaded)
        except Exception as e:
            logger.warning(f"send_file آلبوم fail: {e}")

    except Exception as e:
        logger.warning(f"روش ۱ fail: {e}")

    # ═══════ روش ۲: SendMultiMediaRequest ═══════
    try:
        uploaded = []
        for idx, data in enumerate(photo_bytes_list):
            try:
                f = await client.upload_file(
                    BytesIO(data),
                    file_name=f"photo_{idx}.jpg"
                )
                uploaded.append(f)
            except Exception as e:
                logger.warning(f"upload error {idx}: {e}")

        if not uploaded:
            return 0

        if len(uploaded) == 1:
            await client.send_file(chat_id, uploaded[0], caption=caption, force_document=False)
            return 1

        media_list = []
        for idx, f in enumerate(uploaded):
            media = InputMediaPhoto(
                file=f,
                caption=caption if idx == 0 else None,
            )
            media_list.append(InputSingleMedia(media=media))

        await client(SendMultiMediaRequest(
            peer=chat_id,
            multi_media=media_list
        ))
        logger.info(f"آلبوم {len(uploaded)} عکسی ارسال شد (روش SendMultiMedia)")
        return len(uploaded)

    except Exception as e:
        logger.exception(f"روش ۲ fail: {e}")

    # ═══════ روش ۳: تک تک ═══════
    sent = 0
    for idx, data in enumerate(photo_bytes_list):
        try:
            await client.send_file(
                chat_id,
                BytesIO(data),
                caption=caption if idx == 0 else None,
                force_document=False
            )
            sent += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"single send error {idx}: {e}")

    logger.info(f"روش ۳: {sent}/{total} عکس ارسال شد")
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


# ==================== استخراج عکس‌های دارای چهره ====================
async def extract_photos_with_faces(client, target, status_msg=None, max_photos=MAX_PHOTOS_TOTAL):
    target_id = target.id
    first_name = target.first_name or ""
    last_name = target.last_name or ""
    full_name = (first_name + " " + last_name).strip() or "بدون نام"
    username = target.username or ""
    user_id_str = str(target.id)

    try:
        photos = await client(GetUserPhotosRequest(
            user_id=target_id, offset=0, max_id=0, limit=200
        ))
        photo_list = photos.photos if hasattr(photos, 'photos') else []
    except Exception as e:
        logger.warning(f"get photos error: {e}")
        photo_list = []

    total = len(photo_list)
    logger.info(f"extract: user={full_name} total={total}")

    if status_msg and total > 0:
        try:
            await status_msg.edit(
                f"🔍 در حال بررسی {total} فایل...\n"
                f"کاربر: {full_name}"
            )
        except:
            pass

    all_photos = []
    seen_hashes = set()

    for idx, photo in enumerate(photo_list):
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
                                logger.info(f"video {idx}: +1 (total {len(all_photos)})")
                finally:
                    try:
                        os.remove(tmp_path)
                    except:
                        pass
            else:
                # عکس اصلی — bytes رو نگه‌دار
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
                        logger.info(f"photo {idx}: +1 (total {len(all_photos)})")

            if status_msg and (idx + 1) % 3 == 0:
                try:
                    await status_msg.edit(
                        f"🔍 بررسی... {idx+1}/{total}\n"
                        f"عکس‌های دارای چهره: {len(all_photos)}"
                    )
                except:
                    pass

        except Exception as e:
            logger.warning(f"file {idx} error: {e}")
            continue

    logger.info(f"extract done: {len(all_photos)} photos with faces")
    return all_photos, total, full_name, username, user_id_str


def extract_frames_from_video(video_path, skip=30):
    frames = []
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        count = 0
        max_frames = 10

        while len(frames) < max_frames:
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
    await event.respond("عملیات لغو شد.", buttons=back_kb())


# ==================== Callback Handler ====================
@bot.on(events.CallbackQuery)
async def handle_callback(event):
    if not is_owner(event.sender_id):
        return

    data = event.data.decode('utf-8')
    uid = event.sender_id

    try:
        if data.startswith("send_channel_"):
            key = data.replace("send_channel_", "")
            payload = pending_channel_sends.get(key)
            if not payload:
                await event.answer("منقضی شده", alert=True)
                return

            await event.answer("در حال ارسال...")
            faces = payload['faces']
            caption = payload['caption']

            try:
                sent = await send_photo_album(bot, CHANNEL_ID, faces, caption=caption or None)
                await event.edit(f"✅ به کانال ارسال شد ({sent} عکس)", buttons=back_kb())
            except Exception as e:
                await event.edit(f"❌ خطا: {str(e)[:100]}", buttons=back_kb())

            pending_channel_sends.pop(key, None)
            return

        if data.startswith("cancel_channel_"):
            key = data.replace("cancel_channel_", "")
            pending_channel_sends.pop(key, None)
            await event.edit("لغو شد.", buttons=back_kb())
            return

        if data == "menu_main":
            text = await main_menu_text(uid)
            await event.edit(text, buttons=main_menu_kb())

        elif data == "menu_accounts":
            await show_accounts_menu(event, uid)

        elif data == "acc_add":
            # ═══════ نکته: دکمه شماره به صورت Reply Keyboard ═══════
            user_states[uid] = "WAIT_PHONE"
            user_temp[uid] = {}
            await event.edit(
                "افزودن اکانت\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "برای افزودن شماره، از دکمه شیشه‌ای زیر استفاده کنید.\n\n"
                "⚠️ برای انصراف /cancel بزنید.",
                buttons=back_kb(b"menu_accounts")
            )
            # ارسال Reply Keyboard با دکمه شماره
            await bot.send_message(
                uid,
                "📱 لطفاً شماره‌ت رو با دکمه زیر بفرست:",
                buttons=Button.request_phone("📱 شماره من", resize=True)
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
                "قالب جدید\n\nعنوان قالب:",
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
                await event.edit("ابتدا یک اکانت انتخاب فرمایید.", buttons=back_kb())
                return
            user_states[uid] = "WAIT_TARGET_INPUT"
            await event.edit(
                "دریافت پروفایل\n\n"
                "شناسه کاربر:\n"
                "• آیدی عددی: `123456789`\n"
                "• یوزرنیم: `@username`\n"
                "• لینک: `https://t.me/username`",
                buttons=back_kb()
            )

        elif data == "menu_multi":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("ابتدا یک اکانت انتخاب فرمایید.", buttons=back_kb())
                return
            user_states[uid] = "WAIT_MULTI_IDS"
            await event.edit(
                "چند شناسه\n\nهر خط یه شناسه:",
                buttons=back_kb()
            )

        elif data == "menu_group":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("ابتدا یک اکانت انتخاب فرمایید.", buttons=back_kb())
                return
            user_states[uid] = "WAIT_GROUP_LINK"
            await event.edit(
                "گروه\n\nلینک گروه:",
                buttons=back_kb()
            )

    except Exception as e:
        logger.exception(f"callback error: {e}")


# ==================== نمایش اکانت‌ها ====================
async def show_accounts_menu(event, uid):
    accounts = await db_get_accounts(uid)
    if not accounts:
        text = "هنوز اکانتی اضافه نشده."
    else:
        lines = ["اکانت‌ها:\n"]
        for i, a in enumerate(accounts, 1):
            lines.append(f"{i}. {a[6]}\n   `{a[2]}`")
        text = "\n".join(lines)

    kb = []
    for a in accounts:
        kb.append([btn(f"{a[6]} | {a[2][-4:]} 📱", f"acc_view_{a[0]}".encode(), "primary")])
    kb.append([btn("افزودن اکانت ➕", b"acc_add", "success")])
    kb.append([btn("بازگشت 🔙", b"menu_main", "danger")])
    await event.edit(text, buttons=kb)


async def show_account_detail(event, uid, acc_id):
    a = await db_get_account(acc_id)
    if not a:
        await event.answer("یافت نشد", alert=True)
        return

    s = await db_get_settings(uid)
    is_current = s and s[1] == acc_id

    text = (
        f"اطلاعات اکانت\n\n"
        f"نام: {a[6]}\n"
        f"یوزرنیم: `@{a[7] or 'ندارد'}`\n"
        f"شماره: `{a[2]}`\n\n"
        f"{'✅ فعال' if is_current else '❌ غیرفعال'}"
    )

    kb = []
    if not is_current:
        kb.append([btn("فعال کردن ✅", f"acc_use_{acc_id}".encode(), "success")])
    kb.append([btn("حذف 🗑", f"acc_del_{acc_id}".encode(), "danger")])
    kb.append([btn("بازگشت 🔙", b"menu_accounts", "primary")])
    await event.edit(text, buttons=kb)


async def show_templates_menu(event, uid):
    templates = await db_get_templates(uid)
    if not templates:
        text = "هنوز قالبی نیست."
    else:
        lines = ["قالب‌ها:\n"]
        for i, t in enumerate(templates, 1):
            preview = t[3][:60].replace('\n', ' ')
            lines.append(f"{i}. {t[2]}\n   {preview}...")
        text = "\n".join(lines)

    kb = []
    for t in templates:
        kb.append([btn(f"{t[2]} 📝", f"tmpl_view_{t[0]}".encode(), "primary")])
    kb.append([btn("قالب جدید ➕", b"tmpl_add", "success")])
    kb.append([btn("بازگشت 🔙", b"menu_main", "danger")])
    await event.edit(text, buttons=kb)


async def show_template_detail(event, uid, tid):
    t = await db_get_template(tid)
    if not t:
        await event.answer("یافت نشد", alert=True)
        return

    s = await db_get_settings(uid)
    is_current = s and s[2] == tid

    text = (
        f"قالب: {t[2]}\n\n"
        f"متن:\n`{t[3]}`\n\n"
        f"متغیرها: `{{name}}` `{{username}}` `{{id}}`\n\n"
        f"{'✅ فعال' if is_current else '❌ غیرفعال'}"
    )

    kb = []
    if not is_current:
        kb.append([btn("فعال کردن ✅", f"tmpl_use_{tid}".encode(), "success")])
    kb.append([btn("حذف 🗑", f"tmpl_del_{tid}".encode(), "danger")])
    kb.append([btn("بازگشت 🔙", b"menu_templates", "primary")])
    await event.edit(text, buttons=kb)


# ==================== هندلر پیام‌ها ====================
@bot.on(events.NewMessage)
async def handle_message(event):
    if not is_owner(event.sender_id):
        return

    uid = event.sender_id
    state = user_states.get(uid)

    # ═══════ چک کردن شماره با contact ═══════
    if state == "WAIT_PHONE" and event.message.contact:
        phone = event.message.contact.phone_number
        if not phone.startswith('+'):
            phone = '+' + phone
        user_temp[uid]['phone'] = phone
        user_states[uid] = "WAIT_API_ID"

        # حذف Reply Keyboard
        await bot.send_message(uid, "✅ شماره دریافت شد.", buttons=Button.clear())

        await event.respond(
            f"شماره: `{phone}`\n\n"
            "لطفاً API ID رو بفرست (از my.telegram.org):"
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
            # اگه دستی نوشت
            if not re.match(r'^\+?[0-9]{10,15}$', text):
                await event.respond("شماره نامعتبر. از دکمه 📱 شماره من استفاده کن یا /cancel")
                return
            user_temp[uid]['phone'] = text
            user_states[uid] = "WAIT_API_ID"
            await bot.send_message(uid, "✅ شماره دریافت شد.", buttons=Button.clear())
            await event.respond(f"شماره: `{text}`\n\nAPI ID:")

        elif state == "WAIT_API_ID":
            if not text.isdigit():
                await event.respond("API ID باید عدد باشد.")
                return
            user_temp[uid]['api_id'] = int(text)
            user_states[uid] = "WAIT_API_HASH"
            await event.respond("API Hash:")

        elif state == "WAIT_API_HASH":
            if len(text) < 20:
                await event.respond("API Hash نامعتبر.")
                return
            user_temp[uid]['api_hash'] = text
            user_states[uid] = "WAIT_CODE"
            phone = user_temp[uid]['phone']
            api_id = user_temp[uid]['api_id']
            api_hash = user_temp[uid]['api_hash']
            msg = await event.respond("در حال ارسال کد...")
            try:
                client = TelegramClient(StringSession(), api_id, api_hash)
                await client.connect()
                sent = await client.send_code_request(phone)
                user_temp[uid]['client'] = client
                user_temp[uid]['phone_code_hash'] = sent.phone_code_hash
                await msg.edit(f"کد به `{phone}` ارسال شد:\n`1.2.3.4.5`")
            except Exception as e:
                await msg.edit(f"خطا: {str(e)[:200]}")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)

        elif state == "WAIT_CODE":
            code = text.replace('.', '').replace(' ', '').strip()
            if not code.isdigit():
                await event.respond("کد باید عدد باشد.")
                return
            client = user_temp[uid].get('client')
            phone = user_temp[uid]['phone']
            phone_code_hash = user_temp[uid].get('phone_code_hash')
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
                await finalize_account(event, uid, client)
            except SessionPasswordNeededError:
                user_states[uid] = "WAIT_PASSWORD"
                await event.respond("رمز دو مرحله‌ای:")
            except PhoneCodeInvalidError:
                await event.respond("کد اشتباه.")
            except Exception as e:
                await event.respond(f"خطا: {str(e)[:200]}")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)

        elif state == "WAIT_PASSWORD":
            password = text.strip()
            client = user_temp[uid].get('client')
            try:
                await client.sign_in(password=password)
                await finalize_account(event, uid, client)
            except Exception as e:
                await event.respond(f"خطا: {str(e)[:200]}")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)

        elif state == "WAIT_NEW_TEMPLATE":
            user_temp[uid]['tmpl_title'] = text
            user_states[uid] = "WAIT_TEMPLATE_TEXT"
            await event.respond("متن قالب:\n\nمتغیرها: `{name}` `{username}` `{id}`")

        elif state == "WAIT_TEMPLATE_TEXT":
            title = user_temp[uid].get('tmpl_title', 'بدون عنوان')
            tid = await db_add_template(uid, title, text)
            await event.respond(f"✅ قالب «{title}» ذخیره شد.", buttons=back_kb(b"menu_templates"))
            user_states.pop(uid, None)
            user_temp.pop(uid, None)

        elif state == "WAIT_TARGET_INPUT":
            user_states.pop(uid, None)
            asyncio.create_task(process_single_target(event, uid, text))

        elif state == "WAIT_MULTI_IDS":
            user_states.pop(uid, None)
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            asyncio.create_task(process_multi_targets(event, uid, lines))

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
        await client.disconnect()
        await event.respond(
            f"✅ اکانت اضافه شد\n\n"
            f"نام: {acc_name}\n"
            f"یوزرنیم: `@{username or 'ندارد'}`\n"
            f"شماره: `{phone}`",
            buttons=[[btn("مدیریت اکانت‌ها 📁", b"menu_accounts", "success")]]
        )
    except Exception as e:
        await event.respond(f"خطا: {str(e)[:200]}")
    user_states.pop(uid, None)
    user_temp.pop(uid, None)


# ==================== پردازش کاربر ====================
async def process_single_target(event, uid, target_raw):
    client = None
    try:
        s = await db_get_settings(uid)
        if not s or not s[1]:
            await event.respond("اکانت فعال نداری.")
            return
        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await event.respond("سشن منقضی شده.")
            return

        target = await resolve_entity(client, target_raw)
        if not target:
            await event.respond(f"❌ کاربر `{target_raw}` پیدا نشد.")
            return

        template_text = ""
        if s[2]:
            t = await db_get_template(s[2])
            if t:
                template_text = t[3]

        status = await event.respond(f"🔍 در حال بررسی...")

        photos, total, full_name, username, user_id_str = await extract_photos_with_faces(
            client, target, status
        )

        if not photos:
            await status.edit(f"❌ هیچ عکسی با چهره از {full_name} پیدا نشد.")
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

        await status.edit(f"📤 در حال ارسال {len(photos)} عکس...")

        sent_owner = await send_photo_album(bot, uid, photos, caption=footer)

        if sent_owner == 0:
            await status.edit("❌ خطا در ارسال.")
            return

        import uuid
        send_key = str(uuid.uuid4())[:12]
        pending_channel_sends[send_key] = {
            'faces': photos,
            'caption': footer,
        }

        if len(pending_channel_sends) > 50:
            keys = list(pending_channel_sends.keys())[:20]
            for k in keys:
                pending_channel_sends.pop(k, None)

        await status.edit(
            f"✅ به پیوی ارسال شد ({sent_owner} عکس)\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 {full_name}\n"
            f"🆔 `@{username or 'ندارد'}`\n"
            f"📊 کل فایل: {total}\n"
            f"🖼 عکس دارای چهره: {len(photos)}\n\n"
            f"آیا به کانال ارسال شود؟",
            buttons=[
                [btn("📡 ارسال به کانال", f"send_channel_{send_key}".encode(), "success")],
                [btn("❌ لغو", f"cancel_channel_{send_key}".encode(), "danger")]
            ]
        )

    except Exception as e:
        logger.exception(f"process error: {e}")
        try:
            await event.respond(f"خطا: {str(e)[:200]}")
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
            await event.respond("اکانت فعال نداری.")
            return
        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await event.respond("سشن منقضی شده.")
            return

        template_text = ""
        if s[2]:
            t = await db_get_template(s[2])
            if t:
                template_text = t[3]

        total = len(targets)
        success = 0
        failed = 0
        status = await event.respond(f"پردازش {total} شناسه...")

        for idx, raw in enumerate(targets, 1):
            try:
                await status.edit(f"پردازش {idx}/{total}\nموفق: {success}")
            except:
                pass

            target = await resolve_entity(client, raw)
            if not target:
                failed += 1
                continue

            try:
                photos, cnt, full_name, username, user_id_str = await extract_photos_with_faces(
                    client, target, None
                )
                if not photos:
                    failed += 1
                    continue

                footer = template_text
                if footer:
                    footer = footer.replace("{name}", full_name)
                    footer = footer.replace("{username}", f"@{username}" if username else "")
                    footer = footer.replace("{first_name}", target.first_name or "")
                    footer = footer.replace("{last_name}", target.last_name or "")
                    footer = footer.replace("{id}", user_id_str)

                await send_photo_album(bot, uid, photos, caption=footer or None)

                if CHANNEL_ID:
                    try:
                        await send_photo_album(bot, CHANNEL_ID, photos, caption=footer or None)
                    except:
                        pass

                success += 1
            except Exception as e:
                logger.warning(f"multi target error: {e}")
                failed += 1

            await asyncio.sleep(1)

        await status.edit(
            f"✅ تکمیل\nکل: {total}\nموفق: {success}\nناموفق: {failed}",
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
            await event.respond("اکانت فعال نداری.")
            return
        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await event.respond("سشن منقضی شده.")
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
            await event.respond(f"گروه پیدا نشد.\n{str(e)[:200]}")
            return

        group_title = getattr(entity, 'title', 'بدون نام')
        status = await event.respond(f"گروه: {group_title}\nدریافت اعضا...")

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
            await status.edit("عضوی پیدا نشد.")
            return

        await status.edit(f"گروه: {group_title}\nتعداد: {total}\nپردازش...")

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
                await status.edit(f"پردازش {idx}/{total}\nموفق: {success}")
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

                await send_photo_album(bot, uid, photos, caption=footer or None)

                if CHANNEL_ID:
                    try:
                        await send_photo_album(bot, CHANNEL_ID, photos, caption=footer or None)
                    except:
                        pass

                success += 1
            except Exception as e:
                logger.warning(f"group target error: {e}")

            await asyncio.sleep(1)

        await status.edit(
            f"✅ تکمیل\nگروه: {group_title}\nتعداد: {total}\nپردازش: {success}",
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

    logger.info(f"مالکان: {ADMIN_IDS}")
    logger.info(f"کانال: {CHANNEL_ID}")

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
