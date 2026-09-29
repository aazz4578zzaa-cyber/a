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
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
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
                try: shutil.copy2(DATA_FILE, DATA_BACKUP_FILE)
                except: pass
            os.replace(temp_file, DATA_FILE)
        except Exception as e:
            logger.exception(f"Save error: {e}")

load_data()

# ============ کمکی ============
def escape_html(text): return html.escape(str(text))
def get_iran_time(): return datetime.now(IRAN_TZ)
def get_iran_time_str(): return get_iran_time().strftime("%H:%M:%S")
def get_iran_time_short(): return get_iran_time().strftime("%H:%M")
def get_iran_date_str(): return get_iran_time().strftime("%Y/%m/%d")
def get_self_key(user_id, account_index): return (str(user_id), account_index)

def convert_to_font(text, font_type):
    font_map = FONTS.get(font_type, FONTS['1'])['map']
    return ''.join(font_map[int(c)] if c.isdigit() else c for c in text)

def clean_clock_from_name(name):
    if not name: return name
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
    d = seconds // 86400; h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60; s = seconds % 60
    parts = []
    if d > 0: parts.append(f"{d} روز")
    if h > 0: parts.append(f"{h} ساعت")
    if m > 0: parts.append(f"{m} دقیقه")
    if s > 0 or not parts: parts.append(f"{s} ثانیه")
    return " و ".join(parts)

async def safe_disconnect(client):
    if client:
        try: await client.disconnect()
        except: pass

async def clear_user_state(user_id):
    if user_id in user_sessions:
        try:
            c = user_sessions[user_id].get('client')
            if c: await c.disconnect()
        except: pass
        del user_sessions[user_id]

# ============ ساخت پنل ============
def build_panel_keyboard(owner_id, account_index, sa):
    """کیبورد پنل کامل — همه امکانات"""
    clock_active = sa.get('clock_active', False) if sa else False
    
    rows = [
        [InlineKeyboardButton("👤 حساب من", callback_data=f"sp_me_{owner_id}_{account_index}")],
        [InlineKeyboardButton("📊 وضعیت سرویس", callback_data=f"sp_status_{owner_id}_{account_index}")],
        [
            InlineKeyboardButton("⏰ ساعت", callback_data=f"sp_clock_{owner_id}_{account_index}"),
            InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"sp_refresh_{owner_id}_{account_index}")
        ],
        [InlineKeyboardButton("📸 تنظیم پروفایل", callback_data=f"sp_profile_{owner_id}_{account_index}")],
        [InlineKeyboardButton("📋 کپی پروفایل", callback_data=f"sp_copy_{owner_id}_{account_index}")],
    ]
    
    if clock_active:
        rows.append([InlineKeyboardButton("❌ غیرفعال کردن ساعت", callback_data=f"sp_deact_{owner_id}_{account_index}")])
        rows.append([InlineKeyboardButton("🎨 تغییر فونت", callback_data=f"sp_fontsel_{owner_id}_{account_index}")])
    else:
        rows.append([InlineKeyboardButton("✅ فعال کردن ساعت", callback_data=f"sp_act_{owner_id}_{account_index}")])
    
    rows.append([InlineKeyboardButton("❌ بستن پنل", callback_data=f"sp_close_{owner_id}_{account_index}")])
    
    return InlineKeyboardMarkup(rows)

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
        f"👇 تمام کارها رو از دکمه‌های زیر انجام بده:"
    )

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
    """حذف پیام پنل و ارسال پنل از طرف ربات به Saved Messages"""
    try:
        chat_id = event.chat_id
        msg_id = event.message.id
        
        # حذف پیام دستور
        try:
            await client.delete_messages(chat_id, [msg_id])
            logger.info(f"🗑️ Panel cmd deleted in {chat_id}")
        except Exception as e:
            logger.warning(f"Delete err: {e}")

        # ارسال پنل از طرف ربات به Saved Messages (self_user_id)
        await send_panel_to_saved(client, self_user_id, owner_id, account_index)
        
        # اگه توی چت دیگه‌ای بود (نه Saved Messages)، یه پیام اطلاع بده
        if chat_id != self_user_id:
            try:
                await client.send_message(
                    entity=chat_id,
                    message="📩 <b>پنل به Saved Messages ارسال شد</b>\n\nبرو به چت «Saved Messages» و از دکمه‌های پنل استفاده کن.",
                    parse_mode='html'
                )
            except: pass
    except Exception as e:
        logger.exception(f"handle_panel_command err: {e}")

