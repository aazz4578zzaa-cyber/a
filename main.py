"""
ربات مدیریت پروفایل + جداسازی چهره
- چند مالک (ADMIN_IDS)
- دریافت پروفایل + ارسال آلبومی Photo
- جداسازی چهره با YuNet
"""

import asyncio
import logging
import os
import re
import sqlite3
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

# لیست مالکان — با کاما جدا شدن
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "8055930343,7803165903")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

if not ADMIN_IDS:
    raise ValueError("ADMIN_IDS environment variable is not set!")

PRIMARY_OWNER = ADMIN_IDS[0]

DATABASE_URL = os.environ.get("DATABASE_URL", "")

BOT_API_ID = int(os.environ.get("BOT_API_ID", "2040"))
BOT_API_HASH = os.environ.get("BOT_API_HASH", "b18441a1ff607e10a989891a5462e627")

ALBUM_CHUNK_SIZE = 10
FACE_CONFIDENCE = 0.6
MIN_FACE_SIZE = 40
MARGIN_RATIO = 0.3


# ==================== State ====================
user_states = {}
user_temp = {}


# ==================== YuNet Face Detector ====================
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
        logger.info("در حال دانلود مدل YuNet...")
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
            logger.error("مدل YuNet پیدا نشد")
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


def crop_faces_from_image(img_bgr):
    """تشخیص و کراپ چهره‌ها از یه عکس"""
    detector = init_yunet()
    if detector is None:
        return []

    try:
        h, w = img_bgr.shape[:2]
        detector.setInputSize((w, h))
        _, faces = detector.detect(img_bgr)

        if faces is None or len(faces) == 0:
            return []

        cropped = []
        for face in faces:
            x, y, fw, fh = face[0:4]
            score = face[-1]
            if score < FACE_CONFIDENCE:
                continue
            if fw < MIN_FACE_SIZE or fh < MIN_FACE_SIZE:
                continue

            mx = int(fw * MARGIN_RATIO)
            my = int(fh * MARGIN_RATIO)

            x1 = max(0, int(x) - mx)
            y1 = max(0, int(y) - my)
            x2 = min(w, int(x + fw) + mx)
            y2 = min(h, int(y + fh) + my)

            crop = img_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            ok, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if ok:
                cropped.append(buf.tobytes())

        return cropped
    except Exception as e:
        logger.exception(f"crop_faces error: {e}")
        return []


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
                        current_template_id INTEGER,
                        channel_id TEXT,
                        channel_title TEXT
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
        current_account_id INTEGER, current_template_id INTEGER,
        channel_id TEXT, channel_title TEXT
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
        [btn("جداسازی چهره 🎭", b"menu_crop_face", "success")],
        [
            btn("چند شناسه همزمان 📋", b"menu_multi", "primary"),
            btn("گروه و کانال 👥", b"menu_group", "primary")
        ],
        [
            btn("اتصال به کانال 📡", b"menu_channel", "primary"),
            btn("تنظیمات ⚙️", b"menu_settings", "primary")
        ]
    ]


def back_kb(target=b"menu_main"):
    return [[btn("بازگشت 🔙", target, "danger")]]


async def main_menu_text(owner_id):
    s = await db_get_settings(owner_id)
    acc_name = "انتخاب نشده"
    tmpl_name = "انتخاب نشده"
    ch_name = "متصل نشده"

    if s and s[1]:
        a = await db_get_account(s[1])
        if a:
            acc_name = a[6]
    if s and s[2]:
        t = await db_get_template(s[2])
        if t:
            tmpl_name = t[2]
    if s and s[3]:
        ch_name = s[4] or "متصل"

    return (
        "پنل مدیریت خصوصی\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"اکانت فعال: {acc_name}\n"
        f"قالب فعال: {tmpl_name}\n"
        f"کانال مقصد: {ch_name}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "لطفاً از گزینه‌های زیر انتخاب فرمایید:"
    )


# ==================== ربات ====================
bot = TelegramClient(StringSession(), BOT_API_ID, BOT_API_HASH)


