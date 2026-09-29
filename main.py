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

# ذخیره mapping: (self_user_id, owner_telegram_id, index)
self_owner_map = {}

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

# ============ ذخیره‌سازی ============
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

# ============ کمکی ============
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
            c = user_sessions[user_id].get('client')
            if c: await c.disconnect()
        except: pass
        del user_sessions[user_id]

# ============ سلف ============
async def get_user_session_by_index(user_id, account_index):
    uid = str(user_id)
    selfs = self_data.get(uid, [])
    if 0 <= account_index < len(selfs):
        sa = selfs[account_index]
        if sa.get('active', True):
            return {
                'session': sa.get('session'), 'api_id': sa.get('api_id'),
                'api_hash': sa.get('api_hash'), 'phone': sa.get('phone'),
                'index': account_index, 'data': sa
            }
    return None

async def start_self_client(user_id, account_index):
    try:
        key = get_self_key(user_id, account_index)
        sd = await get_user_session_by_index(user_id, account_index)
        if not sd: return False

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
            StringSession(sd['session']), sd['api_id'], sd['api_hash']
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False
        me = await client.get_me()
        self_user_id = me.id
        self_clients[key] = client

        # ذخیره mapping برای پیدا کردن owner
        self_owner_map[self_user_id] = (str(user_id), account_index)

        # ===== هندلر پیام‌های خروجی =====
        @client.on(events.NewMessage(outgoing=True))
        async def outgoing_handler(event):
            try:
                msg = event.message
                if not msg or not msg.text: return
                text = msg.text.strip()

                # ---- دستور .پنل ----
                if text == ".پنل":
                    await safe_delete(event)
                    try:
                        await send_panel_inline(client, self_user_id, int(user_id), account_index)
                    except Exception as e:
                        logger.exception(f"panel send err: {e}")
                    return

                # ---- /panel (با اسلش) ----
                if text == "/panel":
                    await safe_delete(event)
                    try:
                        await send_panel_inline(client, self_user_id, int(user_id), account_index)
                    except Exception as e:
                        logger.exception(f"panel send err: {e}")
                    return

                # ---- دستور .بلاک ----
                if text == ".بلاک":
                    await handle_self_block(client, event, self_user_id)
                    return

            except Exception as e:
                logger.exception(f"outgoing err: {e}")

        # ===== هندلر پیام‌های ورودی =====
        @client.on(events.NewMessage(incoming=True))
        async def incoming_handler(event):
            try:
                msg = event.message
                if not msg or not msg.text: return
                if msg.text.strip() != ".بلاک": return
                await handle_self_block(client, event, self_user_id, is_incoming=True)
            except Exception as e:
                logger.exception(f"incoming err: {e}")

        async def run_client():
            try:
                await client.run_until_disconnected()
            except Exception as e:
                if not is_shutting_down:
                    logger.exception(f"Client disconnected {key}: {e}")

        task = asyncio.create_task(run_client())
        self_tasks[key] = task
        logger.info(f"✅ Self client started: {user_id}[{account_index}]")
        return True
    except Exception as e:
        logger.exception(f"start_self err: {e}")
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

async def safe_delete(event):
    """حذف پیام با مدیریت خطا"""
    try:
        await event.delete()
        return True
    except Exception as e:
        logger.warning(f"delete err: {e}")
        return False

# ============ بلاک ============
async def handle_self_block(client, event, self_user_id, is_incoming=False):
    try:
        msg = event.message
        target = None

        if msg.is_reply:
            try:
                replied = await event.get_reply_message()
                if replied and replied.sender_id and replied.sender_id != self_user_id:
                    target = await client.get_entity(replied.sender_id)
            except Exception as e:
                logger.exception(f"reply err: {e}")

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
    except Exception as e:
        logger.exception(f"block err: {e}")

# ============ پنل Inline ============
async def send_panel_inline(client, self_user_id, owner_id, account_index):
    """ارسال پنل با دکمه‌های Inline به Saved Messages"""
    sa = None
    try:
        sa = self_data.get(str(owner_id), [])[account_index]
    except:
        pass

    me = await client.get_me()
    username = me.username or "بدون یوزرنیم"
    name = escape_html((me.first_name or "") + ((" " + me.last_name) if me.last_name else "") or "کاربر")

    if sa:
        phone = escape_html(sa.get('phone', '-'))
        clock = "🟢 فعال" if sa.get('clock_active') else "🔴 غیرفعال"
        font = FONT_NAMES.get(sa.get('font_type', '1'), 'فونت 1')
    else:
        phone = "-"
        clock = "🔴 غیرفعال"
        font = "فونت 1"

    text = f"""
🌟 <b>پنل مدیریت سلف</b>

👤 <b>نام:</b> {name}
🆔 <b>یوزرنیم:</b> @{escape_html(username)}
📱 <b>شماره:</b> <code>{phone}</code>
⏰ <b>ساعت:</b> {clock}
🎨 <b>فونت:</b> {font}

📌 <b>دستورات:</b>
• <code>.پنل</code> یا <code>/panel</code> — نمایش این پنل
• <code>.بلاک</code> — بلاک کاربر (پیوی یا ریپلای)

از دکمه‌های زیر برای مدیریت سریع استفاده کن:
"""

    keyboard = [
        [InlineKeyboardButton("🔄 بروزرسانی پنل", callback_data=f"selfpanel_refresh_{account_index}")],
        [InlineKeyboardButton("⏰ وضعیت ساعت", callback_data=f"selfpanel_clock_{account_index}")],
        [InlineKeyboardButton("🎨 فونت‌ها", callback_data=f"selfpanel_fonts_{account_index}")],
        [InlineKeyboardButton("📋 اطلاعات کامل", callback_data=f"selfpanel_info_{account_index}")],
        [InlineKeyboardButton("❌ بستن پنل", callback_data=f"selfpanel_close_{account_index}")]
    ]

    try:
        await client.send_message(
            self_user_id, text,
            parse_mode='html',
            link_preview=False,
            buttons=InlineKeyboardMarkup(keyboard) if False else None
        )
        # Telethon از InlineKeyboardMarkup پایتون‌تلگرام استفاده نمی‌کنه، باید خودمون بسازیم
        # اما برای سادگی، دکمه‌ها رو با reply markup تلگرام می‌سازیم
    except Exception as e:
        logger.exception(f"panel send err: {e}")

