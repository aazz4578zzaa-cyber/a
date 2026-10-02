import asyncio
import os
import re
import sqlite3
from datetime import datetime
from io import BytesIO

import cv2
import numpy as np
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
    KeyboardButton, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, filters, MessageHandler
)
from telethon import TelegramClient, functions
from telethon.errors import (
    FloodWaitError, PhoneCodeExpiredError, PhoneCodeInvalidError,
    PhoneNumberInvalidError, SessionPasswordNeededError,
    UsernameInvalidError, UsernameNotOccupiedError
)
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.types import ChannelParticipantsSearch

# ==================== تنظیمات ====================
TOKEN = os.environ.get("TOKEN", "PASTE_YOUR_NEW_BOT_TOKEN_HERE")
MY_USER_ID = int(os.environ.get("MY_USER_ID", "7803165903"))
DB_FILE = os.environ.get("DB_FILE", "private_bot.db")

# Conversation states
(
    WAIT_PHONE, WAIT_API_ID, WAIT_API_HASH, WAIT_CODE, WAIT_PASSWORD,
    WAIT_TARGET_INPUT, WAIT_TEMPLATE_TEXT, WAIT_NEW_TEMPLATE,
    WAIT_CHANNEL_LINK, WAIT_MULTI_IDS, WAIT_GROUP_LINK
) = range(11)


# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
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

    c.execute('''
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            title TEXT,
            text TEXT,
            created_date TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            owner_id INTEGER PRIMARY KEY,
            current_account_id INTEGER,
            current_template_id INTEGER,
            channel_id TEXT,
            channel_title TEXT
        )
    ''')

    conn.commit()
    conn.close()


init_db()


