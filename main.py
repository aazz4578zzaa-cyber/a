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
from telethon.tl.types import User
import urllib.request

# ============ تنظیمات ============
TOKEN = os.environ.get("TOKEN", "8692551717:AAFnJeLHoQEPWXsyMoCIPxMHBIvj8wyQz4Y")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ تنظیمات ثابت ============
IRAN_TZ = ZoneInfo("Asia/Tehran")
DATA_FILE = os.environ.get("DATA_FILE", "/data/selfs.json")
DATA_BACKUP_FILE = os.environ.get("DATA_BACKUP_FILE", "/data/selfs_backup.json")

# اگه مسیر /data وجود نداشت، از مسیر لوکال استفاده کن
if not os.path.isdir(os.path.dirname(DATA_FILE) or "."):
    DATA_FILE = "selfs.json"
    DATA_BACKUP_FILE = "selfs_backup.json"

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PROFILE_COUNT = 5000
MAX_PER_DAY = 500

# ============ State ============
user_sessions = {}
self_data = {}
clock_tasks = {}
profile_tasks = {}
self_clients = {}
self_tasks = {}
DATA_LOCK = asyncio.Lock()
is_shutting_down = False
_save_lock = asyncio.Lock()

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
    except Exception as e:
        logger.exception(f"Load error: {e}")
        self_data = {}

async def save_data():
    async with _save_lock:
        try:
            temp_file = DATA_FILE + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self_data, f, ensure_ascii=False, indent=2)
            if os.path.exists(DATA_FILE):
                try:
                    shutil.copy2(DATA_FILE, DATA_BACKUP_FILE)
                except:
                    pass
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
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if days > 0: parts.append(f"{days} روز")
    if hours > 0: parts.append(f"{hours} ساعت")
    if minutes > 0: parts.append(f"{minutes} دقیقه")
    if secs > 0 or not parts: parts.append(f"{secs} ثانیه")
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
        sa = selfs[account_index]
        if sa.get('active', True):
            return {
                'session': sa.get('session'),
                'api_id': sa.get('api_id'),
                'api_hash': sa.get('api_hash'),
                'phone': sa.get('phone'),
                'index': account_index,
                'data': sa
            }
    return None

async def start_self_client(user_id, account_index):
    try:
        key = get_self_key(user_id, account_index)
        session_data = await get_user_session_by_index(user_id, account_index)
        if not session_data:
            return False
        if key in self_clients:
            try: await self_clients[key].disconnect()
            except: pass
            del self_clients[key]
        if key in self_tasks:
            self_tasks[key].cancel()
            try: await self_tasks[key]
            except: pass
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

        # ====== هندلر پیام‌های خروجی: .پنل و .بلاک ======
        @client.on(events.NewMessage(outgoing=True))
        async def outgoing_handler(event):
            try:
                msg = event.message
                if not msg or not msg.text:
                    return
                text = msg.text.strip()

                # --- دستور .پنل ---
                if text == ".پنل":
                    try:
                        await event.delete()
                    except:
                        pass
                    try:
                        await send_panel_to_self(client, self_user_id, account_index)
                    except Exception as e:
                        logger.exception(f"panel err: {e}")
                    return

                # --- دستور .بلاک ---
                if text == ".بلاک":
                    await handle_self_block(client, event, self_user_id)
                    return

            except Exception as e:
                logger.exception(f"outgoing handler error: {e}")

        # ====== هندلر پیام‌های ورودی: .بلاک ======
        @client.on(events.NewMessage(incoming=True))
        async def incoming_handler(event):
            try:
                msg = event.message
                if not msg or not msg.text:
                    return
                if msg.text.strip() != ".بلاک":
                    return
                await handle_self_block(client, event, self_user_id, is_incoming=True)
            except Exception as e:
                logger.exception(f"incoming handler error: {e}")

        async def run_client():
            try:
                await client.run_until_disconnected()
            except Exception as e:
                if not is_shutting_down:
                    logger.exception(f"Client disconnected for {key}: {e}")

        task = asyncio.create_task(run_client())
        self_tasks[key] = task
        logger.info(f"Self client started for {user_id} idx {account_index}")
        return True
    except Exception as e:
        logger.exception(f"Error starting self client: {e}")
        return False

async def stop_self_client(user_id, account_index):
    key = get_self_key(user_id, account_index)
    if key in self_clients:
        try: await self_clients[key].disconnect()
        except: pass
        del self_clients[key]
    if key in self_tasks:
        self_tasks[key].cancel()
        try: await self_tasks[key]
        except: pass
        del self_tasks[key]

async def handle_self_block(client, event, self_user_id, is_incoming=False):
    """هندل دستور .بلاک — پیوی یا ریپلای گروه"""
    try:
        msg = event.message
        target = None

        # اگه ریپلای هست
        if msg.is_reply:
            try:
                replied = await event.get_reply_message()
                if replied:
                    sender_id = replied.sender_id
                    if sender_id and sender_id != self_user_id:
                        target = await client.get_entity(sender_id)
            except Exception as e:
                logger.exception(f"reply get err: {e}")

        # اگه پیوی هست و ریپلای نیست، خود فرستنده پیوی هدفه
        if not target and event.is_private:
            try:
                peer = await event.get_chat()
                if peer and peer.id != self_user_id:
                    target = peer
            except Exception as e:
                logger.exception(f"peer err: {e}")

        if not target:
            try:
                await event.reply("⚠️ کاربری برای بلاک پیدا نشد.")
            except: pass
            return

        await client(BlockRequest(id=target.id))
        name = getattr(target, 'first_name', '') or getattr(target, 'username', '') or str(target.id)
        try:
            await event.reply(f"🚫 کاربر {name} بلاک شد!")
        except:
            try:
                await client.send_message(event.chat_id, f"🚫 کاربر {name} بلاک شد!")
            except: pass
        logger.info(f"Blocked {target.id} via .بلاک")
    except Exception as e:
        logger.exception(f"block cmd err: {e}")
        try:
            await event.reply(f"❌ خطا: {str(e)[:100]}")
        except: pass