# ==================== ارسال آلبومی Photo ====================
async def send_photo_album(client, chat_id, photo_bytes_list, caption=None):
    """ارسال لیست عکس‌ها به صورت آلبوم Photo (نه فایل)"""
    if not photo_bytes_list:
        return 0

    total_sent = 0

    for i in range(0, len(photo_bytes_list), ALBUM_CHUNK_SIZE):
        chunk = photo_bytes_list[i:i + ALBUM_CHUNK_SIZE]

        try:
            uploaded = []
            for idx, data in enumerate(chunk):
                try:
                    f = await client.upload_file(
                        BytesIO(data),
                        file_name=f"photo_{i + idx}.jpg"
                    )
                    uploaded.append(f)
                except Exception as e:
                    logger.warning(f"upload error: {e}")

            if not uploaded:
                continue

            media_list = []
            for idx, f in enumerate(uploaded):
                media = InputMediaPhoto(
                    file=f,
                    caption=caption if (i == 0 and idx == 0) else None,
                )
                media_list.append(InputSingleMedia(media=media))

            if len(media_list) == 1:
                await client.send_file(
                    chat_id, uploaded[0],
                    caption=caption,
                    force_document=False
                )
            else:
                await client(SendMultiMediaRequest(
                    peer=chat_id,
                    multi_media=media_list
                ))

            total_sent += len(uploaded)
            await asyncio.sleep(1)

        except Exception as e:
            logger.exception(f"album send error: {e}")
            for idx, f in enumerate(uploaded):
                try:
                    c = caption if (i == 0 and idx == 0) else None
                    await client.send_file(chat_id, f, caption=c, force_document=False)
                    total_sent += 1
                except Exception as e2:
                    logger.warning(f"single send: {e2}")

    return total_sent


