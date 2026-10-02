"""
ربات خصوصی مدیریت پروفایل
تک‌فایل — بدون پوشه‌های اضافی
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
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, filters, MessageHandler
)
from telethon import TelegramClient
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
TOKEN = os.environ.get(
    "TOKEN",
    "8816493813:AAHSSd5Xz1i4jCbZ-jW9QrW8QcRAZi41BzQ"
)
MY_USER_ID = int(os.environ.get("MY_USER_ID", "7803165903"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ==================== Conversation States ====================
(
    WAIT_PHONE, WAIT_API_ID, WAIT_API_HASH, WAIT_CODE, WAIT_PASSWORD,
    WAIT_TARGET_INPUT, WAIT_TEMPLATE_TEXT, WAIT_NEW_TEMPLATE,
    WAIT_CHANNEL_LINK, WAIT_MULTI_IDS, WAIT_GROUP_LINK
) = range(11)


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
            logger.info("✅ PostgreSQL آماده شد")
            return
        except Exception as e:
            logger.error(f"PostgreSQL error: {e}")
            USE_POSTGRES = False

    # SQLite
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
    logger.info("✅ SQLite آماده شد")


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
        return await loop.run_in_executor(
            None, _sqlite_exec, query, params, fetch, fetch_one
        )


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

        # روبرو با چک چشم
        faces = _cascade_frontal.detectMultiScale(gray, 1.05, 6, minSize=(min_size, min_size))
        if len(faces) > 0:
            for (x, y, fw, fh) in faces:
                roi = gray[y:y + fh, x:x + fw]
                eyes = _cascade_eye.detectMultiScale(roi, 1.1, 4)
                if len(eyes) >= 1:
                    return True

        # نیم‌رخ
        faces = _cascade_profile.detectMultiScale(gray, 1.05, 6, minSize=(min_size, min_size))
        if len(faces) > 0:
            return True

        # نیم‌رخ آینه
        flipped = cv2.flip(gray, 1)
        faces = _cascade_profile.detectMultiScale(flipped, 1.05, 6, minSize=(min_size, min_size))
        if len(faces) > 0:
            return True

        return False
    except Exception as e:
        print(f"face filter error: {e}")
        return False


# ==================== کیبوردها (رنگی) ====================
class C:
    PRIMARY = "🔵"
    SUCCESS = "🟢"
    DANGER = "🔴"
    WARNING = "🟡"
    INFO = "🔷"
    NEUTRAL = "⚪"
    PURPLE = "🟣"
    ORANGE = "🟠"


def main_menu_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{C.PRIMARY} مدیریت اکانت‌ها", callback_data="menu_accounts"),
            InlineKeyboardButton(f"{C.PURPLE} مدیریت قالب‌ها", callback_data="menu_templates")
        ],
        [InlineKeyboardButton(f"{C.SUCCESS} گرفتن پروفایل", callback_data="menu_get_profile")],
        [
            InlineKeyboardButton(f"{C.INFO} چند آیدی همزمان", callback_data="menu_multi"),
            InlineKeyboardButton(f"{C.INFO} گروه/کانال", callback_data="menu_group")
        ],
        [
            InlineKeyboardButton(f"{C.ORANGE} اتصال به کانال", callback_data="menu_channel"),
            InlineKeyboardButton(f"{C.NEUTRAL} تنظیمات", callback_data="menu_settings")
        ]
    ])


def back_kb(target="menu_main"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{C.NEUTRAL} بازگشت", callback_data=target)]
    ])


# ==================== منوی اصلی ====================
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text(
        await main_menu_text(update.effective_user.id),
        reply_markup=main_menu_kb()
    )


async def menu_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    try:
        await q.edit_message_text(
            await main_menu_text(q.from_user.id),
            reply_markup=main_menu_kb()
        )
    except:
        pass


async def menu_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    await q.edit_message_text(
        "تنظیمات\n━━━━━━━━━━━━━━━━━━\n\nبخش تنظیمات.",
        reply_markup=back_kb()
    )


# ==================== اکانت‌ها ====================
async def menu_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    accounts = await db_get_accounts(q.from_user.id)

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
        kb.append([InlineKeyboardButton(
            f"{C.PRIMARY} {a[6]}  |  {a[2][-4:]}",
            callback_data=f"acc_view_{a[0]}"
        )])
    kb.append([InlineKeyboardButton(f"{C.SUCCESS} افزودن اکانت جدید", callback_data="acc_add")])
    kb.append([InlineKeyboardButton(f"{C.NEUTRAL} بازگشت", callback_data="menu_main")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def acc_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    await q.edit_message_text(
        "افزودن اکانت جدید\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "مرحله 1 از 5\n\n"
        "شماره موبایل اکانت رو با کد کشور بفرست:\n"
        "مثال: +989123456789\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb("menu_accounts")
    )
    return WAIT_PHONE


async def acc_wait_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    phone = update.message.text.strip()
    if not re.match(r'^\+?[0-9]{10,15}$', phone):
        await update.message.reply_text("شماره نامعتبره. دوباره بفرست یا /cancel.")
        return WAIT_PHONE
    context.user_data['new_acc_phone'] = phone
    await update.message.reply_text(
        f"مرحله 2 از 5\n\nشماره: {phone}\n\nحالا API ID رو بفرست:"
    )
    return WAIT_API_ID


async def acc_wait_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("API ID باید عدد باشه.")
        return WAIT_API_ID
    context.user_data['new_acc_api_id'] = int(txt)
    await update.message.reply_text("مرحله 3 از 5\n\nحالا API Hash رو بفرست:")
    return WAIT_API_HASH


async def acc_wait_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    api_hash = update.message.text.strip()
    if len(api_hash) < 20:
        await update.message.reply_text("API Hash نامعتبره.")
        return WAIT_API_HASH

    context.user_data['new_acc_api_hash'] = api_hash
    phone = context.user_data['new_acc_phone']
    api_id = context.user_data['new_acc_api_id']

    msg = await update.message.reply_text("در حال ارسال کد تایید...")

    try:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        context.user_data['new_acc_client'] = client
        context.user_data['new_acc_phone_code_hash'] = sent.phone_code_hash

        await msg.edit_text(
            f"مرحله 4 از 5\n\nکد تایید به {phone} ارسال شد.\n\n"
            "کد رو بفرست: 1.2.3.4.5 یا 12345"
        )
        return WAIT_CODE
    except PhoneNumberInvalidError:
        await msg.edit_text("شماره نامعتبره. /cancel بزن.")
        return ConversationHandler.END
    except FloodWaitError as e:
        await msg.edit_text(f"محدودیت: {e.seconds} ثانیه.")
        return ConversationHandler.END
    except Exception as e:
        await msg.edit_text(f"خطا: {str(e)[:200]}")
        return ConversationHandler.END


async def acc_wait_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip()
    code = raw.replace('.', '').replace(' ', '').strip()
    if not code.isdigit():
        await update.message.reply_text("کد باید عدد باشه.")
        return WAIT_CODE

    client = context.user_data.get('new_acc_client')
    phone = context.user_data['new_acc_phone']
    phone_code_hash = context.user_data.get('new_acc_phone_code_hash')

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        return await _finalize_account(update, context, client)
    except SessionPasswordNeededError:
        await update.message.reply_text(
            "مرحله 5 از 5\n\nاکانت دو مرحله‌ای داره.\nپسوردت رو بفرست:"
        )
        return WAIT_PASSWORD
    except PhoneCodeInvalidError:
        await update.message.reply_text("کد اشتباهه.")
        return WAIT_CODE
    except PhoneCodeExpiredError:
        await update.message.reply_text("کد منقضی شده. /cancel بزن.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"خطا: {str(e)[:200]}")
        return ConversationHandler.END


async def acc_wait_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    password = update.message.text.strip()
    client = context.user_data.get('new_acc_client')
    try:
        await client.sign_in(password=password)
        return await _finalize_account(update, context, client)
    except Exception as e:
        await update.message.reply_text(f"خطا: {str(e)[:200]}")
        return ConversationHandler.END


async def _finalize_account(update, context, client):
    try:
        me = await client.get_me()
        session_str = client.session.save()
        phone = context.user_data['new_acc_phone']
        api_id = context.user_data['new_acc_api_id']
        api_hash = context.user_data['new_acc_api_hash']
        acc_name = me.first_name or "بدون نام"
        username = me.username or ""

        acc_id = await db_add_account(MY_USER_ID, phone, api_id, api_hash,
                                      session_str, acc_name, username)
        await client.disconnect()

        await update.message.reply_text(
            "اکانت اضافه شد\n━━━━━━━━━━━━━━━━━━\n\n"
            f"نام: {acc_name}\n"
            f"یوزرنیم: @{username or 'ندارد'}\n"
            f"شماره: {phone}\n"
            f"شناسه: {acc_id}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{C.SUCCESS} مدیریت اکانت‌ها", callback_data="menu_accounts")]
            ])
        )
    except Exception as e:
        await update.message.reply_text(f"خطا: {str(e)[:200]}")

    for k in ['new_acc_phone', 'new_acc_api_id', 'new_acc_api_hash',
              'new_acc_client', 'new_acc_phone_code_hash']:
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def acc_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    acc_id = int(q.data.split('_')[2])
    a = await db_get_account(acc_id)
    if not a:
        await q.answer("پیدا نشد", show_alert=True)
        return

    s = await db_get_settings(q.from_user.id)
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
        kb.append([InlineKeyboardButton(f"{C.SUCCESS} انتخاب به عنوان فعال", callback_data=f"acc_use_{acc_id}")])
    kb.append([InlineKeyboardButton(f"{C.DANGER} حذف", callback_data=f"acc_del_{acc_id}")])
    kb.append([InlineKeyboardButton(f"{C.NEUTRAL} بازگشت", callback_data="menu_accounts")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def acc_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    acc_id = int(q.data.split('_')[2])
    await db_update_setting(q.from_user.id, current_account_id=acc_id)
    await q.answer("انتخاب شد")
    await acc_view(update, context)


async def acc_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    acc_id = int(q.data.split('_')[2])
    await db_delete_account(acc_id)
    s = await db_get_settings(q.from_user.id)
    if s and s[1] == acc_id:
        await db_update_setting(q.from_user.id, current_account_id=None)
    await q.answer("حذف شد")
    await menu_accounts(update, context)


# ==================== قالب‌ها ====================
async def menu_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    templates = await db_get_templates(q.from_user.id)

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
        kb.append([InlineKeyboardButton(f"{C.PURPLE} {t[2]}", callback_data=f"tmpl_view_{t[0]}")])
    kb.append([InlineKeyboardButton(f"{C.SUCCESS} قالب جدید", callback_data="tmpl_add")])
    kb.append([InlineKeyboardButton(f"{C.NEUTRAL} بازگشت", callback_data="menu_main")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def tmpl_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    await q.edit_message_text(
        "قالب جدید\n━━━━━━━━━━━━━━━━━━\n\n"
        "عنوان قالب رو بفرست:\nمثال: چهره‌های زیبا\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb("menu_templates")
    )
    return WAIT_NEW_TEMPLATE


async def tmpl_wait_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['new_tmpl_title'] = update.message.text.strip()
    await update.message.reply_text(
        "حالا متن قالب رو بفرست:\n\n"
        "متغیرها:\n"
        "{name} نام کامل\n"
        "{username} یوزرنیم\n"
        "{id} شناسه\n"
        "{first_name} اسم کوچک\n"
        "{last_name} فامیل"
    )
    return WAIT_TEMPLATE_TEXT


async def tmpl_wait_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text
    title = context.user_data.pop('new_tmpl_title', 'بدون عنوان')
    tid = await db_add_template(MY_USER_ID, title, text)
    await update.message.reply_text(
        f"قالب {title} ذخیره شد. شناسه: {tid}",
        reply_markup=back_kb("menu_templates")
    )
    return ConversationHandler.END


async def tmpl_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    tid = int(q.data.split('_')[2])
    t = await db_get_template(tid)
    if not t:
        await q.answer("پیدا نشد", show_alert=True)
        return

    s = await db_get_settings(q.from_user.id)
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
        kb.append([InlineKeyboardButton(f"{C.SUCCESS} انتخاب به عنوان فعال", callback_data=f"tmpl_use_{tid}")])
    kb.append([InlineKeyboardButton(f"{C.DANGER} حذف", callback_data=f"tmpl_del_{tid}")])
    kb.append([InlineKeyboardButton(f"{C.NEUTRAL} بازگشت", callback_data="menu_templates")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def tmpl_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    tid = int(q.data.split('_')[2])
    await db_update_setting(q.from_user.id, current_template_id=tid)
    await q.answer("انتخاب شد")
    await tmpl_view(update, context)


async def tmpl_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    tid = int(q.data.split('_')[2])
    await db_delete_template(tid)
    s = await db_get_settings(q.from_user.id)
    if s and s[2] == tid:
        await db_update_setting(q.from_user.id, current_template_id=None)
    await q.answer("حذف شد")
    await menu_templates(update, context)


# ==================== گرفتن پروفایل ====================
async def _resolve_entity(client, raw):
    raw = raw.strip()
    try:
        if raw.startswith('@'):
            raw = raw[1:]
        if raw.isdigit():
            return await client.get_entity(int(raw))
        return await client.get_entity(raw)
    except Exception as e:
        print(f"resolve error: {e}")
        return None


async def menu_get_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    s = await db_get_settings(q.from_user.id)
    if not s or not s[1]:
        await q.edit_message_text("اول یه اکانت انتخاب کن.", reply_markup=back_kb())
        return

    a = await db_get_account(s[1])
    t_text = "انتخاب نشده"
    if s[2]:
        t = await db_get_template(s[2])
        if t:
            t_text = t[2]
    ch_text = s[4] if s[3] else "متصل نشده"

    await q.edit_message_text(
        "گرفتن پروفایل\n━━━━━━━━━━━━━━━━━━\n\n"
        f"اکانت: {a[6]}\n"
        f"قالب: {t_text}\n"
        f"کانال: {ch_text}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "شناسه طرف رو بفرست:\n\n"
        "- آیدی عددی: 123456789\n"
        "- یوزرنیم: @username\n\n"
        "فقط چهره‌های انسانی فیلتر می‌شن.",
        reply_markup=back_kb()
    )
    return WAIT_TARGET_INPUT


async def handle_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip()
    owner_id = update.effective_user.id

    s = await db_get_settings(owner_id)
    if not s or not s[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = await db_get_account(s[1])
    status_msg = await update.message.reply_text(f"در حال پردازش {raw}...")

    asyncio.create_task(_process_single(
        bot=context.bot, owner_id=owner_id, account_row=a, target_raw=raw,
        template_id=s[2], channel_id=s[3],
        status_message_id=status_msg.message_id,
        chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def _process_single(bot, owner_id, account_row, target_raw,
                          template_id, channel_id, status_message_id, chat_id):
    client = None
    try:
        _, _, phone, api_id, api_hash, session_str, _, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text("سشن منقضی شده.", chat_id=chat_id, message_id=status_message_id)
            return

        target = await _resolve_entity(client, target_raw)
        if not target:
            await bot.edit_message_text(f"کاربر {target_raw} پیدا نشد.",
                                        chat_id=chat_id, message_id=status_message_id)
            return

        await _process_user_profile(bot, client, owner_id, target,
                                    template_id, channel_id,
                                    status_message_id, chat_id)
    except Exception as e:
        print(f"error: {e}")
        try:
            await bot.edit_message_text(f"خطا: {str(e)[:200]}",
                                        chat_id=chat_id, message_id=status_message_id)
        except:
            pass
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def _process_user_profile(bot, client, owner_id, target,
                                template_id, channel_id,
                                status_message_id, chat_id):
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
        await bot.edit_message_text(f"خطا در دریافت پروفایل‌ها: {str(e)[:150]}",
                                    chat_id=chat_id, message_id=status_message_id)
        return

    total = len(photo_list)
    if total == 0:
        await bot.edit_message_text(f"کاربر {full_name} پروفایلی نداره.",
                                    chat_id=chat_id, message_id=status_message_id)
        return

    await bot.edit_message_text(f"در حال پردازش...\n\nکاربر: {full_name}\nتعداد: {total}",
                                chat_id=chat_id, message_id=status_message_id)

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
                    await bot.edit_message_text(
                        f"در حال پردازش...\n\n{i+1}/{total}\nچهره: {len(human_faces)}",
                        chat_id=chat_id, message_id=status_message_id
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
            await bot.send_photo(chat_id=owner_id, photo=data, caption=caption)
            sent_owner += 1
        except:
            pass
        if channel_id:
            try:
                await bot.send_photo(chat_id=channel_id, photo=data, caption=caption)
                sent_channel += 1
            except:
                pass
        await asyncio.sleep(0.3)

    text = (
        "تکمیل شد\n━━━━━━━━━━━━━━━━━━\n\n"
        f"کاربر: {full_name}\n"
        f"یوزرنیم: @{username or 'ندارد'}\n"
        f"شناسه: {user_id_str}\n\n"
        f"کل: {total}\n"
        f"ارسال شده: {sent_owner}\n"
        f"فیلتر: {total - sent_owner}"
    )
    if channel_id:
        text += f"\nکانال: {sent_channel}"

    await bot.edit_message_text(text, chat_id=chat_id, message_id=status_message_id,
                                reply_markup=back_kb())


# ==================== چند آیدی ====================
async def menu_multi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    s = await db_get_settings(q.from_user.id)
    if not s or not s[1]:
        await q.edit_message_text("اول یه اکانت انتخاب کن.", reply_markup=back_kb())
        return

    await q.edit_message_text(
        "چند آیدی همزمان\n━━━━━━━━━━━━━━━━━━\n\n"
        "شناسه‌ها رو یکی در هر خط بفرست:\n\n"
        "123456789\n@username1\n@username2\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb()
    )
    return WAIT_MULTI_IDS


async def handle_multi_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip()
    lines = [l.strip() for l in raw.split('\n') if l.strip()]
    if not lines:
        await update.message.reply_text("چیزی نفرستادی.")
        return WAIT_MULTI_IDS

    owner_id = update.effective_user.id
    s = await db_get_settings(owner_id)
    if not s or not s[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = await db_get_account(s[1])
    status_msg = await update.message.reply_text(f"در حال پردازش {len(lines)} شناسه...")

    asyncio.create_task(_process_multi(
        bot=context.bot, owner_id=owner_id, account_row=a,
        targets=lines, template_id=s[2], channel_id=s[3],
        status_message_id=status_msg.message_id, chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def _process_multi(bot, owner_id, account_row, targets,
                         template_id, channel_id, status_message_id, chat_id):
    client = None
    try:
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text("سشن منقضی شده.", chat_id=chat_id, message_id=status_message_id)
            return

        total = len(targets)
        success = 0
        failed = 0

        for idx, raw in enumerate(targets, 1):
            try:
                await bot.edit_message_text(
                    f"پردازش {idx}/{total}...\nشناسه: {raw}\nموفق: {success}  ناموفق: {failed}",
                    chat_id=chat_id, message_id=status_message_id
                )
            except:
                pass

            target = await _resolve_entity(client, raw)
            if not target:
                failed += 1
                continue
            try:
                await _process_silent(bot, client, owner_id, target, template_id, channel_id)
                success += 1
            except:
                failed += 1
            await asyncio.sleep(1)

        await bot.edit_message_text(
            f"تکمیل شد\nکل: {total}\nموفق: {success}\nناموفق: {failed}",
            chat_id=chat_id, message_id=status_message_id, reply_markup=back_kb()
        )
    except Exception as e:
        print(f"multi error: {e}")
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def _process_silent(bot, client, owner_id, target, template_id, channel_id):
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
                    await bot.send_photo(chat_id=owner_id, photo=data, caption=caption)
                except:
                    pass
                if channel_id:
                    try:
                        await bot.send_photo(chat_id=channel_id, photo=data, caption=caption)
                    except:
                        pass
                sent += 1
                await asyncio.sleep(0.3)
        except:
            continue


# ==================== گروه ====================
async def menu_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    s = await db_get_settings(q.from_user.id)
    if not s or not s[1]:
        await q.edit_message_text("اول یه اکانت انتخاب کن.", reply_markup=back_kb())
        return

    await q.edit_message_text(
        "گروه یا کانال\n━━━━━━━━━━━━━━━━━━\n\n"
        "لینک گروه یا کانال رو بفرست:\n\n"
        "- https://t.me/groupname\n"
        "- https://t.me/+AbCdEf123\n"
        "- @groupname\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb()
    )
    return WAIT_GROUP_LINK


async def handle_group_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    link = update.message.text.strip()
    owner_id = update.effective_user.id

    s = await db_get_settings(owner_id)
    if not s or not s[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = await db_get_account(s[1])
    status_msg = await update.message.reply_text(f"در حال پردازش گروه...")

    asyncio.create_task(_process_group(
        bot=context.bot, owner_id=owner_id, account_row=a, link=link,
        template_id=s[2], channel_id=s[3],
        status_message_id=status_msg.message_id, chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def _process_group(bot, owner_id, account_row, link,
                         template_id, channel_id, status_message_id, chat_id):
    client = None
    try:
        _, _, _, api_id, api_hash, session_str, _, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text("سشن منقضی شده.", chat_id=chat_id, message_id=status_message_id)
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
            await bot.edit_message_text(f"گروه پیدا نشد.\n{str(e)[:200]}",
                                        chat_id=chat_id, message_id=status_message_id)
            return

        group_title = getattr(entity, 'title', 'بدون نام')
        await bot.edit_message_text(f"گروه: {group_title}\nدر حال دریافت اعضا...",
                                    chat_id=chat_id, message_id=status_message_id)

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
            await bot.edit_message_text("عضوی پیدا نشد.",
                                        chat_id=chat_id, message_id=status_message_id)
            return

        await bot.edit_message_text(f"اعضا: {total}\nپردازش...",
                                    chat_id=chat_id, message_id=status_message_id)

        success = 0
        for idx, user in enumerate(participants, 1):
            if user.bot or user.deleted:
                continue
            try:
                await bot.edit_message_text(
                    f"پردازش {idx}/{total}...\nموفق: {success}",
                    chat_id=chat_id, message_id=status_message_id
                )
            except:
                pass
            try:
                await _process_silent(bot, client, owner_id, user, template_id, channel_id)
                success += 1
            except:
                pass
            await asyncio.sleep(0.5)

        await bot.edit_message_text(
            f"تکمیل شد\nگروه: {group_title}\nکل: {total}\nپردازش شده: {success}",
            chat_id=chat_id, message_id=status_message_id, reply_markup=back_kb()
        )
    except Exception as e:
        print(f"group error: {e}")
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


# ==================== کانال ====================
async def menu_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    s = await db_get_settings(q.from_user.id)
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
        kb.append([InlineKeyboardButton(f"{C.DANGER} قطع اتصال", callback_data="channel_disconnect")])
    kb.append([InlineKeyboardButton(f"{C.NEUTRAL} بازگشت", callback_data="menu_main")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return WAIT_CHANNEL_LINK


async def handle_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    owner_id = update.effective_user.id

    if update.message.forward_from_chat:
        chat = update.message.forward_from_chat
        await db_update_setting(owner_id, channel_id=str(chat.id), channel_title=chat.title or "کانال")
        await update.message.reply_text(
            f"کانال متصل شد.\nنام: {chat.title}\nشناسه: {chat.id}",
            reply_markup=back_kb()
        )
        return ConversationHandler.END

    text = update.message.text.strip() if update.message.text else ""
    if text.startswith('@') or text.startswith('https://t.me/'):
        name = text.split('/')[-1]
        if name.startswith('@'):
            name = name[1:]

        s = await db_get_settings(owner_id)
        if not s or not s[1]:
            await update.message.reply_text("اول یه اکانت انتخاب کن.")
            return ConversationHandler.END

        a = await db_get_account(s[1])
        client = None
        try:
            client = TelegramClient(StringSession(a[5]), a[3], a[4])
            await client.connect()
            entity = await client.get_entity(name)
            await db_update_setting(owner_id, channel_id=str(entity.id),
                                    channel_title=getattr(entity, 'title', name))
            await update.message.reply_text(
                f"کانال متصل شد.\nنام: {getattr(entity, 'title', name)}\nشناسه: {entity.id}",
                reply_markup=back_kb()
            )
        except Exception as e:
            await update.message.reply_text(f"خطا: {str(e)[:200]}")
        finally:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
        return ConversationHandler.END

    await update.message.reply_text("ورودی نامعتبر.")
    return WAIT_CHANNEL_LINK


async def channel_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    await db_update_setting(q.from_user.id, channel_id=None, channel_title=None)
    await menu_main(update, context)


# ==================== Cancel ====================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("لغو شد.", reply_markup=back_kb())
    return ConversationHandler.END


# ==================== Main ====================
async def post_init(app):
    await init_db()


def main():
    if not TOKEN or "PASTE" in TOKEN:
        print("توکن تنظیم نشده.")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    # افزودن اکانت
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(acc_add_start, pattern="^acc_add$")],
        states={
            WAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_phone)],
            WAIT_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_api_id)],
            WAIT_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_api_hash)],
            WAIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_code)],
            WAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # افزودن قالب
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(tmpl_add_start, pattern="^tmpl_add$")],
        states={
            WAIT_NEW_TEMPLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_wait_title)],
            WAIT_TEMPLATE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_wait_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # پروفایل
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_get_profile, pattern="^menu_get_profile$")],
        states={
            WAIT_TARGET_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_target_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # چند آیدی
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_multi, pattern="^menu_multi$")],
        states={
            WAIT_MULTI_IDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_multi_ids)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # گروه
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_group, pattern="^menu_group$")],
        states={
            WAIT_GROUP_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # کانال
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_channel, pattern="^menu_channel$")],
        states={
            WAIT_CHANNEL_LINK: [
                MessageHandler(filters.FORWARDED, handle_channel_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channel_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # Callback ها
    app.add_handler(CallbackQueryHandler(menu_main, pattern="^menu_main$"))
    app.add_handler(CallbackQueryHandler(menu_accounts, pattern="^menu_accounts$"))
    app.add_handler(CallbackQueryHandler(menu_templates, pattern="^menu_templates$"))
    app.add_handler(CallbackQueryHandler(menu_settings, pattern="^menu_settings$"))
    app.add_handler(CallbackQueryHandler(acc_view, pattern="^acc_view_"))
    app.add_handler(CallbackQueryHandler(acc_use, pattern="^acc_use_"))
    app.add_handler(CallbackQueryHandler(acc_del, pattern="^acc_del_"))
    app.add_handler(CallbackQueryHandler(tmpl_view, pattern="^tmpl_view_"))
    app.add_handler(CallbackQueryHandler(tmpl_use, pattern="^tmpl_use_"))
    app.add_handler(CallbackQueryHandler(tmpl_del, pattern="^tmpl_del_"))
    app.add_handler(CallbackQueryHandler(channel_disconnect, pattern="^channel_disconnect$"))

    print("ربات در حال اجراست...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
