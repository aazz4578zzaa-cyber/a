import logging
import re
import asyncio
import os
import json
import html
import shutil
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, InlineQueryHandler
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon import events
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, FloodWaitError, PhoneNumberInvalidError,
    MessageIdInvalidError, MessageNotModifiedError, ChatWriteForbiddenError
)
from telethon.errors.rpcerrorlist import RPCError
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest, GetUserPhotosRequest
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.types import User
import urllib.request

# ============ تنظیمات ============
TOKEN = os.environ.get("TOKEN", "8713123512:AAELU9GqYLD9P3QOoQRvoBi0_7DXTFM4r7s")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "AuxiliarySelfPanelxbot")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
_save_lock = asyncio.Lock()
is_shutting_down = False
bot_app = None
panel_sessions = {}

# ============ فونت‌ها ============
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
                except: pass
            os.replace(temp_file, DATA_FILE)
        except Exception as e:
            logger.exception(f"Save error: {e}")

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
        except: pass

async def clear_user_state(user_id):
    if user_id in user_sessions:
        try:
            c = user_sessions[user_id].get('client')
            if c: await c.disconnect()
        except: pass
        del user_sessions[user_id]

# ============ ساخت پنل ============
def build_panel_keyboard(owner_id, account_index):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 حساب من", callback_data=f"sp_me_{owner_id}_{account_index}")],
        [InlineKeyboardButton("📊 وضعیت سرویس", callback_data=f"sp_status_{owner_id}_{account_index}")],
        [InlineKeyboardButton("⏰ وضعیت ساعت", callback_data=f"sp_clock_{owner_id}_{account_index}")],
        [InlineKeyboardButton("🎨 فونت‌ها", callback_data=f"sp_fonts_{owner_id}_{account_index}")],
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"sp_refresh_{owner_id}_{account_index}")],
        [InlineKeyboardButton("❌ بستن پنل", callback_data=f"sp_close_{owner_id}_{account_index}")]
    ])

def build_panel_text(user, sa):
    name = ((user.first_name or "") + ((" " + user.last_name) if getattr(user, 'last_name', None) else "")) or "کاربر"
    username = getattr(user, 'username', None) or "بدون یوزرنیم"
    phone = sa.get('phone', '-') if sa else '-'
    clock = "🟢 فعال" if (sa and sa.get('clock_active')) else "🔴 غیرفعال"
    font = FONT_NAMES.get(sa.get('font_type', '1'), 'فونت 1') if sa else 'فونت 1'
    account_name = sa.get('account_name', 'بدون نام') if sa else 'بدون نام'
    return (
        f"🌟 <b>پنل مدیریت سلف</b>\n\n"
        f"👤 <b>نام:</b> {html.escape(name)}\n"
        f"🆔 <b>یوزرنیم:</b> @{html.escape(username)}\n"
        f"📱 <b>شماره:</b> <code>{html.escape(phone)}</code>\n"
        f"👥 <b>سلف:</b> {html.escape(account_name)}\n"
        f"⏰ <b>ساعت:</b> {clock}\n"
        f"🎨 <b>فونت:</b> {font}\n\n"
        f"👇 از دکمه‌های زیر استفاده کن:"
    )

