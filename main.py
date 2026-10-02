"""
ربات خصوصی مدیریت پروفایل
نسخه تک‌فایل با Telethon
دکمه‌ها با استایل رنگی (primary/success/danger)
AI فیلتر چهره با حساسیت بالا
ارسال آلبومی (Media Group)
"""

import asyncio
import logging
import os
import re
import sqlite3
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
    ImportChatInviteRequest, CheckChatInviteRequest
)
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.types import ChannelParticipantsSearch

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==================== تنظیمات ====================
BOT_TOKEN = os.environ.get(
    "TOKEN",
    "8843126535:AAEtN5avPoX6AnYjVQAWuAUxu9_3nyr2ybg"
)
MY_USER_ID = int(os.environ.get("MY_USER_ID", "7803165903"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

BOT_API_ID = int(os.environ.get("BOT_API_ID", "2040"))
BOT_API_HASH = os.environ.get("BOT_API_HASH", "b18441a1ff607e10a989891a5462e627")

ALBUM_CHUNK_SIZE = 10  # حداکثر عکس در هر آلبوم (تلگرام = 10)


# ==================== State ====================
user_states = {}
user_temp = {}


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
    return user_id == MY_USER_ID


# ==================== AI فیلتر چهره (حساسیت بالا) ====================
_cascade_frontal = None
_cascade_profile = None
_cascade_eye = None


def _init_cascades():
    global _cascade_frontal, _cascade_profile, _cascade_eye
    if _cascade_frontal is None:
        _cascade_frontal = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        _cascade_profile = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_profileface.xml'
        )
        _cascade_eye = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_eye.xml'
        )