# ==================== DB HELPERS ====================
def db_add_account(owner_id, phone, api_id, api_hash, session_string, account_name, username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO accounts (owner_id, phone, api_id, api_hash, session_string, account_name, username, created_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (owner_id, phone, api_id, api_hash, session_string, account_name, username, datetime.now().isoformat()))
    conn.commit()
    acc_id = c.lastrowid
    conn.close()
    return acc_id


def db_get_accounts(owner_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT * FROM accounts WHERE owner_id = ? AND is_active = 1 ORDER BY id DESC', (owner_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def db_get_account(acc_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT * FROM accounts WHERE id = ?', (acc_id,))
    row = c.fetchone()
    conn.close()
    return row


def db_delete_account(acc_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE accounts SET is_active = 0 WHERE id = ?', (acc_id,))
    conn.commit()
    conn.close()


def db_add_template(owner_id, title, text):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO templates (owner_id, title, text, created_date)
        VALUES (?, ?, ?, ?)
    ''', (owner_id, title, text, datetime.now().isoformat()))
    conn.commit()
    tid = c.lastrowid
    conn.close()
    return tid


def db_get_templates(owner_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT * FROM templates WHERE owner_id = ? ORDER BY id DESC', (owner_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def db_get_template(tid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT * FROM templates WHERE id = ?', (tid,))
    row = c.fetchone()
    conn.close()
    return row


def db_delete_template(tid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM templates WHERE id = ?', (tid,))
    conn.commit()
    conn.close()


def db_get_settings(owner_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT * FROM settings WHERE owner_id = ?', (owner_id,))
    row = c.fetchone()
    if not row:
        c.execute('INSERT INTO settings (owner_id) VALUES (?)', (owner_id,))
        conn.commit()
        c.execute('SELECT * FROM settings WHERE owner_id = ?', (owner_id,))
        row = c.fetchone()
    conn.close()
    return row


def db_update_setting(owner_id, **kwargs):
    db_get_settings(owner_id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for key, val in kwargs.items():
        c.execute(f'UPDATE settings SET {key} = ? WHERE owner_id = ?', (val, owner_id))
    conn.commit()
    conn.close()


# ==================== کمک‌کننده‌ها ====================
def is_owner(user_id):
    return user_id == MY_USER_ID


def esc(text):
    """escape متن برای Markdown"""
    if not text:
        return ""
    return str(text).replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')


def status_line(owner_id):
    s = db_get_settings(owner_id)
    acc = "ندارد"
    tmpl = "ندارد"
    ch = "ندارد"
    if s and s[1]:
        a = db_get_account(s[1])
        if a:
            acc = f"{a[6]}"
    if s and s[2]:
        t = db_get_template(s[2])
        if t:
            tmpl = t[2]
    if s and s[3]:
        ch = s[4] or "متصل"
    return f"اکانت: {acc}  |  قالب: {tmpl}  |  کانال: {ch}"


# ==================== متن‌ها ====================
def main_menu_text(owner_id):
    s = db_get_settings(owner_id)
    acc_name = "انتخاب نشده"
    tmpl_name = "انتخاب نشده"
    ch_name = "متصل نشده"

    if s and s[1]:
        a = db_get_account(s[1])
        if a:
            acc_name = a[6]
    if s and s[2]:
        t = db_get_template(s[2])
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


def accounts_menu_text(accounts):
    if not accounts:
        return "هنوز اکانتی اضافه نشده.\n\nاز دکمه زیر یه اکانت اضافه کن."

    lines = ["اکانت‌های فعال:\n"]
    for i, a in enumerate(accounts, 1):
        lines.append(
            f"{i}. {a[6]}\n"
            f"   شماره: {a[2]}\n"
            f"   یوزرنیم: @{a[7] or 'ندارد'}\n"
            f"   شناسه: {a[0]}"
        )
    lines.append("\nبرای مدیریت، روی هرکدوم کلیک کن.")
    return "\n".join(lines)


def templates_menu_text(templates):
    if not templates:
        return "هنوز قالبی نساختی.\n\nیه قالب بساز تا زیر پروفایل‌ها بیاد."
    lines = ["قالب‌های موجود:\n"]
    for i, t in enumerate(templates, 1):
        preview = t[3][:80].replace('\n', ' ')
        lines.append(f"{i}. {t[2]}\n   {preview}...")
    lines.append("\nروی هرکدوم کلیک کن تا مدیریتش کنی.")
    return "\n".join(lines)


# ==================== کیبوردها ====================
def main_menu_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("مدیریت اکانت‌ها", callback_data="menu_accounts"),
            InlineKeyboardButton("مدیریت قالب‌ها", callback_data="menu_templates")
        ],
        [
            InlineKeyboardButton("گرفتن پروفایل", callback_data="menu_get_profile"),
        ],
        [
            InlineKeyboardButton("چند آیدی همزمان", callback_data="menu_multi"),
            InlineKeyboardButton("گروه/کانال", callback_data="menu_group")
        ],
        [
            InlineKeyboardButton("اتصال به کانال", callback_data="menu_channel"),
            InlineKeyboardButton("تنظیمات", callback_data="menu_settings")
        ]
    ])


def back_kb(target="menu_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data=target)]])


# ==================== AI فیلتر چهره ====================
def init_face_detectors():
    """دو تا الگوریتم برای دقت بالاتر"""
    frontal = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    )
    profile = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_profileface.xml'
    )
    return frontal, profile


def is_human_face(img_bgr):
    """چک کن عکس حاوی چهره انسانیه یا نه"""
    try:
        frontal, profile = init_face_detectors()

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        h, w = gray.shape
        min_size = max(30, int(min(h, w) * 0.08))

        # چهره روبرو
        faces = frontal.detectMultiScale(
            gray, scaleFactor=1.05, minNeighbors=6, minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            return True

        # چهره نیم‌رخ
        faces = profile.detectMultiScale(
            gray, scaleFactor=1.05, minNeighbors=6, minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            return True

        # چهره نیم‌رخ آینه‌ای
        flipped = cv2.flip(gray, 1)
        faces = profile.detectMultiScale(
            flipped, scaleFactor=1.05, minNeighbors=6, minSize=(min_size, min_size)
        )
        if len(faces) > 0:
            return True

        return False
    except Exception as e:
        print(f"face detect error: {e}")
        return False


# ==================== هندلرها ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text(
        main_menu_text(update.effective_user.id),
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
            main_menu_text(q.from_user.id),
            reply_markup=main_menu_kb()
        )
    except:
        pass


# ==================== اکانت‌ها ====================
async def menu_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    accounts = db_get_accounts(q.from_user.id)

    kb = []
    for a in accounts:
        label = f"{a[6]}  |  {a[2][-4:]}"
        kb.append([InlineKeyboardButton(label, callback_data=f"acc_view_{a[0]}")])
    kb.append([InlineKeyboardButton("افزودن اکانت جدید", callback_data="acc_add")])
    kb.append([InlineKeyboardButton("بازگشت", callback_data="menu_main")])

    await q.edit_message_text(
        accounts_menu_text(accounts),
        reply_markup=InlineKeyboardMarkup(kb)
    )


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
        "مرحله 2 از 5\n\n"
        f"شماره: {phone}\n\n"
        "حالا API ID رو بفرست:\n"
        "(عددیه، از my.telegram.org)"
    )
    return WAIT_API_ID


async def acc_wait_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("API ID باید عدد باشه. دوباره بفرست.")
        return WAIT_API_ID
    context.user_data['new_acc_api_id'] = int(txt)
    await update.message.reply_text("مرحله 3 از 5\n\nحالا API Hash رو بفرست:")
    return WAIT_API_HASH


async def acc_wait_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    api_hash = update.message.text.strip()
    if len(api_hash) < 20:
        await update.message.reply_text("API Hash نامعتبره. دوباره بفرست.")
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
            "مرحله 4 از 5\n\n"
            f"کد تایید به شماره {phone} ارسال شد.\n\n"
            "کد رو به این صورت بفرست:\n"
            "1.2.3.4.5  یا  12345"
        )
        return WAIT_CODE
    except PhoneNumberInvalidError:
        await msg.edit_text("شماره نامعتبره. /cancel بزن و دوباره شروع کن.")
        return ConversationHandler.END
    except FloodWaitError as e:
        await msg.edit_text(f"محدودیت تلگرام: {e.seconds} ثانیه صبر کن و دوباره.")
        return ConversationHandler.END
    except Exception as e:
        await msg.edit_text(f"خطا: {str(e)[:200]}\n\n/cancel بزن.")
        return ConversationHandler.END


async def acc_wait_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip()
    code = raw.replace('.', '').replace(' ', '').strip()
    if not code.isdigit():
        await update.message.reply_text("کد باید عدد باشه. دوباره بفرست.")
        return WAIT_CODE

    client = context.user_data.get('new_acc_client')
    phone = context.user_data['new_acc_phone']
    phone_code_hash = context.user_data.get('new_acc_phone_code_hash')

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        return await finalize_account(update, context, client)
    except SessionPasswordNeededError:
        await update.message.reply_text(
            "مرحله 5 از 5\n\n"
            "اکانت دو مرحله‌ای داره.\n"
            "پسوردت رو بفرست:"
        )
        return WAIT_PASSWORD
    except PhoneCodeInvalidError:
        await update.message.reply_text("کد اشتباهه. دوباره بفرست.")
        return WAIT_CODE
    except PhoneCodeExpiredError:
        await update.message.reply_text("کد منقضی شده. /cancel بزن و دوباره شروع کن.")
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
        return await finalize_account(update, context, client)
    except Exception as e:
        await update.message.reply_text(f"خطا: {str(e)[:200]}")
        return ConversationHandler.END


async def finalize_account(update, context, client):
    try:
        me = await client.get_me()
        session_str = client.session.save()
        phone = context.user_data['new_acc_phone']
        api_id = context.user_data['new_acc_api_id']
        api_hash = context.user_data['new_acc_api_hash']
        acc_name = me.first_name or "بدون نام"
        username = me.username or ""

        acc_id = db_add_account(MY_USER_ID, phone, api_id, api_hash,
                                session_str, acc_name, username)

        await client.disconnect()

        await update.message.reply_text(
            "اکانت با موفقیت اضافه شد\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"نام: {acc_name}\n"
            f"یوزرنیم: @{username or 'ندارد'}\n"
            f"شماره: {phone}\n"
            f"شناسه داخلی: {acc_id}\n\n"
            "از منوی اکانت‌ها می‌تونی انتخابش کنی.",
            reply_markup=back_kb("menu_accounts")
        )
    except Exception as e:
        await update.message.reply_text(f"خطا در ذخیره: {str(e)[:200]}")

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
    a = db_get_account(acc_id)
    if not a:
        await q.answer("پیدا نشد", show_alert=True)
        return

    settings = db_get_settings(q.from_user.id)
    is_current = settings and settings[1] == acc_id

    text = (
        "اطلاعات اکانت\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"نام: {a[6]}\n"
        f"یوزرنیم: @{a[7] or 'ندارد'}\n"
        f"شماره: {a[2]}\n"
        f"API ID: {a[3]}\n"
        f"تاریخ افزودن: {a[8][:10]}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{'این اکانت الان فعاله' if is_current else 'این اکانت فعال نیست'}"
    )

    kb = []
    if not is_current:
        kb.append([InlineKeyboardButton("انتخاب به عنوان فعال", callback_data=f"acc_use_{acc_id}")])
    kb.append([InlineKeyboardButton("حذف", callback_data=f"acc_del_{acc_id}")])
    kb.append([InlineKeyboardButton("بازگشت", callback_data="menu_accounts")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def acc_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    acc_id = int(q.data.split('_')[2])
    db_update_setting(q.from_user.id, current_account_id=acc_id)
    await q.answer("انتخاب شد")
    await acc_view(update, context)


async def acc_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    acc_id = int(q.data.split('_')[2])
    db_delete_account(acc_id)
    settings = db_get_settings(q.from_user.id)
    if settings and settings[1] == acc_id:
        db_update_setting(q.from_user.id, current_account_id=None)
    await q.answer("حذف شد")
    await menu_accounts(update, context)


# ==================== قالب‌ها ====================
async def menu_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    templates = db_get_templates(q.from_user.id)

    kb = []
    for t in templates:
        kb.append([InlineKeyboardButton(t[2], callback_data=f"tmpl_view_{t[0]}")])
    kb.append([InlineKeyboardButton("قالب جدید", callback_data="tmpl_add")])
    kb.append([InlineKeyboardButton("بازگشت", callback_data="menu_main")])

    await q.edit_message_text(
        templates_menu_text(templates),
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def tmpl_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    await q.edit_message_text(
        "قالب جدید\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "عنوان قالب رو بفرست:\n"
        "مثال: چهره‌های زیبا\n\n"
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
        "این متن زیر پروفایل‌ها ارسال می‌شه.\n\n"
        "متغیرهای قابل استفاده:\n"
        "{name}  →  نام کامل\n"
        "{username}  →  یوزرنیم بدون @\n"
        "{id}  →  شناسه عددی\n"
        "{first_name}  →  اسم کوچک\n"
        "{last_name}  →  فامیل"
    )
    return WAIT_TEMPLATE_TEXT


async def tmpl_wait_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text
    title = context.user_data.pop('new_tmpl_title', 'بدون عنوان')
    tid = db_add_template(MY_USER_ID, title, text)
    await update.message.reply_text(
        f"قالب {title} ذخیره شد. (شناسه: {tid})",
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
    t = db_get_template(tid)
    if not t:
        await q.answer("پیدا نشد", show_alert=True)
        return

    settings = db_get_settings(q.from_user.id)
    is_current = settings and settings[2] == tid

    text = (
        "اطلاعات قالب\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"عنوان: {t[2]}\n\n"
        f"متن:\n{t[3]}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{'این قالب الان فعاله' if is_current else 'این قالب فعال نیست'}"
    )

    kb = []
    if not is_current:
        kb.append([InlineKeyboardButton("انتخاب به عنوان فعال", callback_data=f"tmpl_use_{tid}")])
    kb.append([InlineKeyboardButton("حذف", callback_data=f"tmpl_del_{tid}")])
    kb.append([InlineKeyboardButton("بازگشت", callback_data="menu_templates")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def tmpl_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    tid = int(q.data.split('_')[2])
    db_update_setting(q.from_user.id, current_template_id=tid)
    await q.answer("انتخاب شد")
    await tmpl_view(update, context)


async def tmpl_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    tid = int(q.data.split('_')[2])
    db_delete_template(tid)
    settings = db_get_settings(q.from_user.id)
    if settings and settings[2] == tid:
        db_update_setting(q.from_user.id, current_template_id=None)
    await q.answer("حذف شد")
    await menu_templates(update, context)


# ==================== گرفتن پروفایل ====================
async def menu_get_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    settings = db_get_settings(q.from_user.id)
    if not settings or not settings[1]:
        await q.edit_message_text(
            "اول یه اکانت انتخاب کن.\n\n"
            "از منوی اکانت‌ها برو و اکانت فعال رو انتخاب کن.",
            reply_markup=back_kb("menu_main")
        )
        return

    a = db_get_account(settings[1])
    t_text = "انتخاب نشده"
    if settings[2]:
        t = db_get_template(settings[2])
        if t:
            t_text = t[2]

    ch_text = settings[4] if settings[3] else "متصل نشده"

    await q.edit_message_text(
        "گرفتن پروفایل\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"اکانت: {a[6]}\n"
        f"قالب: {t_text}\n"
        f"کانال: {ch_text}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "شناسه طرف رو بفرست:\n\n"
        "- آیدی عددی:  123456789\n"
        "- یوزرنیم:  @username  یا  username\n\n"
        "فقط عکس‌های چهره انسانی فیلتر و ارسال می‌شن.",
        reply_markup=back_kb("menu_main")
    )
    return WAIT_TARGET_INPUT


async def handle_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    raw = update.message.text.strip()
    owner_id = update.effective_user.id

    settings = db_get_settings(owner_id)
    if not settings or not settings[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = db_get_account(settings[1])
    if not a:
        await update.message.reply_text("اکانت پیدا نشد.")
        return ConversationHandler.END

    status_msg = await update.message.reply_text(
        f"در حال پردازش {raw}...\n\n"
        f"اکانت: {a[6]}"
    )

    asyncio.create_task(process_single_target(
        bot=context.bot,
        owner_id=owner_id,
        account_row=a,
        target_raw=raw,
        template_id=settings[2],
        channel_id=settings[3],
        status_message_id=status_msg.message_id,
        chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def process_single_target(bot, owner_id, account_row, target_raw,
                                template_id, channel_id, status_message_id, chat_id):
    """پردازش یه آیدی/یوزرنیم"""
    client = None
    try:
        acc_id, _, phone, api_id, api_hash, session_str, acc_name, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text(
                "سشن منقضی شده. اکانت رو دوباره اضافه کن.",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        target = await resolve_entity(client, target_raw)
        if not target:
            await bot.edit_message_text(
                f"کاربر {target_raw} پیدا نشد.",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        await process_user_profile(
            bot, client, owner_id, target,
            template_id, channel_id,
            status_message_id, chat_id
        )

    except Exception as e:
        print(f"error: {e}")
        try:
            await bot.edit_message_text(
                f"خطا: {str(e)[:200]}",
                chat_id=chat_id, message_id=status_message_id
            )
        except:
            pass
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def resolve_entity(client, raw):
    """تبدیل ورودی به entity"""
    raw = raw.strip()
    try:
        if raw.startswith('@'):
            raw = raw[1:]

        if raw.isdigit():
            return await client.get_entity(int(raw))

        # یوزرنیم
        return await client.get_entity(raw)
    except Exception as e:
        print(f"resolve error: {e}")
        return None


async def process_user_profile(bot, client, owner_id, target,
                               template_id, channel_id,
                               status_message_id, chat_id):
    """گرفتن پروفایل‌های کاربر، فیلتر چهره، ارسال"""
    target_id = target.id

    # اسم‌ها
    first_name = target.first_name or ""
    last_name = target.last_name or ""
    full_name = (first_name + " " + last_name).strip() or "بدون نام"
    username = target.username or ""
    user_id_str = str(target.id)

    # لیست پروفایل‌ها
    try:
        photos = await client(GetUserPhotosRequest(
            user_id=target_id, offset=0, max_id=0, limit=200
        ))
        photo_list = photos.photos if hasattr(photos, 'photos') else []
    except Exception as e:
        await bot.edit_message_text(
            f"خطا در دریافت پروفایل‌ها: {str(e)[:150]}",
            chat_id=chat_id, message_id=status_message_id
        )
        return

    total = len(photo_list)

    if total == 0:
        await bot.edit_message_text(
            f"کاربر {full_name} هیچ پروفایلی نداره.",
            chat_id=chat_id, message_id=status_message_id
        )
        return

    await bot.edit_message_text(
        f"در حال پردازش پروفایل‌ها...\n\n"
        f"کاربر: {full_name}\n"
        f"تعداد کل: {total}\n"
        f"در حال تشخیص چهره...",
        chat_id=chat_id, message_id=status_message_id
    )

    # فیلتر
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
                        f"در حال پردازش...\n\n"
                        f"کاربر: {full_name}\n"
                        f"پیشرفت: {i+1}/{total}\n"
                        f"چهره‌های انسانی پیدا شده: {len(human_faces)}",
                        chat_id=chat_id, message_id=status_message_id
                    )
                except:
                    pass

        except Exception as e:
            print(f"error in photo {i}: {e}")
            continue

    # متن قالب
    footer = ""
    if template_id:
        t = db_get_template(template_id)
        if t:
            footer = t[3]
            footer = footer.replace("{name}", full_name)
            footer = footer.replace("{username}", username or "")
            footer = footer.replace("{first_name}", first_name or "")
            footer = footer.replace("{last_name}", last_name or "")
            footer = footer.replace("{id}", user_id_str)

    # ارسال
    await bot.edit_message_text(
        f"ارسال...\n\n"
        f"کاربر: {full_name}\n"
        f"چهره‌های انسانی: {len(human_faces)}\n"
        f"در حال ارسال...",
        chat_id=chat_id, message_id=status_message_id
    )

    sent_to_owner = 0
    sent_to_channel = 0

    for idx, data in enumerate(human_faces):
        caption = footer if idx == 0 else None

        # به خودت
        try:
            await bot.send_photo(
                chat_id=owner_id,
                photo=data,
                caption=caption
            )
            sent_to_owner += 1
        except Exception as e:
            print(f"send to owner: {e}")

        # به کانال
        if channel_id:
            try:
                await bot.send_photo(
                    chat_id=channel_id,
                    photo=data,
                    caption=caption
                )
                sent_to_channel += 1
            except Exception as e:
                print(f"send to channel: {e}")

        await asyncio.sleep(0.3)

    final_text = (
        f"تکمیل شد\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"کاربر: {full_name}\n"
        f"یوزرنیم: @{username or 'ندارد'}\n"
        f"شناسه: {user_id_str}\n\n"
        f"کل پروفایل‌ها: {total}\n"
        f"چهره‌های ارسال شده: {sent_to_owner}\n"
        f"فیلتر شده (فیک/منظره): {total - sent_to_owner}\n"
    )
    if channel_id:
        final_text += f"ارسال به کانال: {sent_to_channel}"

    await bot.edit_message_text(
        final_text,
        chat_id=chat_id, message_id=status_message_id,
        reply_markup=back_kb("menu_main")
    )


# ==================== چند آیدی همزمان ====================
async def menu_multi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    settings = db_get_settings(q.from_user.id)
    if not settings or not settings[1]:
        await q.edit_message_text(
            "اول یه اکانت انتخاب کن.",
            reply_markup=back_kb("menu_main")
        )
        return

    await q.edit_message_text(
        "چند آیدی همزمان\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "شناسه‌ها رو یکی در هر خط بفرست:\n\n"
        "123456789\n"
        "@username1\n"
        "@username2\n"
        "987654321\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb("menu_main")
    )
    return WAIT_MULTI_IDS


async def handle_multi_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    raw = update.message.text.strip()
    lines = [l.strip() for l in raw.split('\n') if l.strip()]

    if not lines:
        await update.message.reply_text("چیزی نفرستادی. دوباره.")
        return WAIT_MULTI_IDS

    owner_id = update.effective_user.id
    settings = db_get_settings(owner_id)
    if not settings or not settings[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = db_get_account(settings[1])

    status_msg = await update.message.reply_text(
        f"در حال پردازش {len(lines)} شناسه...\n\n"
        f"اکانت: {a[6]}"
    )

    asyncio.create_task(process_multi_targets(
        bot=context.bot,
        owner_id=owner_id,
        account_row=a,
        targets=lines,
        template_id=settings[2],
        channel_id=settings[3],
        status_message_id=status_msg.message_id,
        chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def process_multi_targets(bot, owner_id, account_row, targets,
                                template_id, channel_id,
                                status_message_id, chat_id):
    client = None
    try:
        acc_id, _, phone, api_id, api_hash, session_str, acc_name, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text(
                "سشن منقضی شده.",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        total = len(targets)
        success = 0
        failed = 0

        for idx, raw in enumerate(targets, 1):
            try:
                await bot.edit_message_text(
                    f"در حال پردازش {idx}/{total}...\n\n"
                    f"شناسه فعلی: {raw}\n"
                    f"موفق: {success}  |  ناموفق: {failed}",
                    chat_id=chat_id, message_id=status_message_id
                )
            except:
                pass

            target = await resolve_entity(client, raw)
            if not target:
                failed += 1
                continue

            try:
                await process_user_profile_silent(
                    bot, client, owner_id, target,
                    template_id, channel_id
                )
                success += 1
            except Exception as e:
                print(f"error for {raw}: {e}")
                failed += 1

            await asyncio.sleep(1)

        await bot.edit_message_text(
            f"تکمیل شد\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"کل: {total}\n"
            f"موفق: {success}\n"
            f"ناموفق: {failed}",
            chat_id=chat_id, message_id=status_message_id,
            reply_markup=back_kb("menu_main")
        )

    except Exception as e:
        print(f"multi error: {e}")
        try:
            await bot.edit_message_text(
                f"خطا: {str(e)[:200]}",
                chat_id=chat_id, message_id=status_message_id
            )
        except:
            pass
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def process_user_profile_silent(bot, client, owner_id, target,
                                      template_id, channel_id):
    """نسخه بدون پیام وضعیت برای multi"""
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
    except:
        return

    if not photo_list:
        return

    footer = ""
    if template_id:
        t = db_get_template(template_id)
        if t:
            footer = t[3]
            footer = footer.replace("{name}", full_name)
            footer = footer.replace("{username}", username or "")
            footer = footer.replace("{first_name}", first_name or "")
            footer = footer.replace("{last_name}", last_name or "")
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


# ==================== گروه/کانال ====================
async def menu_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    settings = db_get_settings(q.from_user.id)
    if not settings or not settings[1]:
        await q.edit_message_text(
            "اول یه اکانت انتخاب کن.",
            reply_markup=back_kb("menu_main")
        )
        return

    await q.edit_message_text(
        "گروه یا کانال\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "لینک گروه یا کانال رو بفرست:\n\n"
        "- https://t.me/groupname\n"
        "- https://t.me/+AbCdEf123\n"
        "- @groupname\n\n"
        "همه اعضا چک می‌شن و پروفایل‌های چهره ارسال می‌شن.\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb("menu_main")
    )
    return WAIT_GROUP_LINK


async def handle_group_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    link = update.message.text.strip()
    owner_id = update.effective_user.id

    settings = db_get_settings(owner_id)
    if not settings or not settings[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = db_get_account(settings[1])

    status_msg = await update.message.reply_text(
        f"در حال پردازش گروه...\n\n"
        f"لینک: {link}\n"
        f"اکانت: {a[6]}"
    )

    asyncio.create_task(process_group(
        bot=context.bot,
        owner_id=owner_id,
        account_row=a,
        link=link,
        template_id=settings[2],
        channel_id=settings[3],
        status_message_id=status_msg.message_id,
        chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def process_group(bot, owner_id, account_row, link,
                        template_id, channel_id,
                        status_message_id, chat_id):
    client = None
    try:
        acc_id, _, phone, api_id, api_hash, session_str, acc_name, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text(
                "سشن منقضی شده.",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        # پیدا کردن گروه
        entity = None
        try:
            if 'joinchat' in link or '+Ab' in link or '+aB' in link:
                # لینک دعوت خصوصی
                hash_part = link.split('/')[-1]
                if hash_part.startswith('+'):
                    hash_part = hash_part[1:]
                try:
                    from telethon.tl.functions.messages import ImportChatInviteRequest
                    result = await client(ImportChatInviteRequest(hash_part))
                    entity = result.chats[0]
                except Exception as e:
                    # شاید قبلاً join کرده
                    from telethon.tl.functions.messages import CheckChatInviteRequest
                    try:
                        result = await client(CheckChatInviteRequest(hash_part))
                        entity = result.chat
                    except:
                        raise e
            else:
                # یوزرنیم عمومی
                name = link.split('/')[-1]
                if name.startswith('@'):
                    name = name[1:]
                entity = await client.get_entity(name)
        except Exception as e:
            await bot.edit_message_text(
                f"گروه پیدا نشد یا دسترسی ندارم.\n\n{str(e)[:200]}",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        # اسم گروه
        group_title = getattr(entity, 'title', 'بدون نام')

        await bot.edit_message_text(
            f"گروه پیدا شد: {group_title}\n\n"
            f"در حال دریافت اعضا...",
            chat_id=chat_id, message_id=status_message_id
        )

        # اعضا
        participants = []
        try:
            offset = 0
            limit = 100
            while True:
                result = await client(GetParticipantsRequest(
                    channel=entity,
                    filter=ChannelParticipantsSearch(''),
                    offset=offset,
                    limit=limit,
                    hash=0
                ))
                if not result.users:
                    break
                participants.extend(result.users)
                offset += len(result.users)
                if len(result.users) < limit:
                    break
                if len(participants) >= 500:
                    break
                await asyncio.sleep(0.5)
        except Exception as e:
            await bot.edit_message_text(
                f"خطا در دریافت اعضا: {str(e)[:200]}",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        total = len(participants)
        if total == 0:
            await bot.edit_message_text(
                "هیچ عضوی پیدا نشد.",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        await bot.edit_message_text(
            f"اعضا: {total}\n\n"
            f"در حال پردازش پروفایل‌ها...",
            chat_id=chat_id, message_id=status_message_id
        )

        success = 0
        for idx, user in enumerate(participants, 1):
            if user.bot or user.deleted:
                continue

            try:
                await bot.edit_message_text(
                    f"در حال پردازش {idx}/{total}...\n\n"
                    f"موفق: {success}",
                    chat_id=chat_id, message_id=status_message_id
                )
            except:
                pass

            try:
                await process_user_profile_silent(
                    bot, client, owner_id, user,
                    template_id, channel_id
                )
                success += 1
            except Exception as e:
                print(f"error {user.id}: {e}")

            await asyncio.sleep(0.5)

        await bot.edit_message_text(
            f"تکمیل شد\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"گروه: {group_title}\n"
            f"کل اعضا: {total}\n"
            f"پردازش شده: {success}",
            chat_id=chat_id, message_id=status_message_id,
            reply_markup=back_kb("menu_main")
        )

    except Exception as e:
        print(f"group error: {e}")
        try:
            await bot.edit_message_text(
                f"خطا: {str(e)[:200]}",
                chat_id=chat_id, message_id=status_message_id
            )
        except:
            pass
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


# ==================== اتصال به کانال ====================
async def menu_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    settings = db_get_settings(q.from_user.id)
    current = settings[3] if settings and settings[3] else None
    title = settings[4] if settings and settings[4] else "متصل نشده"

    text = (
        "اتصال به کانال\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"کانال فعلی: {title}\n\n"
        "پروفایل‌ها همزمان به این کانال هم ارسال می‌شن.\n\n"
        "برای اتصال:\n"
        "1. ربات رو به کانال اضافه کن\n"
        "2. توی کانال یه پیام بفرست\n"
        "3. اون پیام رو برای ربات فوروارد کن\n\n"
        "یا اگه کانال عمومیه، یوزرنیمش رو بفرست:\n"
        "@channel_username\n\n"
        "برای انصراف /cancel بزن."
    )

    kb = []
    if current:
        kb.append([InlineKeyboardButton("قطع اتصال", callback_data="channel_disconnect")])
    kb.append([InlineKeyboardButton("بازگشت", callback_data="menu_main")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return WAIT_CHANNEL_LINK


async def handle_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    owner_id = update.effective_user.id

    # اگه فوروارد شده
    if update.message.forward_from_chat:
        chat = update.message.forward_from_chat
        db_update_setting(owner_id, channel_id=str(chat.id), channel_title=chat.title or "کانال")
        await update.message.reply_text(
            f"کانال متصل شد.\n\n"
            f"نام: {chat.title}\n"
            f"شناسه: {chat.id}",
            reply_markup=back_kb("menu_main")
        )
        return ConversationHandler.END

    # اگه یوزرنیم
    text = update.message.text.strip() if update.message.text else ""
    if text.startswith('@') or text.startswith('https://t.me/'):
        name = text.split('/')[-1]
        if name.startswith('@'):
            name = name[1:]

        settings = db_get_settings(owner_id)
        if not settings or not settings[1]:
            await update.message.reply_text("اول یه اکانت انتخاب کن.")
            return ConversationHandler.END

        a = db_get_account(settings[1])
        client = None
        try:
            client = TelegramClient(StringSession(a[5]), a[3], a[4])
            await client.connect()
            entity = await client.get_entity(name)
            db_update_setting(
                owner_id,
                channel_id=str(entity.id),
                channel_title=getattr(entity, 'title', name)
            )
            await update.message.reply_text(
                f"کانال متصل شد.\n\n"
                f"نام: {getattr(entity, 'title', name)}\n"
                f"شناسه: {entity.id}",
                reply_markup=back_kb("menu_main")
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

    await update.message.reply_text(
        "ورودی نامعتبر. یه پیام از کانال فوروارد کن یا یوزرنیم بفرست."
    )
    return WAIT_CHANNEL_LINK


async def channel_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    db_update_setting(q.from_user.id, channel_id=None, channel_title=None)
    await menu_main(update, context)


# ==================== تنظیمات ====================
async def menu_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    kb = [
        [InlineKeyboardButton("بازگشت", callback_data="menu_main")]
    ]
    await q.edit_message_text(
        "تنظیمات\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "این بخش بعداً کامل‌تر می‌شه.",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ==================== Cancel ====================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("لغو شد.", reply_markup=back_kb("menu_main"))
    return ConversationHandler.END


# ==================== MAIN ====================
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_NEW_BOT_TOKEN_HERE":
        print("توکن ربات تنظیم نشده.")
        return

    app = Application.builder().token(TOKEN).build()

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

    # گرفتن پروفایل تک
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

    print("ربات خصوصی در حال اجراست...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