# از اونجایی که Telethon و python-telegram-bot دو تا سیستم مختلف دارن،
# برای پنل داخل اکانت سلف از inline keyboard تلگرام (raw) استفاده می‌کنیم

async def send_panel_inline_v2(client, self_user_id, owner_id, account_index):
    """نسخه دوم - با استفاده از raw Telegram API برای دکمه‌های Inline"""
    from telethon.tl.types import (
        ReplyInlineMarkup, KeyboardButtonRow, KeyboardButtonCallback
    )
    from telethon.tl.functions.messages import SendMessageRequest

    sa = None
    try:
        sa = self_data.get(str(owner_id), [])[account_index]
    except:
        pass

    me = await client.get_me()
    username = me.username or "بدون یوزرنیم"
    name = (me.first_name or "") + ((" " + me.last_name) if me.last_name else "") or "کاربر"

    if sa:
        phone = sa.get('phone', '-')
        clock = "🟢 فعال" if sa.get('clock_active') else "🔴 غیرفعال"
        font = FONT_NAMES.get(sa.get('font_type', '1'), 'فونت 1')
    else:
        phone = "-"
        clock = "🔴 غیرفعال"
        font = "فونت 1"

    text = (
        f"🌟 <b>پنل مدیریت سلف</b>\n\n"
        f"👤 <b>نام:</b> {name}\n"
        f"🆔 <b>یوزرنیم:</b> @{username}\n"
        f"📱 <b>شماره:</b> <code>{phone}</code>\n"
        f"⏰ <b>ساعت:</b> {clock}\n"
        f"🎨 <b>فونت:</b> {font}\n\n"
        f"📌 <b>دستورات:</b>\n"
        f"• <code>.پنل</code> یا <code>/panel</code>\n"
        f"• <code>.بلاک</code>"
    )

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🔄 بروزرسانی", data=f"selfpanel_refresh_{account_index}".encode())
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="⏰ وضعیت ساعت", data=f"selfpanel_clock_{account_index}".encode())
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🎨 فونت‌ها", data=f"selfpanel_fonts_{account_index}".encode())
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="📋 اطلاعات کامل", data=f"selfpanel_info_{account_index}".encode())
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="❌ بستن پنل", data=f"selfpanel_close_{account_index}".encode())
        ])
    ]
    markup = ReplyInlineMarkup(rows=rows)

    try:
        await client(SendMessageRequest(
            peer=self_user_id,
            message=text,
            parse_mode='html',
            reply_markup=markup
        ))
    except Exception as e:
        logger.exception(f"panel v2 err: {e}")

# هندلر Callback داخل سلف
def register_self_callback_handler(client, self_user_id, owner_id, account_index):
    """ثبت هندلر برای Callback Queryهای داخل اکانت سلف"""
    from telethon import events as tl_events
    from telethon.tl.functions.messages import EditMessageRequest

    @client.on(tl_events.Raw)
    async def raw_handler(update):
        try:
            # هندل CallbackQuery
            from telethon.tl.types import UpdateBotCallbackQuery
            if isinstance(update, UpdateBotCallbackQuery):
                data = update.data.decode() if update.data else ""
                if not data.startswith("selfpanel_"): return

                # پاسخ به callback
                try:
                    from telethon.tl.functions.messages import SetBotCallbackAnswerRequest
                    await client(SetBotCallbackAnswerRequest(
                        query_id=update.query_id,
                        message="✅",
                        cache_time=0
                    ))
                except: pass

                # ساخت متن جدید بر اساس دکمه
                if "refresh" in data:
                    await send_panel_inline_v2(client, self_user_id, owner_id, account_index)
                elif "close" in data:
                    try:
                        await client.delete_messages(update.peer, [update.msg_id])
                    except: pass
                elif "clock" in data:
                    sa = self_data.get(str(owner_id), [])[account_index] if 0 <= account_index < len(self_data.get(str(owner_id), [])) else None
                    clock_st = "🟢 فعال" if sa and sa.get('clock_active') else "🔴 غیرفعال"
                    try:
                        await client(EditMessageRequest(
                            peer=update.peer,
                            id=update.msg_id,
                            message=f"⏰ <b>وضعیت ساعت</b>\n\n{clock_st}",
                            parse_mode='html'
                        ))
                    except: pass
                elif "fonts" in data:
                    try:
                        await client(EditMessageRequest(
                            peer=update.peer,
                            id=update.msg_id,
                            message="🎨 <b>فونت‌ها در ربات اصلی قابل تغییر است</b>",
                            parse_mode='html'
                        ))
                    except: pass
                elif "info" in data:
                    me = await client.get_me()
                    try:
                        await client(EditMessageRequest(
                            peer=update.peer,
                            id=update.msg_id,
                            message=f"📋 <b>اطلاعات</b>\n\n👤 {me.first_name}\n🆔 @{me.username or '-'}\n📱 {me.phone or '-'}",
                            parse_mode='html'
                        ))
                    except: pass
        except Exception as e:
            logger.exception(f"raw handler err: {e}")