def is_human_face(img_bgr):
    """تشخیص چهره انسانی با ۴ الگوریتم و حساسیت بالا"""
    try:
        _init_cascades()
        h, w = img_bgr.shape[:2]
        if w > 1000:
            scale = 1000 / w
            img_bgr = cv2.resize(img_bgr, (1000, int(h * scale)))

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        min_size = max(25, int(min(gray.shape) * 0.05))

        # ۱. چهره روبرو
        faces = _cascade_frontal.detectMultiScale(
            gray, scaleFactor=1.03, minNeighbors=3,
            minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            return True

        # ۲. چهره نیم‌رخ
        faces = _cascade_profile.detectMultiScale(
            gray, scaleFactor=1.03, minNeighbors=3,
            minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            return True

        # ۳. چهره نیم‌رخ آینه‌ای
        flipped = cv2.flip(gray, 1)
        faces = _cascade_profile.detectMultiScale(
            flipped, scaleFactor=1.03, minNeighbors=3,
            minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            return True

        # ۴. اگه دو تا چشم پیدا شد
        eyes = _cascade_eye.detectMultiScale(
            gray, scaleFactor=1.03, minNeighbors=3, minSize=(15, 15)
        )
        if len(eyes) >= 2:
            return True

        return False
    except Exception as e:
        print(f"face filter error: {e}")
        return False


# ==================== دکمه‌های رنگی ====================
def btn(text, data, style=None):
    """ساخت دکمه با استایل رنگی"""
    try:
        if style in ("primary", "success", "danger"):
            return Button.inline(text, data, style=style)
        return Button.inline(text, data)
    except (TypeError, ValueError) as e:
        logger.debug(f"style failed: {e}")
        return Button.inline(text, data)


# ==================== کیبوردها ====================
def main_menu_kb():
    return [
        [
            btn("مدیریت اکانت‌ها 📁", b"menu_accounts", "primary"),
            btn("مدیریت قالب‌ها 📝", b"menu_templates", "primary")
        ],
        [btn("دریافت پروفایل 🔍", b"menu_get_profile", "success")],
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


# ==================== متن‌های رسمی ====================
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


# ==================== ربات اصلی ====================
bot = TelegramClient(StringSession(), BOT_API_ID, BOT_API_HASH)


# ==================== توابع کمکی برای ارسال آلبوم ====================
async def send_album(client, chat_id, files_data, caption=None):
    """
    ارسال لیست عکس‌ها به صورت آلبوم (Media Group)
    files_data: لیست بایت‌های عکس
    caption: کپشن روی عکس اول
    """
    if not files_data:
        return 0

    sent_count = 0

    # تکه‌تکه کردن به آلبوم‌های حداکثر 10 تایی
    for i in range(0, len(files_data), ALBUM_CHUNK_SIZE):
        chunk = files_data[i:i + ALBUM_CHUNK_SIZE]

        try:
            # آپلود همه فایل‌های چانک
            uploaded = []
            for idx, data in enumerate(chunk):
                try:
                    f = await client.upload_file(BytesIO(data))
                    uploaded.append(f)
                except Exception as e:
                    logger.warning(f"upload error: {e}")

            if not uploaded:
                continue

            # تعیین کپشن: فقط روی عکس اول اولین چانک
            cap = caption if (i == 0) else None

            # ارسال آلبوم
            try:
                await client.send_file(
                    chat_id,
                    uploaded,
                    caption=cap,
                    force_document=False
                )
                sent_count += len(uploaded)
            except Exception as e:
                logger.warning(f"album send error: {e}")
                # fallback: ارسال تک تک
                for idx, f in enumerate(uploaded):
                    try:
                        c = cap if idx == 0 else None
                        await client.send_file(chat_id, f, caption=c, force_document=False)
                        sent_count += 1
                    except Exception as e2:
                        logger.warning(f"single send error: {e2}")

            await asyncio.sleep(1)

        except Exception as e:
            logger.exception(f"send_album chunk error: {e}")

    return sent_count


# ==================== هندلر /start ====================
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

            a = await db_get_account(s[1])
            t_text = "انتخاب نشده"
            if s[2]:
                t = await db_get_template(s[2])
                if t:
                    t_text = t[2]
            ch_text = s[4] if s[3] else "متصل نشده"

            user_states[uid] = "WAIT_TARGET_INPUT"
            await event.edit(
                "دریافت پروفایل\n━━━━━━━━━━━━━━━━━━\n\n"
                f"اکانت: {a[6]}\n"
                f"قالب: {t_text}\n"
                f"کانال: {ch_text}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "لطفاً شناسه کاربر مورد نظر را ارسال فرمایید:\n\n"
                "• شناسه عددی: `123456789`\n"
                "• نام کاربری: `@username`\n\n"
                "تنها چهره‌های انسانی فیلتر و ارسال خواهند شد.",
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


# ==================== نمایش منو قالب‌ها ====================
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


# ==================== هندلر پیام‌های متنی ====================
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
                await event.respond("شماره وارد شده نامعتبر است. مجدداً ارسال فرمایید یا /cancel.")
                return
            user_temp[uid]['phone'] = text
            user_states[uid] = "WAIT_API_ID"
            await event.respond(
                f"مرحله 2 از 5\n\nشماره: `{text}`\n\n"
                "لطفاً API ID را ارسال فرمایید:\n"
                "(عدد است، از my.telegram.org دریافت کنید)"
            )

        elif state == "WAIT_API_ID":
            if not text.isdigit():
                await event.respond("API ID باید عدد باشد.")
                return
            user_temp[uid]['api_id'] = int(text)
            user_states[uid] = "WAIT_API_HASH"
            await event.respond("مرحله 3 از 5\n\nلطفاً API Hash را ارسال فرمایید:")

        elif state == "WAIT_API_HASH":
            if len(text) < 20:
                await event.respond("API Hash نامعتبر است.")
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
                    "لطفاً کد را به این صورت ارسال فرمایید: `1.2.3.4.5` یا `12345`"
                )
            except PhoneNumberInvalidError:
                await msg.edit("شماره نامعتبر است. /cancel را ارسال کنید.")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)
            except FloodWaitError as e:
                await msg.edit(f"محدودیت تلگرام: {e.seconds} ثانیه صبر کنید.")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)
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
                await event.respond(
                    "مرحله 5 از 5\n\nاین اکانت دارای رمز دو مرحله‌ای است.\n"
                    "لطفاً رمز عبور خود را ارسال فرمایید:"
                )
            except PhoneCodeInvalidError:
                await event.respond("کد وارد شده اشتباه است.")
            except PhoneCodeExpiredError:
                await event.respond("کد منقضی شده است. /cancel را ارسال کنید.")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)
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
                "لطفاً متن قالب را ارسال فرمایید:\n\n"
                "متغیرهای قابل استفاده:\n"
                "`{name}` — نام کامل\n"
                "`{username}` — نام کاربری\n"
                "`{id}` — شناسه\n"
                "`{first_name}` — نام کوچک\n"
                "`{last_name}` — نام خانوادگی"
            )

        elif state == "WAIT_TEMPLATE_TEXT":
            title = user_temp[uid].get('tmpl_title', 'بدون عنوان')
            tid = await db_add_template(MY_USER_ID, title, text)
            await event.respond(
                f"قالب «{title}» با موفقیت ذخیره شد. (شناسه: `{tid}`)",
                buttons=back_kb(b"menu_templates")
            )
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

        elif state == "WAIT_CHANNEL_LINK":
            user_states.pop(uid, None)
            if text.startswith('@') or text.startswith('https://t.me/'):
                name = text.split('/')[-1]
                if name.startswith('@'):
                    name = name[1:]

                s = await db_get_settings(uid)
                a = await db_get_account(s[1]) if s and s[1] else None
                if not a:
                    await event.respond("ابتدا یک اکانت انتخاب فرمایید.")
                    return

                client = None
                try:
                    client = TelegramClient(StringSession(a[5]), a[3], a[4])
                    await client.connect()
                    entity = await client.get_entity(name)
                    await db_update_setting(uid, channel_id=str(entity.id),
                                            channel_title=getattr(entity, 'title', name))
                    await event.respond(
                        f"کانال با موفقیت متصل شد.\n\n"
                        f"نام: {getattr(entity, 'title', name)}\n"
                        f"شناسه: `{entity.id}`",
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
                await event.respond("ورودی نامعتبر است.")

    except Exception as e:
        logger.exception(f"message handler error: {e}")


# ==================== نهایی کردن اکانت ====================
async def finalize_account(event, uid, client):
    try:
        me = await client.get_me()
        session_str = client.session.save()
        phone = user_temp[uid]['phone']
        api_id = user_temp[uid]['api_id']
        api_hash = user_temp[uid]['api_hash']
        acc_name = me.first_name or "بدون نام"
        username = me.username or ""

        acc_id = await db_add_account(MY_USER_ID, phone, api_id, api_hash,
                                      session_str, acc_name, username)
        await client.disconnect()

        await event.respond(
            "اکانت با موفقیت اضافه شد\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"نام: {acc_name}\n"
            f"نام کاربری: `@{username or 'ندارد'}`\n"
            f"شماره: `{phone}`\n"
            f"شناسه داخلی: `{acc_id}`",
            buttons=[[btn("مدیریت اکانت‌ها 📁", b"menu_accounts", "success")]]
        )
    except Exception as e:
        await event.respond(f"خطا: {str(e)[:200]}")

    user_states.pop(uid, None)
    user_temp.pop(uid, None)


# ==================== Resolve Entity ====================
async def resolve_entity(client, raw):
    raw = raw.strip()
    try:
        if raw.startswith('@'):
            raw = raw[1:]
        if raw.isdigit():
            return await client.get_entity(int(raw))
        return await client.get_entity(raw)
    except Exception as e:
        logger.warning(f"resolve error: {e}")
        return None


# ==================== پردازش یک پروفایل ====================
async def process_single_target(event, uid, target_raw):
    client = None
    try:
        s = await db_get_settings(uid)
        if not s or not s[1]:
            await event.respond("اکانت فعالی وجود ندارد.")
            return

        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a

        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await event.respond("سشن منقضی شده است.")
            return

        target = await resolve_entity(client, target_raw)
        if not target:
            await event.respond(f"کاربر {target_raw} یافت نشد.")
            return

        await process_user_profile(event, client, uid, target, s[2], s[3])
    except Exception as e:
        logger.exception(f"process_single error: {e}")
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
        await event.respond(f"کاربر {full_name} پروفایلی ندارد.")
        return

    status_msg = await event.respond(
        f"در حال پردازش...\n\n"
        f"کاربر: {full_name}\n"
        f"تعداد کل: {total}"
    )

    # جمع‌آوری عکس‌های تأییدشده
    human_faces = []
    for i, photo in enumerate(photo_list):
        try:
            buf = BytesIO()
            await client.download_media(photo, buf)
            buf.seek(0)
            data = buf.getvalue()
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if is_human_face(img):
                human_faces.append(data)
            if (i + 1) % 5 == 0:
                try:
                    await status_msg.edit(
                        f"در حال پردازش...\n\n"
                        f"پیشرفت: {i+1}/{total}\n"
                        f"چهره‌های شناسایی شده: {len(human_faces)}"
                    )
                except:
                    pass
        except:
            continue

    # اگه هیچ چهره‌ای پیدا نشد، همه رو بفرست
    if not human_faces:
        logger.info(f"هیچ چهره‌ای پیدا نشد — ارسال همه عکس‌های {full_name}")
        for photo in photo_list:
            try:
                buf = BytesIO()
                await client.download_media(photo, buf)
                buf.seek(0)
                human_faces.append(buf.getvalue())
            except:
                continue

    # متن قالب
    footer = ""
    if template_id:
        t = await db_get_template(template_id)
        if t:
            footer = t[3]
            footer = footer.replace("{name}", full_name).replace("{username}", username or "")
            footer = footer.replace("{first_name}", first_name).replace("{last_name}", last_name)
            footer = footer.replace("{id}", user_id_str)

    # ═══════ ارسال آلبومی به خودت ═══════
    sent_owner = 0
    if human_faces:
        try:
            await status_msg.edit(
                f"در حال ارسال آلبوم...\n\n"
                f"کاربر: {full_name}\n"
                f"تعداد: {len(human_faces)}"
            )
        except:
            pass

        sent_owner = await send_album(bot, owner_id, human_faces, caption=footer or None)

    # ═══════ ارسال آلبومی به کانال ═══════
    sent_channel = 0
    if channel_id and human_faces:
        try:
            sent_channel = await send_album(bot, int(channel_id), human_faces, caption=footer or None)
        except Exception as e:
            logger.warning(f"channel album error: {e}")

    final_text = (
        f"عملیات با موفقیت تکمیل شد\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"نام: {full_name}\n"
        f"نام کاربری: `@{username or 'ندارد'}`\n"
        f"شناسه: `{user_id_str}`\n\n"
        f"تعداد کل پروفایل‌ها: {total}\n"
        f"چهره‌های ارسال شده: {sent_owner}\n"
        f"فیلتر شده: {total - len(human_faces)}"
    )
    if channel_id:
        final_text += f"\nارسال شده به کانال: {sent_channel}"

    try:
        await status_msg.edit(final_text, buttons=back_kb())
    except:
        await event.respond(final_text, buttons=back_kb())


# ==================== پردازش چند شناسه ====================
async def process_multi_targets(event, uid, targets):
    client = None
    try:
        s = await db_get_settings(uid)
        if not s or not s[1]:
            await event.respond("اکانت فعالی وجود ندارد.")
            return

        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a

        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await event.respond("سشن منقضی شده است.")
            return

        total = len(targets)
        success = 0
        failed = 0
        status = await event.respond(f"در حال پردازش {total} شناسه...")

        for idx, raw in enumerate(targets, 1):
            try:
                await status.edit(
                    f"در حال پردازش...\n\n"
                    f"شناسه فعلی: {idx}/{total}\n"
                    f"موفق: {success} | ناموفق: {failed}"
                )
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
            f"عملیات تکمیل شد\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"تعداد کل: {total}\n"
            f"موفق: {success}\n"
            f"ناموفق: {failed}",
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

    # جمع‌آوری عکس‌های تأییدشده
    approved = []
    for photo in photo_list:
        try:
            buf = BytesIO()
            await client.download_media(photo, buf)
            buf.seek(0)
            data = buf.getvalue()
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if is_human_face(img):
                approved.append(data)
        except:
            continue

    # اگه هیچی تأیید نشد، همه رو بفرست
    if not approved:
        logger.info(f"هیچ چهره‌ای پیدا نشد — ارسال همه عکس‌های {full_name}")
        for photo in photo_list:
            try:
                buf = BytesIO()
                await client.download_media(photo, buf)
                buf.seek(0)
                approved.append(buf.getvalue())
            except:
                continue

    if not approved:
        return

    # ارسال آلبومی
    try:
        await send_album(bot, owner_id, approved, caption=footer or None)
    except Exception as e:
        logger.warning(f"silent album owner error: {e}")

    if channel_id:
        try:
            await send_album(bot, int(channel_id), approved, caption=footer or None)
        except Exception as e:
            logger.warning(f"silent album channel error: {e}")


# ==================== پردازش گروه ====================
async def process_group(event, uid, link):
    client = None
    try:
        s = await db_get_settings(uid)
        if not s or not s[1]:
            await event.respond("اکانت فعالی وجود ندارد.")
            return

        a = await db_get_account(s[1])
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = a

        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await event.respond("سشن منقضی شده است.")
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
            await event.respond(f"گروه یافت نشد.\n{str(e)[:200]}")
            return

        group_title = getattr(entity, 'title', 'بدون نام')
        status = await event.respond(
            f"گروه: {group_title}\n"
            f"در حال دریافت اعضا..."
        )

        participants = []
        offset = 0
        limit = 100
        while True:
            try:
                result = await client(GetParticipantsRequest(
                    channel=entity, filter=ChannelParticipantsSearch(''),
                    offset=offset, limit=limit, hash=0
                ))
                if not result.users:
                    break
                participants.extend(result.users)
                offset += len(result.users)
                if len(result.users) < limit or len(participants) >= 500:
                    break
                await asyncio.sleep(0.5)
            except:
                break

        total = len(participants)
        if total == 0:
            await status.edit("عضوی یافت نشد.")
            return

        await status.edit(
            f"گروه: {group_title}\n"
            f"تعداد اعضا: {total}\n"
            f"در حال پردازش..."
        )

        success = 0
        for idx, user in enumerate(participants, 1):
            if user.bot or user.deleted:
                continue
            try:
                await status.edit(
                    f"در حال پردازش...\n\n"
                    f"پیشرفت: {idx}/{total}\n"
                    f"موفق: {success}"
                )
            except:
                pass
            try:
                await process_silent(client, uid, user, s[2], s[3])
                success += 1
            except:
                pass
            await asyncio.sleep(0.5)

        await status.edit(
            f"عملیات تکمیل شد\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"گروه: {group_title}\n"
            f"تعداد کل: {total}\n"
            f"پردازش شده: {success}",
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


# ==================== Main (با مدیریت FloodWait) ====================
async def main():
    await init_db()

    max_retries = 10
    connected = False

    for attempt in range(max_retries):
        try:
            await bot.start(bot_token=BOT_TOKEN)
            me = await bot.get_me()
            logger.info(f"ربات @{me.username} با موفقیت راه‌اندازی شد")
            connected = True
            break
        except FloodWaitError as e:
            wait_time = e.seconds + 30
            logger.warning(
                f"FloodWait: باید {e.seconds} ثانیه صبر کنم. "
                f"الان {wait_time} ثانیه صبر می‌کنم... "
                f"(تلاش {attempt+1}/{max_retries})"
            )
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.exception(f"خطا در start: {e}")
            await asyncio.sleep(60)

    if not connected:
        logger.error("ربات بعد از چند تلاش راه‌اندازی نشد.")
        return

    logger.info("ربات آنلاین است. منتظر پیام‌ها...")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("ربات متوقف شد.")
