"""
ربات خصوصی مدیریت پروفایل
تک‌فایل با Telethon — دکمه‌های رنگی واقعی
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
    "8816493813:AAHSSd5Xz1i4jCbZ-jW9QrW8QcRAZi41BzQ"
)
MY_USER_ID = int(os.environ.get("MY_USER_ID", "7803165903"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# API برای Telethon
# از یه اپ تلگرام رسمی می‌گیریم یا از خودت
BOT_API_ID = int(os.environ.get("BOT_API_ID", "2040"))
BOT_API_HASH = os.environ.get("BOT_API_HASH", "b18441a1ff607e10a989891a5462e627")


# ==================== State ====================
user_states = {}  # حالت مکالمه هر کاربر
user_temp = {}    # داده‌های موقت


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

    # SQLite fallback
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
        (owner_id, phone, api_id, api_hash, session_str, name, username, datetime.now().isoformat())
    )


async def db_get_accounts(owner_id):
    rows = await db_exec(
        'SELECT * FROM accounts WHERE owner_id = ? AND is_active = 1 ORDER BY id DESC',
        (owner_id,), fetch=True
    )
    return rows or []


async def db_get_account(acc_id):
    return await db_exec('SELECT * FROM accounts WHERE id = ?', (acc_id,), fetch_one=True)


async def db_delete_account(acc_id):
    await db_exec('UPDATE accounts SET is_active = 0 WHERE id = ?', (acc_id,))


async def db_add_template(owner_id, title, text):
    return await db_exec(
        'INSERT INTO templates (owner_id, title, text, created_date) VALUES (?, ?, ?, ?) RETURNING id',
        (owner_id, title, text, datetime.now().isoformat())
    )


async def db_get_templates(owner_id):
    rows = await db_exec('SELECT * FROM templates WHERE owner_id = ? ORDER BY id DESC', (owner_id,), fetch=True)
    return rows or []


async def db_get_template(tid):
    return await db_exec('SELECT * FROM templates WHERE id = ?', (tid,), fetch_one=True)


async def db_delete_template(tid):
    await db_exec('DELETE FROM templates WHERE id = ?', (tid,))


async def db_get_settings(owner_id):
    row = await db_exec('SELECT * FROM settings WHERE owner_id = ?', (owner_id,), fetch_one=True)
    if not row:
        await db_exec('INSERT INTO settings (owner_id) VALUES (?)', (owner_id,))
        row = await db_exec('SELECT * FROM settings WHERE owner_id = ?', (owner_id,), fetch_one=True)
    return row


async def db_update_setting(owner_id, **kwargs):
    await db_get_settings(owner_id)
    for key, val in kwargs.items():
        await db_exec(f'UPDATE settings SET {key} = ? WHERE owner_id = ?', (val, owner_id))


def is_owner(user_id):
    return user_id == MY_USER_ID


# ==================== AI فیلتر چهره ====================
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
    try:
        _init_cascades()
        h, w = img_bgr.shape[:2]
        if w > 800:
            scale = 800 / w
            img_bgr = cv2.resize(img_bgr, (800, int(h * scale)))

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        min_size = max(40, int(min(gray.shape) * 0.08))

        faces = _cascade_frontal.detectMultiScale(gray, 1.05, 6, minSize=(min_size, min_size))
        if len(faces) > 0:
            for (x, y, fw, fh) in faces:
                roi = gray[y:y + fh, x:x + fw]
                eyes = _cascade_eye.detectMultiScale(roi, 1.1, 4)
                if len(eyes) >= 1:
                    return True

        faces = _cascade_profile.detectMultiScale(gray, 1.05, 6, minSize=(min_size, min_size))
        if len(faces) > 0:
            return True

        flipped = cv2.flip(gray, 1)
        faces = _cascade_profile.detectMultiScale(flipped, 1.05, 6, minSize=(min_size, min_size))
        if len(faces) > 0:
            return True

        return False
    except Exception as e:
        print(f"face filter error: {e}")
        return False


# ==================== دکمه‌های رنگی ====================
# 🎨 تلگرام دکمه‌های Inline رو با رنگ‌های primary/success/danger پشتیبانی می‌کنه
# ساختار: Button.inline(text, data) + پارامتر style در نسخه‌های جدید Telethon

def btn_primary(text, data):
    """دکمه آبی"""
    try:
        return Button.inline(text, data, style="primary")
    except TypeError:
        # اگه Telethon از style پشتیبانی نکنه، بدون style برمی‌گردونه
        return Button.inline(f"● {text}", data)


def btn_success(text, data):
    """دکمه سبز"""
    try:
        return Button.inline(text, data, style="success")
    except TypeError:
        return Button.inline(f"● {text}", data)


def btn_danger(text, data):
    """دکمه قرمز"""
    try:
        return Button.inline(text, data, style="danger")
    except TypeError:
        return Button.inline(f"● {text}", data)


# ==================== کیبوردها ====================
def main_menu_kb():
    return [
        [
            btn_primary("مدیریت اکانت‌ها", b"menu_accounts"),
            btn_primary("مدیریت قالب‌ها", b"menu_templates")
        ],
        [btn_success("گرفتن پروفایل", b"menu_get_profile")],
        [
            btn_primary("چند آیدی همزمان", b"menu_multi"),
            btn_primary("گروه / کانال", b"menu_group")
        ],
        [
            btn_primary("اتصال به کانال", b"menu_channel"),
            btn_primary("تنظیمات", b"menu_settings")
        ]
    ]


def back_kb(target=b"menu_main"):
    return [[btn_primary("بازگشت", target)]]


# ==================== متن‌ها ====================
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
        "از دکمه‌های زیر انتخاب کنید:"
    )


# ==================== ربات اصلی ====================
bot = TelegramClient(
    StringSession(),
    BOT_API_ID,
    BOT_API_HASH
)


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
    await event.respond("لغو شد.", buttons=back_kb())


# ==================== Callback Handler ====================
@bot.on(events.CallbackQuery)
async def handle_callback(event):
    if not is_owner(event.sender_id):
        return

    data = event.data.decode('utf-8')
    uid = event.sender_id

    try:
        # === منوی اصلی ===
        if data == "menu_main":
            text = await main_menu_text(uid)
            await event.edit(text, buttons=main_menu_kb())

        elif data == "menu_settings":
            await event.edit(
                "تنظیمات\n━━━━━━━━━━━━━━━━━━\n\nبخش تنظیمات.",
                buttons=back_kb()
            )

        # === اکانت‌ها ===
        elif data == "menu_accounts":
            await show_accounts_menu(event, uid)

        elif data == "acc_add":
            user_states[uid] = "WAIT_PHONE"
            user_temp[uid] = {}
            await event.edit(
                "افزودن اکانت جدید\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "مرحله 1 از 5\n\n"
                "شماره موبایل اکانت رو با کد کشور بفرست:\n"
                "مثال: +989123456789\n\n"
                "برای انصراف /cancel بزن.",
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

        # === قالب‌ها ===
        elif data == "menu_templates":
            await show_templates_menu(event, uid)

        elif data == "tmpl_add":
            user_states[uid] = "WAIT_NEW_TEMPLATE"
            user_temp[uid] = {}
            await event.edit(
                "قالب جدید\n━━━━━━━━━━━━━━━━━━\n\n"
                "عنوان قالب رو بفرست:\nمثال: چهره‌های زیبا\n\n"
                "برای انصراف /cancel بزن.",
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

        # === گرفتن پروفایل ===
        elif data == "menu_get_profile":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("اول یه اکانت انتخاب کن.", buttons=back_kb())
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
                "گرفتن پروفایل\n━━━━━━━━━━━━━━━━━━\n\n"
                f"اکانت: {a[6]}\n"
                f"قالب: {t_text}\n"
                f"کانال: {ch_text}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "شناسه طرف رو بفرست:\n\n"
                "- آیدی عددی: 123456789\n"
                "- یوزرنیم: @username\n\n"
                "فقط چهره‌های انسانی فیلتر می‌شن.",
                buttons=back_kb()
            )

        # === چند آیدی ===
        elif data == "menu_multi":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("اول یه اکانت انتخاب کن.", buttons=back_kb())
                return
            user_states[uid] = "WAIT_MULTI_IDS"
            await event.edit(
                "چند آیدی همزمان\n━━━━━━━━━━━━━━━━━━\n\n"
                "شناسه‌ها رو یکی در هر خط بفرست:\n\n"
                "123456789\n@username1\n@username2\n\n"
                "برای انصراف /cancel بزن.",
                buttons=back_kb()
            )

        # === گروه ===
        elif data == "menu_group":
            s = await db_get_settings(uid)
            if not s or not s[1]:
                await event.edit("اول یه اکانت انتخاب کن.", buttons=back_kb())
                return
            user_states[uid] = "WAIT_GROUP_LINK"
            await event.edit(
                "گروه یا کانال\n━━━━━━━━━━━━━━━━━━\n\n"
                "لینک گروه یا کانال رو بفرست:\n\n"
                "- https://t.me/groupname\n"
                "- https://t.me/+AbCdEf123\n"
                "- @groupname\n\n"
                "برای انصراف /cancel بزن.",
                buttons=back_kb()
            )

        # === کانال ===
        elif data == "menu_channel":
            s = await db_get_settings(uid)
            current = s[3] if s and s[3] else None
            title = s[4] if s and s[4] else "متصل نشده"

            text = (
                "اتصال به کانال\n━━━━━━━━━━━━━━━━━━\n\n"
                f"کانال فعلی: {title}\n\n"
                "پروفایل‌ها همزمان به این کانال هم ارسال می‌شن.\n\n"
                "برای اتصال:\n"
                "1. ربات رو به کانال اضافه کن\n"
                "2. یه پیام از کانال فوروارد کن\n"
                "3. یا یوزرنیم بفرست: @channel\n\n"
                "برای انصراف /cancel بزن."
            )
            kb = []
            if current:
                kb.append([btn_danger("قطع اتصال", b"channel_disconnect")])
            kb.append([btn_primary("بازگشت", b"menu_main")])
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
        text = "هنوز اکانتی اضافه نشده.\n\nاز دکمه زیر یه اکانت اضافه کن."
    else:
        lines = ["اکانت‌های فعال:\n"]
        for i, a in enumerate(accounts, 1):
            lines.append(
                f"{i}. {a[6]}\n"
                f"   شماره: {a[2]}\n"
                f"   یوزرنیم: @{a[7] or 'ندارد'}\n"
                f"   شناسه: {a[0]}"
            )
        text = "\n".join(lines)

    kb = []
    for a in accounts:
        kb.append([btn_primary(f"{a[6]}  |  {a[2][-4:]}", f"acc_view_{a[0]}".encode())])
    kb.append([btn_success("افزودن اکانت جدید", b"acc_add")])
    kb.append([btn_primary("بازگشت", b"menu_main")])

    await event.edit(text, buttons=kb)


async def show_account_detail(event, uid, acc_id):
    a = await db_get_account(acc_id)
    if not a:
        await event.answer("پیدا نشد", alert=True)
        return

    s = await db_get_settings(uid)
    is_current = s and s[1] == acc_id

    text = (
        "اطلاعات اکانت\n━━━━━━━━━━━━━━━━━━\n\n"
        f"نام: {a[6]}\n"
        f"یوزرنیم: @{a[7] or 'ندارد'}\n"
        f"شماره: {a[2]}\n"
        f"API ID: {a[3]}\n"
        f"تاریخ: {a[8][:10]}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{'این اکانت فعاله' if is_current else 'فعال نیست'}"
    )

    kb = []
    if not is_current:
        kb.append([btn_success("انتخاب به عنوان فعال", f"acc_use_{acc_id}".encode())])
    kb.append([btn_danger("حذف", f"acc_del_{acc_id}".encode())])
    kb.append([btn_primary("بازگشت", b"menu_accounts")])

    await event.edit(text, buttons=kb)


# ==================== نمایش منو قالب‌ها ====================
async def show_templates_menu(event, uid):
    templates = await db_get_templates(uid)

    if not templates:
        text = "هنوز قالبی نساختی."
    else:
        lines = ["قالب‌های موجود:\n"]
        for i, t in enumerate(templates, 1):
            preview = t[3][:80].replace('\n', ' ')
            lines.append(f"{i}. {t[2]}\n   {preview}...")
        text = "\n".join(lines)

    kb = []
    for t in templates:
        kb.append([btn_primary(t[2], f"tmpl_view_{t[0]}".encode())])
    kb.append([btn_success("قالب جدید", b"tmpl_add")])
    kb.append([btn_primary("بازگشت", b"menu_main")])

    await event.edit(text, buttons=kb)


async def show_template_detail(event, uid, tid):
    t = await db_get_template(tid)
    if not t:
        await event.answer("پیدا نشد", alert=True)
        return

    s = await db_get_settings(uid)
    is_current = s and s[2] == tid

    text = (
        "اطلاعات قالب\n━━━━━━━━━━━━━━━━━━\n\n"
        f"عنوان: {t[2]}\n\n"
        f"متن:\n{t[3]}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{'فعاله' if is_current else 'فعال نیست'}"
    )

    kb = []
    if not is_current:
        kb.append([btn_success("انتخاب به عنوان فعال", f"tmpl_use_{tid}".encode())])
    kb.append([btn_danger("حذف", f"tmpl_del_{tid}".encode())])
    kb.append([btn_primary("بازگشت", b"menu_templates")])

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
        # ===== افزودن اکانت =====
        if state == "WAIT_PHONE":
            if not re.match(r'^\+?[0-9]{10,15}$', text):
                await event.respond("شماره نامعتبره. دوباره بفرست یا /cancel.")
                return
            user_temp[uid]['phone'] = text
            user_states[uid] = "WAIT_API_ID"
            await event.respond(
                f"مرحله 2 از 5\n\nشماره: {text}\n\n"
                "حالا API ID رو بفرست:\n(عددیه، از my.telegram.org)"
            )

        elif state == "WAIT_API_ID":
            if not text.isdigit():
                await event.respond("API ID باید عدد باشه.")
                return
            user_temp[uid]['api_id'] = int(text)
            user_states[uid] = "WAIT_API_HASH"
            await event.respond("مرحله 3 از 5\n\nحالا API Hash رو بفرست:")

        elif state == "WAIT_API_HASH":
            if len(text) < 20:
                await event.respond("API Hash نامعتبره.")
                return
            user_temp[uid]['api_hash'] = text
            user_states[uid] = "WAIT_CODE"

            phone = user_temp[uid]['phone']
            api_id = user_temp[uid]['api_id']
            api_hash = user_temp[uid]['api_hash']

            msg = await event.respond("در حال ارسال کد تایید...")

            try:
                client = TelegramClient(StringSession(), api_id, api_hash)
                await client.connect()
                sent = await client.send_code_request(phone)
                user_temp[uid]['client'] = client
                user_temp[uid]['phone_code_hash'] = sent.phone_code_hash

                await msg.edit(
                    f"مرحله 4 از 5\n\nکد تایید به {phone} ارسال شد.\n\n"
                    "کد رو بفرست: 1.2.3.4.5 یا 12345"
                )
            except PhoneNumberInvalidError:
                await msg.edit("شماره نامعتبره. /cancel بزن.")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)
            except FloodWaitError as e:
                await msg.edit(f"محدودیت: {e.seconds} ثانیه.")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)
            except Exception as e:
                await msg.edit(f"خطا: {str(e)[:200]}")
                user_states.pop(uid, None)
                user_temp.pop(uid, None)

        elif state == "WAIT_CODE":
            code = text.replace('.', '').replace(' ', '').strip()
            if not code.isdigit():
                await event.respond("کد باید عدد باشه.")
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
                    "مرحله 5 از 5\n\nاکانت دو مرحله‌ای داره.\nپسوردت رو بفرست:"
                )
            except PhoneCodeInvalidError:
                await event.respond("کد اشتباهه.")
            except PhoneCodeExpiredError:
                await event.respond("کد منقضی شده. /cancel بزن.")
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

        # ===== افزودن قالب =====
        elif state == "WAIT_NEW_TEMPLATE":
            user_temp[uid]['tmpl_title'] = text
            user_states[uid] = "WAIT_TEMPLATE_TEXT"
            await event.respond(
                "حالا متن قالب رو بفرست:\n\n"
                "متغیرها:\n"
                "{name} نام کامل\n"
                "{username} یوزرنیم\n"
                "{id} شناسه\n"
                "{first_name} اسم کوچک\n"
                "{last_name} فامیل"
            )

        elif state == "WAIT_TEMPLATE_TEXT":
            title = user_temp[uid].get('tmpl_title', 'بدون عنوان')
            tid = await db_add_template(MY_USER_ID, title, text)
            await event.respond(
                f"قالب {title} ذخیره شد. شناسه: {tid}",
                buttons=back_kb(b"menu_templates")
            )
            user_states.pop(uid, None)
            user_temp.pop(uid, None)

        # ===== گرفتن پروفایل =====
        elif state == "WAIT_TARGET_INPUT":
            user_states.pop(uid, None)
            asyncio.create_task(process_single_target(event, uid, text))

        # ===== چند آیدی =====
        elif state == "WAIT_MULTI_IDS":
            user_states.pop(uid, None)
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            asyncio.create_task(process_multi_targets(event, uid, lines))

        # ===== گروه =====
        elif state == "WAIT_GROUP_LINK":
            user_states.pop(uid, None)
            asyncio.create_task(process_group(event, uid, text))

        # ===== کانال =====
        elif state == "WAIT_CHANNEL_LINK":
            user_states.pop(uid, None)
            if text.startswith('@') or text.startswith('https://t.me/'):
                name = text.split('/')[-1]
                if name.startswith('@'):
                    name = name[1:]

                s = await db_get_settings(uid)
                a = await db_get_account(s[1]) if s and s[1] else None
                if not a:
                    await event.respond("اول یه اکانت انتخاب کن.")
                    return

                client = None
                try:
                    client = TelegramClient(StringSession(a[5]), a[3], a[4])
                    await client.connect()
                    entity = await client.get_entity(name)
                    await db_update_setting(uid, channel_id=str(entity.id),
                                            channel_title=getattr(entity, 'title', name))
                    await event.respond(
                        f"کانال متصل شد.\nنام: {getattr(entity, 'title', name)}\nشناسه: {entity.id}",
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


# ==================== هندلر فوروارد (کانال) ====================
@bot.on(events.NewMessage)
async def handle_forward(event):
    if not is_owner(event.sender_id):
        return
    if user_states.get(event.sender_id) != "WAIT_CHANNEL_LINK":
        return

    if event.message.fwd_from and event.message.fwd_from.from_id:
        # نمیشه مستقیم چک کرد، پس فقط از متن استفاده می‌کنیم
        pass


# ==================== پردازش اکانت ====================
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
            "اکانت اضافه شد\n━━━━━━━━━━━━━━━━━━\n\n"
            f"نام: {acc_name}\n"
            f"یوزرنیم: @{username or 'ندارد'}\n"
            f"شماره: {phone}\n"
            f"شناسه: {acc_id}",
            buttons=[[btn_success("مدیریت اکانت‌ها", b"menu_accounts")]]
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


# ==================== پردازش یه پروفایل ====================
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
            await event.respond(f"کاربر {target_raw} پیدا نشد.")
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
        await event.respond(f"کاربر {full_name} پروفایلی نداره.")
        return

    status_msg = await event.respond(
        f"در حال پردازش...\n\nکاربر: {full_name}\nتعداد: {total}"
    )

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
                        f"در حال پردازش...\n\n{i+1}/{total}\nچهره: {len(human_faces)}"
                    )
                except:
                    pass
        except:
            continue

    footer = ""
    if template_id:
        t = await db_get_template(template_id)
        if t:
            footer = t[3]
            footer = footer.replace("{name}", full_name).replace("{username}", username or "")
            footer = footer.replace("{first_name}", first_name).replace("{last_name}", last_name)
            footer = footer.replace("{id}", user_id_str)

    sent_owner = 0
    sent_channel = 0

    for idx, data in enumerate(human_faces):
        caption = footer if idx == 0 else None
        try:
            await bot.send_file(owner_id, data, caption=caption)
            sent_owner += 1
        except Exception as e:
            logger.warning(f"send to owner: {e}")
        if channel_id:
            try:
                await bot.send_file(int(channel_id), data, caption=caption)
                sent_channel += 1
            except:
                pass
        await asyncio.sleep(0.3)

    final_text = (
        f"تکمیل شد\n━━━━━━━━━━━━━━━━━━\n\n"
        f"کاربر: {full_name}\n"
        f"یوزرنیم: @{username or 'ندارد'}\n"
        f"شناسه: {user_id_str}\n\n"
        f"کل: {total}\n"
        f"ارسال شده: {sent_owner}\n"
        f"فیلتر: {total - sent_owner}"
    )
    if channel_id:
        final_text += f"\nکانال: {sent_channel}"

    try:
        await status_msg.edit(final_text, buttons=back_kb())
    except:
        await event.respond(final_text, buttons=back_kb())


# ==================== پردازش چند آیدی ====================
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
                await status.edit(
                    f"پردازش {idx}/{total}...\nشناسه: {raw}\nموفق: {success}  ناموفق: {failed}"
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
            f"تکمیل شد\nکل: {total}\nموفق: {success}\nناموفق: {failed}",
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

    sent = 0
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
                caption = footer if sent == 0 else None
                try:
                    await bot.send_file(owner_id, data, caption=caption)
                except:
                    pass
                if channel_id:
                    try:
                        await bot.send_file(int(channel_id), data, caption=caption)
                    except:
                        pass
                sent += 1
                await asyncio.sleep(0.3)
        except:
            continue


# ==================== پردازش گروه ====================
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
            await status.edit("عضوی پیدا نشد.")
            return

        await status.edit(f"اعضا: {total}\nپردازش...")

        success = 0
        for idx, user in enumerate(participants, 1):
            if user.bot or user.deleted:
                continue
            try:
                await status.edit(f"پردازش {idx}/{total}...\nموفق: {success}")
            except:
                pass
            try:
                await process_silent(client, uid, user, s[2], s[3])
                success += 1
            except:
                pass
            await asyncio.sleep(0.5)

        await status.edit(
            f"تکمیل شد\nگروه: {group_title}\nکل: {total}\nپردازش شده: {success}",
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

    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    logger.info(f"ربات @{me.username} شروع شد")

    await bot.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("ربات متوقف شد.")