# ==================== Resolve Entity ====================
async def resolve_entity(client, raw):
    """تبدیل ورودی به entity با پشتیبانی از آیدی/یوزرنیم/لینک"""
    raw = raw.strip()

    if "t.me/" in raw:
        raw = raw.split("t.me/")[-1].split("/")[0].split("?")[0]

    if raw.startswith('@'):
        raw = raw[1:]

    if raw.isdigit():
        try:
            entity = await client.get_entity(int(raw))
            logger.info(f"resolved numeric id: {raw}")
            return entity
        except Exception as e:
            logger.warning(f"numeric id failed: {e}")

    try:
        entity = await client.get_entity(raw)
        logger.info(f"resolved username: {raw}")
        return entity
    except Exception as e:
        logger.warning(f"username failed: {e}")

    try:
        entity = await client.get_entity(f"@{raw}")
        logger.info(f"resolved @username: {raw}")
        return entity
    except Exception as e:
        logger.warning(f"@username failed: {e}")

    try:
        from telethon.tl.functions.contacts import ResolveUsernameRequest
        result = await client(ResolveUsernameRequest(raw))
        if result and result.users:
            logger.info(f"resolved via ResolveUsername: {raw}")
            return result.users[0]
    except Exception as e:
        logger.warning(f"ResolveUsername failed: {e}")

    return None


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
        if data == "menu_main":
            text = await main_menu_text(uid)
            await event.edit(text, buttons=main_menu_kb())

        elif data == "menu_settings":
            await event.edit(
                "تنظیمات\n━━━━━━━━━━━━━━━━━━\n\n"
                "این بخش در آینده تکمیل خواهد شد.",
                buttons=back_kb()
            )

        elif data == "menu_accounts":
            await show_accounts_menu(event, uid)

        elif data == "acc_add":
            user_states[uid] = "WAIT_PHONE"
            user_temp[uid] = {}
            await event.edit(
                "افزودن اکانت جدید\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "مرحله 1 از 5\n\n"
                "لطفاً شماره موبایل اکانت را با کد کشور ارسال فرمایید:\n"
                "مثال: `+989123456789`\n\n"
                "برای انصراف دستور /cancel را ارسال کنید.",
                buttons=back_kb(b"menu_accounts")
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
                "قالب جدید\n━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً عنوان قالب را ارسال فرمایید:\n"
                "مثال: چهره‌های زیبا\n\n"
                "برای انصراف دستور /cancel را ارسال کنید.",
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
                "دریافت پروفایل\n━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً شناسه کاربر مورد نظر را ارسال فرمایید:\n\n"
                "• شناسه عددی: `123456789`\n"
                "• نام کاربری: `@username`\n"
                "• لینک: `https://t.me/username`\n\n"
                "عکس‌ها به صورت آلبوم Photo ارسال می‌شن.",
                buttons=back_kb()
            )

        elif data == "menu_crop_face":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("ابتدا یک اکانت انتخاب فرمایید.", buttons=back_kb())
                return
            user_states[uid] = "WAIT_CROP_TARGET"
            await event.edit(
                "جداسازی چهره\n━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً شناسه کاربر مورد نظر را ارسال فرمایید:\n\n"
                "• شناسه عددی: `123456789`\n"
                "• نام کاربری: `@username`\n"
                "• لینک: `https://t.me/username`\n\n"
                "فقط چهره‌های انسانی کراپ و ارسال می‌شن.",
                buttons=back_kb()
            )

        elif data == "menu_multi":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("ابتدا یک اکانت انتخاب فرمایید.", buttons=back_kb())
                return
            user_states[uid] = "WAIT_MULTI_IDS"
            await event.edit(
                "چند شناسه همزمان\n━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً شناسه‌ها را هر کدام در یک خط ارسال فرمایید:\n\n"
                "`123456789`\n"
                "`@username1`\n"
                "`@username2`\n\n"
                "برای انصراف دستور /cancel را ارسال کنید.",
                buttons=back_kb()
            )

        elif data == "menu_group":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("ابتدا یک اکانت انتخاب فرمایید.", buttons=back_kb())
                return
            user_states[uid] = "WAIT_GROUP_LINK"
            await event.edit(
                "گروه یا کانال\n━━━━━━━━━━━━━━━━━━\n\n"
                "لطفاً لینک گروه یا کانال را ارسال فرمایید:\n\n"
                "• `https://t.me/groupname`\n"
                "• `https://t.me/+AbCdEf123`\n"
                "• `@groupname`\n\n"
                "برای انصراف دستور /cancel را ارسال کنید.",
                buttons=back_kb()
            )

        elif data == "menu_channel":
            s = await db_get_settings(uid)
            current = s[3] if s and s[3] else None
            title = s[4] if s and s[4] else "متصل نشده"
            text = (
                "اتصال به کانال\n━━━━━━━━━━━━━━━━━━\n\n"
                f"کانال فعلی: {title}\n\n"
                "پروفایل‌ها همزمان به این کانال نیز ارسال می‌شوند.\n\n"
                "جهت اتصال:\n"
                "1. ربات را به کانال اضافه کنید\n"
                "2. یک پیام از کانال را فوروارد کنید\n"
                "3. یا نام کاربری کانال را ارسال کنید: `@channel`\n\n"
                "برای انصراف دستور /cancel را ارسال کنید."
            )
            kb = []
            if current:
                kb.append([btn("قطع اتصال 🔌", b"channel_disconnect", "danger")])
            kb.append([btn("بازگشت 🔙", b"menu_main", "primary")])
            user_states[uid] = "WAIT_CHANNEL_LINK"
            await event.edit(text, buttons=kb)

        elif data == "channel_disconnect":
            await db_update_setting(uid, channel_id=None, channel_title=None)
            text = await main_menu_text(uid)
            await event.edit(text, buttons=main_menu_kb())

    except Exception as e:
        logger.exception(f"callback error: {e}")


