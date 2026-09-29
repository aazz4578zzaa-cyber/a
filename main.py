import logging
import re
import asyncio
import os
import json
import html
import shutil
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon import events
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    PhoneNumberInvalidError
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest, GetUserPhotosRequest
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import User, InputPhoneContact
import urllib.request

# ============ تنظیمات ============
TOKEN = "8810050319:AAH5T1qehg7U-oplDB_yp4JVGZl6W866BzY"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ تنظیمات ثابت ============
IRAN_TZ = ZoneInfo("Asia/Tehran")
DATA_FILE = "selfs.json"
DATA_BACKUP_FILE = "selfs_backup.json"
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PROFILE_COUNT = 1000
MAX_PER_DAY = 500

# ============ State ============
user_sessions = {}
self_data = {}
clock_tasks = {}
profile_tasks = {}
self_clients = {}
self_tasks = {}
account_locks = {}
job_ids = {}
DATA_LOCK = asyncio.Lock()
is_shutting_down = False

# ============ فونت‌های ساعت ============
FONTS = {
    '1': {'name': 'فونت 1', 'display': '𝟎𝟎:𝟎𝟎', 'map': '𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗'},
    '2': {'name': 'فونت 2', 'display': '𝟬𝟬:𝟬𝟬', 'map': '𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵'},
    '3': {'name': 'فونت 3', 'display': '⓿⓿:⓿⓿', 'map': '⓿⓵⓶⓷⓸⓹⓺⓻⓼⓽'},
    '4': {'name': 'فونت 4', 'display': '⓪⓪:⓪⓪', 'map': '⓪①②③④⑤⑥⑦⑧⑨'},
    '5': {'name': 'فونت 5', 'display': '₀₀:₀₀', 'map': '₀₁₂₃₄₅₆₇₈₉'},
    '6': {'name': 'فونت 6', 'display': '𝟶𝟶:𝟶𝟶', 'map': '𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿'},
    '7': {'name': 'فونت 7', 'display': '⁰⁰:⁰⁰', 'map': '⁰¹²³⁴⁵⁶⁷⁸⁹'},
    '8': {'name': 'فونت 8', 'display': '⊘⊘:⊘⊘', 'map': '⊘①②③④⑤⑥⑦⑧⑨'},
    '9': {'name': 'فونت 9', 'display': '𝟶𝟷:ӠӠ', 'map': '𝟶𝟷ӠӠ4ƼϬ7𝟾९'},
    '10': {'name': 'فونت 10', 'display': '𝟷ϩ:Ӡ4', 'map': '𝟷ϩӠ4ƼϬ7𝟾₉₀'},
    '11': {'name': 'فونت 11', 'display': '¹²:³⁴', 'map': '¹²³⁴⁵₆₇₈₉₀'}
}

FONT_NAMES = {k: v['name'] for k, v in FONTS.items()}

# ============ توابع ذخیره‌سازی ============
def load_data():
    global self_data
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            self_data = json.load(f)
        if not isinstance(self_data, dict):
            self_data = {}
    except:
        self_data = {}

async def save_data():
    async with DATA_LOCK:
        try:
            temp_file = DATA_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self_data, f, ensure_ascii=False, indent=2)
            if os.path.exists(DATA_FILE):
                shutil.copy2(DATA_FILE, DATA_BACKUP_FILE)
            os.replace(temp_file, DATA_FILE)
        except Exception as e:
            logger.exception(f"Error saving data: {e}")

load_data()

# ============ توابع کمکی ============
def escape_html(text):
    return html.escape(str(text))

def get_iran_time():
    return datetime.now(IRAN_TZ)

def get_iran_time_str():
    return get_iran_time().strftime("%H:%M:%S")

def get_iran_time_short():
    return get_iran_time().strftime("%H:%M")

def get_iran_date_str():
    return get_iran_time().strftime("%Y/%m/%d")

def get_self_key(user_id, account_index):
    return (str(user_id), account_index)

def convert_to_font(text, font_type):
    font_map = FONTS.get(font_type, FONTS['1'])['map']
    return ''.join(font_map[int(c)] if c.isdigit() else c for c in text)

def clean_clock_from_name(name):
    if not name:
        return name
    for font in FONTS.values():
        chars = re.escape(font['map'])
        name = re.sub(rf'\s*[{chars}]+:[{chars}]+$', '', name)
    name = re.sub(r'\s*\d{1,2}:\d{1,2}(:\d{1,2})?$', '', name)
    return name.strip()

def is_valid_phone(text):
    phone = re.sub(r'[^0-9+]', '', text)
    return 10 <= len(phone) <= 15

def format_seconds(seconds):
    """تبدیل ثانیه به فرمت خوانا"""
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if days > 0:
        parts.append(f"{days} روز")
    if hours > 0:
        parts.append(f"{hours} ساعت")
    if minutes > 0:
        parts.append(f"{minutes} دقیقه")
    if secs > 0 or not parts:
        parts.append(f"{secs} ثانیه")
    return " و ".join(parts)

async def safe_disconnect(client):
    if client:
        try:
            await client.disconnect()
        except:
            pass

async def clear_user_state(user_id):
    if user_id in user_sessions:
        try:
            client = user_sessions[user_id].get('client')
            if client:
                await client.disconnect()
        except:
            pass
        del user_sessions[user_id]

# ============ توابع سلف ============
async def get_user_session_by_index(user_id, account_index):
    user_id_str = str(user_id)
    selfs = self_data.get(user_id_str, [])
    if 0 <= account_index < len(selfs):
        self_account = selfs[account_index]
        if self_account.get('active', True):
            return {
                'session': self_account.get('session'),
                'api_id': self_account.get('api_id'),
                'api_hash': self_account.get('api_hash'),
                'phone': self_account.get('phone'),
                'index': account_index,
                'data': self_account
            }
    return None

async def start_self_client(user_id, account_index):
    try:
        key = get_self_key(user_id, account_index)
        session_data = await get_user_session_by_index(user_id, account_index)
        if not session_data:
            return False

        if key in self_clients:
            try:
                await self_clients[key].disconnect()
            except:
                pass
            del self_clients[key]

        if key in self_tasks:
            self_tasks[key].cancel()
            try:
                await self_tasks[key]
            except:
                pass
            del self_tasks[key]

        client = TelegramClient(
            StringSession(session_data['session']),
            session_data['api_id'],
            session_data['api_hash']
        )
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return False

        me = await client.get_me()
        self_user_id = me.id
        self_clients[key] = client

        @client.on(events.NewMessage(outgoing=True))
        async def outgoing_handler(event):
            await self_outgoing_message_handler(event, client, self_user_id, account_index)

        @client.on(events.NewMessage(incoming=True))
        async def incoming_handler(event):
            await self_incoming_message_handler(event, client, self_user_id, account_index)

        async def run_client():
            try:
                await client.run_until_disconnected()
            except Exception as e:
                if not is_shutting_down:
                    logger.exception(f"Client disconnected for {key}: {e}")

        task = asyncio.create_task(run_client())
        self_tasks[key] = task

        logger.info(f"Self client started for user {user_id} index {account_index}")
        return True

    except Exception as e:
        logger.exception(f"Error starting self client: {e}")
        return False

async def stop_self_client(user_id, account_index):
    key = get_self_key(user_id, account_index)
    if key in self_clients:
        try:
            await self_clients[key].disconnect()
        except:
            pass
        del self_clients[key]
    if key in self_tasks:
        self_tasks[key].cancel()
        try:
            await self_tasks[key]
        except:
            pass
        del self_tasks[key]