async def send_panel_to_self(client, self_user_id, account_index):
    """ارسال پنل به Saved Messages با دکمه‌های Inline"""
    # پیدا کردن شماره سلف در self_data
    owner_id = None
    for uid, arr in self_data.items():
        if 0 <= account_index < len(arr):
            # چک می‌کنیم که آیا این کلاینت همون سلفه؟ با شماره تطبیق می‌دیم
            try:
                me = await client.get_me()
                if arr[account_index].get('phone') and me.phone and str(me.phone) in str(arr[account_index].get('phone')):
                    owner_id = uid
                    break
            except:
                pass

    # اگه پیدا نشد از اولین user_id استفاده کن
    if owner_id is None:
        # fallback: پنل عمومی
        text = (
            "🌟 <b>پنل مدیریت سلف</b>\n\n"
            "این پنل از داخل اکانت سلف فعال شد.\n"
            "برای مدیریت کامل، ربات اصلی رو باز کنید.\n\n"
            "📌 دستورات در دسترس:\n"
            "• <code>.پنل</code> — نمایش این پنل\n"
            "• <code>.بلاک</code> — بلاک کردن کاربر (پیوی/ریپلای)"
        )
        try:
            await client.send_message(self_user_id, text, parse_mode='html')
        except Exception as e:
            logger.exception(f"panel send err: {e}")
        return

    sa = self_data[owner_id][account_index]
    name = escape_html(sa.get('account_name', 'کاربر'))
    phone = escape_html(sa.get('phone', '-'))
    clock = "🟢 فعال" if sa.get('clock_active') else "🔴 غیرفعال"
    font = FONT_NAMES.get(sa.get('font_type', '1'), 'فونت 1')

    text = f"""
🌟 <b>پنل مدیریت سلف</b>

👤 نام: <b>{name}</b>
📱 شماره: <code>{phone}</code>
⏰ ساعت: {clock}
🎨 فونت: {font}

📌 <b>دستورات در دسترس:</b>
• <code>.پنل</code> — نمایش این پنل
• <code>.بلاک</code> — بلاک کاربر (پیوی/ریپلای)

💡 برای مدیریت کامل (پروفایل، ساعت، ...) ربات اصلی رو باز کنید.
"""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 بروزرسانی پنل", url="https://t.me")],
    ])
    try:
        await client.send_message(self_user_id, text, parse_mode='html', link_preview=False)
    except Exception as e:
        logger.exception(f"panel send err: {e}")

# ============ توابع ساعت ============
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
        logger.exception(f"set_clock err: {e}")
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
        logger.exception(f"remove_clock err: {e}")
        return False
    finally:
        await safe_disconnect(client)

async def clock_loop(user_id, account_index, session_string, api_id, api_hash, font_type):
    """حلقه ساعت - بسیار سریع"""
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
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"clock loop err: {e}")
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
            logger.exception(f"main_menu edit err: {e}")
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )

# ============ لیست سلف‌ها ============
async def list_selfs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    selfs = self_data.get(user_id, [])
    if not selfs:
        text = "📋 <b>لیست سلف‌ها</b>\n\n❌ <b>هیچ سلفی ثبت نشده است.</b>"
        keyboard = [
            [InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    text = f"📋 <b>لیست سلف‌های ثبت شده ({len(selfs)})</b>\n\n"
    keyboard = []
    for i, sa in enumerate(selfs):
        phone = escape_html(sa.get('phone', 'نامشخص'))
        active_time = escape_html(sa.get('active_time', 'تنظیم نشده'))
        account_name = escape_html(sa.get('account_name', 'بدون نام'))
        clock_active = sa.get('clock_active', False)
        font_type = sa.get('font_type', '1')
        font_name = FONT_NAMES.get(font_type, 'فونت 1')
        clock_status = "🟢 فعال" if clock_active else "🔴 غیرفعال"
        text += f"""
🔹 <b>سلف شماره {i+1}</b>
   📱 شماره: <code>{phone}</code>
   👤 نام: <b>{account_name}</b>
   🕐 ساعت: <code>{active_time}</code>
   🎨 فونت: {font_name}
   📊 وضعیت: {clock_status}
"""
        keyboard.append([InlineKeyboardButton(f"⚙️ مدیریت سلف {i+1}", callback_data=f"manage_{i}")])
    keyboard.append([InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ============ مدیریت سلف ============
async def manage_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[1])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف یافت نشد.</b>", parse_mode='HTML'); return
    sa = selfs[index]
    phone = escape_html(sa.get('phone', 'نامشخص'))
    account_name = escape_html(sa.get('account_name', 'بدون نام'))
    clock_active = sa.get('clock_active', False)
    active_time = escape_html(sa.get('active_time', 'تنظیم نشده'))
    font_type = sa.get('font_type', '1')
    font_name = FONT_NAMES.get(font_type, 'فونت 1')
    key = get_self_key(int(user_id), index)
    is_connected = key in self_clients
    clock_status = "🟢 فعال" if clock_active else "🔴 غیرفعال"
    status_self = "🟢 متصل" if is_connected else "🔴 قطع"
    job_running = user_id in profile_tasks and not profile_tasks[user_id].done()

    text = f"""
⚙️ <b>مدیریت سلف شماره {index + 1}</b>

📱 شماره: <code>{phone}</code>
👤 نام: <b>{account_name}</b>
🕐 ساعت: <code>{active_time}</code>
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
    keyboard.append([InlineKeyboardButton("📋 کپی پروفایل", callback_data=f"copy_profile_{index}")])
    if clock_active:
        keyboard.append([InlineKeyboardButton("⏰ غیرفعال کردن ساعت", callback_data=f"deactivate_clock_{index}")])
        keyboard.append([InlineKeyboardButton("🎨 تغییر فونت", callback_data=f"font_select_{index}")])
    else:
        keyboard.append([InlineKeyboardButton("⏰ فعال کردن ساعت", callback_data=f"activate_clock_{index}")])
    if job_running:
        keyboard.append([InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_profile")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="list_selfs")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ============ اتصال/قطع سلف ============
async def connect_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    result = await start_self_client(int(user_id), index)
    text = f"✅ <b>سلف {index + 1} متصل شد!</b>" if result else f"❌ <b>خطا در اتصال سلف {index + 1}!</b>"
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def disconnect_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    await stop_self_client(int(user_id), index)
    text = f"✅ <b>سلف {index + 1} قطع شد!</b>"
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

# ============ ساعت ============
async def activate_clock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف یافت نشد.</b>", parse_mode='HTML'); return
    sa = selfs[index]
    session_string = sa.get('session'); api_id = sa.get('api_id'); api_hash = sa.get('api_hash')
    font_type = sa.get('font_type', '1')
    time_str = get_iran_time_short()
    font_time = convert_to_font(time_str, font_type)
    result = await set_clock_on_profile(session_string, api_id, api_hash, font_type)
    if result:
        selfs[index]['active_time'] = time_str
        selfs[index]['clock_active'] = True
        await save_data()
        clock_key = get_self_key(int(user_id), index)
        clock_tasks[clock_key] = True
        asyncio.create_task(clock_loop(int(user_id), index, session_string, api_id, api_hash, font_type))
        text = f"✅ <b>ساعت فعال شد!</b>\n\n🕐 <code>{font_time}</code>\n🎨 {FONT_NAMES.get(font_type, 'فونت 1')}"
    else:
        text = "❌ <b>خطا در فعال‌سازی ساعت!</b>"
    kb = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
        [InlineKeyboardButton("🏠 منو", callback_data="back")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def deactivate_clock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف یافت نشد.</b>", parse_mode='HTML'); return
    sa = selfs[index]
    result = await remove_clock_from_profile(sa.get('session'), sa.get('api_id'), sa.get('api_hash'))
    if result:
        selfs[index]['clock_active'] = False
        await save_data()
        clock_key = get_self_key(int(user_id), index)
        if clock_key in clock_tasks:
            clock_tasks[clock_key] = False
            del clock_tasks[clock_key]
        text = "❌ <b>ساعت غیرفعال شد!</b>"
    else:
        text = "❌ <b>خطا در غیرفعال‌سازی!</b>"
    kb = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
        [InlineKeyboardButton("🏠 منو", callback_data="back")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

# ============ فونت ============
async def font_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    selfs = self_data.get(user_id, [])
    if not selfs:
        text = "🎨 <b>تنظیم فونت</b>\n\n❌ سلفی ثبت نشده."
        kb = [
            [InlineKeyboardButton("🔷 ایجاد سلف", callback_data="new_session")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML'); return
    text = "🎨 <b>انتخاب سلف برای تغییر فونت</b>"
    kb = []
    for i, sa in enumerate(selfs):
        nm = escape_html(sa.get('account_name', 'بدون نام'))
        ft = FONT_NAMES.get(sa.get('font_type', '1'), 'فونت 1')
        kb.append([InlineKeyboardButton(f"{i+1}. {nm} - {ft}", callback_data=f"font_select_{i}")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def font_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف یافت نشد.</b>", parse_mode='HTML'); return
    text = "🎨 <b>انتخاب فونت</b>"
    kb = []
    for fid, finfo in FONTS.items():
        kb.append([InlineKeyboardButton(f"{finfo['display']} - {finfo['name']}", callback_data=f"font_apply_{index}_{fid}")])
    kb.append([InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def font_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    parts = query.data.split('_')
    try:
        index = int(parts[2]); font_type = parts[3]
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    if font_type not in FONTS:
        await query.edit_message_text("❌ فونت نامعتبر", parse_mode='HTML'); return
    user_id = str(query.from_user.id)
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف یافت نشد.</b>", parse_mode='HTML'); return
    sa = selfs[index]
    result = await set_clock_on_profile(sa.get('session'), sa.get('api_id'), sa.get('api_hash'), font_type)
    if result:
        selfs[index]['font_type'] = font_type
        t = get_iran_time_short()
        selfs[index]['active_time'] = t
        await save_data()
        clock_key = get_self_key(int(user_id), index)
        if clock_key in clock_tasks and clock_tasks.get(clock_key):
            clock_tasks[clock_key] = False
            del clock_tasks[clock_key]
            clock_tasks[clock_key] = True
            asyncio.create_task(clock_loop(int(user_id), index, sa.get('session'), sa.get('api_id'), sa.get('api_hash'), font_type))
        text = f"✅ فونت تغییر کرد!\n🎨 {FONT_NAMES[font_type]}\n🕐 <code>{convert_to_font(t, font_type)}</code>"
    else:
        text = "❌ خطا در تغییر فونت!"
    kb = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
        [InlineKeyboardButton("🏠 منو", callback_data="back")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

# ============ پروفایل ============
async def new_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف یافت نشد.</b>", parse_mode='HTML'); return
    job_running = user_id in profile_tasks and not profile_tasks[user_id].done()
    context.user_data['profile_index'] = index
    context.user_data['profile_step'] = 'waiting_media'
    context.user_data.setdefault('profile_files', [])
    if job_running:
        text = "📸 <b>افزودن فایل به عملیات در حال اجرا</b>\n\nفایل‌ها به صف اضافه می‌شن."
        kb = [
            [InlineKeyboardButton("✅ افزودن", callback_data=f"add_to_job_{index}")],
            [InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]
        ]
    else:
        text = "📸 <b>تنظیم پروفایل</b>\n\nعکس/فیلم بفرستید (چندتایی) و دکمه اتمام رو بزنید.\n\nحداکثر 10MB"
        kb = [
            [InlineKeyboardButton("✅ اتمام", callback_data=f"done_profile_{index}")],
            [InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]
        ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_profile_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if context.user_data.get('profile_step') != 'waiting_media':
        await update.message.reply_text("❌ از دکمه تنظیم پروفایل استفاده کنید.", parse_mode='HTML'); return
    file = None; ext = None
    if update.message.photo:
        file = await update.message.photo[-1].get_file(); ext = ".jpg"
    elif update.message.video:
        file = await update.message.video.get_file(); ext = ".mp4"
    elif update.message.document:
        file = await update.message.document.get_file()
        name = update.message.document.file_name or ""
        ext = os.path.splitext(name)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png', '.mp4']:
            await update.message.reply_text("❌ فرمت پشتیبانی نمی‌شود!", parse_mode='HTML'); return
    else:
        await update.message.reply_text("❌ فقط عکس/فیلم بفرستید!", parse_mode='HTML'); return
    if file.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("❌ حجم بیش از 10MB!", parse_mode='HTML'); return
    fp = f"temp_{user_id}_{len(context.user_data.get('profile_files', []))}{ext}"
    try:
        await file.download_to_drive(fp)
    except Exception as e:
        logger.exception(f"dl err: {e}")
        await update.message.reply_text("❌ خطای دانلود!", parse_mode='HTML'); return
    context.user_data.setdefault('profile_files', []).append(fp)
    count = len(context.user_data['profile_files'])
    index = context.user_data.get('profile_index', 0)
    job_running = user_id in profile_tasks and not profile_tasks[user_id].done()
    if job_running:
        kb = [
            [InlineKeyboardButton("✅ افزودن به عملیات", callback_data=f"add_to_job_{index}")],
            [InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]
        ]
    else:
        kb = [
            [InlineKeyboardButton("✅ اتمام", callback_data=f"done_profile_{index}")],
            [InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]
        ]
    await update.message.reply_text(f"✅ فایل {count} دریافت شد!", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def done_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف یافت نشد.</b>", parse_mode='HTML'); return
    files = context.user_data.get('profile_files', [])
    if not files:
        await query.edit_message_text("❌ فایلی ارسال نشد!", parse_mode='HTML'); return
    context.user_data['profile_step'] = 'waiting_count'
    text = f"✅ <b>{len(files)} فایل دریافت شد!</b>\n\n🔢 تعداد دفعات هر فایل رو وارد کنید (1 تا {MAX_PROFILE_COUNT}):"
    kb = [[InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_profile_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if context.user_data.get('profile_step') != 'waiting_count':
        await update.message.reply_text("❌ از دکمه تنظیم پروفایل استفاده کنید.", parse_mode='HTML'); return
    try:
        count = int(update.message.text.strip())
        if count < 1 or count > MAX_PROFILE_COUNT:
            await update.message.reply_text(f"❌ تعداد باید بین 1 تا {MAX_PROFILE_COUNT} باشد!", parse_mode='HTML'); return
    except:
        await update.message.reply_text("❌ عدد معتبر وارد کنید!", parse_mode='HTML'); return
    index = context.user_data['profile_index']
    files = context.user_data.get('profile_files', [])
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await update.message.reply_text("❌ سلف یافت نشد!", parse_mode='HTML'); return
    sa = selfs[index]
    session_string = sa.get('session'); api_id = sa.get('api_id'); api_hash = sa.get('api_hash')
    account_name = escape_html(sa.get('account_name', 'کاربر'))
    total_count = len(files) * count
    status_msg = await update.message.reply_text(
        f"🚀 <b>شروع تنظیم پروفایل</b>\n\n👤 {account_name}\n📁 {len(files)} فایل\n🔢 {count} بار هرکدام\n📊 مجموع: {total_count}\n\n⏳ در حال آماده‌سازی...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")]]),
        parse_mode='HTML'
    )
    files_copy = files.copy()
    context.user_data['profile_files'] = []
    context.user_data.pop('profile_step', None)
    task = asyncio.create_task(run_profile_job(
        user_id, index, session_string, api_id, api_hash,
        files_copy, count, update.effective_chat.id, context, status_msg.message_id
    ))
    profile_tasks[user_id] = task

async def add_to_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[3])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    files = context.user_data.get('profile_files', [])
    if not files:
        await query.edit_message_text("❌ فایلی ارسال نشد!", parse_mode='HTML'); return
    if user_id in profile_tasks and not profile_tasks[user_id].done():
        context.user_data.setdefault('pending_files', []).extend(files)
        context.user_data['profile_files'] = []
        await query.edit_message_text(
            f"✅ <b>{len(files)} فایل به صف اضافه شد!</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]),
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text("❌ عملیات در حال اجرا نیست!", parse_mode='HTML')

async def run_profile_job(user_id, index, session_string, api_id, api_hash, files, count, chat_id, context, status_msg_id):
    """سرعت فوق‌العاده بالا — بدون تأخیر اضافه"""
    client = None
    last_edit = 0
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            try:
                await context.bot.edit_message_text("❌ اکانت معتبر نیست!", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
            except: pass
            return
        account_name = escape_html(self_data.get(str(user_id), [])[index].get('account_name', 'کاربر'))
        total_success = 0; total_fail = 0; today_count = 0; day = 1

        # آپلود همه فایل‌ها یک‌بار
        uploaded_files = []
        for file_path in files:
            try:
                uploaded = await client.upload_file(file_path)
                uploaded_files.append((uploaded, file_path))
            except Exception as e:
                logger.exception(f"upload err: {e}")
        if not uploaded_files:
            try:
                await context.bot.edit_message_text("❌ خطا در آپلود فایل‌ها!", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
            except: pass
            return

        total_operations = len(uploaded_files) * count
        file_index = 0

        for uploaded, file_path in uploaded_files:
            file_index += 1
            for i in range(count):
                if user_id not in profile_tasks or profile_tasks[user_id].cancelled():
                    break
                # محدودیت روزانه
                if today_count >= MAX_PER_DAY:
                    now = get_iran_time()
                    tomorrow = now + timedelta(days=1)
                    next_day = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
                    wait_seconds = (next_day - now).total_seconds()
                    if wait_seconds > 0:
                        try:
                            await context.bot.edit_message_text(
                                f"⏳ محدودیت روزانه\n⏰ {format_seconds(wait_seconds)}\n✅ {total_success} ❌ {total_fail}\n📅 روز {day}",
                                chat_id=chat_id, message_id=status_msg_id,
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")]]),
                                parse_mode='HTML'
                            )
                        except: pass
                        await asyncio.sleep(wait_seconds)
                        day += 1; today_count = 0
                try:
                    await client(UploadProfilePhotoRequest(file=uploaded))
                    total_success += 1; today_count += 1
                    # ویرایش هر 1.5 ثانیه
                    now_ts = asyncio.get_event_loop().time()
                    if now_ts - last_edit >= 1.5 or total_success == total_operations:
                        last_edit = now_ts
                        try:
                            await context.bot.edit_message_text(
                                f"🚀 <b>در حال اجرا</b>\n\n👤 {account_name}\n📁 {file_index}/{len(uploaded_files)}\n📊 {total_success}/{total_operations}\n✅ {total_success} ❌ {total_fail}\n📅 روز {day}",
                                chat_id=chat_id, message_id=status_msg_id,
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")]]),
                                parse_mode='HTML'
                            )
                        except: pass
                    # ====== تأخیر فوق‌العاده کم ======
                    await asyncio.sleep(0.001)
                except FloodWaitError as e:
                    wt = e.seconds
                    try:
                        await context.bot.edit_message_text(
                            f"⏳ <b>محدودیت تلگرام</b>\n⏰ {format_seconds(wt)}\n✅ {total_success} ❌ {total_fail}\n📅 روز {day}",
                            chat_id=chat_id, message_id=status_msg_id,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")]]),
                            parse_mode='HTML'
                        )
                    except: pass
                    await asyncio.sleep(wt + 2)
                    try:
                        await client(UploadProfilePhotoRequest(file=uploaded))
                        total_success += 1; today_count += 1
                    except Exception as e2:
                        logger.exception(f"retry err: {e2}"); total_fail += 1
                except Exception as e:
                    logger.exception(f"prof err: {e}")
                    total_fail += 1
                    await asyncio.sleep(0.05)
        try:
            await context.bot.edit_message_text(
                f"✅ <b>پایان!</b>\n\n👤 {account_name}\n✅ موفق: {total_success}\n❌ ناموفق: {total_fail}\n📁 {len(uploaded_files)} فایل\n📅 {day} روز",
                chat_id=chat_id, message_id=status_msg_id,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
                    [InlineKeyboardButton("🏠 منو", callback_data="back")]
                ]),
                parse_mode='HTML'
            )
        except: pass
        for _, fp in uploaded_files:
            try:
                if os.path.exists(fp): os.remove(fp)
            except: pass
        if user_id in profile_tasks: del profile_tasks[user_id]
    except asyncio.CancelledError:
        logger.info(f"profile job cancelled: {user_id}")
        for fp in files:
            try:
                if os.path.exists(fp): os.remove(fp)
            except: pass
        if user_id in profile_tasks: del profile_tasks[user_id]
        try:
            await context.bot.edit_message_text("❌ عملیات لغو شد!", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
        except: pass
        raise
    except Exception as e:
        logger.exception(f"profile job err: {e}")
        try:
            await context.bot.edit_message_text(f"❌ خطا: {escape_html(str(e)[:200])}", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
        except: pass
    finally:
        await safe_disconnect(client)

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    if user_id in profile_tasks:
        profile_tasks[user_id].cancel()
        try: await profile_tasks[user_id]
        except: pass
        if user_id in profile_tasks: del profile_tasks[user_id]
    if 'profile_files' in context.user_data:
        for fp in context.user_data['profile_files']:
            try:
                if os.path.exists(fp): os.remove(fp)
            except: pass
        del context.user_data['profile_files']
    context.user_data.pop('profile_step', None)
    context.user_data.pop('profile_index', None)
    try:
        await query.edit_message_text("❌ <b>عملیات لغو شد!</b>", parse_mode='HTML')
    except: pass

# ============ کپی پروفایل (بدون بلاک) ============
async def copy_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست آیدی از کاربر برای کپی پروفایل"""
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = str(query.from_user.id)
    parts = query.data.split('_')
    try: index = int(parts[2])
    except:
        await query.edit_message_text("❌ خطا", parse_mode='HTML'); return
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await query.edit_message_text("❌ <b>سلف یافت نشد.</b>", parse_mode='HTML'); return
    context.user_data['copy_index'] = index
    context.user_data['copy_step'] = 'waiting_id'
    text = (
        "📋 <b>کپی پروفایل</b>\n\n"
        "آیدی عددی (User ID) کاربر مورد نظر رو وارد کنید.\n\n"
        "<b>مثال:</b> <code>123456789</code>\n\n"
        "⚡ تمام پروفایل‌های کاربر با سرعت فوق‌العاده بالا کپی می‌شن (بدون بلاک)."
    )
    kb = [[InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_copy_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if context.user_data.get('copy_step') != 'waiting_id':
        return
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ آیدی باید عدد باشد!", parse_mode='HTML'); return
    target_id = int(text)
    index = context.user_data.get('copy_index')
    context.user_data.pop('copy_step', None)
    context.user_data.pop('copy_index', None)
    selfs = self_data.get(user_id, [])
    if not (0 <= index < len(selfs)):
        await update.message.reply_text("❌ سلف یافت نشد!", parse_mode='HTML'); return
    sa = selfs[index]
    status_msg = await update.message.reply_text(
        f"🔄 <b>شروع کپی پروفایل</b>\n\n🆔 آیدی: <code>{target_id}</code>\n\n⏳ در حال دریافت اطلاعات...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]),
        parse_mode='HTML'
    )
    asyncio.create_task(run_copy_job(
        user_id, index, sa.get('session'), sa.get('api_id'), sa.get('api_hash'),
        target_id, update.effective_chat.id, context, status_msg.message_id
    ))

async def run_copy_job(user_id, index, session_string, api_id, api_hash, target_id, chat_id, context, status_msg_id):
    """کپی همه پروفایل‌های کاربر با سرعت فوق‌العاده بالا (بدون بلاک)"""
    client = None
    last_edit = 0
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            try:
                await context.bot.edit_message_text("❌ اکانت معتبر نیست!", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
            except: pass
            return
        account_name = escape_html(self_data.get(str(user_id), [])[index].get('account_name', 'کاربر'))

        # دریافت اطلاعات کاربر
        try:
            target = await client.get_entity(target_id)
        except Exception as e:
            logger.exception(f"get target err: {e}")
            try:
                await context.bot.edit_message_text(f"❌ کاربر با آیدی {target_id} یافت نشد!", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
            except: pass
            return
        target_name = (target.first_name or "") + (" " + target.last_name if target.last_name else "") or target.username or str(target_id)

        try:
            await context.bot.edit_message_text(
                f"🔄 <b>در حال دریافت پروفایل‌ها...</b>\n\n👤 سلف: {account_name}\n🆔 {target_id}\n📝 {escape_html(target_name)}",
                chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
            )
        except: pass

        # دریافت لیست پروفایل‌ها
        photo_list = []
        try:
            photos = await client(GetUserPhotosRequest(user_id=target_id, offset=0, max_id=0, limit=1000))
            photo_list = photos.photos if hasattr(photos, 'photos') else []
        except Exception as e:
            logger.exception(f"get photos err: {e}")

        total = len(photo_list)
        if total == 0:
            try:
                await context.bot.edit_message_text(
                    f"⚠️ کاربر {escape_html(target_name)} هیچ پروفایلی نداره!",
                    chat_id=chat_id, message_id=status_msg_id,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]),
                    parse_mode='HTML'
                )
            except: pass
            await safe_disconnect(client)
            return

        success = 0; fail = 0; last_edit = 0

        # ====== سرعت فوق‌العاده: اولین پروفایل رو فوری ست کن ======
        for i, photo in enumerate(photo_list):
            try:
                fp = f"cp_{user_id}_{target_id}_{i}.jpg"
                await client.download_media(photo, fp)
                if os.path.exists(fp):
                    uploaded = await client.upload_file(fp)
                    await client(UploadProfilePhotoRequest(file=uploaded))
                    success += 1
                    try: os.remove(fp)
                    except: pass
                # ====== تأخیر صفر ======
                now_ts = asyncio.get_event_loop().time()
                if now_ts - last_edit >= 1.2 or i == total - 1:
                    last_edit = now_ts
                    try:
                        await context.bot.edit_message_text(
                            f"🚀 <b>کپی پروفایل</b>\n\n👤 {account_name}\n🆔 {target_id}\n📝 {escape_html(target_name)}\n📊 {i+1}/{total}\n✅ {success} ❌ {fail}",
                            chat_id=chat_id, message_id=status_msg_id,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")]]),
                            parse_mode='HTML'
                        )
                    except: pass
            except FloodWaitError as e:
                wt = e.seconds
                try:
                    await context.bot.edit_message_text(
                        f"⏳ <b>محدودیت تلگرام</b>\n⏰ {format_seconds(wt)}\n📊 {i+1}/{total}",
                        chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML'
                    )
                except: pass
                await asyncio.sleep(wt + 2)
                # تلاش مجدد
                try:
                    fp = f"cp_{user_id}_{target_id}_{i}.jpg"
                    if os.path.exists(fp):
                        uploaded = await client.upload_file(fp)
                        await client(UploadProfilePhotoRequest(file=uploaded))
                        success += 1
                        try: os.remove(fp)
                        except: pass
                except Exception as e2:
                    logger.exception(f"retry err: {e2}")
            except Exception as e:
                logger.exception(f"copy err {i}: {e}")
                fail += 1

        try:
            await context.bot.edit_message_text(
                f"✅ <b>کپی پروفایل کامل شد!</b>\n\n👤 سلف: {account_name}\n📝 کاربر: {escape_html(target_name)}\n📸 کل: {total}\n✅ موفق: {success}\n❌ ناموفق: {fail}",
                chat_id=chat_id, message_id=status_msg_id,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
                    [InlineKeyboardButton("🏠 منو", callback_data="back")]
                ]),
                parse_mode='HTML'
            )
        except: pass
    except Exception as e:
        logger.exception(f"copy job err: {e}")
        try:
            await context.bot.edit_message_text(f"❌ خطا: {escape_html(str(e)[:200])}", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
        except: pass
    finally:
        await safe_disconnect(client)

# ============ ایجاد سلف جدید ============
async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = query.from_user.id
    await clear_user_state(user_id)
    user_sessions[user_id] = {"step": "phone"}
    text = "📱 <b>شماره تلفن</b>\n\nبا کد کشور (مثال: <code>989123456789</code>)"
    kb = [[InlineKeyboardButton("🔙 لغو", callback_data="back")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "phone":
        await update.message.reply_text("❌ از دکمه ایجاد سلف استفاده کنید.", parse_mode='HTML'); return
    phone = re.sub(r'[^0-9+]', '', text)
    if not is_valid_phone(phone):
        await update.message.reply_text("❌ شماره نامعتبر!\nمثال: <code>989123456789</code>", parse_mode='HTML'); return
    user_sessions[user_id]['phone'] = phone
    user_sessions[user_id]['step'] = "api_id"
    text = f"✅ شماره: <code>{escape_html(phone)}</code>\n\n🔑 <b>API ID</b> رو وارد کنید:"
    kb = [[InlineKeyboardButton("🔙 لغو", callback_data="back")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "api_id":
        await update.message.reply_text("❌ از دکمه ایجاد سلف استفاده کنید.", parse_mode='HTML'); return
    if not text.isdigit():
        await update.message.reply_text("❌ API ID باید عدد باشد.", parse_mode='HTML'); return
    user_sessions[user_id]['api_id'] = int(text)
    user_sessions[user_id]['step'] = "api_hash"
    text = f"✅ API ID: <code>{escape_html(text)}</code>\n\n🔐 <b>API Hash</b> رو وارد کنید:"
    kb = [[InlineKeyboardButton("🔙 لغو", callback_data="back")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "api_hash":
        await update.message.reply_text("❌ از دکمه ایجاد سلف استفاده کنید.", parse_mode='HTML'); return
    if len(text) < 30:
        await update.message.reply_text("❌ API Hash نامعتبر.", parse_mode='HTML'); return
    user_sessions[user_id]['api_hash'] = text
    user_sessions[user_id]['step'] = "code"
    msg = await update.message.reply_text("⏳ در حال ارسال کد...", parse_mode='HTML')
    client = None
    try:
        data = user_sessions[user_id]
        client = TelegramClient(StringSession(), data['api_id'], data['api_hash'])
        await client.connect()
        await client.send_code_request(data['phone'])
        user_sessions[user_id]['client'] = client
        text = f"✅ کد ارسال شد به <code>{escape_html(data['phone'])}</code>\n\n📝 کد ۵ رقمی رو وارد کنید:"
        kb = [[InlineKeyboardButton("🔙 لغو", callback_data="back")]]
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except PhoneNumberInvalidError:
        await safe_disconnect(client)
        await context.bot.edit_message_text("❌ شماره نامعتبر!", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode='HTML')
        await clear_user_state(user_id)
    except FloodWaitError as e:
        await safe_disconnect(client)
        await context.bot.edit_message_text(f"⏳ محدودیت! {format_seconds(e.seconds)}", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode='HTML')
        await clear_user_state(user_id)
    except Exception as e:
        logger.exception(f"send code err: {e}")
        await safe_disconnect(client)
        await context.bot.edit_message_text("❌ خطا در ارسال کد!", chat_id=update.effective_chat.id, message_id=msg.message_id, parse_mode='HTML')
        await clear_user_state(user_id)

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "code":
        await update.message.reply_text("❌ از دکمه ایجاد سلف استفاده کنید.", parse_mode='HTML'); return
    code = raw.replace('.', '').replace(' ', '').replace('-', '').strip()
    if not code.isdigit() or len(code) != 5:
        await update.message.reply_text("❌ کد باید ۵ رقم باشد.", parse_mode='HTML'); return
    data = user_sessions[user_id]
    client = data.get('client')
    if not client:
        await update.message.reply_text("❌ اتصال معتبر نیست.", parse_mode='HTML')
        await clear_user_state(user_id); return
    try:
        phone = data['phone']; api_id = data['api_id']; api_hash = data['api_hash']
        await client.sign_in(phone, code)
        session_string = client.session.save()
        await client.disconnect()
        account_name = "بدون نام"
        try:
            c2 = TelegramClient(StringSession(session_string), api_id, api_hash)
            await c2.connect()
            if await c2.is_user_authorized():
                me = await c2.get_me()
                if me and me.first_name: account_name = me.first_name
                elif me and me.username: account_name = me.username
            await c2.disconnect()
        except Exception as e:
            logger.exception(f"get name err: {e}")
        uid = str(user_id)
        if uid not in self_data: self_data[uid] = []
        for ex in self_data[uid]:
            if ex.get('phone') == phone:
                await update.message.reply_text("⚠️ این شماره قبلاً ثبت شده!", parse_mode='HTML')
                await clear_user_state(user_id); return
        now = f"{get_iran_date_str()} {get_iran_time_str()}"
        self_data[uid].append({
            "session": session_string, "phone": phone, "api_id": api_id, "api_hash": api_hash,
            "account_name": account_name, "active": True, "clock_active": False,
            "active_time": "تنظیم نشده", "font_type": "1",
            "created": now, "last_update": now
        })
        await save_data()
        await clear_user_state(user_id)
        new_index = len(self_data[uid]) - 1
        await start_self_client(user_id, new_index)
        text = f"✅ <b>سلف ساخته شد!</b>\n\n📱 {escape_html(phone)}\n👤 {escape_html(account_name)}\n\n📌 دستورات در اکانت سلف:\n• <code>.پنل</code> — نمایش پنل\n• <code>.بلاک</code> — بلاک کاربر"
        kb = [
            [InlineKeyboardButton("🔷 سلف جدید", callback_data="new_session")],
            [InlineKeyboardButton("📋 لیست", callback_data="list_selfs")],
            [InlineKeyboardButton("🏠 منو", callback_data="back")]
        ]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except SessionPasswordNeededError:
        user_sessions[user_id]['step'] = "password"
        await update.message.reply_text("🔐 رمز دو مرحله‌ای:\n\nرمز رو وارد کنید:", parse_mode='HTML')
    except PhoneCodeExpiredError:
        await client.send_code_request(data['phone'])
        await update.message.reply_text("🔄 کد جدید ارسال شد. مجدداً وارد کنید:", parse_mode='HTML')
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ کد اشتباه!", parse_mode='HTML')
    except Exception as e:
        logger.exception(f"code err: {e}")
        await update.message.reply_text("❌ خطا!", parse_mode='HTML')
        await clear_user_state(user_id)

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text.strip()
    if user_id not in user_sessions or user_sessions[user_id].get("step") != "password":
        await update.message.reply_text("❌ از دکمه ایجاد سلف استفاده کنید.", parse_mode='HTML'); return
    data = user_sessions[user_id]
    client = data.get('client')
    if not client:
        await update.message.reply_text("❌ اتصال معتبر نیست.", parse_mode='HTML')
        await clear_user_state(user_id); return
    try:
        await client.sign_in(password=password)
        session_string = client.session.save()
        await client.disconnect()
        account_name = "بدون نام"
        try:
            c2 = TelegramClient(StringSession(session_string), data['api_id'], data['api_hash'])
            await c2.connect()
            if await c2.is_user_authorized():
                me = await c2.get_me()
                if me and me.first_name: account_name = me.first_name
                elif me and me.username: account_name = me.username
            await c2.disconnect()
        except: pass
        uid = str(user_id)
        if uid not in self_data: self_data[uid] = []
        for ex in self_data[uid]:
            if ex.get('phone') == data['phone']:
                await update.message.reply_text("⚠️ این شماره قبلاً ثبت شده!", parse_mode='HTML')
                await clear_user_state(user_id); return
        now = f"{get_iran_date_str()} {get_iran_time_str()}"
        self_data[uid].append({
            "session": session_string, "phone": data['phone'], "api_id": data['api_id'], "api_hash": data['api_hash'],
            "account_name": account_name, "active": True, "clock_active": False,
            "active_time": "تنظیم نشده", "font_type": "1",
            "created": now, "last_update": now
        })
        await save_data()
        await clear_user_state(user_id)
        new_index = len(self_data[uid]) - 1
        await start_self_client(user_id, new_index)
        text = f"✅ <b>سلف ساخته شد!</b>\n\n📱 {escape_html(data['phone'])}\n👤 {escape_html(account_name)}"
        kb = [
            [InlineKeyboardButton("🔷 سلف جدید", callback_data="new_session")],
            [InlineKeyboardButton("📋 لیست", callback_data="list_selfs")],
            [InlineKeyboardButton("🏠 منو", callback_data="back")]
        ]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    except Exception as e:
        logger.exception(f"pass err: {e}")
        await update.message.reply_text("❌ رمز اشتباه!", parse_mode='HTML')

# ============ بازگشت ============
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    user_id = query.from_user.id
    await clear_user_state(user_id)
    if 'profile_files' in context.user_data:
        for fp in context.user_data['profile_files']:
            try:
                if os.path.exists(fp): os.remove(fp)
            except: pass
        del context.user_data['profile_files']
    for k in ('profile_step', 'profile_index', 'copy_step', 'copy_index', 'block_step', 'block_index'):
        context.user_data.pop(k, None)
    await main_menu(update, context, edit=True)

# ============ هندلر پیام‌ها ============
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        step = user_sessions[user_id].get("step")
        if step == "phone": await handle_phone(update, context); return
        elif step == "api_id": await handle_api_id(update, context); return
        elif step == "api_hash": await handle_api_hash(update, context); return
        elif step == "code": await handle_code(update, context); return
        elif step == "password": await handle_password(update, context); return
    if 'profile_step' in context.user_data:
        step = context.user_data['profile_step']
        if step == 'waiting_media': await handle_profile_media(update, context); return
        elif step == 'waiting_count': await handle_profile_count(update, context); return
    if context.user_data.get('copy_step') == 'waiting_id':
        await handle_copy_id(update, context); return
    await update.message.reply_text(
        "❌ لطفاً از دکمه‌های منو استفاده فرمایید.\n\n"
        "برای شروع: /start",
        parse_mode='HTML'
    )

# ============ اجرا ============
def main():
    try:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook"
            with urllib.request.urlopen(url, timeout=5) as r: pass
        except: pass
        print("=" * 60)
        print("🌟 ربات مدیریت حساب‌های شخصی")
        print("=" * 60)
        print("✅ ربات با موفقیت راه‌اندازی شد.")
        print("=" * 60)
        application = Application.builder().token(TOKEN).build()
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
        application.add_handler(CallbackQueryHandler(copy_profile, pattern="^copy_profile_"))
        application.add_handler(CallbackQueryHandler(activate_clock, pattern="^activate_clock_"))
        application.add_handler(CallbackQueryHandler(deactivate_clock, pattern="^deactivate_clock_"))
        application.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back$"))
        application.add_handler(CommandHandler("start", main_menu))
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND | filters.PHOTO | filters.VIDEO | filters.Document.ALL,
            handle_messages
        ))
        async def error_handler(update, context):
            if "Conflict" in str(context.error):
                logger.warning("Conflict error - ignoring"); return
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