# ==================== نمایش منو اکانت‌ها ====================
async def show_accounts_menu(event, uid):
    accounts = await db_get_accounts(uid)

    if not accounts:
        text = "هنوز اکانتی اضافه نشده است.\n\nاز دکمه زیر یک اکانت اضافه فرمایید."
    else:
        lines = ["اکانت‌های فعال:\n"]
        for i, a in enumerate(accounts, 1):
            lines.append(
                f"{i}. {a[6]}\n"
                f"   شماره: `{a[2]}`\n"
                f"   نام کاربری: `@{a[7] or 'ندارد'}`\n"
                f"   شناسه: `{a[0]}`"
            )
        text = "\n".join(lines)

    kb = []
    for a in accounts:
        kb.append([btn(f"{a[6]} | {a[2][-4:]} 📱", f"acc_view_{a[0]}".encode(), "primary")])
    kb.append([btn("افزودن اکانت جدید ➕", b"acc_add", "success")])
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
        "اطلاعات اکانت\n━━━━━━━━━━━━━━━━━━\n\n"
        f"نام: {a[6]}\n"
        f"نام کاربری: `@{a[7] or 'ندارد'}`\n"
        f"شماره: `{a[2]}`\n"
        f"API ID: `{a[3]}`\n"
        f"تاریخ افزودن: {a[8][:10]}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{'این اکانت در حال حاضر فعال است.' if is_current else 'این اکانت فعال نیست.'}"
    )

    kb = []
    if not is_current:
        kb.append([btn("انتخاب به عنوان فعال ✅", f"acc_use_{acc_id}".encode(), "success")])
    kb.append([btn("حذف اکانت 🗑", f"acc_del_{acc_id}".encode(), "danger")])
    kb.append([btn("بازگشت 🔙", b"menu_accounts", "primary")])

    await event.edit(text, buttons=kb)


async def show_templates_menu(event, uid):
    templates = await db_get_templates(uid)

    if not templates:
        text = "هنوز قالبی ساخته نشده است."
    else:
        lines = ["قالب‌های موجود:\n"]
        for i, t in enumerate(templates, 1):
            preview = t[3][:80].replace('\n', ' ')
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
        "اطلاعات قالب\n━━━━━━━━━━━━━━━━━━\n\n"
        f"عنوان: {t[2]}\n\n"
        f"متن:\n`{t[3]}`\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{'این قالب در حال حاضر فعال است.' if is_current else 'این قالب فعال نیست.'}"
    )

    kb = []
    if not is_current:
        kb.append([btn("انتخاب به عنوان فعال ✅", f"tmpl_use_{tid}".encode(), "success")])
    kb.append([btn("حذف قالب 🗑", f"tmpl_del_{tid}".encode(), "danger")])
    kb.append([btn("بازگشت 🔙", b"menu_templates", "primary")])

    await event.edit(text, buttons=kb)