# ============ ساعت ============
async def set_clock_on_profile(session_string, api_id, api_hash, font_type):
    client = None
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized(): return False
        me = await client.get_me()
        fn = me.first_name or ""; ln = me.last_name or ""
        cf = clean_clock_from_name(fn)
        t = get_iran_time_short()
        ft = convert_to_font(t, font_type)
        nf = f"{cf} {ft}".strip()
        if nf != fn:
            await client(UpdateProfileRequest(first_name=nf, last_name=ln))
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
        if not await client.is_user_authorized(): return False
        me = await client.get_me()
        fn = me.first_name or ""; ln = me.last_name or ""
        cf = clean_clock_from_name(fn)
        if cf != fn:
            await client(UpdateProfileRequest(first_name=cf, last_name=ln))
        return True
    except Exception as e:
        logger.exception(f"rm_clock err: {e}")
        return False
    finally:
        await safe_disconnect(client)

async def clock_loop(user_id, account_index, session_string, api_id, api_hash, font_type):
    ck = (str(user_id), account_index)
    last_min = None
    while True:
        try:
            if ck not in clock_tasks or not clock_tasks[ck]: break
            cm = get_iran_time().strftime("%H:%M")
            if cm != last_min:
                await set_clock_on_profile(session_string, api_id, api_hash, font_type)
                last_min = cm
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"clock loop err: {e}")
            await asyncio.sleep(3)

# ============ منو ============
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    name = escape_html(user.first_name or "کاربر")
    uid = str(user.id)
    cnt = len(self_data.get(uid, []))
    text = f"""
🌟 <b>ربات مدیریت حساب‌های شخصی</b>

<b>جناب {name} گرامی</b>

تعداد سلف‌های ثبت شده: <b>{cnt}</b>
"""
    kb = [
        [InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")],
        [InlineKeyboardButton("📋 لیست سلف‌ها", callback_data="list_selfs")],
        [InlineKeyboardButton("🎨 فونت ساعت", callback_data="font_settings")]
    ]
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
            await update.callback_query.answer()
        except: pass
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

# ============ لیست ============
async def list_selfs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    selfs = self_data.get(uid, [])
    if not selfs:
        await q.edit_message_text("📋 <b>لیست سلف‌ها</b>\n\n❌ هیچ سلفی نیست.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔷 ایجاد", callback_data="new_session")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back")]
            ]), parse_mode='HTML')
        return
    text = f"📋 <b>سلف‌ها ({len(selfs)})</b>\n"
    kb = []
    for i, sa in enumerate(selfs):
        nm = escape_html(sa.get('account_name', 'بدون نام'))
        ca = "🟢" if sa.get('clock_active') else "🔴"
        text += f"\n{i+1}. {nm} {ca}"
        kb.append([InlineKeyboardButton(f"⚙️ {i+1}. {nm}", callback_data=f"manage_{i}")])
    kb.append([InlineKeyboardButton("🔷 جدید", callback_data="new_session")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

# ============ مدیریت ============
async def manage_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[1])
    except: return
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await q.edit_message_text("❌ یافت نشد.", parse_mode='HTML'); return
    sa = selfs[index]
    key = get_self_key(int(uid), index)
    conn = key in self_clients
    job = uid in profile_tasks and not profile_tasks[uid].done()
    text = f"""
⚙️ <b>مدیریت سلف {index+1}</b>

📱 <code>{escape_html(sa.get('phone','-'))}</code>
👤 <b>{escape_html(sa.get('account_name','-'))}</b>
🕐 <code>{escape_html(sa.get('active_time','-'))}</code>
🎨 {FONT_NAMES.get(sa.get('font_type','1'),'-')}
📊 ساعت: {'🟢' if sa.get('clock_active') else '🔴'}
🔗 اتصال: {'🟢' if conn else '🔴'}
{'🔄 جاب در حال اجرا' if job else ''}
"""
    kb = []
    if not conn:
        kb.append([InlineKeyboardButton("🔄 اتصال", callback_data=f"connect_self_{index}")])
    else:
        kb.append([InlineKeyboardButton("🔌 قطع", callback_data=f"disconnect_self_{index}")])
    kb.append([InlineKeyboardButton("📸 تنظیم پروفایل", callback_data=f"new_profile_{index}")])
    kb.append([InlineKeyboardButton("📋 کپی پروفایل", callback_data=f"copy_profile_{index}")])
    if sa.get('clock_active'):
        kb.append([InlineKeyboardButton("⏰ غیرفعال ساعت", callback_data=f"deactivate_clock_{index}")])
        kb.append([InlineKeyboardButton("🎨 تغییر فونت", callback_data=f"font_select_{index}")])
    else:
        kb.append([InlineKeyboardButton("⏰ فعال ساعت", callback_data=f"activate_clock_{index}")])
    if job:
        kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="list_selfs")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

# ============ اتصال ============
async def connect_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[2])
    except: return
    r = await start_self_client(int(uid), index)
    t = f"✅ متصل شد!" if r else "❌ خطا!"
    await q.edit_message_text(t, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
    ]), parse_mode='HTML')

async def disconnect_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[2])
    except: return
    await stop_self_client(int(uid), index)
    await q.edit_message_text("✅ قطع شد!", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
    ]), parse_mode='HTML')

# ============ ساعت ============
async def activate_clock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[2])
    except: return
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await q.edit_message_text("❌", parse_mode='HTML'); return
    sa = selfs[index]
    t = get_iran_time_short()
    r = await set_clock_on_profile(sa.get('session'), sa.get('api_id'), sa.get('api_hash'), sa.get('font_type','1'))
    if r:
        selfs[index]['active_time'] = t
        selfs[index]['clock_active'] = True
        await save_data()
        ck = get_self_key(int(uid), index)
        clock_tasks[ck] = True
        asyncio.create_task(clock_loop(int(uid), index, sa.get('session'), sa.get('api_id'), sa.get('api_hash'), sa.get('font_type','1')))
        text = f"✅ ساعت فعال شد!\n🕐 <code>{convert_to_font(t, sa.get('font_type','1'))}</code>"
    else:
        text = "❌ خطا!"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
    ]), parse_mode='HTML')