async def self_outgoing_message_handler(event, client, self_user_id, account_index):
    try:
        if not event.is_private:
            return
        if event.sender_id != self_user_id:
            return
        message = event.message
        if not message or not message.text:
            return
        if message.text.strip() != "بلاک":
            return
        chat = await client.get_entity(event.chat_id)
        if not chat or not isinstance(chat, User):
            return
        if chat.id == self_user_id:
            return
        await client(BlockRequest(id=chat.id))
        username = chat.username if chat.username else str(chat.id)
        new_text = f"◂ کاربر @{username} بلاک شد !"
        try:
            await client.edit_message(event.chat_id, message.id, new_text)
        except:
            try:
                await client.send_message(event.chat_id, new_text)
            except:
                pass
        logger.info(f"User {chat.id} blocked by self {self_user_id}")
    except Exception as e:
        logger.exception(f"Error in outgoing handler: {e}")

async def self_incoming_message_handler(event, client, self_user_id, account_index):
    try:
        if not event.is_private:
            return
        sender = await event.get_sender()
        if not sender or sender.id == self_user_id:
            return
        message = event.message
        if not message or not message.text:
            return
        if message.text.strip() != "بلاک":
            return
        target_user = None
        if message.is_reply:
            try:
                replied = await event.get_reply_message()
                if replied:
                    target_user = await client.get_entity(replied.sender_id)
            except:
                pass
        if not target_user:
            target_user = sender
        await client(BlockRequest(id=target_user.id))
        username = target_user.username if target_user.username else str(target_user.id)
        new_text = f"◂ کاربر @{username} بلاک شد !"
        try:
            await client.edit_message(event.chat_id, message.id, new_text)
        except:
            try:
                await client.send_message(event.chat_id, new_text)
            except:
                pass
        logger.info(f"User {target_user.id} blocked by self {self_user_id}")
    except Exception as e:
        logger.exception(f"Error in incoming handler: {e}")

# ============ توابع ساعت (سریع و لحظه‌ای) ============
async def set_clock_on_profile(session_string, api_id, api_hash, font_type):
    client = None
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            return False
        me = await client.get_me()
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        clean_first = clean_clock_from_name(first_name)
        time_str = get_iran_time_short()
        font_time = convert_to_font(time_str, font_type)
        new_first = f"{clean_first} {font_time}".strip()
        if new_first != first_name:
            await client(UpdateProfileRequest(first_name=new_first, last_name=last_name))
        return True
    except Exception as e:
        logger.exception(f"Error in set_clock_on_profile: {e}")
        return False
    finally:
        await safe_disconnect(client)

async def remove_clock_from_profile(session_string, api_id, api_hash):
    client = None
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            return False
        me = await client.get_me()
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        clean_first = clean_clock_from_name(first_name)
        if clean_first != first_name:
            await client(UpdateProfileRequest(first_name=clean_first, last_name=last_name))
        return True
    except Exception as e:
        logger.exception(f"Error in remove_clock_from_profile: {e}")
        return False
    finally:
        await safe_disconnect(client)

async def clock_loop(user_id, account_index, session_string, api_id, api_hash, font_type):
    """حلقه ساعت - هر 3 ثانیه چک می‌کنه و فقط وقتی دقیقه تغییر کرد آپدیت می‌کنه"""
    clock_key = (str(user_id), account_index)
    last_minute = None

    while True:
        try:
            if clock_key not in clock_tasks or not clock_tasks[clock_key]:
                break
            current_minute = get_iran_time().strftime("%H:%M")
            if current_minute != last_minute:
                await set_clock_on_profile(session_string, api_id, api_hash, font_type)
                last_minute = current_minute
            await asyncio.sleep(3)  # چک سریع‌تر
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Error in clock loop: {e}")
            await asyncio.sleep(3)

# ============ منوی اصلی ============
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    name = escape_html(user.first_name or "کاربر")
    user_id = str(user.id)
    self_count = len(self_data.get(user_id, []))

    text = f"""
🌟 <b>ربات مدیریت حساب‌های شخصی</b>

<b>جناب {name} گرامی</b>

با سلام و احترام، به ربات مدیریت حساب‌های شخصی خود خوش آمدید.
این ربات به شما امکان مدیریت سلف‌های تلگرام را می‌دهد.

<b>تعداد سلف‌های ثبت شده: {self_count}</b>

لطفاً از منوی زیر انتخاب فرمایید:
"""

    keyboard = [
        [InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")],
        [InlineKeyboardButton("📋 لیست سلف‌ها", callback_data="list_selfs")],
        [InlineKeyboardButton("🎨 فونت ساعت", callback_data="font_settings")]
    ]

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
            )
            await update.callback_query.answer()
        except Exception as e:
            logger.exception(f"Error in main_menu edit: {e}")
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )

# ============ لیست سلف‌ها ============
async def list_selfs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    user_id = str(query.from_user.id)
    selfs = self_data.get(user_id, [])

    if not selfs:
        text = "📋 <b>لیست سلف‌ها</b>\n\n❌ <b>هیچ سلفی ثبت نشده است.</b>\n\nلطفاً از گزینه \"ایجاد سلف جدید\" استفاده فرمایید."
        keyboard = [
            [InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return

    text = f"📋 <b>لیست سلف‌های ثبت شده ({len(selfs)})</b>\n\n"
    keyboard = []
    for i, self_account in enumerate(selfs):
        phone = escape_html(self_account.get('phone', 'نامشخص'))
        active_time = escape_html(self_account.get('active_time', 'تنظیم نشده'))
        account_name = escape_html(self_account.get('account_name', 'بدون نام'))
        clock_active = self_account.get('clock_active', False)
        font_type = self_account.get('font_type', '1')
        font_name = FONT_NAMES.get(font_type, 'فونت 1')
        clock_status = "🟢 <b>فعال</b>" if clock_active else "🔴 <b>غیرفعال</b>"
        time_display = f"{account_name} {active_time}" if active_time != 'تنظیم نشده' else f"{account_name} - ساعت تنظیم نشده"

        text += f"""
🔹 <b>سلف شماره {i+1}</b>
   📱 شماره: <code>{phone}</code>
   👤 نام: <b>{account_name}</b>
   🕐 ساعت: <code>{time_display}</code>
   🎨 فونت: {font_name}
   📊 وضعیت ساعت: {clock_status}
"""
        keyboard.append([InlineKeyboardButton(f"⚙️ مدیریت سلف {i+1}", callback_data=f"manage_{i}")])

    keyboard.append([InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ============ مدیریت سلف ============
async def manage_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    if len(parts) < 2:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return
    try:
        index = int(parts[1])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return

    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return

    self_account = selfs[index]
    phone = escape_html(self_account.get('phone', 'نامشخص'))
    account_name = escape_html(self_account.get('account_name', 'بدون نام'))
    clock_active = self_account.get('clock_active', False)
    active_time = escape_html(self_account.get('active_time', 'تنظیم نشده'))
    font_type = self_account.get('font_type', '1')
    font_name = FONT_NAMES.get(font_type, 'فونت 1')

    key = get_self_key(int(user_id), index)
    is_connected = key in self_clients

    time_display = f"{account_name} {active_time}" if active_time != 'تنظیم نشده' else f"{account_name} - ساعت تنظیم نشده"
    clock_status = "🟢 <b>فعال</b>" if clock_active else "🔴 <b>غیرفعال</b>"
    status_self = "🟢 متصل" if is_connected else "🔴 قطع"

    # چک کن آیا جاب پروفایل در حال اجرا هست
    job_running = user_id in profile_tasks and not profile_tasks[user_id].done()

    text = f"""
⚙️ <b>مدیریت سلف شماره {index + 1}</b>

📱 شماره: <code>{phone}</code>
👤 نام اکانت: <b>{account_name}</b>
🕐 ساعت: <code>{time_display}</code>
🎨 فونت: {font_name}
📊 وضعیت ساعت: {clock_status}
🔗 وضعیت سلف: {status_self}
{'🔄 <b>عملیات پروفایل در حال اجراست</b>' if job_running else ''}

لطفاً یکی از گزینه‌های زیر را انتخاب فرمایید:
"""

    keyboard = []
    if not is_connected:
        keyboard.append([InlineKeyboardButton("🔄 اتصال سلف", callback_data=f"connect_self_{index}")])
    else:
        keyboard.append([InlineKeyboardButton("🔌 قطع سلف", callback_data=f"disconnect_self_{index}")])

    keyboard.append([InlineKeyboardButton("📸 تنظیم پروفایل", callback_data=f"new_profile_{index}")])
    keyboard.append([InlineKeyboardButton("🆔 بلاک با آیدی", callback_data=f"block_by_id_{index}")])

    if clock_active:
        keyboard.append([InlineKeyboardButton("⏰ غیرفعال کردن ساعت", callback_data=f"deactivate_clock_{index}")])
        keyboard.append([InlineKeyboardButton("🎨 تغییر فونت", callback_data=f"font_select_{index}")])
    else:
        keyboard.append([InlineKeyboardButton("⏰ فعال کردن ساعت", callback_data=f"activate_clock_{index}")])

    if job_running:
        keyboard.append([InlineKeyboardButton("❌ لغو عملیات پروفایل", callback_data="cancel_profile")])

    keyboard.append([InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="list_selfs")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ============ اتصال/قطع سلف ============
async def connect_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return
    result = await start_self_client(int(user_id), index)
    if result:
        text = f"✅ <b>سلف شماره {index + 1} با موفقیت متصل شد!</b>"
    else:
        text = f"❌ <b>خطا در اتصال سلف شماره {index + 1}!</b>"
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def disconnect_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return
    await stop_self_client(int(user_id), index)
    text = f"✅ <b>سلف شماره {index + 1} با موفقیت قطع شد!</b>"
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ============ فعال/غیرفعال کردن ساعت ============
async def activate_clock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return

    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return

    self_account = selfs[index]
    session_string = self_account.get('session')
    api_id = self_account.get('api_id')
    api_hash = self_account.get('api_hash')
    font_type = self_account.get('font_type', '1')

    time_str = get_iran_time_short()
    font_time = convert_to_font(time_str, font_type)

    result = await set_clock_on_profile(session_string, api_id, api_hash, font_type)

    if result:
        selfs[index]['active_time'] = time_str
        selfs[index]['clock_active'] = True
        await save_data()
        clock_key = get_clock_key(int(user_id), index)
        clock_tasks[clock_key] = True
        if clock_key not in clock_tasks or not clock_tasks.get(clock_key):
            asyncio.create_task(clock_loop(int(user_id), index, session_string, api_id, api_hash, font_type))
        text = f"""
✅ <b>ساعت با موفقیت فعال شد!</b>

👤 نام اکانت: <b>{escape_html(selfs[index].get('account_name', 'کاربر'))}</b>
🕐 ساعت فعال: <code>{font_time}</code>
🎨 فونت: {FONT_NAMES.get(font_type, 'فونت 1')}

ساعت هر دقیقه به‌طور خودکار بروزرسانی می‌شود.
"""
    else:
        text = "❌ <b>خطا در فعال کردن ساعت!</b>\n\nلطفاً مطمئن شوید که اکانت معتبر است و دوباره تلاش کنید."

    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
        [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="back")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def deactivate_clock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return

    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return

    self_account = selfs[index]
    session_string = self_account.get('session')
    api_id = self_account.get('api_id')
    api_hash = self_account.get('api_hash')
    account_name = escape_html(selfs[index].get('account_name', 'کاربر'))

    result = await remove_clock_from_profile(session_string, api_id, api_hash)

    if result:
        selfs[index]['clock_active'] = False
        await save_data()
        clock_key = get_clock_key(int(user_id), index)
        if clock_key in clock_tasks:
            clock_tasks[clock_key] = False
            del clock_tasks[clock_key]
        text = f"❌ <b>ساعت با موفقیت غیرفعال شد!</b>\n\n👤 نام اکانت: <b>{account_name}</b>\n\nساعت برای این سلف با موفقیت غیرفعال گردید."
    else:
        text = "❌ <b>خطا در غیرفعال کردن ساعت!</b>\n\nلطفاً مطمئن شوید که اکانت معتبر است و دوباره تلاش کنید."

    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
        [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="back")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ============ فونت ============
async def font_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    selfs = self_data.get(user_id, [])
    if not selfs:
        text = "🎨 <b>تنظیم فونت ساعت</b>\n\n❌ <b>هیچ سلفی ثبت نشده است.</b>\n\nلطفاً ابتدا یک سلف ایجاد کنید."
        keyboard = [
            [InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    text = "🎨 <b>تنظیم فونت ساعت</b>\n\nلطفاً سلف مورد نظر را انتخاب کنید:"
    keyboard = []
    for i, self_account in enumerate(selfs):
        account_name = escape_html(self_account.get('account_name', 'بدون نام'))
        font_type = self_account.get('font_type', '1')
        font_name = FONT_NAMES.get(font_type, 'فونت 1')
        keyboard.append([InlineKeyboardButton(f"{i+1}. {account_name} - {font_name}", callback_data=f"font_select_{i}")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def font_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return
    text = "🎨 <b>انتخاب فونت ساعت</b>\n\nلطفاً یکی از فونت‌های زیر را انتخاب کنید:"
    keyboard = []
    for font_id, font_info in FONTS.items():
        keyboard.append([InlineKeyboardButton(
            f"{font_info['display']} - {font_info['name']}",
            callback_data=f"font_apply_{index}_{font_id}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 لغو و بازگشت", callback_data=f"manage_{index}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def font_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    parts = query.data.split('_')
    try:
        index = int(parts[2])
        font_type = parts[3]
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return
    if font_type not in FONTS:
        await query.edit_message_text("❌ فونت نامعتبر", parse_mode='HTML')
        return
    user_id = str(query.from_user.id)
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return
    self_account = selfs[index]
    session_string = self_account.get('session')
    api_id = self_account.get('api_id')
    api_hash = self_account.get('api_hash')
    account_name = escape_html(self_account.get('account_name', 'کاربر'))

    result = await set_clock_on_profile(session_string, api_id, api_hash, font_type)

    if result:
        selfs[index]['font_type'] = font_type
        time_str = get_iran_time_short()
        font_time = convert_to_font(time_str, font_type)
        selfs[index]['active_time'] = time_str
        await save_data()
        # ری‌استارت حلقه ساعت با فونت جدید
        clock_key = get_clock_key(int(user_id), index)
        if clock_key in clock_tasks and clock_tasks.get(clock_key):
            clock_tasks[clock_key] = False
            del clock_tasks[clock_key]
            clock_tasks[clock_key] = True
            asyncio.create_task(clock_loop(int(user_id), index, session_string, api_id, api_hash, font_type))

        text = f"""
✅ <b>فونت با موفقیت تغییر کرد!</b>

👤 نام اکانت: <b>{account_name}</b>
🎨 فونت انتخابی: <b>{FONT_NAMES.get(font_type, 'فونت 1')}</b>
🕐 ساعت فعلی: <code>{font_time}</code>

ساعت با فونت جدید بروزرسانی شد.
"""
    else:
        text = "❌ <b>خطا در تغییر فونت!</b>\n\nلطفاً مطمئن شوید که اکانت معتبر است و دوباره تلاش کنید."

    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به مدیریت", callback_data=f"manage_{index}")],
        [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="back")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ============ تنظیم پروفایل (ویرایش‌شده) ============
async def new_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return

    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return

    # چک کن آیا جاب در حال اجراست - اگه هست، فایل‌ها به جاب فعلی اضافه میشن
    job_running = user_id in profile_tasks and not profile_tasks[user_id].done()

    context.user_data['profile_index'] = index
    context.user_data['profile_step'] = 'waiting_media'
    if 'profile_files' not in context.user_data:
        context.user_data['profile_files'] = []

    if job_running:
        text = """
📸 <b>افزودن فایل به عملیات در حال اجرا</b>

⚠️ <b>یک عملیات پروفایل در حال اجراست.</b>
فایل‌هایی که ارسال می‌کنید به صف عملیات فعلی اضافه می‌شوند.

لطفاً عکس یا فیلم مورد نظر را ارسال کنید:

• می‌توانید چندین عکس و فیلم ارسال کنید
• پس از ارسال همه، دکمه "افزودن به عملیات" را بزنید
• حجم فایل حداکثر 10 مگابایت
"""
        keyboard = [
            [InlineKeyboardButton("✅ افزودن به عملیات", callback_data=f"add_to_job_{index}")],
            [InlineKeyboardButton("🔙 لغو و بازگشت", callback_data=f"manage_{index}")]
        ]
    else:
        text = """
📸 <b>تنظیم پروفایل جدید</b>

لطفاً عکس یا فیلمی که می‌خواهید به عنوان پروفایل تنظیم شود را ارسال کنید.

⚠️ <b>نکات مهم:</b>
• می‌توانید چندین عکس و فیلم ارسال کنید
• پس از ارسال همه، دکمه "اتمام ارسال" را بزنید
• حجم فایل حداکثر 10 مگابایت
• فرمت‌های پشتیبانی شده: JPG, PNG, MP4
"""
        keyboard = [
            [InlineKeyboardButton("✅ اتمام ارسال", callback_data=f"done_profile_{index}")],
            [InlineKeyboardButton("🔙 لغو و بازگشت", callback_data=f"manage_{index}")]
        ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def handle_profile_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if 'profile_step' not in context.user_data or context.user_data['profile_step'] != 'waiting_media':
        await update.message.reply_text("❌ <b>لطفاً از دکمه تنظیم پروفایل استفاده کنید.</b>", parse_mode='HTML')
        return

    file = None
    ext = None

    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        ext = ".jpg"
    elif update.message.video:
        file = await update.message.video.get_file()
        ext = ".mp4"
    elif update.message.document:
        file = await update.message.document.get_file()
        name = update.message.document.file_name or ""
        ext = os.path.splitext(name)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png', '.mp4']:
            await update.message.reply_text("❌ <b>فرمت فایل پشتیبانی نمی‌شود!</b>", parse_mode='HTML')
            return
    else:
        await update.message.reply_text("❌ <b>لطفاً فقط عکس یا فیلم ارسال کنید!</b>", parse_mode='HTML')
        return

    if file.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("❌ <b>حجم فایل بیشتر از 10 مگابایت است!</b>", parse_mode='HTML')
        return

    file_path = f"temp_{user_id}_{len(context.user_data.get('profile_files', []))}{ext}"
    try:
        await file.download_to_drive(file_path)
    except Exception as e:
        logger.exception(f"Error downloading file: {e}")
        await update.message.reply_text("❌ <b>خطا در دانلود فایل!</b>", parse_mode='HTML')
        return

    if 'profile_files' not in context.user_data:
        context.user_data['profile_files'] = []
    context.user_data['profile_files'].append(file_path)

    count = len(context.user_data['profile_files'])
    index = context.user_data.get('profile_index', 0)

    # چک کن جاب در حال اجراست یا نه
    job_running = user_id in profile_tasks and not profile_tasks[user_id].done()

    if job_running:
        text = f"✅ <b>فایل {count} دریافت شد!</b>\n\nلطفاً فایل بعدی را ارسال کنید یا دکمه \"افزودن به عملیات\" را بزنید."
        keyboard = [
            [InlineKeyboardButton("✅ افزودن به عملیات", callback_data=f"add_to_job_{index}")],
            [InlineKeyboardButton("🔙 لغو و بازگشت", callback_data=f"manage_{index}")]
        ]
    else:
        text = f"✅ <b>فایل {count} با موفقیت دریافت شد!</b>\n\nلطفاً فایل بعدی را ارسال کنید یا دکمه اتمام را بزنید."
        keyboard = [
            [InlineKeyboardButton("✅ اتمام ارسال", callback_data=f"done_profile_{index}")],
            [InlineKeyboardButton("🔙 لغو و بازگشت", callback_data=f"manage_{index}")]
        ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def done_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اتمام ارسال پروفایل - شروع جاب جدید"""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return

    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return

    files = context.user_data.get('profile_files', [])
    if not files:
        await query.edit_message_text("❌ <b>هیچ فایلی ارسال نشده است!</b>\n\nلطفاً حداقل یک عکس یا فیلم ارسال کنید.", parse_mode='HTML')
        return

    text = f"""
✅ <b>{len(files)} فایل با موفقیت دریافت شد!</b>

🔢 لطفاً تعداد دفعاتی که می‌خواهید این پروفایل‌ها برای اکانت شما تنظیم شود را وارد کنید.

<b>حداکثر: {MAX_PROFILE_COUNT} بار برای هر فایل</b>
<b>حداقل: 1 بار</b>

⚠️ توجه: در صورت تعداد بالا، عملیات در چند روز انجام خواهد شد.
"""
    context.user_data['profile_step'] = 'waiting_count'
    keyboard = [[InlineKeyboardButton("🔙 لغو و بازگشت", callback_data=f"manage_{index}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def handle_profile_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if 'profile_step' not in context.user_data or context.user_data['profile_step'] != 'waiting_count':
        await update.message.reply_text("❌ <b>لطفاً از دکمه تنظیم پروفایل استفاده کنید.</b>", parse_mode='HTML')
        return
    try:
        count = int(update.message.text.strip())
        if count < 1 or count > MAX_PROFILE_COUNT:
            await update.message.reply_text(f"❌ <b>تعداد باید بین 1 تا {MAX_PROFILE_COUNT} باشد!</b>", parse_mode='HTML')
            return
    except:
        await update.message.reply_text("❌ <b>لطفاً یک عدد معتبر وارد کنید!</b>", parse_mode='HTML')
        return

    index = context.user_data['profile_index']
    files = context.user_data.get('profile_files', [])
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await update.message.reply_text("❌ <b>سلف مورد نظر یافت نشد!</b>", parse_mode='HTML')
        return

    self_account = selfs[index]
    session_string = self_account.get('session')
    api_id = self_account.get('api_id')
    api_hash = self_account.get('api_hash')
    account_name = escape_html(self_account.get('account_name', 'کاربر'))
    total_count = len(files) * count

    # پیام اصلی که ویرایش میشه
    status_msg = await update.message.reply_text(
        f"""
🚀 <b>شروع تنظیم پروفایل</b>

👤 نام اکانت: <b>{account_name}</b>
📁 تعداد فایل‌ها: <b>{len(files)}</b>
🔢 تعداد دفعات هر فایل: <b>{count}</b>
📊 مجموع تنظیمات: <b>{total_count}</b>

⏳ وضعیت: <b>در حال آماده‌سازی...</b>
✅ موفق: 0
❌ ناموفق: 0
📅 روز: 1

⚠️ این عملیات ممکن است چند دقیقه طول بکشد.
""",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_profile")]
        ]),
        parse_mode='HTML'
    )

    # پاک کردن فایل‌ها از context چون به جاب منتقل میشن
    files_copy = files.copy()
    context.user_data['profile_files'] = []
    context.user_data.pop('profile_step', None)

    # ایجاد جاب
    task = asyncio.create_task(
        run_profile_job(
            user_id, index, session_string, api_id, api_hash,
            files_copy, count, update.effective_chat.id, context, status_msg.message_id
        )
    )
    profile_tasks[user_id] = task

async def add_to_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """افزودن فایل‌های جدید به جاب در حال اجرا"""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[3])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return

    files = context.user_data.get('profile_files', [])
    if not files:
        await query.edit_message_text("❌ <b>هیچ فایلی ارسال نشده است!</b>", parse_mode='HTML')
        return

    # اضافه کردن به صف جاب فعلی
    if user_id in profile_tasks and not profile_tasks[user_id].done():
        # ذخیره در context برای جاب اصلی
        if 'pending_files' not in context.user_data:
            context.user_data['pending_files'] = []
        context.user_data['pending_files'].extend(files)
        context.user_data['profile_files'] = []

        await query.edit_message_text(
            f"""
✅ <b>{len(files)} فایل به صف عملیات اضافه شد!</b>

فایل‌ها پس از اتمام فایل‌های فعلی، به‌طور خودکار تنظیم می‌شوند.

🔙 برای بازگشت به منو کلیک کنید.
""",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به مدیریت", callback_data=f"manage_{index}")]
            ]),
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text("❌ <b>عملیات در حال اجرا نیست!</b>", parse_mode='HTML')

async def run_profile_job(user_id, index, session_string, api_id, api_hash, files, count, chat_id, context, status_msg_id):
    """اجرای جاب پروفایل با ویرایش پیام اصلی"""
    client = None
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            try:
                await context.bot.edit_message_text(
                    "❌ <b>اکانت معتبر نیست!</b>",
                    chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
                )
            except:
                pass
            return

        account_name = escape_html(self_data.get(str(user_id), [])[index].get('account_name', 'کاربر'))
        total_success = 0
        total_fail = 0
        today_count = 0
        day = 1
        last_edit_time = 0

        # آپلود همه فایل‌ها یک‌بار (بهینه‌سازی)
        uploaded_files = []
        for file_path in files:
            try:
                uploaded = await client.upload_file(file_path)
                uploaded_files.append((uploaded, file_path))
            except Exception as e:
                logger.exception(f"Error uploading {file_path}: {e}")

        if not uploaded_files:
            try:
                await context.bot.edit_message_text(
                    f"❌ <b>خطا در آپلود فایل‌ها!</b>\n\n👤 {account_name}",
                    chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
                )
            except:
                pass
            return

        total_operations = len(uploaded_files) * count
        file_index = 0

        for uploaded, file_path in uploaded_files:
            file_index += 1

            for i in range(count):
                # چک کن جاب کنسل نشده
                if user_id not in profile_tasks or profile_tasks[user_id].cancelled():
                    break

                # چک محدودیت روزانه
                if today_count >= MAX_PER_DAY:
                    now = get_iran_time()
                    tomorrow = now + timedelta(days=1)
                    next_day = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
                    wait_seconds = (next_day - now).total_seconds()

                    if wait_seconds > 0:
                        try:
                            await context.bot.edit_message_text(
                                f"""
🚀 <b>تنظیم پروفایل در حال اجرا</b>

👤 نام اکانت: <b>{account_name}</b>
📁 فایل‌ها: <b>{file_index}/{len(uploaded_files)}</b>
📊 پیشرفت: <b>{total_success}/{total_operations}</b>

⏳ <b>محدودیت روزانه تلگرام</b>
📅 روز بعد: {format_seconds(wait_seconds)} دیگر
✅ موفق: {total_success}
❌ ناموفق: {total_fail}
📅 روز: {day}

⚠️ عملیات به‌طور خودکار ادامه می‌یابد.
""",
                                chat_id=chat_id, message_id=status_msg_id,
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_profile")]
                                ]),
                                parse_mode='HTML'
                            )
                        except:
                            pass
                        await asyncio.sleep(wait_seconds)
                        day += 1
                        today_count = 0

                try:
                    await client(UploadProfilePhotoRequest(file=uploaded))
                    total_success += 1
                    today_count += 1

                    # ویرایش پیام (نه ارسال پیام جدید) - هر 2 ثانیه یک بار
                    now_ts = asyncio.get_event_loop().time()
                    if now_ts - last_edit_time >= 2 or total_success == total_operations:
                        last_edit_time = now_ts
                        try:
                            await context.bot.edit_message_text(
                                f"""
🚀 <b>تنظیم پروفایل در حال اجرا</b>

👤 نام اکانت: <b>{account_name}</b>
📁 فایل‌ها: <b>{file_index}/{len(uploaded_files)}</b>
📊 پیشرفت: <b>{total_success}/{total_operations}</b>

✅ موفق: <b>{total_success}</b>
❌ ناموفق: <b>{total_fail}</b>
📅 روز: <b>{day}</b>

⏳ در حال اجرا...
""",
                                chat_id=chat_id, message_id=status_msg_id,
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_profile")]
                                ]),
                                parse_mode='HTML'
                            )
                        except:
                            pass

                    # تأخیر خیلی کم - سرعت بالا
                    await asyncio.sleep(0.1)

                except FloodWaitError as e:
                    wait_time = e.seconds
                    try:
                        await context.bot.edit_message_text(
                            f"""
🚀 <b>تنظیم پروفایل در حال اجرا</b>

👤 نام اکانت: <b>{account_name}</b>
📁 فایل‌ها: <b>{file_index}/{len(uploaded_files)}</b>
📊 پیشرفت: <b>{total_success}/{total_operations}</b>

⏳ <b>محدودیت تلگرام (FloodWait)</b>
⏰ زمان انتظار: {format_seconds(wait_time)}
✅ موفق: {total_success}
❌ ناموفق: {total_fail}
📅 روز: {day}

⚠️ عملیات به‌طور خودکار ادامه می‌یابد.
""",
                            chat_id=chat_id, message_id=status_msg_id,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_profile")]
                            ]),
                            parse_mode='HTML'
                        )
                    except:
                        pass
                    await asyncio.sleep(wait_time + 2)
                    # تلاش مجدد بعد از FloodWait
                    try:
                        await client(UploadProfilePhotoRequest(file=uploaded))
                        total_success += 1
                        today_count += 1
                    except Exception as e2:
                        logger.exception(f"Retry failed: {e2}")
                        total_fail += 1

                except Exception as e:
                    logger.exception(f"Error setting profile: {e}")
                    total_fail += 1
                    await asyncio.sleep(0.5)

        # پایان جاب
        try:
            await context.bot.edit_message_text(
                f"""
✅ <b>تنظیم پروفایل به پایان رسید!</b>

👤 نام اکانت: <b>{account_name}</b>
📊 نتیجه نهایی:
• ✅ موفق: <b>{total_success}</b>
• ❌ ناموفق: <b>{total_fail}</b>
• 📁 مجموع فایل‌ها: <b>{len(uploaded_files)}</b>
• 📅 تعداد روزها: <b>{day}</b>
""",
                chat_id=chat_id, message_id=status_msg_id,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت به مدیریت", callback_data=f"manage_{index}")],
                    [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="back")]
                ]),
                parse_mode='HTML'
            )
        except:
            pass

        # پاکسازی فایل‌های temp
        for _, fp in uploaded_files:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except:
                pass

        if user_id in profile_tasks:
            del profile_tasks[user_id]

    except asyncio.CancelledError:
        logger.info(f"Profile job for user {user_id} cancelled")
        for file_path in files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
        if user_id in profile_tasks:
            del profile_tasks[user_id]
        try:
            await context.bot.edit_message_text(
                "❌ <b>عملیات لغو شد!</b>",
                chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
            )
        except:
            pass
        raise
    except Exception as e:
        logger.exception(f"Error in profile job: {e}")
        try:
            await context.bot.edit_message_text(
                f"❌ <b>خطا:</b> {escape_html(str(e)[:200])}",
                chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
            )
        except:
            pass
    finally:
        await safe_disconnect(client)

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    if user_id in profile_tasks:
        profile_tasks[user_id].cancel()
        try:
            await profile_tasks[user_id]
        except:
            pass
        if user_id in profile_tasks:
            del profile_tasks[user_id]
    if 'profile_files' in context.user_data:
        for file_path in context.user_data['profile_files']:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
        del context.user_data['profile_files']
    context.user_data.pop('profile_step', None)
    context.user_data.pop('profile_index', None)

    try:
        await query.edit_message_text("❌ <b>عملیات لغو شد!</b>", parse_mode='HTML')
    except:
        pass

# ============ بلاک با آیدی ============
async def block_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try:
        index = int(parts[3])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML')
        return

    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return

    context.user_data['block_index'] = index
    context.user_data['block_step'] = 'waiting_id'

    text = f"""
🆔 <b>بلاک با آیدی</b>

👤 سلف شماره: <b>{index + 1}</b>

لطفاً آیدی عددی (User ID) کاربر مورد نظر را وارد کنید.

<b>مثال:</b> <code>123456789</code>

⚠️ توجه: با این کار، تمام پروفایل‌های کاربر در اکانت شما کپی می‌شود و سپس بلاک می‌گردد.
"""
    keyboard = [
        [InlineKeyboardButton("🔙 لغو و بازگشت", callback_data=f"manage_{index}")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def handle_block_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if context.user_data.get('block_step') != 'waiting_id':
        return

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ <b>آیدی باید عدد باشد!</b>", parse_mode='HTML')
        return

    target_id = int(text)
    index = context.user_data.get('block_index')
    context.user_data.pop('block_step', None)
    context.user_data.pop('block_index', None)

    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await update.message.reply_text("❌ <b>سلف مورد نظر یافت نشد.</b>", parse_mode='HTML')
        return

    self_account = selfs[index]
    session_string = self_account.get('session')
    api_id = self_account.get('api_id')
    api_hash = self_account.get('api_hash')
    account_name = escape_html(self_account.get('account_name', 'کاربر'))

    # پیام اصلی وضعیت
    status_msg = await update.message.reply_text(
        f"""
🔄 <b>در حال بلاک کردن کاربر...</b>

👤 سلف: <b>{account_name}</b>
🆔 آیدی هدف: <code>{target_id}</code>

⏳ وضعیت: در حال دریافت اطلاعات کاربر...
""",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
        ]),
        parse_mode='HTML'
    )

    # اجرا در پس‌زمینه
    asyncio.create_task(
        run_block_job(
            user_id, index, session_string, api_id, api_hash,
            target_id, update.effective_chat.id, context, status_msg.message_id
        )
    )

async def run_block_job(user_id, index, session_string, api_id, api_hash, target_id, chat_id, context, status_msg_id):
    """بلاک کاربر با کپی همه پروفایل‌هایش"""
    client = None
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            try:
                await context.bot.edit_message_text(
                    "❌ <b>اکانت معتبر نیست!</b>",
                    chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
                )
            except:
                pass
            return

        account_name = escape_html(self_data.get(str(user_id), [])[index].get('account_name', 'کاربر'))

        # دریافت اطلاعات کاربر
        try:
            target = await client.get_entity(target_id)
        except Exception as e:
            logger.exception(f"Error getting target: {e}")
            try:
                await context.bot.edit_message_text(
                    f"❌ <b>کاربر با آیدی {target_id} یافت نشد!</b>",
                    chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
                )
            except:
                pass
            return

        target_name = target.first_name or target.username or str(target_id)

        try:
            await context.bot.edit_message_text(
                f"""
🔄 <b>در حال دریافت پروفایل‌های کاربر...</b>

👤 سلف: <b>{account_name}</b>
🆔 آیدی هدف: <code>{target_id}</code>
📝 نام کاربر: <b>{escape_html(target_name)}</b>

⏳ وضعیت: در حال دریافت لیست پروفایل‌ها...
""",
                chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
            )
        except:
            pass

        # دریافت لیست پروفایل‌های کاربر
        try:
            photos = await client(GetUserPhotosRequest(
                user_id=target_id, offset=0, max_id=0, limit=100
            ))
            photo_list = photos.photos if hasattr(photos, 'photos') else []
        except Exception as e:
            logger.exception(f"Error getting photos: {e}")
            photo_list = []

        total = len(photo_list)
        success = 0

        try:
            await context.bot.edit_message_text(
                f"""
🔄 <b>در حال کپی پروفایل‌ها...</b>

👤 سلف: <b>{account_name}</b>
🆔 آیدی هدف: <code>{target_id}</code>
📝 نام کاربر: <b>{escape_html(target_name)}</b>
📸 تعداد پروفایل‌ها: <b>{total}</b>

📊 پیشرفت: 0/{total}
✅ موفق: 0
❌ ناموفق: 0

⏳ در حال اجرا...
""",
                chat_id=chat_id, message_id=status_msg_id,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
                ]),
                parse_mode='HTML'
            )
        except:
            pass

        # دانلود و آپلود هر پروفایل
        last_edit = 0
        for i, photo in enumerate(photo_list):
            try:
                # دانلود عکس
                file_path = f"block_temp_{user_id}_{target_id}_{i}.jpg"
                await client.download_media(photo, file_path)

                # آپلود به عنوان پروفایل خود
                if os.path.exists(file_path):
                    uploaded = await client.upload_file(file_path)
                    await client(UploadProfilePhotoRequest(file=uploaded))
                    success += 1
                    try:
                        os.remove(file_path)
                    except:
                        pass

                # ویرایش پیام هر 2 ثانیه
                now_ts = asyncio.get_event_loop().time()
                if now_ts - last_edit >= 2 or i == total - 1:
                    last_edit = now_ts
                    try:
                        await context.bot.edit_message_text(
                            f"""
🔄 <b>در حال کپی پروفایل‌ها...</b>

👤 سلف: <b>{account_name}</b>
🆔 آیدی هدف: <code>{target_id}</code>
📝 نام کاربر: <b>{escape_html(target_name)}</b>
📸 تعداد پروفایل‌ها: <b>{total}</b>

📊 پیشرفت: {i+1}/{total}
✅ موفق: {success}
❌ ناموفق: {i+1-success}

⏳ در حال اجرا...
""",
                            chat_id=chat_id, message_id=status_msg_id,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
                            ]),
                            parse_mode='HTML'
                        )
                    except:
                        pass

                await asyncio.sleep(0.1)

            except FloodWaitError as e:
                try:
                    await context.bot.edit_message_text(
                        f"""
🔄 <b>در حال کپی پروفایل‌ها...</b>

⏳ <b>محدودیت تلگرام</b>
⏰ انتظار: {format_seconds(e.seconds)}

📊 پیشرفت: {i}/{total}
✅ موفق: {success}
""",
                        chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
                    )
                except:
                    pass
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                logger.exception(f"Error copying photo {i}: {e}")

        # بلاک کردن کاربر
        try:
            await client(BlockRequest(id=target_id))
        except Exception as e:
            logger.exception(f"Error blocking: {e}")

        # پیام نهایی
        try:
            await context.bot.edit_message_text(
                f"""
✅ <b>عملیات بلاک با آیدی کامل شد!</b>

👤 سلف: <b>{account_name}</b>
🆔 آیدی هدف: <code>{target_id}</code>
📝 نام کاربر: <b>{escape_html(target_name)}</b>
📸 تعداد پروفایل‌ها: <b>{total}</b>
✅ کپی شده: <b>{success}</b>
🚫 کاربر بلاک شد!
""",
                chat_id=chat_id, message_id=status_msg_id,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت به مدیریت", callback_data=f"manage_{index}")],
                    [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="back")]
                ]),
                parse_mode='HTML'
            )
        except:
            pass

    except Exception as e:
        logger.exception(f"Error in block job: {e}")
        try:
            await context.bot.edit_message_text(
                f"❌ <b>خطا:</b> {escape_html(str(e)[:200])}",
                chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
            )
        except:
            pass
    finally:
        await safe_disconnect(client)

# ============ ایجاد سلف جدید ============
async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = query.from_user.id
    await clear_user_state(user_id)
    user_sessions[user_id] = {"step": "phone"}
    text = """
📱 <b>مرحله اول: وارد کردن شماره تلفن</b>

لطفاً شماره تلفن مورد نظر را به همراه کد کشور وارد فرمایید.

<b>مثال:</b> <code>989123456789</code>

⚠️ <b>تذکر:</b> شماره را بدون علامت (+) وارد نمایید.
"""
    keyboard = [[InlineKeyboardButton("🔙 لغو و بازگشت", callback_data="back")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "phone":
        await update.message.reply_text("❌ <b>لطفاً از دکمه ایجاد سلف استفاده فرمایید.</b>", parse_mode='HTML')
        return
    phone = re.sub(r'[^0-9+]', '', text)
    if not is_valid_phone(phone):
        await update.message.reply_text(
            "❌ <b>شماره تلفن نامعتبر است!</b>\n\nلطفاً شماره را به صورت صحیح وارد نمایید.\n<b>مثال:</b> <code>989123456789</code>",
            parse_mode='HTML'
        )
        return
    user_sessions[user_id]['phone'] = phone
    user_sessions[user_id]['step'] = "api_id"
    text = f"✅ <b>شماره تلفن با موفقیت ثبت شد.</b>\n\n📱 شماره: <code>{escape_html(phone)}</code>\n\n🔑 <b>مرحله دوم: وارد کردن API ID</b>\n\nلطفاً API ID خود را از سایت my.telegram.org دریافت و وارد فرمایید."
    keyboard = [[InlineKeyboardButton("🔙 لغو و بازگشت", callback_data="back")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def handle_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "api_id":
        await update.message.reply_text("❌ <b>لطفاً از دکمه ایجاد سلف استفاده فرمایید.</b>", parse_mode='HTML')
        return
    if not text.isdigit():
        await update.message.reply_text("❌ <b>API ID باید عدد باشد.</b>\n\nلطفاً مجدداً وارد نمایید.", parse_mode='HTML')
        return
    user_sessions[user_id]['api_id'] = int(text)
    user_sessions[user_id]['step'] = "api_hash"
    text = f"✅ <b>API ID با موفقیت ثبت شد.</b>\n\n🔑 API ID: <code>{escape_html(text)}</code>\n\n🔐 <b>مرحله سوم: وارد کردن API Hash</b>\n\nلطفاً API Hash خود را از سایت my.telegram.org دریافت و وارد فرمایید."
    keyboard = [[InlineKeyboardButton("🔙 لغو و بازگشت", callback_data="back")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def handle_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "api_hash":
        await update.message.reply_text("❌ <b>لطفاً از دکمه ایجاد سلف استفاده فرمایید.</b>", parse_mode='HTML')
        return
    if len(text) < 30:
        await update.message.reply_text("❌ <b>API Hash باید حداقل 30 کاراکتر باشد.</b>\n\nلطفاً مجدداً وارد نمایید.", parse_mode='HTML')
        return
    user_sessions[user_id]['api_hash'] = text
    user_sessions[user_id]['step'] = "code"
    msg = await update.message.reply_text("⏳ <b>در حال ارسال کد تایید...</b>\n\nلطفاً چند لحظه صبر فرمایید.", parse_mode='HTML')
    client = None
    try:
        data = user_sessions[user_id]
        phone = data['phone']
        api_id = data['api_id']
        api_hash = data['api_hash']
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        await client.send_code_request(phone)
        user_sessions[user_id]['client'] = client
        user_sessions[user_id]['msg_id'] = msg.message_id
        text = f"✅ <b>کد تایید با موفقیت ارسال شد.</b>\n\n📩 کد ۵ رقمی به شماره <code>{escape_html(phone)}</code> ارسال گردید.\n\n📝 لطفاً کد دریافتی را وارد فرمایید.\n\n<b>مثال:</b> <code>12345</code> یا <code>1.2.3.4.5</code>"
        keyboard = [[InlineKeyboardButton("🔙 لغو و بازگشت", callback_data="back")]]
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, message_id=msg.message_id,
            text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )
    except PhoneNumberInvalidError:
        await safe_disconnect(client)
        await context.bot.edit_message_text("❌ <b>شماره تلفن نامعتبر است!</b>", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode='HTML')
        await clear_user_state(user_id)
    except FloodWaitError as e:
        await safe_disconnect(client)
        await context.bot.edit_message_text(f"⏳ <b>محدودیت تلگرام!</b>\n{format_seconds(e.seconds)} صبر کنید...", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode='HTML')
        await clear_user_state(user_id)
    except Exception as e:
        logger.exception(f"Error sending code: {e}")
        await safe_disconnect(client)
        await context.bot.edit_message_text("❌ <b>خطا در ارسال کد!</b>\nلطفاً دوباره تلاش کنید.", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode='HTML')
        await clear_user_state(user_id)

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw_code = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "code":
        await update.message.reply_text("❌ <b>لطفاً از دکمه ایجاد سلف استفاده فرمایید.</b>", parse_mode='HTML')
        return
    code = raw_code.replace('.', '').replace(' ', '').replace('-', '').strip()
    if not code.isdigit() or len(code) != 5:
        await update.message.reply_text("❌ <b>کد باید ۵ رقم باشد.</b>\n\n<b>مثال:</b> <code>12345</code>", parse_mode='HTML')
        return
    data = user_sessions[user_id]
    client = data.get('client')
    if not client:
        await update.message.reply_text("❌ <b>اتصال معتبر نیست.</b>", parse_mode='HTML')
        await clear_user_state(user_id)
        return
    try:
        phone = data['phone']
        api_id = data['api_id']
        api_hash = data['api_hash']
        await client.sign_in(phone, code)
        session_string = client.session.save()
        await client.disconnect()
        account_name = "بدون نام"
        try:
            client2 = TelegramClient(StringSession(session_string), api_id, api_hash)
            await client2.connect()
            if await client2.is_user_authorized():
                me = await client2.get_me()
                if me and me.first_name:
                    account_name = me.first_name
                elif me and me.username:
                    account_name = me.username
            await client2.disconnect()
        except Exception as e:
            logger.exception(f"Error getting account name: {e}")
        user_id_str = str(user_id)
        if user_id_str not in self_data:
            self_data[user_id_str] = []
        for existing in self_data[user_id_str]:
            if existing.get('phone') == phone:
                await update.message.reply_text("⚠️ <b>این شماره قبلاً ثبت شده است!</b>", parse_mode='HTML')
                await clear_user_state(user_id)
                return
        time_str = get_iran_time_str()
        date_str = get_iran_date_str()
        new_account = {
            "session": session_string, "phone": phone, "api_id": api_id, "api_hash": api_hash,
            "account_name": account_name, "active": True, "clock_active": False,
            "active_time": "تنظیم نشده", "font_type": "1",
            "created": f"{date_str} {time_str}", "last_update": f"{date_str} {time_str}"
        }
        self_data[user_id_str].append(new_account)
        await save_data()
        await clear_user_state(user_id)
        new_index = len(self_data[user_id_str]) - 1
        await start_self_client(user_id, new_index)
        text = f"✅ <b>سلف جدید با موفقیت ایجاد شد!</b>\n\n📱 شماره: <code>{escape_html(phone)}</code>\n👤 نام اکانت: <b>{escape_html(account_name)}</b>\n\nسلف جدید به لیست شما اضافه گردید.\n🔹 سلف به‌طور خودکار فعال شده است!\n📌 حالا می‌توانید به پیوی دوستان خود بروید و \"بلاک\" بنویسید تا بلاک شوند."
        keyboard = [
            [InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")],
            [InlineKeyboardButton("📋 مشاهده سلف‌ها", callback_data="list_selfs")],
            [InlineKeyboardButton("🏠 بازگشت به صفحه اصلی", callback_data="back")]
        ]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except SessionPasswordNeededError:
        user_sessions[user_id]['step'] = "password"
        text = "🔐 <b>رمز عبور دو مرحله‌ای</b>\n\nحساب کاربری مورد نظر دارای رمز عبور دو مرحله‌ای می‌باشد.\n\nلطفاً رمز عبور خود را وارد فرمایید."
        keyboard = [[InlineKeyboardButton("🔙 لغو و بازگشت", callback_data="back")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except PhoneCodeExpiredError:
        await client.send_code_request(phone)
        await update.message.reply_text("🔄 <b>کد قبلی منقضی شده است.</b>\n\n📩 کد جدید ارسال گردید.\n\n📝 لطفاً کد جدید را وارد فرمایید:", parse_mode='HTML')
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ <b>کد اشتباه است!</b>\n\nلطفاً مجدداً وارد کنید.", parse_mode='HTML')
    except Exception as e:
        logger.exception(f"Error in handle_code: {e}")
        await update.message.reply_text("❌ <b>خطا در ورود!</b>\nلطفاً دوباره تلاش کنید.", parse_mode='HTML')
        await clear_user_state(user_id)

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "password":
        await update.message.reply_text("❌ <b>لطفاً از دکمه ایجاد سلف استفاده فرمایید.</b>", parse_mode='HTML')
        return
    data = user_sessions[user_id]
    client = data.get('client')
    if not client:
        await update.message.reply_text("❌ <b>اتصال معتبر نیست.</b>", parse_mode='HTML')
        await clear_user_state(user_id)
        return
    try:
        await client.sign_in(password=password)
        session_string = client.session.save()
        await client.disconnect()
        account_name = "بدون نام"
        try:
            client2 = TelegramClient(StringSession(session_string), data['api_id'], data['api_hash'])
            await client2.connect()
            if await client2.is_user_authorized():
                me = await client2.get_me()
                if me and me.first_name:
                    account_name = me.first_name
                elif me and me.username:
                    account_name = me.username
            await client2.disconnect()
        except Exception as e:
            logger.exception(f"Error getting account name: {e}")
        user_id_str = str(user_id)
        if user_id_str not in self_data:
            self_data[user_id_str] = []
        for existing in self_data[user_id_str]:
            if existing.get('phone') == data['phone']:
                await update.message.reply_text("⚠️ <b>این شماره قبلاً ثبت شده است!</b>", parse_mode='HTML')
                await clear_user_state(user_id)
                return
        time_str = get_iran_time_str()
        date_str = get_iran_date_str()
        new_account = {
            "session": session_string, "phone": data['phone'], "api_id": data['api_id'], "api_hash": data['api_hash'],
            "account_name": account_name, "active": True, "clock_active": False,
            "active_time": "تنظیم نشده", "font_type": "1",
            "created": f"{date_str} {time_str}", "last_update": f"{date_str} {time_str}"
        }
        self_data[user_id_str].append(new_account)
        await save_data()
        await clear_user_state(user_id)
        new_index = len(self_data[user_id_str]) - 1
        await start_self_client(user_id, new_index)
        text = f"✅ <b>سلف جدید با موفقیت ایجاد شد!</b>\n\n📱 شماره: <code>{escape_html(data['phone'])}</code>\n👤 نام اکانت: <b>{escape_html(account_name)}</b>\n\nسلف جدید به لیست شما اضافه گردید.\n🔹 سلف به‌طور خودکار فعال شده است!"
        keyboard = [
            [InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")],
            [InlineKeyboardButton("📋 مشاهده سلف‌ها", callback_data="list_selfs")],
            [InlineKeyboardButton("🏠 بازگشت به صفحه اصلی", callback_data="back")]
        ]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except Exception as e:
        logger.exception(f"Error in handle_password: {e}")
        await update.message.reply_text("❌ <b>رمز عبور اشتباه است!</b>\n\nلطفاً دوباره وارد کنید.", parse_mode='HTML')

# ============ بازگشت ============
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = query.from_user.id
    await clear_user_state(user_id)
    if 'profile_files' in context.user_data:
        for file_path in context.user_data['profile_files']:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
        del context.user_data['profile_files']
    context.user_data.pop('profile_step', None)
    context.user_data.pop('profile_index', None)
    context.user_data.pop('block_step', None)
    context.user_data.pop('block_index', None)
    await main_menu(update, context, edit=True)

# ============ هندلر پیام‌ها ============
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # چک حالت ساخت سلف
    if user_id in user_sessions:
        step = user_sessions[user_id].get("step")
        if step == "phone":
            await handle_phone(update, context); return
        elif step == "api_id":
            await handle_api_id(update, context); return
        elif step == "api_hash":
            await handle_api_hash(update, context); return
        elif step == "code":
            await handle_code(update, context); return
        elif step == "password":
            await handle_password(update, context); return

    # چک حالت تنظیم پروفایل
    if 'profile_step' in context.user_data:
        step = context.user_data['profile_step']
        if step == 'waiting_media':
            await handle_profile_media(update, context); return
        elif step == 'waiting_count':
            await handle_profile_count(update, context); return

    # چک حالت بلاک با آیدی
    if context.user_data.get('block_step') == 'waiting_id':
        await handle_block_id(update, context); return

    # هیچ حالتی فعال نیست
    await update.message.reply_text(
        "❌ <b>لطفاً از دکمه‌های منو استفاده فرمایید.</b>\n\n"
        "برای شروع، روی دکمه‌های زیر کلیک کنید:\n"
        "• 🔷 ایجاد سلف جدید\n"
        "• 📋 لیست سلف‌ها\n"
        "• 🎨 فونت ساعت",
        parse_mode='HTML'
    )

# ============ اجرا ============
def main():
    try:
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook"
            with urllib.request.urlopen(url, timeout=5) as response:
                pass
        except:
            pass

        print("=" * 60)
        print("🌟 ربات مدیریت حساب‌های شخصی")
        print("=" * 60)
        print("✅ ربات با موفقیت راه‌اندازی شد.")
        print("💡 برای شروع از /start استفاده فرمایید.")
        print("=" * 60)

        application = Application.builder().token(TOKEN).build()

        # Callback handlers - ترتیب مهم است!
        application.add_handler(CallbackQueryHandler(new_session, pattern="^new_session$"))
        application.add_handler(CallbackQueryHandler(list_selfs, pattern="^list_selfs$"))
        application.add_handler(CallbackQueryHandler(manage_self, pattern="^manage_"))
        application.add_handler(CallbackQueryHandler(font_settings, pattern="^font_settings$"))
        application.add_handler(CallbackQueryHandler(font_select, pattern="^font_select_"))
        application.add_handler(CallbackQueryHandler(font_apply, pattern="^font_apply_"))
        application.add_handler(CallbackQueryHandler(connect_self, pattern="^connect_self_"))
        application.add_handler(CallbackQueryHandler(disconnect_self, pattern="^disconnect_self_"))
        application.add_handler(CallbackQueryHandler(new_profile, pattern="^new_profile_"))
        application.add_handler(CallbackQueryHandler(done_profile, pattern="^done_profile_"))
        application.add_handler(CallbackQueryHandler(add_to_job, pattern="^add_to_job_"))
        application.add_handler(CallbackQueryHandler(cancel_profile, pattern="^cancel_profile$"))
        application.add_handler(CallbackQueryHandler(block_by_id, pattern="^block_by_id_"))
        application.add_handler(CallbackQueryHandler(activate_clock, pattern="^activate_clock_"))
        application.add_handler(CallbackQueryHandler(deactivate_clock, pattern="^deactivate_clock_"))
        application.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back$"))

        application.add_handler(CommandHandler("start", main_menu))

        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND | filters.PHOTO | filters.VIDEO | filters.Document.ALL,
                handle_messages
            )
        )

        async def error_handler(update, context):
            if "Conflict" in str(context.error):
                logger.warning("Conflict error - ignoring")
                return
            logger.error(f"Update {update} caused error {context.error}")

        application.add_error_handler(error_handler)

        application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        print(f"❌ خطا: {e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 ربات متوقف شد.")