# ==================== هندلر پیام‌ها ====================
@bot.on(events.NewMessage)
async def handle_message(event):
    if not is_owner(event.sender_id):
        return
    if not event.text:
        return
    if event.text.startswith('/'):
        return

    uid = event.sender_id
    state = user_states.get(uid)
    if not state:
        return

    text = event.text.strip()

    try:
        if state == "WAIT_PHONE":
            if not re.match(r'^\+?[0-9]{10,15}$', text):
                await event.respond("شماره نامعتبر. /cancel")
                return
            user_temp[uid]['phone'] = text
            user_states[uid] = "WAIT_API_ID"
            await event.respond(f"مرحله 2 از 5\n\nشماره: `{text}`\n\nلطفاً API ID:")

        elif state == "WAIT_API_ID":
            if not text.isdigit():
                await event.respond("API ID باید عدد باشد.")
                return
            user_temp[uid]['api_id'] = int(text)
            user_states[uid] = "WAIT_API_HASH"
            await event.respond("مرحله 3 از 5\n\nلطفاً API Hash:")

        elif state == "WAIT_API_HASH":
            if len(text) < 20:
                await event.respond("API Hash نامعتبر.")
                return
            user_temp[uid]['api_hash'] = text
            user_states[uid] = "WAIT_CODE"
            phone = user_temp[uid]['phone']
            api_id = user_temp[uid]['api_id']
            api_hash = user_temp[uid]['api_hash']
            msg = await event.respond("در حال ارسال کد تأیید...")
            try:
                client = TelegramClient(StringSession(), api_id, api_hash)
                await client.connect()
                sent = await client.send_code_request(phone)
                user_temp[uid]['client'] = client
                user_temp[uid]['phone_code_hash'] = sent.phone_code_hash
                await msg.edit(
                    f"مرحله 4 از 5\n\nکد تأیید به `{phone}` ارسال شد.\n\n"
                    "کد رو بفرست: `1.2.3.4.5` یا `12345`"
                )
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
                await event.respond("مرحله 5 از 5\n\nاکانت دو مرحله‌ای. رمزت رو بفرست:")
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
            await event.respond(
                "متن قالب رو بفرست:\n\n"
                "متغیرها:\n`{name}` `{username}` `{id}` `{first_name}` `{last_name}`"
            )

        elif state == "WAIT_TEMPLATE_TEXT":
            title = user_temp[uid].get('tmpl_title', 'بدون عنوان')
            tid = await db_add_template(uid, title, text)
            await event.respond(
                f"قالب «{title}» ذخیره شد. (شناسه: `{tid}`)",
                buttons=back_kb(b"menu_templates")
            )
            user_states.pop(uid, None)
            user_temp.pop(uid, None)

        elif state == "WAIT_TARGET_INPUT":
            user_states.pop(uid, None)
            asyncio.create_task(process_single_target(event, uid, text, crop_mode=False))

        elif state == "WAIT_CROP_TARGET":
            user_states.pop(uid, None)
            asyncio.create_task(process_single_target(event, uid, text, crop_mode=True))

        elif state == "WAIT_MULTI_IDS":
            user_states.pop(uid, None)
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            asyncio.create_task(process_multi_targets(event, uid, lines))

        elif state == "WAIT_GROUP_LINK":
            user_states.pop(uid, None)
            asyncio.create_task(process_group(event, uid, text))

        elif state == "WAIT_CHANNEL_LINK":
            user_states.pop(uid, None)
            if text.startswith('@') or text.startswith('https://t.me/'):
                name = text.split('/')[-1]
                if name.startswith('@'):
                    name = name[1:]
                s = await db_get_settings(uid)
                a = await db_get_account(s[1]) if s and s[1] else None
                if not a:
                    await event.respond("اول اکانت انتخاب کن.")
                    return
                client = None
                try:
                    client = TelegramClient(StringSession(a[5]), a[3], a[4])
                    await client.connect()
                    entity = await client.get_entity(name)
                    await db_update_setting(uid, channel_id=str(entity.id),
                                            channel_title=getattr(entity, 'title', name))
                    await event.respond(
                        f"کانال متصل شد.\nنام: {getattr(entity, 'title', name)}\nشناسه: `{entity.id}`",
                        buttons=back_kb()
                    )
                except Exception as e:
                    await event.respond(f"خطا: {str(e)[:200]}")
                finally:
                    if client:
                        try:
                            await client.disconnect()
                        except:
                            pass
            else:
                await event.respond("ورودی نامعتبر.")

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
            "اکانت اضافه شد\n━━━━━━━━━━━━━━━━━━\n\n"
            f"نام: {acc_name}\n"
            f"یوزرنیم: `@{username or 'ندارد'}`\n"
            f"شماره: `{phone}`\n"
            f"شناسه: `{acc_id}`",
            buttons=[[btn("مدیریت اکانت‌ها 📁", b"menu_accounts", "success")]]
        )
    except Exception as e:
        await event.respond(f"خطا: {str(e)[:200]}")
    user_states.pop(uid, None)
    user_temp.pop(uid, None)


# ==================== پردازش پروفایل ====================
async def process_single_target(event, uid, target_raw, crop_mode=False):
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
            await event.respond(
                f"❌ کاربر `{target_raw}` پیدا نشد.\n\n"
                "دلایل ممکن:\n"
                "• اکانت وجود نداره\n"
                "• یوزرنیم اشتباهه\n"
                "• بلاک هستی\n"
                "• Privacy محدود داره"
            )
            return

        if crop_mode:
            await process_crop_faces(event, client, uid, target)
        else:
            await process_user_profile(event, client, uid, target, s[2], s[3])
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