# ============ Inline Query ============
async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    if not query:
        return
    
    user_id = str(query.from_user.id)
    selfs = self_data.get(user_id, [])
    
    logger.info(f"🔍 Inline query from {user_id}: '{query.query}'")
    
    if not selfs:
        await query.answer(
            [InlineQueryResultArticle(
                id="no_self",
                title="❌ سلفی ثبت نشده",
                description="اول از ربات /start بزن و سلف بساز",
                input_message_content=InputTextMessageContent(
                    "❌ سلفی ثبت نشده.\nاول از ربات /start بزن.",
                    parse_mode='HTML'
                )
            )],
            cache_time=0
        )
        return
    
    results = []
    u = query.from_user
    
    for idx, sa in enumerate(selfs):
        try:
            text = build_panel_text(u, sa)
            account_name = sa.get('account_name', 'بدون نام')
            phone = sa.get('phone', '-')
            clock = "🟢 فعال" if sa.get('clock_active') else "🔴 غیرفعال"
            
            results.append(InlineQueryResultArticle(
                id=f"panel_{idx}",
                title=f"🌟 پنل سلف {idx+1}: {account_name}",
                description=f"📱 {phone} | ⏰ {clock}",
                input_message_content=InputTextMessageContent(text, parse_mode='HTML'),
                reply_markup=build_panel_keyboard(user_id, idx)
            ))
        except Exception as e:
            logger.exception(f"inline result err: {e}")
            continue
    
    await query.answer(results, cache_time=0)

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

        client = TelegramClient(StringSession(sd['session']), sd['api_id'], sd['api_hash'])
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False
        
        me = await client.get_me()
        self_user_id = me.id
        self_clients[key] = client
        logger.info(f"✅ Self connected: {self_user_id} for {user_id}[{account_index}]")

        @client.on(events.NewMessage)
        async def message_handler(event):
            try:
                msg = event.message
                if not msg or not msg.text: return
                text = msg.text.strip()
                sender_id = event.sender_id
                is_out = event.out
                
                logger.info(f"📩 MSG: out={is_out} sender={sender_id} text='{text[:50]}'")
                
                if not is_out or sender_id != self_user_id:
                    return
                
                if text in ("پنل", ".پنل", "/panel", "/پنل"):
                    logger.info(f"✅ PANEL cmd in {event.chat_id}")
                    await handle_panel_command(client, event, self_user_id, int(user_id), account_index)
                    return
                
                if text in (".بلاک", "بلاک", "/block", "/بلاک"):
                    await handle_self_block(client, event, self_user_id)
                    return
                
                panel_key = (str(self_user_id), account_index)
                if text.isdigit() and len(text) <= 2 and panel_key in panel_sessions:
                    ps = panel_sessions[panel_key]
                    if get_iran_time() <= ps['expires'] and event.chat_id == ps['chat_id']:
                        try: await client.delete_messages(event.chat_id, [msg.id])
                        except: pass
                        await process_panel_option(
                            client, event.chat_id, ps['msg_id'], text,
                            self_user_id, int(user_id), account_index
                        )
                        return
            except Exception as e:
                logger.exception(f"msg handler err: {e}")

        async def run_client():
            try: await client.run_until_disconnected()
            except Exception as e:
                if not is_shutting_down:
                    logger.exception(f"Client disconnected {key}: {e}")

        task = asyncio.create_task(run_client())
        self_tasks[key] = task
        logger.info(f"✅ Self started: {self_user_id}[{account_index}]")
        return True
    except Exception as e:
        logger.exception(f"start_self err: {e}")
        return False

async def handle_panel_command(client, event, self_user_id, owner_id, account_index):
    try:
        chat_id = event.chat_id
        msg_id = event.message.id
        
        try:
            await client.delete_messages(chat_id, [msg_id])
            logger.info(f"🗑️ Panel cmd deleted in {chat_id}")
        except Exception as e:
            logger.warning(f"Delete err: {e}")

        await send_panel_hint(client, chat_id, self_user_id, owner_id, account_index)
    except Exception as e:
        logger.exception(f"handle_panel_command err: {e}")