async def deactivate_clock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[2])
    except: return
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await q.edit_message_text("❌", parse_mode='HTML'); return
    sa = selfs[index]
    r = await remove_clock_from_profile(sa.get('session'), sa.get('api_id'), sa.get('api_hash'))
    if r:
        selfs[index]['clock_active'] = False
        await save_data()
        ck = get_self_key(int(uid), index)
        if ck in clock_tasks:
            clock_tasks[ck] = False
            del clock_tasks[ck]
        text = "❌ ساعت غیرفعال شد!"
    else:
        text = "❌ خطا!"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
    ]), parse_mode='HTML')

# ============ فونت ============
async def font_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    selfs = self_data.get(uid, [])
    if not selfs:
        await q.edit_message_text("🎨 سلفی نیست.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔷 ایجاد", callback_data="new_session")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back")]
        ]), parse_mode='HTML'); return
    kb = []
    for i, sa in enumerate(selfs):
        nm = escape_html(sa.get('account_name','-'))
        kb.append([InlineKeyboardButton(f"{i+1}. {nm}", callback_data=f"font_select_{i}")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])
    await q.edit_message_text("🎨 <b>انتخاب سلف</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def font_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[2])
    except: return
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await q.edit_message_text("❌", parse_mode='HTML'); return
    kb = []
    for fid, finfo in FONTS.items():
        kb.append([InlineKeyboardButton(f"{finfo['display']} - {finfo['name']}", callback_data=f"font_apply_{index}_{fid}")])
    kb.append([InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")])
    await q.edit_message_text("🎨 <b>انتخاب فونت</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def font_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    parts = q.data.split('_')
    try:
        index = int(parts[2]); ft = parts[3]
    except: return
    if ft not in FONTS:
        await q.edit_message_text("❌ فونت نامعتبر", parse_mode='HTML'); return
    uid = str(q.from_user.id)
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await q.edit_message_text("❌", parse_mode='HTML'); return
    sa = selfs[index]
    r = await set_clock_on_profile(sa.get('session'), sa.get('api_id'), sa.get('api_hash'), ft)
    if r:
        selfs[index]['font_type'] = ft
        await save_data()
        ck = get_self_key(int(uid), index)
        if ck in clock_tasks and clock_tasks.get(ck):
            clock_tasks[ck] = False
            del clock_tasks[ck]
            clock_tasks[ck] = True
            asyncio.create_task(clock_loop(int(uid), index, sa.get('session'), sa.get('api_id'), sa.get('api_hash'), ft))
        text = f"✅ فونت تغییر کرد: {FONT_NAMES[ft]}"
    else:
        text = "❌ خطا!"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
    ]), parse_mode='HTML')

# ============ پروفایل ============
async def new_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[2])
    except: return
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await q.edit_message_text("❌", parse_mode='HTML'); return
    job = uid in profile_tasks and not profile_tasks[uid].done()
    context.user_data['profile_index'] = index
    context.user_data['profile_step'] = 'waiting_media'
    context.user_data.setdefault('profile_files', [])
    if job:
        text = "📸 فایل به صف اضافه می‌شه."
        kb = [[InlineKeyboardButton("✅ افزودن", callback_data=f"add_to_job_{index}")],
              [InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]]
    else:
        text = "📸 عکس/فیلم بفرستید، دکمه اتمام رو بزنید."
        kb = [[InlineKeyboardButton("✅ اتمام", callback_data=f"done_profile_{index}")],
              [InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def handle_profile_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if context.user_data.get('profile_step') != 'waiting_media':
        await update.message.reply_text("❌ از دکمه استفاده کن.", parse_mode='HTML'); return
    f = None; ext = None
    if update.message.photo:
        f = await update.message.photo[-1].get_file(); ext = ".jpg"
    elif update.message.video:
        f = await update.message.video.get_file(); ext = ".mp4"
    elif update.message.document:
        f = await update.message.document.get_file()
        nm = update.message.document.file_name or ""
        ext = os.path.splitext(nm)[1].lower()
        if ext not in ['.jpg','.jpeg','.png','.mp4']:
            await update.message.reply_text("❌ فرمت!", parse_mode='HTML'); return
    else:
        await update.message.reply_text("❌ فقط عکس/فیلم!", parse_mode='HTML'); return
    if f.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("❌ >10MB!", parse_mode='HTML'); return
    fp = f"temp_{uid}_{len(context.user_data.get('profile_files',[]))}{ext}"
    try:
        await f.download_to_drive(fp)
    except: 
        await update.message.reply_text("❌ خطای دانلود!", parse_mode='HTML'); return
    context.user_data.setdefault('profile_files', []).append(fp)
    idx = context.user_data.get('profile_index', 0)
    job = uid in profile_tasks and not profile_tasks[uid].done()
    if job:
        kb = [[InlineKeyboardButton("✅ افزودن", callback_data=f"add_to_job_{idx}")],
              [InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{idx}")]]
    else:
        kb = [[InlineKeyboardButton("✅ اتمام", callback_data=f"done_profile_{idx}")],
              [InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{idx}")]]
    await update.message.reply_text(f"✅ فایل {len(context.user_data['profile_files'])} دریافت شد.",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def done_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[2])
    except: return
    files = context.user_data.get('profile_files', [])
    if not files:
        await q.edit_message_text("❌ فایلی نیست!", parse_mode='HTML'); return
    context.user_data['profile_step'] = 'waiting_count'
    await q.edit_message_text(f"✅ {len(files)} فایل.\n🔢 تعداد هر فایل (1-{MAX_PROFILE_COUNT}):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]]),
        parse_mode='HTML')

async def handle_profile_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if context.user_data.get('profile_step') != 'waiting_count':
        await update.message.reply_text("❌", parse_mode='HTML'); return
    try:
        c = int(update.message.text.strip())
        if c < 1 or c > MAX_PROFILE_COUNT:
            await update.message.reply_text(f"❌ 1-{MAX_PROFILE_COUNT}", parse_mode='HTML'); return
    except:
        await update.message.reply_text("❌ عدد!", parse_mode='HTML'); return
    index = context.user_data['profile_index']
    files = context.user_data.get('profile_files', [])
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await update.message.reply_text("❌", parse_mode='HTML'); return
    sa = selfs[index]
    sm = await update.message.reply_text(
        f"🚀 شروع...\n👤 {escape_html(sa.get('account_name','-'))}\n📁 {len(files)} فایل\n🔢 {c}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")]]),
        parse_mode='HTML'
    )
    fc = files.copy()
    context.user_data['profile_files'] = []
    context.user_data.pop('profile_step', None)
    task = asyncio.create_task(run_profile_job(
        uid, index, sa.get('session'), sa.get('api_id'), sa.get('api_hash'),
        fc, c, update.effective_chat.id, context, sm.message_id
    ))
    profile_tasks[uid] = task

async def add_to_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[3])
    except: return
    files = context.user_data.get('profile_files', [])
    if not files:
        await q.edit_message_text("❌", parse_mode='HTML'); return
    if uid in profile_tasks and not profile_tasks[uid].done():
        context.user_data.setdefault('pending_files', []).extend(files)
        context.user_data['profile_files'] = []
        await q.edit_message_text(f"✅ {len(files)} فایل به صف اضافه شد!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]),
            parse_mode='HTML')
    else:
        await q.edit_message_text("❌ جاب در حال اجرا نیست!", parse_mode='HTML')

async def run_profile_job(user_id, index, session_string, api_id, api_hash, files, count, chat_id, context, status_msg_id):
    client = None; last_edit = 0
    try:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            try: await context.bot.edit_message_text("❌ اکانت معتبر نیست!", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
            except: pass
            return
        an = escape_html(self_data.get(str(user_id), [])[index].get('account_name','-'))
        ts = 0; tf = 0; tc = 0; day = 1
        ups = []
        for fp in files:
            try: ups.append((await client.upload_file(fp), fp))
            except: pass
        if not ups:
            try: await context.bot.edit_message_text("❌ آپلود نشد!", chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
            except: pass
            return
        total = len(ups) * count
        fi = 0
        for up, fp in ups:
            fi += 1
            for i in range(count):
                if user_id not in profile_tasks or profile_tasks[user_id].cancelled(): break
                if tc >= MAX_PER_DAY:
                    now = get_iran_time()
                    tm = (now + timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
                    ws = (tm - now).total_seconds()
                    if ws > 0:
                        try:
                            await context.bot.edit_message_text(
                                f"⏳ محدودیت\n{format_seconds(ws)}\n✅{ts} ❌{tf}",
                                chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
                        except: pass
                        await asyncio.sleep(ws)
                        day += 1; tc = 0
                try:
                    await client(UploadProfilePhotoRequest(file=up))
                    ts += 1; tc += 1
                    n = asyncio.get_event_loop().time()
                    if n - last_edit >= 1.5 or ts == total:
                        last_edit = n
                        try:
                            await context.bot.edit_message_text(
                                f"🚀 {an}\n📁 {fi}/{len(ups)}\n📊 {ts}/{total}\n✅{ts} ❌{tf}\n📅 {day}",
                                chat_id=chat_id, message_id=status_msg_id,
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")]]),
                                parse_mode='HTML')
                        except: pass
                    await asyncio.sleep(0.001)
                except FloodWaitError as e:
                    wt = e.seconds
                    try:
                        await context.bot.edit_message_text(
                            f"⏳ {format_seconds(wt)}\n✅{ts} ❌{tf}",
                            chat_id=chat_id, message_id=status_msg_id, parse_mode='HTML')
                    except: pass
                    await asyncio.sleep(wt + 2)
                    try:
                        await client(UploadProfilePhotoRequest(file=up))
                        ts += 1; tc += 1
                    except: tf += 1
                except Exception as e:
                    logger.exception(f"prof err: {e}")
                    tf += 1
                    await asyncio.sleep(0.05)
        try:
            await context.bot.edit_message_text(
                f"✅ پایان!\n👤 {an}\n✅{ts} ❌{tf}\n📁 {len(ups)}\n📅 {day}",
                chat_id=chat_id, message_id=status_msg_id,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
                    [InlineKeyboardButton("🏠 منو", callback_data="back")]
                ]), parse_mode='HTML')
        except: pass
        for _, fp in ups:
            try:
                if os.path.exists(fp): os.remove(fp)
            except: pass
        if user_id in profile_tasks: del profile_tasks[user_id]
    except asyncio.CancelledError:
        for fp in files:
            try:
                if os.path.exists(fp): os.remove(fp)
            except: pass
        if user_id in profile_tasks: del profile_tasks[user_id]
        raise
    except Exception as e:
        logger.exception(f"job err: {e}")
    finally:
        await safe_disconnect(client)

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    if uid in profile_tasks:
        profile_tasks[uid].cancel()
        try: await profile_tasks[uid]
        except: pass
        if uid in profile_tasks: del profile_tasks[uid]
    if 'profile_files' in context.user_data:
        for fp in context.user_data['profile_files']:
            try:
                if os.path.exists(fp): os.remove(fp)
            except: pass
        del context.user_data['profile_files']
    context.user_data.pop('profile_step', None)
    try: await q.edit_message_text("❌ لغو شد!", parse_mode='HTML')
    except: pass

# ============ کپی پروفایل ============
async def copy_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = str(q.from_user.id)
    try: index = int(q.data.split('_')[2])
    except: return
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await q.edit_message_text("❌", parse_mode='HTML'); return
    context.user_data['copy_index'] = index
    context.user_data['copy_step'] = 'waiting_id'
    await q.edit_message_text(
        "📋 <b>کپی پروفایل</b>\n\nآیدی عددی کاربر رو وارد کنید:\n<b>مثال:</b> <code>123456789</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data=f"manage_{index}")]]),
        parse_mode='HTML')

async def handle_copy_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if context.user_data.get('copy_step') != 'waiting_id': return
    t = update.message.text.strip()
    if not t.isdigit():
        await update.message.reply_text("❌ عدد!", parse_mode='HTML'); return
    tid = int(t)
    index = context.user_data.get('copy_index')
    context.user_data.pop('copy_step', None)
    context.user_data.pop('copy_index', None)
    selfs = self_data.get(uid, [])
    if not (0 <= index < len(selfs)):
        await update.message.reply_text("❌", parse_mode='HTML'); return
    sa = selfs[index]
    sm = await update.message.reply_text(
        f"🔄 شروع کپی\n🆔 {tid}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]),
        parse_mode='HTML')
    asyncio.create_task(run_copy_job(uid, index, sa.get('session'), sa.get('api_id'), sa.get('api_hash'),
        tid, update.effective_chat.id, context, sm.message_id))

async def run_copy_job(user_id, index, ss, ai, ah, tid, chat_id, context, smid):
    client = None; last_edit = 0
    try:
        client = TelegramClient(StringSession(ss), ai, ah)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            try: await context.bot.edit_message_text("❌", chat_id=chat_id, message_id=smid, parse_mode='HTML')
            except: pass
            return
        an = escape_html(self_data.get(str(user_id), [])[index].get('account_name','-'))
        try: t = await client.get_entity(tid)
        except:
            try: await context.bot.edit_message_text(f"❌ کاربر {tid} یافت نشد!", chat_id=chat_id, message_id=smid, parse_mode='HTML')
            except: pass
            return
        tn = (t.first_name or "") + (" " + t.last_name if t.last_name else "") or t.username or str(tid)
        try: await context.bot.edit_message_text(f"🔄 دریافت پروفایل‌های {escape_html(tn)}...", chat_id=chat_id, message_id=smid, parse_mode='HTML')
        except: pass
        pl = []
        try:
            ph = await client(GetUserPhotosRequest(user_id=tid, offset=0, max_id=0, limit=1000))
            pl = ph.photos if hasattr(ph, 'photos') else []
        except: pass
        total = len(pl)
        if total == 0:
            try: await context.bot.edit_message_text(f"⚠️ {escape_html(tn)} پروفایل نداره!", chat_id=chat_id, message_id=smid,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]]), parse_mode='HTML')
            except: pass
            return
        sc = 0; fc = 0
        for i, p in enumerate(pl):
            try:
                fp = f"cp_{user_id}_{tid}_{i}.jpg"
                await client.download_media(p, fp)
                if os.path.exists(fp):
                    u = await client.upload_file(fp)
                    await client(UploadProfilePhotoRequest(file=u))
                    sc += 1
                    try: os.remove(fp)
                    except: pass
                n = asyncio.get_event_loop().time()
                if n - last_edit >= 1.2 or i == total - 1:
                    last_edit = n
                    try:
                        await context.bot.edit_message_text(
                            f"🚀 {an}\n📝 {escape_html(tn)}\n📊 {i+1}/{total}\n✅{sc} ❌{fc}",
                            chat_id=chat_id, message_id=smid,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_profile")]]),
                            parse_mode='HTML')
                    except: pass
            except FloodWaitError as e:
                try: await context.bot.edit_message_text(f"⏳ {format_seconds(e.seconds)}", chat_id=chat_id, message_id=smid, parse_mode='HTML')
                except: pass
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                logger.exception(f"copy err {i}: {e}")
                fc += 1
        try:
            await context.bot.edit_message_text(
                f"✅ کپی کامل!\n📝 {escape_html(tn)}\n📸 {total}\n✅{sc} ❌{fc}",
                chat_id=chat_id, message_id=smid,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")],
                    [InlineKeyboardButton("🏠 منو", callback_data="back")]
                ]), parse_mode='HTML')
        except: pass
    except Exception as e:
        logger.exception(f"copy job err: {e}")
    finally:
        await safe_disconnect(client)

