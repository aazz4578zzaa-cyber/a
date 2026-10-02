import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime
from io import BytesIO

import cv2
import numpy as np
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.types import User

# ==================== تنظیمات ====================
TOKEN = os.environ.get("TOKEN", "8816493813:AAHSSd5Xz1i4jCbZ-jW9QrW8QcRAZi41BzQ")
MY_USER_ID = int(os.environ.get("MY_USER_ID", "7803165903"))  # آیدی عددی خودت رو اینجا بذار

DB_FILE = "private_bot.db"

# Conversation states
(
    WAIT_PHONE, WAIT_API_ID, WAIT_API_HASH,
    WAIT_CODE, WAIT_PASSWORD,
    WAIT_TARGET_ID, WAIT_TEMPLATE_TEXT,
    WAIT_NEW_TEMPLATE
) = range(8)


# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # اکانت‌ها
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

    # قالب‌های متن
    c.execute('''
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            title TEXT,
            text TEXT,
            created_date TEXT
        )
    ''')

    # تنظیمات کاربر
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            owner_id INTEGER PRIMARY KEY,
            current_account_id INTEGER,
            current_template_id INTEGER,
            last_target_id INTEGER,
            last_target_username TEXT
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
    db_get_settings(owner_id)  # اطمینان از وجود
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for key, val in kwargs.items():
        c.execute(f'UPDATE settings SET {key} = ? WHERE owner_id = ?', (val, owner_id))
    conn.commit()
    conn.close()


# ==================== متن‌ها ====================
TEXTS = {
    'welcome': """◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **پنل مدیریت خصوصی** ✦
◈━━━━━━━━━━━━━━━━━━━◈

⬢ خوش آمدی رفیق.

◆ این ربات مخصوص خودته.
▸ کسی جز تو نمی‌تونه ازش استفاده کنه.

━━━━━━━━━━━━━━━━━━━
▸ از دکمه‌های زیر استفاده کن:
""",

    'main_menu': """◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **منوی اصلی** ✦
◈━━━━━━━━━━━━━━━━━━━◈

◆ وضعیت:
{status}

━━━━━━━━━━━━━━━━━━━
▸ یه گزینه انتخاب کن:
""",

    'accounts_menu': """◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **مدیریت اکانت‌ها** ✦
◈━━━━━━━━━━━━━━━━━━━◈

{accounts_list}

━━━━━━━━━━━━━━━━━━━
""",

    'templates_menu': """◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **قالب‌های متن** ✦
◈━━━━━━━━━━━━━━━━━━━◈

{templates_list}

━━━━━━━━━━━━━━━━━━━
◆ این متن‌ها زیر پروفایل‌ها ارسال می‌شن.
""",

    'get_profile_menu': """◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **گرفتن پروفایل** ✦
◈━━━━━━━━━━━━━━━━━━━◈

⬢ اکانت فعلی: {account}

◆ قالب فعال: {template}

━━━━━━━━━━━━━━━━━━━
▸ آیدی عددی طرف رو بفرست:
▸ مثال: `123456789`

⚠ فقط عکس‌های **چهره انسانی** فیلتر و ارسال می‌شن.
""",

    'settings_menu': """◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **تنظیمات** ✦
◈━━━━━━━━━━━━━━━━━━━◈

⬢ اینجا می‌تونی تنظیمات ربات رو مدیریت کنی.

━━━━━━━━━━━━━━━━━━━
""",
}


def status_text(owner_id):
    acc = db_get_settings(owner_id)
    current_account = "❌ انتخاب نشده"
    current_template = "❌ انتخاب نشده"
    if acc and acc[1]:
        a = db_get_account(acc[1])
        if a:
            current_account = f"◆ {a[6]} (@{a[7] or 'بدون یوزرنیم'})"
    if acc and acc[2]:
        t = db_get_template(acc[2])
        if t:
            current_template = f"◆ {t[2]}"
    return f"▸ اکانت: {current_account}\n▸ قالب: {current_template}"


def is_owner(user_id):
    return user_id == MY_USER_ID


# ==================== کیبوردها ====================
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬢ اکانت‌ها", callback_data="menu_accounts"),
         InlineKeyboardButton("✦ قالب‌ها", callback_data="menu_templates")],
        [InlineKeyboardButton("◆ گرفتن پروفایل", callback_data="menu_get_profile")],
        [InlineKeyboardButton("⚙ تنظیمات", callback_data="menu_settings")],
    ])