async def send_panel_hint(client, chat_id, self_user_id, owner_id, account_index):
    """پیام ساده + راهنمای Inline Mode"""
    try:
        me = await client.get_me()
        sa = None
        try:
            selfs = self_data.get(str(owner_id), [])
            if 0 <= account_index < len(selfs):
                sa = selfs[account_index]
        except: pass

        text = (
            f"🌟 <b>پنل مدیریت سلف</b>\n\n"
            f"👤 <b>نام:</b> {html.escape((me.first_name or '') + ((' ' + me.last_name) if me.last_name else '') or 'کاربر')}\n"
            f"🆔 <b>یوزرنیم:</b> @{html.escape(me.username or 'بدون یوزرنیم')}\n"
            f"📱 <b>شماره:</b> <code>{html.escape(sa.get('phone', '-') if sa else '-')}</code>\n"
            f"⏰ <b>ساعت:</b> {'🟢 فعال' if (sa and sa.get('clock_active')) else '🔴 غیرفعال'}\n"
            f"🎨 <b>فونت:</b> {FONT_NAMES.get(sa.get('font_type', '1'), 'فونت 1') if sa else 'فونت 1'}\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💡 <b>برای پنل تعاملی با دکمه:</b>\n"
            f"توی همین چت بنویس:\n"
            f"<code>@{BOT_USERNAME} پنل</code>\n"
            f"و از نتایج، پنل خودت رو انتخاب کن 👆"
        )
        
        sent = await client.send_message(
            entity=chat_id, message=text,
            parse_mode='html', link_preview=False
        )
        logger.info(f"✅ Panel hint sent to {chat_id}, msg={sent.id}")
        
        panel_sessions[(str(self_user_id), account_index)] = {
            'chat_id': chat_id,
            'msg_id': sent.id,
            'owner_id': owner_id,
            'account_index': account_index,
            'expires': get_iran_time() + timedelta(minutes=10)
        }
    except ChatWriteForbiddenError:
        logger.warning(f"Cannot write to {chat_id}")
    except Exception as e:
        logger.exception(f"send_panel_hint err: {e}")

async def process_panel_option(client, chat_id, panel_msg_id, option, self_user_id, owner_id, account_index):
    """پردازش گزینه عددی (fallback)"""
    try:
        sa = None
        try:
            selfs = self_data.get(str(owner_id), [])
            if 0 <= account_index < len(selfs):
                sa = selfs[account_index]
        except: pass

        me = await client.get_me()
        name = (me.first_name or "") + ((" " + me.last_name) if me.last_name else "") or "کاربر"
        username = me.username or "بدون یوزرنیم"

        def header(t):
            return f"╭━━━━━━━━━━━━━━━━━╮\n   {t}\n╰━━━━━━━━━━━━━━━━━╯\n\n"
        def footer():
            return f"\n━━━━━━━━━━━━━━━━━\n👉 عدد گزینه (0=بستن، 5=بازگشت):"

        new_text = None

        if option == "1":
            new_text = header("👤 <b>حساب من</b>") + (
                f"📝 <b>نام:</b> {html.escape(name)}\n"
                f"🆔 <b>یوزرنیم:</b> @{html.escape(username)}\n"
                f"🔢 <b>آیدی:</b> <code>{me.id}</code>\n"
                f"📱 <b>شماره:</b> <code>{html.escape(me.phone or '-')}</code>\n"
                f"👤 <b>نام سلف:</b> {html.escape(sa.get('account_name','-') if sa else '-')}"
            ) + footer()

        elif option == "2":
            clock_st = "🟢 فعال" if (sa and sa.get('clock_active')) else "🔴 غیرفعال"
            key = get_self_key(int(owner_id), account_index)
            conn_st = "🟢 متصل" if key in self_clients else "🔴 قطع"
            new_text = header("📊 <b>وضعیت سرویس</b>") + (
                f"⏰ <b>ساعت:</b> {clock_st}\n"
                f"🔗 <b>اتصال:</b> {conn_st}\n"
                f"📅 <b>تاریخ:</b> {get_iran_date_str()}\n"
                f"🕐 <b>ساعت:</b> {get_iran_time_short()}"
            ) + footer()

        elif option == "3":
            clock_st = "🟢 فعال" if (sa and sa.get('clock_active')) else "🔴 غیرفعال"
            font = FONT_NAMES.get(sa.get('font_type', '1'), 'فونت 1') if sa else 'فونت 1'
            new_text = header("⏰ <b>وضعیت ساعت</b>") + (
                f"📊 <b>وضعیت:</b> {clock_st}\n"
                f"🎨 <b>فونت:</b> {font}\n"
                f"🕐 <b>زمان:</b> {get_iran_time_short()}"
            ) + footer()

        elif option == "4":
            fl = "\n".join([f"  {v['display']}  {v['name']}" for k, v in list(FONTS.items())[:8]])
            new_text = header("🎨 <b>فونت‌ها</b>") + fl + footer()

        elif option == "5":
            new_text = header("🌟 <b>پنل مدیریت سلف</b>") + (
                f"📋 <b>گزینه‌ها:</b>\n"
                f"  1=👤 حساب من\n  2=📊 وضعیت\n  3=⏰ ساعت\n  4=🎨 فونت‌ها\n  5=🔄 بروزرسانی\n  0=❌ بستن"
            ) + footer()

        elif option == "0":
            try: await client.delete_messages(chat_id, [panel_msg_id])
            except: pass
            panel_sessions.pop((str(self_user_id), account_index), None)
            return

        if new_text:
            try:
                await client.edit_message(
                    entity=chat_id, message=panel_msg_id,
                    text=new_text, parse_mode='html', link_preview=False
                )
            except MessageNotModifiedError:
                pass
    except Exception as e:
        logger.exception(f"process_opt err: {e}")