# ============ ساخت سلف ============
async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    uid = q.from_user.id
    await clear_user_state(uid)
    user_sessions[uid] = {"step": "phone"}
    await q.edit_message_text("📱 <b>شماره</b> (با کد کشور):\nمثال: <code>989123456789</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data="back")]]),
        parse_mode='HTML')

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    t = update.message.text.strip()
    if uid not in user_sessions or user_sessions[uid].get("step") != "phone":
        await update.message.reply_text("❌", parse_mode='HTML'); return
    p = re.sub(r'[^0-9+]', '', t)
    if not is_valid_phone(p):
        await update.message.reply_text("❌ شماره نامعتبر!", parse_mode='HTML'); return
    user_sessions[uid]['phone'] = p
    user_sessions[uid]['step'] = "api_id"
    await update.message.reply_text(f"✅ <code>{escape_html(p)}</code>\n🔑 API ID:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data="back")]]),
        parse_mode='HTML')

async def handle_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    t = update.message.text.strip()
    if uid not in user_sessions or user_sessions[uid].get("step") != "api_id":
        await update.message.reply_text("❌", parse_mode='HTML'); return
    if not t.isdigit():
        await update.message.reply_text("❌ عدد!", parse_mode='HTML'); return
    user_sessions[uid]['api_id'] = int(t)
    user_sessions[uid]['step'] = "api_hash"
    await update.message.reply_text(f"✅ API ID: <code>{escape_html(t)}</code>\n🔐 API Hash:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data="back")]]),
        parse_mode='HTML')