async def send_panel_to_saved(client, self_user_id, owner_id, account_index):
    """ارسال پنل با دکمه‌ها به Saved Messages"""
    global bot_app
    try:
        me = await client.get_me()
        sa = None
        try:
            selfs = self_data.get(str(owner_id), [])
            if 0 <= account_index < len(selfs):
                sa = selfs[account_index]
        except: pass

        # از bot برای ارسال پنل به Saved Messages استفاده کن
        text = build_panel_text(me, sa)
        kb = build_panel_keyboard(owner_id, account_index, sa)

        try:
            await bot_app.bot.send_message(
                chat_id=self_user_id,
                text=text,
                reply_markup=kb,
                parse_mode='HTML'
            )
            logger.info(f"✅ Panel sent to Saved Messages of {self_user_id}")
        except Exception as e:
            logger.exception(f"Bot send panel failed: {e}")
            # fallback
            try:
                await client.send_message(
                    entity=self_user_id,
                    message=text,
                    parse_mode='html',
                    link_preview=False
                )
            except: pass
    except Exception as e:
        logger.exception(f"send_panel_to_saved err: {e}")

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

# ============ هندلر Callback پنل کامل ============
async def handle_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندل کامل دکمه‌های پنل — همه کارها داخل اکانت"""
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

    kb_back = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"sp_back_{owner_id}_{account_index}")],
        [InlineKeyboardButton("❌ بستن", callback_data=f"sp_close_{owner_id}_{account_index}")]
    ])

    # ===== حساب من =====
    if action == "me":
        name = (u.first_name or "") + ((" " + u.last_name) if u.last_name else "") or "کاربر"
        text = (
            f"👤 <b>حساب من</b>\n\n"
            f"📝 <b>نام:</b> {html.escape(name)}\n"
            f"🆔 <b>یوزرنیم:</b> @{html.escape(u.username or '-')}\n"
            f"🔢 <b>آیدی:</b> <code>{u.id}</code>\n"
            f"📱 <b>شماره سلف:</b> <code>{html.escape(sa.get('phone','-'))}</code>\n"
            f"👤 <b>نام سلف:</b> {html.escape(sa.get('account_name','-'))}"
        )
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    # ===== وضعیت =====
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

    # ===== ساعت =====
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

    # ===== فعال کردن ساعت =====
    elif action == "act":
        session_string = sa.get('session'); api_id = sa.get('api_id'); api_hash = sa.get('api_hash')
        font_type = sa.get('font_type', '1')
        t = get_iran_time_short()
        r = await set_clock_on_profile(session_string, api_id, api_hash, font_type)
        if r:
            selfs[account_index]['active_time'] = t
            selfs[account_index]['clock_active'] = True
            await save_data()
            ck = get_self_key(int(owner_id), account_index)
            clock_tasks[ck] = True
            asyncio.create_task(clock_loop(int(owner_id), account_index, session_string, api_id, api_hash, font_type))
            text = f"✅ <b>ساعت فعال شد!</b>\n\n🕐 <code>{convert_to_font(t, font_type)}</code>\n🎨 {FONT_NAMES.get(font_type, 'فونت 1')}"
        else:
            text = "❌ خطا در فعال‌سازی ساعت!"
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    # ===== غیرفعال کردن ساعت =====
    elif action == "deact":
        session_string = sa.get('session'); api_id = sa.get('api_id'); api_hash = sa.get('api_hash')
        r = await remove_clock_from_profile(session_string, api_id, api_hash)
        if r:
            selfs[account_index]['clock_active'] = False
            await save_data()
            ck = get_self_key(int(owner_id), account_index)
            if ck in clock_tasks:
                clock_tasks[ck] = False
                del clock_tasks[ck]
            text = "❌ <b>ساعت غیرفعال شد!</b>"
        else:
            text = "❌ خطا!"
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    # ===== فونت‌ها =====
    elif action == "fonts":
        fl = "\n".join([f"  {v['display']}  {v['name']}" for k, v in list(FONTS.items())[:8]])
        text = f"🎨 <b>فونت‌های موجود</b>\n\n{fl}"
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    # ===== انتخاب فونت =====
    elif action == "fontsel":
        kb = []
        for fid, finfo in FONTS.items():
            kb.append([InlineKeyboardButton(
                f"{finfo['display']} - {finfo['name']}",
                callback_data=f"sp_fontap_{owner_id}_{account_index}_{fid}"
            )])
        kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"sp_back_{owner_id}_{account_index}")])
        try: await q.edit_message_text("🎨 <b>انتخاب فونت</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        except: pass

    # ===== اعمال فونت =====
    elif action == "fontap":
        if len(parts) < 5: return
        ft = parts[4]
        if ft not in FONTS:
            await q.answer("❌ فونت نامعتبر", show_alert=True); return
        session_string = sa.get('session'); api_id = sa.get('api_id'); api_hash = sa.get('api_hash')
        r = await set_clock_on_profile(session_string, api_id, api_hash, ft)
        if r:
            selfs[account_index]['font_type'] = ft
            await save_data()
            ck = get_self_key(int(owner_id), account_index)
            if ck in clock_tasks and clock_tasks.get(ck):
                clock_tasks[ck] = False
                del clock_tasks[ck]
                clock_tasks[ck] = True
                asyncio.create_task(clock_loop(int(owner_id), account_index, session_string, api_id, api_hash, ft))
            text = f"✅ <b>فونت تغییر کرد!</b>\n\n🎨 {FONT_NAMES[ft]}"
        else:
            text = "❌ خطا!"
        try: await q.edit_message_text(text, reply_markup=kb_back, parse_mode='HTML')
        except: pass

    # ===== تنظیم پروفایل =====
    elif action == "profile":
        context.user_data['profile_index'] = account_index
        context.user_data['profile_owner'] = owner_id
        context.user_data['profile_step'] = 'waiting_media'
        context.user_data['profile_files'] = []
        text = (
            "📸 <b>تنظیم پروفایل</b>\n\n"
            "عکس یا فیلم مورد نظر رو توی <b>چت با ربات</b> بفرست.\n"
            "پس از ارسال همه، دکمه اتمام رو بزن."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ اتمام", callback_data=f"sp_profdone_{owner_id}_{account_index}")],
            [InlineKeyboardButton("🔙 لغو", callback_data=f"sp_back_{owner_id}_{account_index}")]
        ])
        # ارسال به چت کاربر از طرف ربات
        try:
            await context.bot.send_message(
                chat_id=u.id,
                text=text,
                reply_markup=kb,
                parse_mode='HTML'
            )
            await q.answer("📸 دستورالعمل به چت ربات ارسال شد")
        except: pass

    # ===== اتمام پروفایل =====
    elif action == "profdone":
        files = context.user_data.get('profile_files', [])
        if not files:
            await q.answer("❌ فایلی ارسال نشده!", show_alert=True); return
        context.user_data['profile_step'] = 'waiting_count'
        try:
            await q.edit_message_text(
                f"✅ {len(files)} فایل دریافت شد.\n\n🔢 تعداد دفعات هر فایل (1-{MAX_PROFILE_COUNT}):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data=f"sp_back_{owner_id}_{account_index}")]]),
                parse_mode='HTML'
            )
        except: pass

    # ===== کپی پروفایل =====
    elif action == "copy":
        context.user_data['copy_owner'] = owner_id
        context.user_data['copy_index'] = account_index
        context.user_data['copy_step'] = 'waiting_id'
        try:
            await context.bot.send_message(
                chat_id=u.id,
                text="📋 <b>کپی پروفایل</b>\n\nآیدی عددی کاربر رو توی چت ربات بفرست:\n<b>مثال:</b> <code>123456789</code>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لغو", callback_data=f"sp_back_{owner_id}_{account_index}")]]),
                parse_mode='HTML'
            )
            await q.answer("📋 دستورالعمل به چت ربات ارسال شد")
        except: pass

    # ===== بروزرسانی =====
    elif action in ("refresh", "back"):
        text = build_panel_text(u, sa)
        kb = build_panel_keyboard(owner_id, account_index, sa)
        try: await q.edit_message_text(text, reply_markup=kb, parse_mode='HTML')
        except: pass

    # ===== بستن =====
    elif action == "close":
        try: await q.message.delete()
        except:
            try: await q.edit_message_text("✅ پنل بسته شد.", parse_mode='HTML')
            except: pass

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

# ============ ربات فقط ساخت سلف ============
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    name = escape_html(user.first_name or "کاربر")
    uid = str(user.id)
    cnt = len(self_data.get(uid, []))
    text = f"""