def back_kb(target="main_menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◂ بازگشت", callback_data=target)]])


# ==================== هندلرها ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # فقط مالک
    if not is_owner(user_id):
        # سکوت کامل — هیچ جوابی نمی‌ده
        return

    await update.message.reply_text(
        TEXTS['welcome'],
        reply_markup=main_menu_kb(),
        parse_mode='Markdown'
    )


async def menu_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔", show_alert=False)
        return
    await q.answer()
    await q.edit_message_text(
        TEXTS['main_menu'].format(status=status_text(q.from_user.id)),
        reply_markup=main_menu_kb(),
        parse_mode='Markdown'
    )


# ================ اکانت‌ها ================
async def menu_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔", show_alert=False)
        return
    await q.answer()
    owner_id = q.from_user.id
    accounts = db_get_accounts(owner_id)

    if accounts:
        lines = []
        for a in accounts:
            lines.append(f"◆ **{a[6]}** — @{a[7] or 'بی‌یوزرنیم'}\n   📱 `{a[2]}`  |  ID: `{a[0]}`")
        acc_list = "\n\n".join(lines)
    else:
        acc_list = "◂ هنوز اکانتی اضافه نکردی."

    kb = []
    for a in accounts:
        kb.append([InlineKeyboardButton(
            f"◆ {a[6]} | {a[2][-4:]}",
            callback_data=f"acc_view_{a[0]}"
        )])
    kb.append([InlineKeyboardButton("➕ افزودن اکانت جدید", callback_data="acc_add")])
    kb.append([InlineKeyboardButton("◂ بازگشت", callback_data="menu_main")])

    await q.edit_message_text(
        TEXTS['accounts_menu'].format(accounts_list=acc_list),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )


async def acc_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    await q.answer()
    await q.edit_message_text(
        """◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **افزودن اکانت** ✦
◈━━━━━━━━━━━━━━━━━━━◈

◆ مرحله 1 از 5

▸ شماره موبایل اکانت رو با کد کشور بفرست:
▸ مثال: `+989123456789`

⚠ برای انصراف /cancel بزن.""",
        reply_markup=back_kb("menu_accounts"),
        parse_mode='Markdown'
    )
    return WAIT_PHONE


async def acc_wait_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    phone = update.message.text.strip()
    if not re.match(r'^\+?[0-9]{10,15}$', phone):
        await update.message.reply_text("❌ شماره نامعتبر! دوباره بفرست یا /cancel")
        return WAIT_PHONE
    context.user_data['new_acc_phone'] = phone
    await update.message.reply_text(
        f"""◈━━━━━━━━━━━━━━━━━━━◈

◆ مرحله 2 از 5

▸ شماره: `{phone}`

▸ حالا **API ID** رو بفرست:
▸ (عددیه، از my.telegram.org)""",
        parse_mode='Markdown'
    )
    return WAIT_API_ID


async def acc_wait_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("❌ API ID باید عدد باشه! دوباره بفرست.")
        return WAIT_API_ID
    context.user_data['new_acc_api_id'] = int(txt)
    await update.message.reply_text(
        "◆ مرحله 3 از 5\n\n▸ حالا **API Hash** رو بفرست:",
        parse_mode='Markdown'
    )
    return WAIT_API_HASH


async def acc_wait_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    api_hash = update.message.text.strip()
    if len(api_hash) < 20:
        await update.message.reply_text("❌ API Hash نامعتبر! دوباره بفرست.")
        return WAIT_API_HASH

    context.user_data['new_acc_api_hash'] = api_hash
    phone = context.user_data['new_acc_phone']
    api_id = context.user_data['new_acc_api_id']

    msg = await update.message.reply_text("⏳ در حال ارسال کد تایید...")

    try:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        context.user_data['new_acc_client'] = client
        context.user_data['new_acc_phone_code_hash'] = sent.phone_code_hash

        await msg.edit_text(
            f"""◈━━━━━━━━━━━━━━━━━━━◈

◆ مرحله 4 از 5

▸ کد تایید به شماره `{phone}` ارسال شد.

▸ کد رو به این صورت بفرست:
▸ `1.2.3.4.5` یا `12345`""",
            parse_mode='Markdown'
        )
        return WAIT_CODE
    except PhoneNumberInvalidError:
        await msg.edit_text("❌ شماره نامعتبره! /cancel بزن و دوباره شروع کن.")
        return ConversationHandler.END
    except FloodWaitError as e:
        await msg.edit_text(f"⏳ FloodWait: {e.seconds} ثانیه صبر کن و دوباره.")
        return ConversationHandler.END
    except Exception as e:
        await msg.edit_text(f"❌ خطا: `{str(e)[:200]}`\n\n/cancel بزن.", parse_mode='Markdown')
        return ConversationHandler.END