async def handle_self_block(client, event, self_user_id, is_incoming=False):
    try:
        msg = event.message
        target = None
        if msg.is_reply:
            try:
                replied = await event.get_reply_message()
                if replied and replied.sender_id and replied.sender_id != self_user_id:
                    target = await client.get_entity(replied.sender_id)
            except: pass
        if not target and event.is_private:
            try:
                peer = await event.get_chat()
                if peer and peer.id != self_user_id:
                    target = peer
            except: pass
        if not target:
            try: await event.reply("⚠️ کاربری برای بلاک پیدا نشد.")
            except: pass
            return
        await client(BlockRequest(id=target.id))
        nm = getattr(target, 'first_name', '') or getattr(target, 'username', '') or str(target.id)
        try: await event.reply(f"🚫 کاربر {nm} بلاک شد!")
        except: pass
    except Exception as e:
        logger.exception(f"block err: {e}")

# ============ هندلر Callback پنل Inline ============
async def handle_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    data = q.data
    parts = data.split('_')
    if len(parts) < 4: return

    action = parts[1]
    owner_id = parts[2]
    try: account_index = int(parts[3])
    except: return

    if str(q.from_user.id) != str(owner_id):
        await q.answer("⛔ این پنل برای شما نیست!", show_alert=True); return

    selfs = self_data.get(str(owner_id), [])
    if not (0 <= account_index < len(selfs)):
        try: await q.edit_message_text("❌ سلف یافت نشد.", parse_mode='HTML')
        except: pass
        return

    sa = selfs[account_index]
    u = q.from_user
    name = (u.first_name or "") + ((" " + u.last_name) if u.last_name else "") or "کاربر"
    username = u.username or "بدون یوزرنیم"

    kb_back = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"sp_back_{owner_id}_{account_index}")],
        [InlineKeyboardButton("❌ بستن", callback_data=f"sp_close_{owner_id}_{account_index}")]
    ])

    if action == "me":
        text = (
            f"👤 <b>حساب من</b>\n\n"
            f"📝 <b>نام:</b> {html.escape(name)}\n"
            f"🆔 <b>یوزرنیم:</b> @{html.escape(username)}\n"
            f"🔢 <b>آیدی:</b> <code>{u.id}</code>\n"
            f"📱 <b>شماره سلف:</b> <code>{html.escape(sa.get('phone','-'))}</code>\n"
            f"👤 <b>نام سلف:</b> {html.escape(sa.get('account_name','-'))}"
        )
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    elif action == "status":
        clock_st = "🟢 فعال" if sa.get('clock_active') else "🔴 غیرفعال"
        key = get_self_key(int(owner_id), account_index)
        conn_st = "🟢 متصل" if key in self_clients else "🔴 قطع"
        text = (
            f"📊 <b>وضعیت سرویس</b>\n\n"
            f"⏰ <b>ساعت:</b> {clock_st}\n"
            f"🔗 <b>اتصال:</b> {conn_st}\n"
            f"📅 <b>تاریخ:</b> {get_iran_date_str()}\n"
            f"🕐 <b>ساعت:</b> {get_iran_time_short()}"
        )
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    elif action == "clock":
        clock_st = "🟢 فعال" if sa.get('clock_active') else "🔴 غیرفعال"
        font = FONT_NAMES.get(sa.get('font_type', '1'), 'فونت 1')
        text = (
            f"⏰ <b>وضعیت ساعت</b>\n\n"
            f"📊 <b>وضعیت:</b> {clock_st}\n"
            f"🎨 <b>فونت:</b> {font}\n"
            f"🕐 <b>زمان:</b> {get_iran_time_short()}"
        )
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    elif action == "fonts":
        fl = "\n".join([f"  {v['display']}  {v['name']}" for k, v in list(FONTS.items())[:8]])
        text = f"🎨 <b>فونت‌ها</b>\n\n{fl}\n\n💡 برای تغییر به ربات برو."
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    elif action in ("refresh", "back"):
        text = build_panel_text(u, sa)
        try: await q.edit_message_text(text, reply_markup=build_panel_keyboard(owner_id, account_index), parse_mode='HTML')
        except: pass

    elif action == "close":
        try: await q.message.delete()
        except:
            try: await q.edit_message_text("✅ پنل بسته شد.")
            except: pass