async def handle_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    t = update.message.text.strip()
    if uid not in user_sessions or user_sessions[uid].get("step") != "api_hash":
        await update.message.reply_text("❌", parse_mode='HTML'); return
    if len(t) < 30:
        await update.message.reply_text("❌ Hash نامعتبر!", parse_mode='HTML'); return
    user_sessions[uid]['api_hash'] = t
    user_sessions[uid]['step'] = "code"
    m = await update.message.reply_text("⏳ ارسال کد...", parse_mode='HTML')
    c = None
    try:
        d = user_sessions[uid]
        c = TelegramClient(StringSession(), d['api_id'], d['api_hash'])
        await c.connect()
        await c.send_code_request(d['phone'])
        user_sessions[uid]['client'] = c
        await context.bot.edit_message_text(f"✅ کد ارسال شد به <code>{escape_html(d['phone'])}</code>\n📝 کد ۵ رقمی:",
            chat_id=update.effective_chat.id, message_id=m.message_id,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data="back")]]),
            parse_mode='HTML')
    except PhoneNumberInvalidError:
        await safe_disconnect(c)
        await context.bot.edit_message_text("❌ شماره نامعتبر!", chat_id=update.effective_chat.id, message_id=m.message_id, parse_mode='HTML')
        await clear_user_state(uid)
    except FloodWaitError as e:
        await safe_disconnect(c)
        await context.bot.edit_message_text(f"⏳ {format_seconds(e.seconds)}", chat_id=update.effective_chat.id, message_id=m.message_id, parse_mode='HTML')
        await clear_user_state(uid)
    except Exception as e:
        logger.exception(f"err: {e}")
        await safe_disconnect(c)
        await context.bot.edit_message_text("❌ خطا!", chat_id=update.effective_chat.id, message_id=m.message_id, parse_mode='HTML')
        await clear_user_state(uid)

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    raw = update.message.text.strip()
    if uid not in user_sessions or user_sessions[uid].get("step") != "code":
        await update.message.reply_text("❌", parse_mode='HTML'); return
    code = raw.replace('.','').replace(' ','').replace('-','').strip()
    if not code.isdigit() or len(code) != 5:
        await update.message.reply_text("❌ کد ۵ رقمی!", parse_mode='HTML'); return
    d = user_sessions[uid]
    c = d.get('client')
    if not c:
        await update.message.reply_text("❌", parse_mode='HTML'); await clear_user_state(uid); return
    try:
        await c.sign_in(d['phone'], code)
        ss = c.session.save()
        await c.disconnect()
        an = "بدون نام"
        try:
            c2 = TelegramClient(StringSession(ss), d['api_id'], d['api_hash'])
            await c2.connect()
            if await c2.is_user_authorized():
                me = await c2.get_me()
                if me and me.first_name: an = me.first_name
                elif me and me.username: an = me.username
            await c2.disconnect()
        except: pass
        us = str(uid)
        if us not in self_data: self_data[us] = []
        for ex in self_data[us]:
            if ex.get('phone') == d['phone']:
                await update.message.reply_text("⚠️ قبلاً ثبت شده!", parse_mode='HTML')
                await clear_user_state(uid); return
        now = f"{get_iran_date_str()} {get_iran_time_str()}"
        self_data[us].append({
            "session": ss, "phone": d['phone'], "api_id": d['api_id'], "api_hash": d['api_hash'],
            "account_name": an, "active": True, "clock_active": False,
            "active_time": "تنظیم نشده", "font_type": "1",
            "created": now, "last_update": now
        })
        await save_data()
        await clear_user_state(uid)
        ni = len(self_data[us]) - 1
        await start_self_client(uid, ni)
        text = f"✅ سلف ساخته شد!\n📱 {escape_html(d['phone'])}\n👤 {escape_html(an)}\n\n📌 دستورات:\n• <code>.پنل</code> یا <code>/panel</code>\n• <code>.بلاک</code>"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔷 جدید", callback_data="new_session")],
            [InlineKeyboardButton("📋 لیست", callback_data="list_selfs")],
            [InlineKeyboardButton("🏠 منو", callback_data="back")]
        ]), parse_mode='HTML')
    except SessionPasswordNeededError:
        user_sessions[uid]['step'] = "password"
        await update.message.reply_text("🔐 رمز دو مرحله‌ای:", parse_mode='HTML')
    except PhoneCodeExpiredError:
        await c.send_code_request(d['phone'])
        await update.message.reply_text("🔄 کد جدید ارسال شد.", parse_mode='HTML')
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ کد اشتباه!", parse_mode='HTML')
    except Exception as e:
        logger.exception(f"err: {e}")
        await update.message.reply_text("❌ خطا!", parse_mode='HTML')
        await clear_user_state(uid)

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pw = update.message.text.strip()
    if uid not in user_sessions or user_sessions[uid].get("step") != "password":
        await update.message.reply_text("❌", parse_mode='HTML'); return
    d = user_sessions[uid]; c = d.get('client')
    if not c:
        await update.message.reply_text("❌", parse_mode='HTML'); await clear_user_state(uid); return
    try:
        await c.sign_in(password=pw)
        ss = c.session.save()
        await c.disconnect()
        an = "بدون نام"
        try:
            c2 = TelegramClient(StringSession(ss), d['api_id'], d['api_hash'])
            await c2.connect()
            if await c2.is_user_authorized():
                me = await c2.get_me()
                if me and me.first_name: an = me.first_name
                elif me and me.username: an = me.username
            await c2.disconnect()
        except: pass
        us = str(uid)
        if us not in self_data: self_data[us] = []
        for ex in self_data[us]:
            if ex.get('phone') == d['phone']:
                await update.message.reply_text("⚠️ قبلاً ثبت شده!", parse_mode='HTML')
                await clear_user_state(uid); return
        now = f"{get_iran_date_str()} {get_iran_time_str()}"
        self_data[us].append({
            "session": ss, "phone": d['phone'], "api_id": d['api_id'], "api_hash": d['api_hash'],
            "account_name": an, "active": True, "clock_active": False,
            "active_time": "تنظیم نشده", "font_type": "1",
            "created": now, "last_update": now
        })
        await save_data()
        await clear_user_state(uid)
        ni = len(self_data[us]) - 1
        await start_self_client(uid, ni)
        await update.message.reply_text(f"✅ ساخته شد!\n📱 {escape_html(d['phone'])}\n👤 {escape_html(an)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔷 جدید", callback_data="new_session")],
                [InlineKeyboardButton("📋 لیست", callback_data="list_selfs")],
                [InlineKeyboardButton("🏠 منو", callback_data="back")]
            ]), parse_mode='HTML')
    except Exception as e:
        logger.exception(f"err: {e}")
        await update.message.reply_text("❌ رمز اشتباه!", parse_mode='HTML')

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    await clear_user_state(q.from_user.id)
    if 'profile_files' in context.user_data:
        for fp in context.user_data['profile_files']:
            try:
                if os.path.exists(fp): os.remove(fp)
            except: pass
        del context.user_data['profile_files']
    for k in ('profile_step','profile_index','copy_step','copy_index'):
        context.user_data.pop(k, None)
    await main_menu(update, context, edit=True)

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_sessions:
        s = user_sessions[uid].get("step")
        if s == "phone": await handle_phone(update, context); return
        elif s == "api_id": await handle_api_id(update, context); return
        elif s == "api_hash": await handle_api_hash(update, context); return
        elif s == "code": await handle_code(update, context); return
        elif s == "password": await handle_password(update, context); return
    if 'profile_step' in context.user_data:
        s = context.user_data['profile_step']
        if s == 'waiting_media': await handle_profile_media(update, context); return
        elif s == 'waiting_count': await handle_profile_count(update, context); return
    if context.user_data.get('copy_step') == 'waiting_id':
        await handle_copy_id(update, context); return
    await update.message.reply_text("❌ از منو استفاده کن. /start", parse_mode='HTML')