async def process_user_profile(event, client, owner_id, target, template_id, channel_id):
    """گرفتن پروفایل‌ها و ارسال آلبومی Photo"""
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
        await event.respond(f"خطا در دریافت پروفایل‌ها: {str(e)[:150]}")
        return

    total = len(photo_list)
    if total == 0:
        await event.respond(f"کاربر {full_name} پروفایلی نداره.")
        return

    status = await event.respond(
        f"در حال پردازش...\nکاربر: {full_name}\nتعداد: {total}"
    )

    photos_bytes = []
    for i, photo in enumerate(photo_list):
        try:
            buf = BytesIO()
            await client.download_media(photo, buf)
            buf.seek(0)
            data = buf.getvalue()
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                ok, jpg_buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ok:
                    photos_bytes.append(jpg_buf.tobytes())
            if (i + 1) % 5 == 0:
                try:
                    await status.edit(f"در حال دانلود... {i+1}/{total}")
                except:
                    pass
        except:
            continue

    if not photos_bytes:
        await status.edit("عکسی دانلود نشد.")
        return

    footer = ""
    if template_id:
        t = await db_get_template(template_id)
        if t:
            footer = t[3]
            footer = footer.replace("{name}", full_name).replace("{username}", username or "")
            footer = footer.replace("{first_name}", first_name).replace("{last_name}", last_name)
            footer = footer.replace("{id}", user_id_str)

    await status.edit(f"در حال ارسال {len(photos_bytes)} عکس...")
    sent_owner = await send_photo_album(bot, owner_id, photos_bytes, caption=footer or None)

    sent_channel = 0
    if channel_id:
        try:
            sent_channel = await send_photo_album(bot, int(channel_id), photos_bytes, caption=footer or None)
        except:
            pass

    final_text = (
        f"✅ تکمیل شد\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"نام: {full_name}\n"
        f"یوزرنیم: `@{username or 'ندارد'}`\n"
        f"شناسه: `{user_id_str}`\n\n"
        f"تعداد کل: {total}\n"
        f"ارسال شده: {sent_owner}"
    )
    if channel_id:
        final_text += f"\nکانال: {sent_channel}"

    try:
        await status.edit(final_text, buttons=back_kb())
    except:
        await event.respond(final_text, buttons=back_kb())