# ============ منو اصلی ربات ============
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    name = escape_html(user.first_name or "کاربر")
    uid = str(user.id)
    cnt = len(self_data.get(uid, []))
    text = f"""
🌟 <b>ربات مدیریت حساب‌های شخصی</b>

<b>جناب {name} گرامی</b>

تعداد سلف‌ها: <b>{cnt}</b>

📌 <b>دستورات داخل اکانت سلف:</b>
  • <code>پنل</code> — نمایش پنل
  • <code>بلاک</code> — بلاک کاربر
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
        conn = "🔗" if get_self_key(int(uid), i) in self_clients else "❌"
        text += f"\n{i+1}. {nm} {ca} {conn}"
        kb.append([InlineKeyboardButton(f"⚙️ {i+1}. {nm}", callback_data=f"manage_{i}")])
    kb.append([InlineKeyboardButton("🔷 جدید", callback_data="new_session")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

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
🔗 اتصال: {'🟢 متصل' if conn else '🔴 قطع'}
{'🔄 جاب در حال اجرا' if job else ''}

📌 دستورات: <code>پنل</code> و <code>بلاک</code>
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
    key = get_self_key(int(uid), index)
    if key in self_clients:
        try: await self_clients[key].disconnect()
        except: pass
        del self_clients[key]
    if key in self_tasks:
        self_tasks[key].cancel()
        try: await self_tasks[key]
        except: pass
        del self_tasks[key]
    await q.edit_message_text("✅ قطع شد!", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"manage_{index}")]
    ]), parse_mode='HTML')

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
        text = f"✅ سلف ساخته شد!\n📱 {escape_html(d['phone'])}\n👤 {escape_html(an)}\n\n📌 دستورات:\n• <code>پنل</code>\n• <code>بلاک</code>"
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

# ============ Restart ============
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
    global bot_app
    try:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook"
            with urllib.request.urlopen(url, timeout=5) as r: pass
        except: pass
        print("=" * 60)
        print("🌟 Self Bot Manager")
        print("=" * 60)
        print(f"🤖 Bot: @{BOT_USERNAME}")
        print("=" * 60)

        app = (
            Application.builder()
            .token(TOKEN)
            .post_init(restart_all_self_clients)
            .build()
        )
        bot_app = app

        # ⭐ Inline Query — مهم برای پنل با دکمه
        app.add_handler(InlineQueryHandler(inline_query_handler))
        
        # Callback پنل Inline
        app.add_handler(CallbackQueryHandler(handle_panel_callback, pattern="^sp_"))
        
        # بقیه هندلرها
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