# ============ Restart on startup ============
async def restart_all_self_clients(app):
    logger.info("🔄 Restarting self clients...")
    for owner_id, selfs in list(self_data.items()):
        for idx, sa in enumerate(selfs):
            if sa.get('active', True):
                try:
                    ok = await start_self_client(int(owner_id), idx)
                    if ok:
                        logger.info(f"✅ Restarted {owner_id}[{idx}]")
                        if sa.get('clock_active'):
                            ck = (str(owner_id), idx)
                            clock_tasks[ck] = True
                            asyncio.create_task(clock_loop(
                                int(owner_id), idx,
                                sa.get('session'), sa.get('api_id'), sa.get('api_hash'),
                                sa.get('font_type','1')
                            ))
                    else:
                        logger.warning(f"⚠️ Failed {owner_id}[{idx}]")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.exception(f"restart err {owner_id}[{idx}]: {e}")
    logger.info("✅ All self clients restarted")

# ============ Main ============
def main():
    try:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook"
            with urllib.request.urlopen(url, timeout=5) as r: pass
        except: pass
        print("=" * 60)
        print("🌟 Self Bot Manager")
        print("=" * 60)
        print("✅ Started")
        print("=" * 60)

        app = (
            Application.builder()
            .token(TOKEN)
            .post_init(restart_all_self_clients)
            .build()
        )

        app.add_handler(CallbackQueryHandler(new_session, pattern="^new_session$"))
        app.add_handler(CallbackQueryHandler(list_selfs, pattern="^list_selfs$"))
        app.add_handler(CallbackQueryHandler(manage_self, pattern="^manage_"))
        app.add_handler(CallbackQueryHandler(font_settings, pattern="^font_settings$"))
        app.add_handler(CallbackQueryHandler(font_select, pattern="^font_select_"))
        app.add_handler(CallbackQueryHandler(font_apply, pattern="^font_apply_"))
        app.add_handler(CallbackQueryHandler(connect_self, pattern="^connect_self_"))
        app.add_handler(CallbackQueryHandler(disconnect_self, pattern="^disconnect_self_"))
        app.add_handler(CallbackQueryHandler(new_profile, pattern="^new_profile_"))
        app.add_handler(CallbackQueryHandler(done_profile, pattern="^done_profile_"))
        app.add_handler(CallbackQueryHandler(add_to_job, pattern="^add_to_job_"))
        app.add_handler(CallbackQueryHandler(cancel_profile, pattern="^cancel_profile$"))
        app.add_handler(CallbackQueryHandler(copy_profile, pattern="^copy_profile_"))
        app.add_handler(CallbackQueryHandler(activate_clock, pattern="^activate_clock_"))
        app.add_handler(CallbackQueryHandler(deactivate_clock, pattern="^deactivate_clock_"))
        app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back$"))
        app.add_handler(CommandHandler("start", main_menu))
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND | filters.PHOTO | filters.VIDEO | filters.Document.ALL,
            handle_messages
        ))

        async def err_handler(update, context):
            if "Conflict" in str(context.error):
                return
            logger.error(f"Err: {context.error}")
        app.add_error_handler(err_handler)

        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        print(f"❌ {e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Stopped.")