async def process_crop_faces(event, client, owner_id, target):
    """گرفتن پروفایل‌ها، تشخیص و کراپ چهره‌ها، ارسال به صورت Photo"""
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
        await event.respond(f"خطا: {str(e)[:150]}")
        return

    total = len(photo_list)
    if total == 0:
        await event.respond(f"کاربر {full_name} پروفایلی نداره.")
        return

    status = await event.respond(
        f"🎭 در حال جداسازی چهره...\n\n"
        f"کاربر: {full_name}\n"
        f"تعداد عکس: {total}"
    )

    all_faces = []
    for i, photo in enumerate(photo_list):
        try:
            buf = BytesIO()
            await client.download_media(photo, buf)
            buf.seek(0)
            arr = np.frombuffer(buf.getvalue(), dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            faces = crop_faces_from_image(img)
            all_faces.extend(faces)
            if (i + 1) % 3 == 0:
                try:
                    await status.edit(
                        f"🎭 در حال جداسازی...\n\n"
                        f"پیشرفت: {i+1}/{total}\n"
                        f"چهره‌های پیدا شده: {len(all_faces)}"
                    )
                except:
                    pass
        except:
            continue

    if not all_faces:
        await status.edit(
            f"❌ هیچ چهره‌ای پیدا نشد.\n\n"
            f"• چهره‌ها واضح نیستن\n"
            f"• زاویه نامناسبه\n"
            f"• کیفیت پایینه"
        )
        return

    unique_faces = []
    seen = set()
    for face in all_faces:
        h = hash(face[:200])
        if h not in seen:
            seen.add(h)
            unique_faces.append(face)

    await status.edit(
        f"🎭 در حال ارسال...\n\n"
        f"چهره‌های منحصربفرد: {len(unique_faces)}"
    )

    footer = f"🎭 چهره‌های {full_name}"
    if username:
        footer += f" | @{username}"

    sent = await send_photo_album(bot, owner_id, unique_faces, caption=footer)

    await status.edit(
        f"✅ تکمیل شد\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"نام: {full_name}\n"
        f"یوزرنیم: `@{username or 'ندارد'}`\n"
        f"شناسه: `{user_id_str}`\n\n"
        f"کل عکس‌ها: {total}\n"
        f"چهره‌های ارسال شده: {sent}",
        buttons=back_kb()
    )


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

        total = len(targets)
        success = 0
        failed = 0
        status = await event.respond(f"در حال پردازش {total} شناسه...")

        for idx, raw in enumerate(targets, 1):
            try:
                await status.edit(f"پردازش {idx}/{total}\nموفق: {success} | ناموفق: {failed}")
            except:
                pass
            target = await resolve_entity(client, raw)
            if not target:
                failed += 1
                continue
            try:
                await process_silent(client, uid, target, s[2], s[3])
                success += 1
            except:
                failed += 1
            await asyncio.sleep(1)

        await status.edit(
            f"✅ تکمیل شد\nکل: {total}\nموفق: {success}\nناموفق: {failed}",
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


async def process_silent(client, owner_id, target, template_id, channel_id):
    target_id = target.id
    first_name = target.first_name or ""
    last_name = target.last_name or ""
    full_name = (first_name + " " + last_name).strip() or "بدون نام"
    username = target.username or ""
    user_id_str = str(target.id)

    try:
        photos = await client(GetUserPhotosRequest(user_id=target_id, offset=0, max_id=0, limit=200))
        photo_list = photos.photos if hasattr(photos, 'photos') else []
    except:
        return
    if not photo_list:
        return

    footer = ""
    if template_id:
        t = await db_get_template(template_id)
        if t:
            footer = t[3]
            footer = footer.replace("{name}", full_name).replace("{username}", username or "")
            footer = footer.replace("{first_name}", first_name).replace("{last_name}", last_name)
            footer = footer.replace("{id}", user_id_str)

    photos_bytes = []
    for photo in photo_list:
        try:
            buf = BytesIO()
            await client.download_media(photo, buf)
            buf.seek(0)
            arr = np.frombuffer(buf.getvalue(), dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                ok, jpg_buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ok:
                    photos_bytes.append(jpg_buf.tobytes())
        except:
            continue

    if photos_bytes:
        try:
            await send_photo_album(bot, owner_id, photos_bytes, caption=footer or None)
        except Exception as e:
            logger.warning(f"silent album: {e}")

        if channel_id:
            try:
                await send_photo_album(bot, int(channel_id), photos_bytes, caption=footer or None)
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
        status = await event.respond(f"گروه: {group_title}\nدر حال دریافت اعضا...")

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
                if len(result.users) < 100 or len(participants) >= 500:
                    break
                await asyncio.sleep(0.5)
            except:
                break

        total = len(participants)
        if total == 0:
            await status.edit("عضوی پیدا نشد.")
            return

        await status.edit(f"گروه: {group_title}\nتعداد: {total}\nدر حال پردازش...")

        success = 0
        for idx, user in enumerate(participants, 1):
            if user.bot or user.deleted:
                continue
            try:
                await status.edit(f"پردازش {idx}/{total}\nموفق: {success}")
            except:
                pass
            try:
                await process_silent(client, uid, user, s[2], s[3])
                success += 1
            except:
                pass
            await asyncio.sleep(0.5)

        await status.edit(
            f"✅ تکمیل\nگروه: {group_title}\nکل: {total}\nپردازش: {success}",
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

    max_retries = 10
    connected = False

    for attempt in range(max_retries):
        try:
            await bot.start(bot_token=BOT_TOKEN)
            me = await bot.get_me()
            logger.info(f"ربات @{me.username} راه‌اندازی شد")
            connected = True
            break
        except FloodWaitError as e:
            wait_time = e.seconds + 30
            logger.warning(f"FloodWait: {wait_time}s (تلاش {attempt+1}/{max_retries})")
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