🌟 <b>ربات ساخت سلف</b>

<b>{name} عزیز</b>

تعداد سلف‌ها: <b>{cnt}</b>

⚠️ <b>نکته مهم:</b>
این ربات فقط برای <b>ساخت سلف</b> هست.
برای مدیریت، داخل اکانت سلف بنویس <code>پنل</code>.
"""
    kb = [
        [InlineKeyboardButton("🔷 ایجاد سلف جدید", callback_data="new_session")],
        [InlineKeyboardButton("📋 لیست سلف‌ها", callback_data="list_selfs")]
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
        await q.edit_message_text("📋 هیچ سلفی نیست.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔷 ایجاد", callback_data="new_session")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back")]
            ]), parse_mode='HTML')
        return
    text = f"📋 <b>سلف‌ها ({len(selfs)})</b>\n\n"
    for i, sa in enumerate(selfs):
        nm = escape_html(sa.get('account_name', 'بدون نام'))
        ca = "🟢" if sa.get('clock_active') else "🔴"
        text += f"{i+1}. {nm} {ca}\n"
    text += "\n💡 برای مدیریت، داخل اکانت سلف بنویس <code>پنل</code>"
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="back")]]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

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
        text = f"✅ <b>سلف ساخته شد!</b>\n\n📱 {escape_html(d['phone'])}\n👤 {escape_html(an)}\n\n📌 حالا داخل اکانت سلفت بنویس <code>پنل</code> تا پنل مدیریت باز بشه."
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
    for k in ('profile_step','profile_index','copy_step','copy_index','profile_owner','copy_owner'):
        context.user_data.pop(k, None)
    await main_menu(update, context, edit=True)

# ============ هندلر پیام‌های ربات ============
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # ساخت سلف
    if uid in user_sessions:
        s = user_sessions[uid].get("step")
        if s == "phone": await handle_phone(update, context); return
        elif s == "api_id": await handle_api_id(update, context); return
        elif s == "api_hash": await handle_api_hash(update, context); return
        elif s == "code": await handle_code(update, context); return
        elif s == "password": await handle_password(update, context); return

    # پروفایل
    if context.user_data.get('profile_step') == 'waiting_media':
        await handle_profile_media(update, context); return
    if context.user_data.get('profile_step') == 'waiting_count':
        await handle_profile_count(update, context); return
    
    # کپی
    if context.user_data.get('copy_step') == 'waiting_id':
        await handle_copy_id(update, context); return

    await update.message.reply_text("❌ از منو استفاده کن. /start", parse_mode='HTML')

# ============ پروفایل ============
async def handle_profile_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
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
    count = len(context.user_data['profile_files'])
    owner = context.user_data.get('profile_owner', uid)
    idx = context.user_data.get('profile_index', 0)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اتمام", callback_data=f"sp_profdone_{owner}_{idx}")],
        [InlineKeyboardButton("🔙 لغو", callback_data=f"sp_back_{owner}_{idx}")]
    ])
    await update.message.reply_text(f"✅ فایل {count} دریافت شد.\nفایل بعدی رو بفرست یا اتمام رو بزن.",
        reply_markup=kb, parse_mode='HTML')

async def handle_profile_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    try:
        c = int(update.message.text.strip())
        if c < 1 or c > MAX_PROFILE_COUNT:
            await update.message.reply_text(f"❌ 1-{MAX_PROFILE_COUNT}", parse_mode='HTML'); return
    except:
        await update.message.reply_text("❌ عدد!", parse_mode='HTML'); return
    owner = context.user_data.get('profile_owner', uid)
    index = context.user_data.get('profile_index', 0)
    files = context.user_data.get('profile_files', [])
    selfs = self_data.get(str(owner), [])
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
        owner, index, sa.get('session'), sa.get('api_id'), sa.get('api_hash'),
        fc, c, update.effective_chat.id, context, sm.message_id
    ))
    profile_tasks[owner] = task

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
async def handle_copy_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    t = update.message.text.strip()
    if not t.isdigit():
        await update.message.reply_text("❌ عدد!", parse_mode='HTML'); return
    tid = int(t)
    owner = context.user_data.get('copy_owner', uid)
    index = context.user_data.get('copy_index', 0)
    context.user_data.pop('copy_step', None)
    selfs = self_data.get(str(owner), [])
    if not (0 <= index < len(selfs)):
        await update.message.reply_text("❌", parse_mode='HTML'); return
    sa = selfs[index]
    sm = await update.message.reply_text(
        f"🔄 شروع کپی\n🆔 {tid}",
        parse_mode='HTML')
    asyncio.create_task(run_copy_job(owner, index, sa.get('session'), sa.get('api_id'), sa.get('api_hash'),
        tid, update.effective_chat.id, context, sm.message_id))

async def run_copy_job(user_id, index, ss, ai, ah, tid, chat_id, context, smid):
    client = None; last_edit = 0
    try:
        client = TelegramClient(StringSession(ss), ai, ah)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return
        an = escape_html(self_data.get(str(user_id), [])[index].get('account_name','-'))
        try: t = await client.get_entity(tid)
        except:
            try: await context.bot.edit_message_text(f"❌ کاربر {tid} یافت نشد!", chat_id=chat_id, message_id=smid, parse_mode='HTML')
            except: pass
            return
        tn = (t.first_name or "") + (" " + t.last_name if t.last_name else "") or t.username or str(tid)
        pl = []
        try:
            ph = await client(GetUserPhotosRequest(user_id=tid, offset=0, max_id=0, limit=1000))
            pl = ph.photos if hasattr(ph, 'photos') else []
        except: pass
        total = len(pl)
        if total == 0:
            try: await context.bot.edit_message_text(f"⚠️ {escape_html(tn)} پروفایل نداره!", chat_id=chat_id, message_id=smid, parse_mode='HTML')
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
                            chat_id=chat_id, message_id=smid, parse_mode='HTML')
                    except: pass
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                logger.exception(f"copy err {i}: {e}")
                fc += 1
        try:
            await context.bot.edit_message_text(
                f"✅ کپی کامل!\n📝 {escape_html(tn)}\n📸 {total}\n✅{sc} ❌{fc}",
                chat_id=chat_id, message_id=smid, parse_mode='HTML')
        except: pass
    except Exception as e:
        logger.exception(f"copy job err: {e}")
    finally:
        await safe_disconnect(client)

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
        print("🌟 Self Bot Manager — فقط ساخت سلف")
        print("=" * 60)

        app = (
            Application.builder()
            .token(TOKEN)
            .post_init(restart_all_self_clients)
            .build()
        )
        bot_app = app

        # هندلر Callback پنل
        app.add_handler(CallbackQueryHandler(handle_panel_callback, pattern="^sp_"))
        
        # هندلرهای ربات
        app.add_handler(CallbackQueryHandler(new_session, pattern="^new_session$"))
        app.add_handler(CallbackQueryHandler(list_selfs, pattern="^list_selfs$"))
        app.add_handler(CallbackQueryHandler(cancel_profile, pattern="^cancel_profile$"))
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