async def acc_wait_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip()
    code = raw.replace('.', '').replace(' ', '').strip()
    if not code.isdigit():
        await update.message.reply_text("❌ کد باید عدد باشه! دوباره بفرست.")
        return WAIT_CODE

    client = context.user_data.get('new_acc_client')
    phone = context.user_data['new_acc_phone']
    phone_code_hash = context.user_data.get('new_acc_phone_code_hash')

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        # موفق
        return await finalize_account(update, context, client)
    except SessionPasswordNeededError:
        await update.message.reply_text(
            "◆ مرحله 5 از 5\n\n▸ اکانت دو مرحله‌ای داره.\n▸ پسوردت رو بفرست:"
        )
        return WAIT_PASSWORD
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ کد اشتباهه! دوباره بفرست.")
        return WAIT_CODE
    except PhoneCodeExpiredError:
        await update.message.reply_text("❌ کد منقضی شده. /cancel بزن و دوباره شروع کن.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: `{str(e)[:200]}`", parse_mode='Markdown')
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
        await update.message.reply_text(f"❌ خطا: `{str(e)[:200]}`", parse_mode='Markdown')
        return ConversationHandler.END


async def finalize_account(update, context, client):
    """ذخیره اکانت بعد از login موفق"""
    try:
        me = await client.get_me()
        session_str = client.session.save()
        phone = context.user_data['new_acc_phone']
        api_id = context.user_data['new_acc_api_id']
        api_hash = context.user_data['new_acc_api_hash']

        acc_name = me.first_name or "بدون نام"
        username = me.username or ""

        acc_id = db_add_account(
            MY_USER_ID, phone, api_id, api_hash,
            session_str, acc_name, username
        )

        await client.disconnect()

        await update.message.reply_text(
            f"""◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **اکانت اضافه شد** ✦
◈━━━━━━━━━━━━━━━━━━━◈

⬢ نام: **{acc_name}**
◆ یوزرنیم: @{username or 'ندارد'}
▸ شماره: `{phone}`
▸ ID داخلی: `{acc_id}`

━━━━━━━━━━━━━━━━━━━
▸ از منوی اکانت‌ها می‌تونی انتخابش کنی.""",
            reply_markup=back_kb("menu_accounts"),
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ذخیره: `{str(e)[:200]}`", parse_mode='Markdown')

    for k in ['new_acc_phone', 'new_acc_api_id', 'new_acc_api_hash',
              'new_acc_client', 'new_acc_phone_code_hash']:
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def acc_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    await q.answer()
    acc_id = int(q.data.split('_')[2])
    a = db_get_account(acc_id)
    if not a:
        await q.answer("❌ پیدا نشد", show_alert=True)
        return

    settings = db_get_settings(q.from_user.id)
    is_current = settings and settings[1] == acc_id

    text = f"""◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **اطلاعات اکانت** ✦
◈━━━━━━━━━━━━━━━━━━━◈

⬢ نام: **{a[6]}**
◆ یوزرنیم: @{a[7] or 'ندارد'}
▸ شماره: `{a[2]}`
▸ API ID: `{a[3]}`
▸ تاریخ افزودن: `{a[8][:10]}`

━━━━━━━━━━━━━━━━━━━
{'✅ این اکانت الان فعاله' if is_current else '◂ این اکانت فعال نیست'}
"""
    kb = []
    if not is_current:
        kb.append([InlineKeyboardButton("✅ انتخاب به عنوان فعال", callback_data=f"acc_use_{acc_id}")])
    kb.append([InlineKeyboardButton("🗑 حذف", callback_data=f"acc_del_{acc_id}")])
    kb.append([InlineKeyboardButton("◂ بازگشت", callback_data="menu_accounts")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


async def acc_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    acc_id = int(q.data.split('_')[2])
    db_update_setting(q.from_user.id, current_account_id=acc_id)
    await q.answer("✅ انتخاب شد!", show_alert=False)
    await acc_view(update, context)


async def acc_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    acc_id = int(q.data.split('_')[2])
    db_delete_account(acc_id)
    settings = db_get_settings(q.from_user.id)
    if settings and settings[1] == acc_id:
        db_update_setting(q.from_user.id, current_account_id=None)
    await q.answer("🗑 حذف شد", show_alert=False)
    await menu_accounts(update, context)


# ================ قالب‌ها ================
async def menu_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    await q.answer()
    owner_id = q.from_user.id
    templates = db_get_templates(owner_id)

    if templates:
        lines = []
        for t in templates:
            preview = t[3][:50].replace('\n', ' ')
            lines.append(f"◆ **{t[2]}**\n   `{preview}...`")
        tmpl_list = "\n\n".join(lines)
    else:
        tmpl_list = "◂ هنوز قالبی نساختی."

    kb = []
    for t in templates:
        kb.append([InlineKeyboardButton(f"◆ {t[2]}", callback_data=f"tmpl_view_{t[0]}")])
    kb.append([InlineKeyboardButton("➕ قالب جدید", callback_data="tmpl_add")])
    kb.append([InlineKeyboardButton("◂ بازگشت", callback_data="menu_main")])

    await q.edit_message_text(
        TEXTS['templates_menu'].format(templates_list=tmpl_list),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )


async def tmpl_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    await q.answer()
    await q.edit_message_text(
        """◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **قالب جدید** ✦
◈━━━━━━━━━━━━━━━━━━━◈

▸ عنوان قالب رو بفرست (مثلاً: «چهره‌های زیبا»):

⚠ /cancel برای انصراف""",
        reply_markup=back_kb("menu_templates"),
        parse_mode='Markdown'
    )
    return WAIT_NEW_TEMPLATE


async def tmpl_wait_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['new_tmpl_title'] = update.message.text.strip()
    await update.message.reply_text(
        "◆ حالا متن قالب رو بفرست:\n\n"
        "▸ این متن زیر پروفایل‌ها ارسال می‌شه.\n"
        "▸ می‌تونی از `{name}` و `{username}` استفاده کنی."
    )
    return WAIT_TEMPLATE_TEXT


async def tmpl_wait_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text
    title = context.user_data.pop('new_tmpl_title', 'بدون عنوان')
    tid = db_add_template(MY_USER_ID, title, text)
    await update.message.reply_text(
        f"✅ قالب **{title}** ذخیره شد! (ID: `{tid}`)",
        reply_markup=back_kb("menu_templates"),
        parse_mode='Markdown'
    )
    return ConversationHandler.END


async def tmpl_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    await q.answer()
    tid = int(q.data.split('_')[2])
    t = db_get_template(tid)
    if not t:
        await q.answer("❌", show_alert=True)
        return

    settings = db_get_settings(q.from_user.id)
    is_current = settings and settings[2] == tid

    text = f"""◈━━━━━━━━━━━━━━━━━━━◈
     ✦ **قالب** ✦
◈━━━━━━━━━━━━━━━━━━━◈

◆ عنوان: **{t[2]}**

▸ متن:
`{t[3]}`

━━━━━━━━━━━━━━━━━━━
{'✅ این قالب فعاله' if is_current else '◂ این قالب فعال نیست'}"""

    kb = []
    if not is_current:
        kb.append([InlineKeyboardButton("✅ انتخاب به عنوان فعال", callback_data=f"tmpl_use_{tid}")])
    kb.append([InlineKeyboardButton("🗑 حذف", callback_data=f"tmpl_del_{tid}")])
    kb.append([InlineKeyboardButton("◂ بازگشت", callback_data="menu_templates")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


async def tmpl_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    tid = int(q.data.split('_')[2])
    db_update_setting(q.from_user.id, current_template_id=tid)
    await q.answer("✅", show_alert=False)
    await tmpl_view(update, context)


async def tmpl_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    tid = int(q.data.split('_')[2])
    db_delete_template(tid)
    settings = db_get_settings(q.from_user.id)
    if settings and settings[2] == tid:
        db_update_setting(q.from_user.id, current_template_id=None)
    await q.answer("🗑 حذف شد", show_alert=False)
    await menu_templates(update, context)


# ================ گرفتن پروفایل ================
async def menu_get_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    await q.answer()

    settings = db_get_settings(q.from_user.id)
    if not settings or not settings[1]:
        await q.edit_message_text(
            "❌ اول یه اکانت انتخاب کن!\n\nاز منوی اکانت‌ها برو و اکانت فعال رو انتخاب کن.",
            reply_markup=back_kb("menu_main"),
            parse_mode='Markdown'
        )
        return

    a = db_get_account(settings[1])
    t_text = "❌ انتخاب نشده"
    if settings[2]:
        t = db_get_template(settings[2])
        if t:
            t_text = t[2]

    await q.edit_message_text(
        TEXTS['get_profile_menu'].format(
            account=f"{a[6]} (`{a[2]}`)",
            template=t_text
        ),
        reply_markup=back_kb("menu_main"),
        parse_mode='Markdown'
    )
    return WAIT_TARGET_ID


async def handle_target_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("❌ آیدی باید عدد باشه! دوباره بفرست.")
        return WAIT_TARGET_ID

    target_id = int(txt)
    owner_id = update.effective_user.id

    settings = db_get_settings(owner_id)
    if not settings or not settings[1]:
        await update.message.reply_text("❌ اکانت فعال نداری!", parse_mode='Markdown')
        return ConversationHandler.END

    a = db_get_account(settings[1])
    if not a:
        await update.message.reply_text("❌ اکانت پیدا نشد!")
        return ConversationHandler.END

    status_msg = await update.message.reply_text(
        f"⏳ در حال دریافت پروفایل‌های `{target_id}`...\n\n▸ اکانت: **{a[6]}**",
        parse_mode='Markdown'
    )

    # اجرای تسک جداگانه
    asyncio.create_task(
        process_target_profile(
            bot=context.bot,
            owner_id=owner_id,
            account_row=a,
            target_id=target_id,
            template_id=settings[2],
            status_message_id=status_msg.message_id,
            chat_id=update.effective_chat.id
        )
    )
    return ConversationHandler.END


async def process_target_profile(bot, owner_id, account_row, target_id, template_id, status_message_id, chat_id):
    """دریافت پروفایل‌ها، فیلتر چهره، ارسال"""
    client = None
    try:
        acc_id, _, phone, api_id, api_hash, session_str, acc_name, _, _, _ = account_row

        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await bot.edit_message_text(
                "❌ سشن منقضی شده! اکانت رو دوباره اضافه کن.",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        # اطلاعات کاربر هدف
        try:
            target = await client.get_entity(target_id)
        except Exception as e:
            await bot.edit_message_text(
                f"❌ کاربر `{target_id}` پیدا نشد یا دسترسی ندارم.\n\n`{str(e)[:150]}`",
                chat_id=chat_id, message_id=status_message_id,
                parse_mode='Markdown'
            )
            return

        target_name = (target.first_name or "") + " " + (target.last_name or "")
        target_name = target_name.strip() or target.username or str(target_id)

        # لیست پروفایل‌ها
        try:
            photos = await client(GetUserPhotosRequest(
                user_id=target_id, offset=0, max_id=0, limit=200
            ))
            photo_list = photos.photos if hasattr(photos, 'photos') else []
        except Exception as e:
            await bot.edit_message_text(
                f"❌ خطا در دریافت پروفایل‌ها: `{str(e)[:150]}`",
                chat_id=chat_id, message_id=status_message_id, parse_mode='Markdown'
            )
            return

        total = len(photo_list)

        if total == 0:
            await bot.edit_message_text(
                f"◂ کاربر **{target_name}** هیچ پروفایلی نداره.",
                chat_id=chat_id, message_id=status_message_id, parse_mode='Markdown'
            )
            return

        await bot.edit_message_text(
            f"⬢ **در حال پردازش پروفایل‌ها**\n\n"
            f"◆ کاربر: **{target_name}**\n"
            f"◆ تعداد کل: `{total}`\n"
            f"◆ در حال تشخیص چهره...\n\n"
            f"⏳ صبر کن...",
            chat_id=chat_id, message_id=status_message_id, parse_mode='Markdown'
        )

        # دانلود و فیلتر
        human_faces = []
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )

        for i, photo in enumerate(photo_list):
            try:
                # دانلود به حافظه
                buf = BytesIO()
                await client.download_media(photo, buf)
                buf.seek(0)
                data = buf.getvalue()

                # تبدیل به OpenCV
                arr = np.frombuffer(data, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    continue

                # تشخیص چهره
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50)
                )

                if len(faces) > 0:
                    human_faces.append(data)

                # بروزرسانی هر 5 عکس
                if (i + 1) % 5 == 0:
                    await bot.edit_message_text(
                        f"⬢ **در حال پردازش**\n\n"
                        f"◆ کاربر: **{target_name}**\n"
                        f"◆ پیشرفت: `{i+1}/{total}`\n"
                        f"◆ چهره‌های انسانی پیدا شده: `{len(human_faces)}`",
                        chat_id=chat_id, message_id=status_message_id, parse_mode='Markdown'
                    )

            except Exception as e:
                print(f"خطا در پردازش عکس {i}: {e}")
                continue

        # متن قالب
        footer = ""
        if template_id:
            t = db_get_template(template_id)
            if t:
                footer = t[3]
                footer = footer.replace("{name}", target_name)
                footer = footer.replace("{username}", target.username or "")

        # ارسال
        await bot.edit_message_text(
            f"⬢ **ارسال**\n\n"
            f"◆ کاربر: **{target_name}**\n"
            f"◆ چهره‌های انسانی: `{len(human_faces)}`\n"
            f"◆ در حال ارسال...",
            chat_id=chat_id, message_id=status_message_id, parse_mode='Markdown'
        )

        sent_count = 0
        for idx, data in enumerate(human_faces):
            try:
                caption = footer if idx == 0 else None
                await bot.send_photo(
                    chat_id=owner_id,
                    photo=data,
                    caption=caption,
                    parse_mode='Markdown'
                )
                sent_count += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"خطا در ارسال: {e}")

        await bot.edit_message_text(
            f"◈━━━━━━━━━━━━━━━━━━━◈\n"
            f"     ✦ **تکمیل شد** ✦\n"
            f"◈━━━━━━━━━━━━━━━━━━━◈\n\n"
            f"◆ کاربر: **{target_name}**\n"
            f"◆ کل پروفایل‌ها: `{total}`\n"
            f"◆ چهره‌های ارسال شده: `{sent_count}`\n"
            f"◆ فیلتر شده (فیک/منظره): `{total - sent_count}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━",
            chat_id=chat_id, message_id=status_message_id,
            reply_markup=back_kb("menu_main"),
            parse_mode='Markdown'
        )

    except Exception as e:
        print(f"خطا: {e}")
        try:
            await bot.edit_message_text(
                f"❌ خطا: `{str(e)[:200]}`",
                chat_id=chat_id, message_id=status_message_id, parse_mode='Markdown'
            )
        except:
            pass
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


# ================ تنظیمات ================
async def menu_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer("⛔")
        return
    await q.answer()

    kb = [
        [InlineKeyboardButton("◂ بازگشت", callback_data="menu_main")]
    ]
    await q.edit_message_text(
        TEXTS['settings_menu'],
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )


# ================ Cancel ================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "◂ لغو شد.",
        reply_markup=back_kb("menu_main")
    )
    return ConversationHandler.END


# ================ MAIN ================
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_NEW_BOT_TOKEN_HERE":
        print("❌ توکن ربات تنظیم نشده!")
        return

    if not MY_USER_ID:
        print("❌ MY_USER_ID تنظیم نشده! آیدی عددی خودت رو بذار.")
        return

    app = Application.builder().token(TOKEN).build()

    # Start
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    # Conversation: افزودن اکانت
    add_acc_conv = ConversationHandler(
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
    )
    app.add_handler(add_acc_conv)

    # Conversation: افزودن قالب
    add_tmpl_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(tmpl_add_start, pattern="^tmpl_add$")],
        states={
            WAIT_NEW_TEMPLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_wait_title)],
            WAIT_TEMPLATE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_wait_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(add_tmpl_conv)

    # Conversation: گرفتن پروفایل
    profile_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_get_profile, pattern="^menu_get_profile$")],
        states={
            WAIT_TARGET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_target_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(profile_conv)

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

    # هندلر عمومی — هیچی جواب نمی‌ده
    async def ignore_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and not is_owner(update.effective_user.id):
            return
    app.add_handler(MessageHandler(filters.ALL, ignore_all), group=99)

    print("🤖 ربات خصوصی در حال اجراست...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
