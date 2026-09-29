import asyncio
import json
import os
import platform
import random
import re
import socket
import sqlite3
import string
import subprocess
import time
from datetime import datetime, timedelta

import psutil
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, filters, MessageHandler)
from telethon import TelegramClient, events
from telethon.errors import (FloodWaitError, PhoneCodeExpiredError,
                             PhoneCodeInvalidError, PhoneNumberInvalidError,
                             SessionPasswordNeededError)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.types import KeyboardButtonCallback

TOKEN = "8961040480:AAHNKEnK7LZuCp9fSJ5td2_XdGFqPtwp_dY"
CHANNEL_USERNAME = "@ReaperSelfChannel"
ADMIN_IDS = [7803165903, 8831703400]
BOT_USERNAME = "@RipperSelfbot"

DB_FILE = "bot_database.db"

# ==================== DATABASE ====================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            joined_date TEXT,
            is_banned INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 0,
            remaining_days INTEGER DEFAULT 0,
            expiry_date TEXT,
            clock_active INTEGER DEFAULT 1
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS codes (
            code TEXT PRIMARY KEY,
            days INTEGER,
            expiry_date TEXT,
            created_date TEXT,
            used INTEGER DEFAULT 0,
            used_by TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            session_string TEXT,
            phone TEXT,
            api_hash TEXT,
            api_id INTEGER,
            created_date TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS verify_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            card_number TEXT,
            photo_id TEXT,
            status TEXT DEFAULT 'pending',
            request_date TEXT,
            admin_response TEXT,
            response_date TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY,
            banned_date TEXT,
            reason TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message TEXT,
            status TEXT DEFAULT 'open',
            created_date TEXT,
            admin_response TEXT,
            response_date TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS self_settings (
            user_id INTEGER PRIMARY KEY,
            clock_enabled INTEGER DEFAULT 0,
            auto_read INTEGER DEFAULT 0,
            auto_reply INTEGER DEFAULT 0,
            anti_insult INTEGER DEFAULT 0,
            animated_msg INTEGER DEFAULT 0,
            smart_secretary INTEGER DEFAULT 0,
            bio INTEGER DEFAULT 0,
            name_setting INTEGER DEFAULT 0,
            analytics INTEGER DEFAULT 0,
            about INTEGER DEFAULT 0,
            title INTEGER DEFAULT 0,
            anti_login INTEGER DEFAULT 0,
            auto_setting INTEGER DEFAULT 0,
            banner INTEGER DEFAULT 0,
            comment INTEGER DEFAULT 0,
            birthday INTEGER DEFAULT 0,
            alert INTEGER DEFAULT 0,
            classic INTEGER DEFAULT 1,
            modern INTEGER DEFAULT 0,
            persian INTEGER DEFAULT 1,
            english INTEGER DEFAULT 0,
            region INTEGER DEFAULT 1,
            public_self INTEGER DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

user_states = {}
salf_login_data = {}
clock_tasks = {}
admin_salf_data = {}
support_mode = {}
pending_verify = {}
user_menu_mode = {}
clock_status = {}
salf_clients = {}

if not os.path.exists("sessions"):
    os.makedirs("sessions")

# ==================== DATABASE FUNCTIONS ====================

def db_add_user(user_id, username, first_name, last_name, phone=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, phone, joined_date)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, last_name, phone, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def db_is_banned(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT is_banned FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1

def db_ban_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def db_unban_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def db_is_verified(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT is_verified FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1

def db_verify_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_verified = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def db_add_code(code, days, expiry_date):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO codes (code, days, expiry_date, created_date)
        VALUES (?, ?, ?, ?)
    ''', (code, days, expiry_date.isoformat(), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def db_get_code(code):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM codes WHERE code = ?', (code,))
    result = cursor.fetchone()
    conn.close()
    return result

def db_use_code(code, user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE codes SET used = 1, used_by = ? WHERE code = ?', (str(user_id), code))
    conn.commit()
    conn.close()

def db_delete_code(code):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM codes WHERE code = ?', (code,))
    conn.commit()
    conn.close()

def db_get_all_codes():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM codes')
    result = cursor.fetchall()
    conn.close()
    return result

def db_save_session(user_id, session_string, phone, api_hash, api_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO sessions (user_id, session_string, phone, api_hash, api_id, created_date)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, session_string, phone, api_hash, api_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def db_get_session(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM sessions WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def db_delete_session(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def db_get_all_sessions():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM sessions')
    result = cursor.fetchall()
    conn.close()
    return result

def db_add_verify_request(user_id, username, card_number, photo_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO verify_requests (user_id, username, card_number, photo_id, request_date)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, card_number, photo_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return cursor.lastrowid

def db_update_verify_request(request_id, status, admin_response=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if admin_response:
        cursor.execute('''
            UPDATE verify_requests 
            SET status = ?, admin_response = ?, response_date = ? 
            WHERE id = ?
        ''', (status, admin_response, datetime.now().isoformat(), request_id))
    else:
        cursor.execute('''
            UPDATE verify_requests 
            SET status = ?, response_date = ? 
            WHERE id = ?
        ''', (status, datetime.now().isoformat(), request_id))
    conn.commit()
    conn.close()

def db_add_support_ticket(user_id, username, message):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO support_tickets (user_id, username, message, created_date)
        VALUES (?, ?, ?, ?)
    ''', (user_id, username, message, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return cursor.lastrowid

def db_update_support_ticket(ticket_id, status, admin_response=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if admin_response:
        cursor.execute('''
            UPDATE support_tickets 
            SET status = ?, admin_response = ?, response_date = ? 
            WHERE id = ?
        ''', (status, admin_response, datetime.now().isoformat(), ticket_id))
    else:
        cursor.execute('''
            UPDATE support_tickets 
            SET status = ?, response_date = ? 
            WHERE id = ?
        ''', (status, datetime.now().isoformat(), ticket_id))
    conn.commit()
    conn.close()

def db_get_self_settings(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM self_settings WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return {
            'clock_enabled': result[1] == 1,
            'auto_read': result[2] == 1,
            'auto_reply': result[3] == 1,
            'anti_insult': result[4] == 1,
            'animated_msg': result[5] == 1,
            'smart_secretary': result[6] == 1,
            'bio': result[7] == 1,
            'name_setting': result[8] == 1,
            'analytics': result[9] == 1,
            'about': result[10] == 1,
            'title': result[11] == 1,
            'anti_login': result[12] == 1,
            'auto_setting': result[13] == 1,
            'banner': result[14] == 1,
            'comment': result[15] == 1,
            'birthday': result[16] == 1,
            'alert': result[17] == 1,
            'classic': result[18] == 1,
            'modern': result[19] == 1,
            'persian': result[20] == 1,
            'english': result[21] == 1,
            'region': result[22] == 1,
            'public_self': result[23] == 1
        }
    return {
        'clock_enabled': False,
        'auto_read': False,
        'auto_reply': False,
        'anti_insult': False,
        'animated_msg': False,
        'smart_secretary': False,
        'bio': False,
        'name_setting': False,
        'analytics': False,
        'about': False,
        'title': False,
        'anti_login': False,
        'auto_setting': False,
        'banner': False,
        'comment': False,
        'birthday': False,
        'alert': False,
        'classic': True,
        'modern': False,
        'persian': True,
        'english': False,
        'region': True,
        'public_self': False
    }

def db_update_self_settings(user_id, settings):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO self_settings 
        (user_id, clock_enabled, auto_read, auto_reply, anti_insult, animated_msg, smart_secretary,
         bio, name_setting, analytics, about, title, anti_login, auto_setting, banner, comment,
         birthday, alert, classic, modern, persian, english, region, public_self)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id,
        1 if settings.get('clock_enabled', False) else 0,
        1 if settings.get('auto_read', False) else 0,
        1 if settings.get('auto_reply', False) else 0,
        1 if settings.get('anti_insult', False) else 0,
        1 if settings.get('animated_msg', False) else 0,
        1 if settings.get('smart_secretary', False) else 0,
        1 if settings.get('bio', False) else 0,
        1 if settings.get('name_setting', False) else 0,
        1 if settings.get('analytics', False) else 0,
        1 if settings.get('about', False) else 0,
        1 if settings.get('title', False) else 0,
        1 if settings.get('anti_login', False) else 0,
        1 if settings.get('auto_setting', False) else 0,
        1 if settings.get('banner', False) else 0,
        1 if settings.get('comment', False) else 0,
        1 if settings.get('birthday', False) else 0,
        1 if settings.get('alert', False) else 0,
        1 if settings.get('classic', True) else 0,
        1 if settings.get('modern', False) else 0,
        1 if settings.get('persian', True) else 0,
        1 if settings.get('english', False) else 0,
        1 if settings.get('region', True) else 0,
        1 if settings.get('public_self', False) else 0
    ))
    conn.commit()
    conn.close()

# ==================== HELPER FUNCTIONS ====================

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_remaining_days(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT remaining_days, expiry_date FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0] > 0:
        return result[0]
    return 0

def get_expiry_date(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT expiry_date FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0]:
        return result[0].split('T')[0] if 'T' in result[0] else result[0]
    return "ندارد"

def has_active_subscription(user_id):
    return get_remaining_days(user_id) > 0

def get_clock_status(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT clock_active FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0] == 1
    return True

def set_clock_status(user_id, status):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET clock_active = ? WHERE user_id = ?', (1 if status else 0, user_id))
    conn.commit()
    conn.close()

def is_user_banned(user_id):
    return db_is_banned(user_id)

def ban_user(user_id):
    db_ban_user(user_id)

def unban_user(user_id):
    db_unban_user(user_id)

def is_user_verified(user_id):
    return db_is_verified(user_id)

def verify_user(user_id):
    db_verify_user(user_id)

def save_user_session(user_id, session_string, phone, api_hash, api_id):
    db_save_session(user_id, session_string, phone, api_hash, api_id)

def get_user_session(user_id):
    result = db_get_session(user_id)
    if result:
        return {
            'user_id': result[0],
            'session': result[1],
            'phone': result[2],
            'api_hash': result[3],
            'api_id': result[4],
            'created': result[5]
        }
    return None

def delete_user_session(user_id):
    db_delete_session(user_id)

def generate_code():
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=15))

def create_new_code(days):
    while True:
        new_code = generate_code()
        existing = db_get_code(new_code)
        if not existing:
            break
    expiry_date = datetime.now() + timedelta(days=days)
    db_add_code(new_code, days, expiry_date)
    return new_code, expiry_date

def validate_code(code):
    result = db_get_code(code)
    if not result:
        return None, "❌ کد وارد شده صحیح نیست!"
    if result[4] == 1:
        return None, "❌ این کد قبلاً استفاده شده است!"
    expiry_date = datetime.fromisoformat(result[2])
    if datetime.now() > expiry_date:
        return None, "⏳ کد وارد شده منقضی شده است!"
    return {'days': result[1], 'expiry': result[2]}, None

def use_code(code, user_id):
    result = db_get_code(code)
    if not result or result[4] == 1:
        return False
    db_use_code(code, user_id)
    days = result[1]
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT remaining_days, expiry_date FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    if user_data:
        remaining = user_data[0] or 0
        new_remaining = remaining + days
        expiry = datetime.now() + timedelta(days=new_remaining)
        cursor.execute('UPDATE users SET remaining_days = ?, expiry_date = ? WHERE user_id = ?', 
                       (new_remaining, expiry.isoformat(), user_id))
    else:
        expiry = datetime.now() + timedelta(days=days)
        cursor.execute('INSERT INTO users (user_id, remaining_days, expiry_date) VALUES (?, ?, ?)', 
                       (user_id, days, expiry.isoformat()))
    conn.commit()
    conn.close()
    return True

# ==================== SELF PANEL ====================

def build_panel_text(settings):
    text = "**⚡ لطفا یکی از گزینه‌های زیر را انتخاب نمایید:**\n\n"
    
    text += "**━━━━━━━━━━━━━━━━━━━━**\n"
    text += f"**🖥️ کلاسیک:** {'✅' if settings.get('classic', True) else '❌'}\n"
    text += f"**🖥️ مدرن:** {'✅' if settings.get('modern', False) else '❌'}\n"
    text += f"**🌍 فارسی:** {'✅' if settings.get('persian', True) else '❌'}\n"
    text += f"**🌍 انگلیسی:** {'✅' if settings.get('english', False) else '❌'}\n"
    text += f"**🗺️ منطقه:** {'✅' if settings.get('region', True) else '❌'}\n"
    text += f"**🌐 سلف همگانی:** {'✅' if settings.get('public_self', False) else '❌'}\n\n"
    
    text += "**━━━━━━━━━━━━━━━━━━━━**\n"
    text += f"**📝 بیوگرافی:** {'✅' if settings.get('bio', False) else '❌'}\n"
    text += f"**📛 اسم:** {'✅' if settings.get('name_setting', False) else '❌'}\n"
    text += f"**📊 آنالیتی:** {'✅' if settings.get('analytics', False) else '❌'}\n"
    text += f"**ℹ️ درباره:** {'✅' if settings.get('about', False) else '❌'}\n"
    text += f"**📌 عنوان:** {'✅' if settings.get('title', False) else '❌'}\n"
    text += f"**🔒 آنتی لاگین:** {'✅' if settings.get('anti_login', False) else '❌'}\n\n"
    
    text += "**━━━━━━━━━━━━━━━━━━━━**\n"
    text += f"**⏰ ساعت:** {'✅' if settings.get('clock_enabled', False) else '❌'}\n"
    text += f"**👁️ خودخوان:** {'✅' if settings.get('auto_read', False) else '❌'}\n"
    text += f"**🤖 پاسخ خودکار:** {'✅' if settings.get('auto_reply', False) else '❌'}\n"
    text += f"**🛡️ ضد توهین:** {'✅' if settings.get('anti_insult', False) else '❌'}\n"
    text += f"**🎬 پیام انیمیشنی:** {'✅' if settings.get('animated_msg', False) else '❌'}\n"
    text += f"**🧠 منشی هوشمند:** {'✅' if settings.get('smart_secretary', False) else '❌'}\n"
    text += f"**🤖 خودکار:** {'✅' if settings.get('auto_setting', False) else '❌'}\n"
    text += f"**🖼️ بنر:** {'✅' if settings.get('banner', False) else '❌'}\n"
    text += f"**💬 کامنت:** {'✅' if settings.get('comment', False) else '❌'}\n"
    text += f"**🎂 تولد:** {'✅' if settings.get('birthday', False) else '❌'}\n"
    text += f"**🔔 هشدار:** {'✅' if settings.get('alert', False) else '❌'}\n"
    
    return text

def build_panel_buttons(settings, user_id):
    buttons = []
    
    row = [
        KeyboardButtonCallback(
            text=f"🖥️ کلاسیک {'✅' if settings.get('classic', True) else '❌'}",
            data=f"self_toggle_classic_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"🖥️ مدرن {'✅' if settings.get('modern', False) else '❌'}",
            data=f"self_toggle_modern_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"🌍 فارسی {'✅' if settings.get('persian', True) else '❌'}",
            data=f"self_toggle_persian_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"🌍 انگلیسی {'✅' if settings.get('english', False) else '❌'}",
            data=f"self_toggle_english_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"🗺️ منطقه {'✅' if settings.get('region', True) else '❌'}",
            data=f"self_toggle_region_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"🌐 سلف همگانی {'✅' if settings.get('public_self', False) else '❌'}",
            data=f"self_toggle_public_self_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"📝 بیوگرافی {'✅' if settings.get('bio', False) else '❌'}",
            data=f"self_toggle_bio_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"📛 اسم {'✅' if settings.get('name_setting', False) else '❌'}",
            data=f"self_toggle_name_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"📊 آنالیتی {'✅' if settings.get('analytics', False) else '❌'}",
            data=f"self_toggle_analytics_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"ℹ️ درباره {'✅' if settings.get('about', False) else '❌'}",
            data=f"self_toggle_about_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"📌 عنوان {'✅' if settings.get('title', False) else '❌'}",
            data=f"self_toggle_title_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"🔒 آنتی لاگین {'✅' if settings.get('anti_login', False) else '❌'}",
            data=f"self_toggle_anti_login_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"⏰ ساعت {'✅' if settings.get('clock_enabled', False) else '❌'}",
            data=f"self_toggle_clock_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"👁️ خودخوان {'✅' if settings.get('auto_read', False) else '❌'}",
            data=f"self_toggle_auto_read_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"🤖 پاسخ خودکار {'✅' if settings.get('auto_reply', False) else '❌'}",
            data=f"self_toggle_auto_reply_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"🛡️ ضد توهین {'✅' if settings.get('anti_insult', False) else '❌'}",
            data=f"self_toggle_anti_insult_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"🎬 پیام انیمیشنی {'✅' if settings.get('animated_msg', False) else '❌'}",
            data=f"self_toggle_animated_msg_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"🧠 منشی هوشمند {'✅' if settings.get('smart_secretary', False) else '❌'}",
            data=f"self_toggle_smart_secretary_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"🤖 خودکار {'✅' if settings.get('auto_setting', False) else '❌'}",
            data=f"self_toggle_auto_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"🖼️ بنر {'✅' if settings.get('banner', False) else '❌'}",
            data=f"self_toggle_banner_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"💬 کامنت {'✅' if settings.get('comment', False) else '❌'}",
            data=f"self_toggle_comment_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text=f"🎂 تولد {'✅' if settings.get('birthday', False) else '❌'}",
            data=f"self_toggle_birthday_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text=f"🔔 هشدار {'✅' if settings.get('alert', False) else '❌'}",
            data=f"self_toggle_alert_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    row = [
        KeyboardButtonCallback(
            text="🔄 بروزرسانی",
            data=f"self_refresh_{user_id}".encode()
        ),
        KeyboardButtonCallback(
            text="❌ بستن پنل",
            data=f"self_close_{user_id}".encode()
        )
    ]
    buttons.append(row)
    
    return buttons

async def send_self_panel(client, user_id, chat_id, message_id=None):
    try:
        settings = db_get_self_settings(user_id)
        
        panel_text = build_panel_text(settings)
        buttons = build_panel_buttons(settings, user_id)
        
        if message_id:
            try:
                await client.edit_message(
                    chat_id,
                    message_id,
                    panel_text,
                    buttons=buttons,
                    parse_mode='markdown'
                )
                return message_id
            except:
                pass
        
        # ارسال پنل با @RipperSelfbot
        sent_msg = await client.send_message(
            chat_id,
            panel_text,
            buttons=buttons,
            parse_mode='markdown'
        )
        
        return sent_msg.id
        
    except Exception as e:
        print(f"خطا در ارسال پنل: {e}")
        return None

async def show_self_panel(client, event):
    try:
        user_id = event.sender_id
        
        if not has_active_subscription(user_id):
            await client.send_message(
                event.message.peer_id,
                "**❌ شما اشتراک فعال ندارید!**\n**💳 لطفا اشتراک خریداری کنید.**",
                parse_mode='markdown'
            )
            return
        
        # حذف پیام کاربر (پنل)
        try:
            await client.delete_messages(event.message.peer_id, [event.message.id])
        except:
            pass
        
        # ارسال پنل جدید
        await send_self_panel(client, user_id, event.message.peer_id)
        
    except Exception as e:
        print(f"خطا در نمایش پنل: {e}")

async def handle_self_callback(event, client):
    try:
        data = event.data.decode('utf-8')
        parts = data.split('_')
        
        if len(parts) < 3:
            return
        
        action = parts[1]
        user_id = int(parts[2])
        
        settings = db_get_self_settings(user_id)
        
        toggle_map = {
            'clock': 'clock_enabled',
            'auto_read': 'auto_read',
            'auto_reply': 'auto_reply',
            'anti_insult': 'anti_insult',
            'animated_msg': 'animated_msg',
            'smart_secretary': 'smart_secretary',
            'bio': 'bio',
            'name': 'name_setting',
            'analytics': 'analytics',
            'about': 'about',
            'title': 'title',
            'anti_login': 'anti_login',
            'auto': 'auto_setting',
            'banner': 'banner',
            'comment': 'comment',
            'birthday': 'birthday',
            'alert': 'alert',
            'classic': 'classic',
            'modern': 'modern',
            'persian': 'persian',
            'english': 'english',
            'region': 'region',
            'public_self': 'public_self'
        }
        
        if action in toggle_map:
            key = toggle_map[action]
            settings[key] = not settings.get(key, False)
            
            if key == 'classic' and settings['classic']:
                settings['modern'] = False
            elif key == 'modern' and settings['modern']:
                settings['classic'] = False
            
            if key == 'persian' and settings['persian']:
                settings['english'] = False
            elif key == 'english' and settings['english']:
                settings['persian'] = False
            
            db_update_self_settings(user_id, settings)
            
            if key == 'clock_enabled':
                set_clock_status(user_id, settings['clock_enabled'])
                if settings['clock_enabled']:
                    await set_clock_on_profile(user_id)
                else:
                    await remove_clock_from_profile(user_id)
            
            chat_id = event.chat_id
            message_id = event.message_id
            
            await send_self_panel(client, user_id, chat_id, message_id)
            
            await event.answer("✅ تغییرات اعمال شد!")
            return
        
        if action == 'refresh':
            chat_id = event.chat_id
            message_id = event.message_id
            await send_self_panel(client, user_id, chat_id, message_id)
            await event.answer("🔄 پنل بروزرسانی شد!")
            return
        
        if action == 'close':
            try:
                await client.delete_messages(event.chat_id, [event.message_id])
            except:
                pass
            await event.answer("❌ پنل بسته شد!")
            return
        
    except Exception as e:
        print(f"خطا در هندلر کالبک سلف: {e}")
        try:
            await event.answer("❌ خطا!")
        except:
            pass

# ==================== SELF CLIENTS ====================

async def start_salf_client(user_id):
    try:
        session_data = get_user_session(user_id)
        if not session_data:
            return False
        
        if user_id in salf_clients:
            try:
                await salf_clients[user_id].disconnect()
            except:
                pass
            del salf_clients[user_id]
        
        client = TelegramClient(
            f"sessions/user_{user_id}",
            session_data['api_id'],
            session_data['api_hash']
        )
        await client.connect()
        
        if not await client.is_user_authorized():
            try:
                await client.sign_in(session_data['phone'])
            except SessionPasswordNeededError:
                for admin_id in ADMIN_IDS:
                    try:
                        await client.send_message(
                            admin_id,
                            "⚠️ اکانت کاربر دارای سیستم تایید دو مرحله‌ای (2FA) است.\n🗝 لطفاً رمز عبور اختصاصی را وارد کنید:"
                        )
                    except:
                        pass
                return False
            except:
                await client.disconnect()
                return False
        
        salf_clients[user_id] = client
        
        @client.on(events.MessageEdited)
        @client.on(events.NewMessage)
        async def panel_handler(event):
            if event.sender_id == user_id:
                if event.message and event.message.text:
                    if event.message.text.strip() == "پنل":
                        await show_self_panel(client, event)
        
        @client.on(events.CallbackQuery)
        async def callback_handler(event):
            await handle_self_callback(event, client)
        
        await client.run_until_disconnected()
        return True
        
    except Exception as e:
        print(f"خطا در شروع سلف کاربر {user_id}: {e}")
        return False

async def start_all_salf_clients():
    sessions = db_get_all_sessions()
    for session in sessions:
        user_id = session[0]
        asyncio.create_task(start_salf_client(user_id))

# ==================== CLOCK FUNCTIONS ====================

async def set_clock_on_profile(user_id):
    try:
        if not get_clock_status(user_id):
            return False
        session_data = get_user_session(user_id)
        if not session_data:
            return False
        if session_data['api_id'] > 2147483647:
            return False
        client = TelegramClient(
            f"sessions/user_{user_id}",
            session_data['api_id'],
            session_data['api_hash']
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False
        me = await client.get_me()
        first_name = me.first_name if me.first_name else ""
        last_name = me.last_name if me.last_name else ""
        current_name = f"{first_name} {last_name}".strip()
        if not current_name:
            current_name = me.username if me.username else "کاربر"
        iran_tz = pytz.timezone('Asia/Tehran')
        iran_time = datetime.now(iran_tz)
        time_str = iran_time.strftime('%H:%M')
        clean_name = re.sub(r'\s*\d{2}:\d{2}$', '', current_name).strip()
        new_name = f"{clean_name} {time_str}".strip()
        if new_name != current_name:
            try:
                await client(UpdateProfileRequest(first_name=new_name))
                await client.disconnect()
                return True
            except:
                await client.disconnect()
                return False
        await client.disconnect()
        return True
    except:
        return False

async def remove_clock_from_profile(user_id):
    try:
        session_data = get_user_session(user_id)
        if not session_data:
            return False
        if session_data['api_id'] > 2147483647:
            return False
        client = TelegramClient(
            f"sessions/user_{user_id}",
            session_data['api_id'],
            session_data['api_hash']
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False
        me = await client.get_me()
        first_name = me.first_name if me.first_name else ""
        last_name = me.last_name if me.last_name else ""
        current_name = f"{first_name} {last_name}".strip()
        if not current_name:
            current_name = me.username if me.username else "کاربر"
        clean_name = re.sub(r'\s*\d{2}:\d{2}$', '', current_name).strip()
        if clean_name != current_name:
            try:
                await client(UpdateProfileRequest(first_name=clean_name))
                await client.disconnect()
                return True
            except:
                await client.disconnect()
                return False
        await client.disconnect()
        return True
    except:
        return False

# ==================== SERVER INFO ====================

async def get_server_info():
    try:
        ping_time = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ping", "-c", "3", "-W", "2", "8.8.8.8",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate(timeout=5)
            if process.returncode == 0:
                output = stdout.decode()
                avg_match = re.search(r'avg\s*=\s*(\d+\.?\d*)/(\d+\.?\d*)/(\d+\.?\d*)', output)
                if avg_match:
                    ping_time = float(avg_match.group(2))
                else:
                    ping_match = re.search(r'time[=<](\d+\.?\d*)\s*ms', output)
                    if ping_match:
                        ping_time = float(ping_match.group(1))
        except:
            ping_time = None
        if ping_time is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                start_time = time.time()
                sock.connect(("8.8.8.8", 53))
                end_time = time.time()
                sock.close()
                ping_time = (end_time - start_time) * 1000
            except:
                ping_time = None
        cpu_percent = f"{psutil.cpu_percent(interval=0.5):.1f}%"
        memory = psutil.virtual_memory()
        memory_info = f"{memory.percent:.1f}% ({memory.used // (1024**3)}GB / {memory.total // (1024**3)}GB)"
        disk = psutil.disk_usage('/')
        disk_info = f"{disk.percent:.1f}% ({disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB)"
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        if days > 0:
            uptime = f"{days} روز، {hours} ساعت، {minutes} دقیقه"
        elif hours > 0:
            uptime = f"{hours} ساعت، {minutes} دقیقه"
        else:
            uptime = f"{minutes} دقیقه"
        if ping_time is None:
            status = "🔴 قطع"
        elif ping_time < 50:
            status = "🟢 عالی"
        elif ping_time < 100:
            status = "🟢 آنلاین"
        elif ping_time < 200:
            status = "🟡 هشدار"
        else:
            status = "🔴 ضعیف"
        return {
            'status': status,
            'ping': f"{ping_time:.1f} ms" if ping_time else "❌ نامشخص",
            'cpu': cpu_percent,
            'memory': memory_info,
            'disk': disk_info,
            'os': platform.system() + " " + platform.release(),
            'uptime': uptime
        }
    except:
        return {
            'status': "🟢 آنلاین",
            'ping': "📶 متصل",
            'cpu': "نامشخص",
            'memory': "نامشخص",
            'disk': "نامشخص",
            'os': platform.system(),
            'uptime': "نامشخص"
        }

def get_host_expiry():
    try:
        start_date = datetime(2026, 7, 28)
        total_days = 30
        today = datetime.now()
        days_passed = (today - start_date).days
        days_left = total_days - days_passed
        if days_left < 0:
            days_left = 0
        expiry_date = start_date + timedelta(days=total_days)
        return {
            'days_left': days_left,
            'total_days': total_days,
            'expiry_date': expiry_date.strftime('%Y-%m-%d'),
            'start_date': start_date.strftime('%Y-%m-%d'),
            'percent': (days_left / total_days) * 100 if days_left > 0 else 0
        }
    except:
        return {
            'days_left': 26,
            'total_days': 30,
            'expiry_date': "2026-08-27",
            'start_date': "2026-07-28",
            'percent': 86.6
        }

# ==================== BOT COMMANDS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    db_add_user(user_id, user.username, user.first_name, user.last_name)
    if is_user_banned(user_id):
        await update.message.reply_text(
            "<b>🚫 شما از طرف مدیریت مسدود شده اید!</b>\n<b>💠 در صورت نیاز با پشتیبانی تماس بگیرید.</b>",
            parse_mode='HTML'
        )
        return
    
    # بررسی اینکه کاربر احراز هویت شده یا نه
    is_verified = is_user_verified(user_id)
    
    user_mention = f"@{user.username}" if user.username else user.first_name
    if user_id in user_states:
        del user_states[user_id]
    if user_id in user_menu_mode:
        del user_menu_mode[user_id]
    if is_admin(user_id):
        text = (
            f"<b>⚡ درود {user_mention} به پنل ریپر سلف خوش آمدید.</b>\n\n"
            "<b>🛠️ در این پنل میتوانید ربات را کنترل و مدیریت کنید.</b>\n\n"
            "<b>🔄 لطفا از منوی زیر انتخاب نمایید.</b>"
        )
        keyboard = [
            [InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings")],
            [InlineKeyboardButton("📊 آمار کل", callback_data="admin_stats")],
            [InlineKeyboardButton("📡 بررسی پینگ", callback_data="admin_ping"), InlineKeyboardButton("⏳ اعتبار هاست", callback_data="admin_host")],
            [InlineKeyboardButton("👥 منوی کاربران", callback_data="admin_users_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        return
    try:
        chat_member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        if chat_member.status in ["member", "administrator", "creator"]:
            remaining_days = get_remaining_days(user_id)
            has_subscription = has_active_subscription(user_id)
            session_data = get_user_session(user_id)
            is_logged_in = session_data is not None
            expiry_date = get_expiry_date(user_id)
            text = (
                f"<b>⚡ سلام {user_mention} به ربات ریپر سلف خوش آمدید!</b>\n\n"
                "<b>🎯 در این ربات میتوانید از پشتیبانی، خرید، نصب ربات سلف بهره ببرید!</b>\n\n"
                "<b>💠 اگر سوالی دارید از بخش پشتیبانی استفاده کنید.</b>"
            )
            keyboard = []
            keyboard.append([InlineKeyboardButton("👨‍💻 پشتیبانی", callback_data="support")])
            keyboard.append([InlineKeyboardButton("🤔 سلف چیست؟", callback_data="what_is_self"), InlineKeyboardButton("📣 کانال ما", url="https://t.me/ReaperSelfChannel")])
            keyboard.append([InlineKeyboardButton(f"📅 انقضا شما: ({remaining_days} روز)", callback_data="expiry")])
            
            # اگر احراز هویت شده، دکمه احراز هویت رو نشون نده
            if is_verified:
                keyboard.append([InlineKeyboardButton("✅ احراز هویت شده", callback_data="verified_already")])
            else:
                keyboard.append([InlineKeyboardButton("✔️ احراز هویت", callback_data="verify")])
            
            keyboard.append([InlineKeyboardButton("💳 خرید اشتراک", callback_data="buy_subscription")])
            keyboard.append([InlineKeyboardButton("💶 خرید با کد", callback_data="buy_with_code")])
            if has_subscription:
                keyboard.append([InlineKeyboardButton("🔑 ورود سلف", callback_data="salf_login")])
            keyboard.append([InlineKeyboardButton("💎 نرخ", callback_data="rate")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
            return
    except:
        pass
    text = (
        "<b>🔗 برای دسترسی به خدمات ما، ابتدا باید در کانال زیر عضو شوید.</b>\n"
        "<b>✅ پس از عضویت، روی دکمه «عضو شدم» کلیک کنید.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔗 ریپر سلف", url="https://t.me/ReaperSelfChannel")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_membership")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = query.from_user
    if is_user_banned(user_id):
        await query.edit_message_text(
            "<b>🚫 شما از طرف مدیریت مسدود شده اید!</b>\n<b>💠 در صورت نیاز با پشتیبانی تماس بگیرید.</b>",
            parse_mode='HTML'
        )
        return
    
    is_verified = is_user_verified(user_id)
    
    user_mention = f"@{user.username}" if user.username else user.first_name
    if is_admin(user_id):
        text = (
            f"<b>⚡ درود {user_mention} به پنل ریپر سلف خوش آمدید.</b>\n\n"
            "<b>🛠️ در این پنل میتوانید ربات را کنترل و مدیریت کنید.</b>\n\n"
            "<b>🔄 لطفا از منوی زیر انتخاب نمایید.</b>"
        )
        keyboard = [
            [InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings")],
            [InlineKeyboardButton("📊 آمار کل", callback_data="admin_stats")],
            [InlineKeyboardButton("📡 بررسی پینگ", callback_data="admin_ping"), InlineKeyboardButton("⏳ اعتبار هاست", callback_data="admin_host")],
            [InlineKeyboardButton("👥 منوی کاربران", callback_data="admin_users_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
        return
    try:
        chat_member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        if chat_member.status in ["member", "administrator", "creator"]:
            remaining_days = get_remaining_days(user_id)
            has_subscription = has_active_subscription(user_id)
            session_data = get_user_session(user_id)
            is_logged_in = session_data is not None
            expiry_date = get_expiry_date(user_id)
            text = (
                f"<b>⚡ سلام {user_mention} به ربات ریپر سلف خوش آمدید!</b>\n\n"
                "<b>🎯 در این ربات میتوانید از پشتیبانی، خرید، نصب ربات سلف بهره ببرید!</b>\n\n"
                "<b>💠 اگر سوالی دارید از بخش پشتیبانی استفاده کنید.</b>"
            )
            keyboard = []
            keyboard.append([InlineKeyboardButton("👨‍💻 پشتیبانی", callback_data="support")])
            keyboard.append([InlineKeyboardButton("🤔 سلف چیست؟", callback_data="what_is_self"), InlineKeyboardButton("📣 کانال ما", url="https://t.me/ReaperSelfChannel")])
            keyboard.append([InlineKeyboardButton(f"📅 انقضا شما: ({remaining_days} روز)", callback_data="expiry")])
            
            if is_verified:
                keyboard.append([InlineKeyboardButton("✅ احراز هویت شده", callback_data="verified_already")])
            else:
                keyboard.append([InlineKeyboardButton("✔️ احراز هویت", callback_data="verify")])
            
            keyboard.append([InlineKeyboardButton("💳 خرید اشتراک", callback_data="buy_subscription")])
            keyboard.append([InlineKeyboardButton("💶 خرید با کد", callback_data="buy_with_code")])
            if has_subscription:
                keyboard.append([InlineKeyboardButton("🔑 ورود سلف", callback_data="salf_login")])
            keyboard.append([InlineKeyboardButton("💎 نرخ", callback_data="rate")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            text = (
                "<b>🔗 شما هنوز عضو کانال زیر نشده اید!</b>\n"
                "<b>✅ ابتدا برای استفاده از ربات در کانال زیر عضو شوید!</b>"
            )
            keyboard = [
                [InlineKeyboardButton("🔗 ریپر سلف", url="https://t.me/ReaperSelfChannel")],
                [InlineKeyboardButton("✅ عضو شدم", callback_data="check_membership")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
    except Exception as e:
        await query.answer("❌ خطا در بررسی عضویت!", show_alert=True)

# ==================== SUPPORT ====================

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if is_user_banned(user_id):
        await query.edit_message_text(
            "<b>🚫 شما از طرف مدیریت مسدود شده اید!</b>\n<b>💠 در صورت نیاز با پشتیبانی تماس بگیرید.</b>",
            parse_mode='HTML'
        )
        return
    support_mode[user_id] = True
    text = (
        "<b>⚡ شما با موفقیت به بخش پشتیبانی متصل شدید.</b>\n\n"
        "<b>⚠️ از ارسال پیام‌های اسپم و تکراری خودداری کنید.</b>\n"
        "<b>🚫 استفاده از دستورات سلف در این بخش ممنوع بوده و باعث مسدود شدن شما خواهد شد.</b>\n\n"
        "<b>✍️ اکنون میتوانید پیام یا سوال خود را برای تیم پشتیبانی ارسال کنید.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("❌ لغو اتصال", callback_data="disconnect_support")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def disconnect_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id in support_mode:
        del support_mode[user_id]
    text = (
        "<b>⚡ اتصال شما با تیم پشتیبانی با موفقیت قطع شد.</b>\n"
        "<b>🔙 با استفاده از دکمه زیر میتوانید به منوی اصلی بازگردید.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in support_mode:
        return
    if is_user_banned(user_id):
        await update.message.reply_text("<b>🚫 شما از طرف مدیریت مسدود شده اید!</b>", parse_mode='HTML')
        return
    user = update.effective_user
    user_mention = f"@{user.username}" if user.username else user.first_name
    user_id_str = str(user_id)
    message_text = update.message.text or update.message.caption or "پیام بدون متن"
    ticket_id = db_add_support_ticket(user_id, user_mention, message_text)
    iran_tz = pytz.timezone('Asia/Tehran')
    iran_time = datetime.now(iran_tz)
    time_str = iran_time.strftime('%H:%M')
    date_str = iran_time.strftime('%Y-%m-%d')
    for admin_id in ADMIN_IDS:
        try:
            admin_text = (
                f"<b>📩 پیام جدید از بخش پشتیبانی</b>\n\n"
                f"<b>🆔 شماره تیکت: {ticket_id}</b>\n"
                f"<b>👤 نام کاربر: {user_mention}</b>\n"
                f"<b>🆔 آیدی عددی: {user_id_str}</b>\n"
                f"<b>📝 متن پیام:</b>\n"
                f"<code>{message_text}</code>\n\n"
                f"<b>🕐 ساعت: {time_str}</b>\n"
                f"<b>📅 تاریخ: {date_str}</b>"
            )
            keyboard = [
                [InlineKeyboardButton("💬 پاسخ به کاربر", callback_data=f"reply_{user_id}")],
                [InlineKeyboardButton("🚫 مسدود کردن کاربر", callback_data=f"block_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            if update.message.photo:
                photo = update.message.photo[-1]
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo.file_id,
                    caption=admin_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            elif update.message.document:
                doc = update.message.document
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=doc.file_id,
                    caption=admin_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            elif update.message.video:
                video = update.message.video
                await context.bot.send_video(
                    chat_id=admin_id,
                    video=video.file_id,
                    caption=admin_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
        except:
            pass
    
    keyboard = [
        [InlineKeyboardButton("❌ لغو اتصال", callback_data="disconnect_support")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "<b>✅ پیام شما با موفقیت به تیم پشتیبانی ارسال شد.</b>\n"
        "<b>⏳ لطفا صبور باشید و منتظر پاسخ بمانید.</b>\n"
        "<b>⚠️ از ارسال پیام‌های تکراری و اسپم خودداری فرمایید.</b>",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# ==================== ADMIN REPLY ====================

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("reply_"):
        user_id = int(data.split("_")[1])
        user_states[query.from_user.id] = f"replying_to_{user_id}"
        # ویرایش پیام و نمایش پیام پاسخ
        await query.edit_message_text(
            "<b>💬 پاسخ به کاربر</b>\n\n"
            "<b>✍️ لطفا پاسخ خود را به صورت متن یا رسانه ارسال کنید.</b>",
            parse_mode='HTML'
        )
        
    elif data.startswith("block_"):
        user_id = int(data.split("_")[1])
        if not is_user_banned(user_id):
            ban_user(user_id)
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="<b>🚫 شما از طرف مدیریت مسدود شده اید!</b>\n<b>💠 در صورت نیاز با پشتیبانی تماس بگیرید.</b>",
                    parse_mode='HTML'
                )
            except:
                pass
            await query.edit_message_text(
                f"<b>🚫 کاربر با آیدی {user_id} با موفقیت مسدود شد!</b>\n"
                f"<b>🔒 کاربر دیگر نمیتواند از ربات استفاده کند.</b>\n\n"
                f"<b>🔙 بازگشت به منوی اصلی:</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_back")]]),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                f"<b>⚠️ کاربر با آیدی {user_id} قبلاً مسدود شده است!</b>",
                parse_mode='HTML'
            )

async def handle_admin_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or not str(user_states[user_id]).startswith("replying_to_"):
        return
    target_user_id = int(user_states[user_id].split("_")[2])
    try:
        if update.message.text:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"<b>📩 پاسخ از تیم پشتیبانی:</b>\n\n{update.message.text}",
                parse_mode='HTML'
            )
        elif update.message.photo:
            photo = update.message.photo[-1]
            caption = f"<b>📩 پاسخ از تیم پشتیبانی:</b>\n\n{update.message.caption if update.message.caption else ''}"
            await context.bot.send_photo(
                chat_id=target_user_id,
                photo=photo.file_id,
                caption=caption,
                parse_mode='HTML'
            )
        elif update.message.document:
            doc = update.message.document
            caption = f"<b>📩 پاسخ از تیم پشتیبانی:</b>\n\n{update.message.caption if update.message.caption else ''}"
            await context.bot.send_document(
                chat_id=target_user_id,
                document=doc.file_id,
                caption=caption,
                parse_mode='HTML'
            )
        elif update.message.video:
            video = update.message.video
            caption = f"<b>📩 پاسخ از تیم پشتیبانی:</b>\n\n{update.message.caption if update.message.caption else ''}"
            await context.bot.send_video(
                chat_id=target_user_id,
                video=video.file_id,
                caption=caption,
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text("<b>❌ نوع پیام پشتیبانی نمیشود!</b>", parse_mode='HTML')
            return
        
        await update.message.reply_text(
            f"<b>✅ پاسخ شما با موفقیت برای کاربر {target_user_id} ارسال شد.</b>\n"
            f"<b>💬 پیام شما به کاربر رسید.</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        await update.message.reply_text(
            f"<b>❌ خطا در ارسال پاسخ: {str(e)}</b>",
            parse_mode='HTML'
        )
    del user_states[user_id]

# ==================== ADMIN FUNCTIONS ====================

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users WHERE is_verified = 1')
    verified_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users WHERE is_banned = 1')
    banned_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM codes')
    total_codes = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM codes WHERE used = 1')
    used_codes = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM sessions')
    total_sessions = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM support_tickets WHERE status = "open"')
    open_tickets = cursor.fetchone()[0]
    conn.close()
    text = (
        "<b>📊 آمار کل ربات</b>\n\n"
        f"<b>👥 تعداد کل کاربران: {total_users}</b>\n"
        f"<b>✅ کاربران احراز هویت شده: {verified_users}</b>\n"
        f"<b>🚫 کاربران مسدود شده: {banned_users}</b>\n"
        f"<b>🔢 تعداد کل کدهای سلف: {total_codes}</b>\n"
        f"<b>✅ کدهای استفاده شده: {used_codes}</b>\n"
        f"<b>❌ کدهای استفاده نشده: {total_codes - used_codes}</b>\n"
        f"<b>👥 تعداد سشن‌های ذخیره شده: {total_sessions}</b>\n"
        f"<b>🎫 تیکت‌های باز پشتیبانی: {open_tickets}</b>\n"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_info = await get_server_info()
    if server_info:
        text = (
            "<b>وضعیت پینگ هاست</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>📡 وضعیت هاست: {server_info['status']}</b>\n"
            f"<b>⚡ پینگ: {server_info['ping']}</b>\n"
            f"<b>💻 سی‌پی‌یو: {server_info['cpu']}</b>\n"
            f"<b>🧠 رم: {server_info['memory']}</b>\n"
            f"<b>💾 هارد: {server_info['disk']}</b>\n"
            f"<b>🖥️ سیستم‌عامل: {server_info['os']}</b>\n"
            f"<b>⏱️ آپ‌تایم: {server_info['uptime']}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        text = "<b>❌ خطا در دریافت اطلاعات سرور!</b>"
    keyboard = [
        [InlineKeyboardButton("🔄 بروزرسانی پینگ", callback_data="admin_ping")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
    except:
        pass

async def admin_host(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    host_info = get_host_expiry()
    bar_length = 10
    percent = host_info['percent']
    filled = int((percent / 100) * bar_length) if percent > 0 else 0
    bar = "█" * filled + "░" * (bar_length - filled)
    text = (
        "<b>⏳ اطلاعات اعتبار هاست</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📅 تاریخ شروع: {host_info['start_date']}</b>\n"
        f"<b>📆 تاریخ انقضا: {host_info['expiry_date']}</b>\n"
        f"<b>⏱️ روزهای باقی‌مانده: {host_info['days_left']} روز</b>\n"
        f"<b>📊 وضعیت: {bar} {percent:.1f}%</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    )
    if host_info['days_left'] <= 0:
        text += "\n<b>⚠️ هاست شما منقضی شده است! لطفا تمدید کنید.</b>"
    elif host_info['days_left'] <= 5:
        text += "\n<b>⚠️ هاست شما به زودی منقضی میشود! لطفا تمدید کنید.</b>"
    else:
        text += "\n<b>✅ هاست شما فعال است.</b>"
    keyboard = [
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin_host")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
    except:
        pass

async def show_user_menu(update, context, query):
    user_id = query.from_user.id
    user_mention = f"@{query.from_user.username}" if query.from_user.username else query.from_user.first_name
    remaining_days = get_remaining_days(user_id)
    has_subscription = has_active_subscription(user_id)
    is_verified = is_user_verified(user_id)
    
    text = (
        f"<b>⚡ سلام {user_mention} به ربات ریپر سلف خوش آمدید!</b>\n\n"
        "<b>🎯 در این ربات میتوانید از پشتیبانی، خرید، نصب ربات سلف بهره ببرید!</b>\n\n"
        "<b>💠 اگر سوالی دارید از بخش پشتیبانی استفاده کنید.</b>"
    )
    
    keyboard = []
    keyboard.append([InlineKeyboardButton("👨‍💻 پشتیبانی", callback_data="support")])
    keyboard.append([InlineKeyboardButton("🤔 سلف چیست؟", callback_data="what_is_self"), InlineKeyboardButton("📣 کانال ما", url="https://t.me/ReaperSelfChannel")])
    keyboard.append([InlineKeyboardButton(f"📅 انقضا شما: ({remaining_days} روز)", callback_data="expiry")])
    
    if is_verified:
        keyboard.append([InlineKeyboardButton("✅ احراز هویت شده", callback_data="verified_already")])
    else:
        keyboard.append([InlineKeyboardButton("✔️ احراز هویت", callback_data="verify")])
    
    keyboard.append([InlineKeyboardButton("💳 خرید اشتراک", callback_data="buy_subscription")])
    keyboard.append([InlineKeyboardButton("💶 خرید با کد", callback_data="buy_with_code")])
    
    if has_subscription:
        keyboard.append([InlineKeyboardButton("🔑 ورود سلف", callback_data="salf_login")])
    
    keyboard.append([InlineKeyboardButton("💎 نرخ", callback_data="rate")])
    
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("🎈 پنل مدیریت", callback_data="admin_back")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_menu_mode[user_id] = True
    await show_user_menu(update, context, query)

async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_mention = f"@{query.from_user.username}" if query.from_user.username else query.from_user.first_name
    text = (
        f"<b>⚡ درود {user_mention} به بخش تنظیمات پنل مدیریت خوش آمدید.</b>\n\n"
        "<b>🛠️ در این بخش میتوانید تمامی تنظیمات و مدیریت ربات را انجام دهید.</b>\n\n"
        "<b>🔄 لطفا از منوی زیر یکی از گزینه‌های مورد نظر خود را انتخاب نمایید.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("➕ ساختن کد سلف", callback_data="admin_create_code"), InlineKeyboardButton("❌ باطل کردن کد سلف", callback_data="admin_cancel_code")],
        [InlineKeyboardButton("🚫 مسدود کردن کاربر", callback_data="admin_block_user"), InlineKeyboardButton("✅ آزاد کردن کاربر", callback_data="admin_unblock_user")],
        [InlineKeyboardButton("📤 انتقال انقضا", callback_data="admin_transfer_credit"), InlineKeyboardButton("📉 کسر انقضا", callback_data="admin_deduct_credit")],
        [InlineKeyboardButton("🔑 ورود سلف", callback_data="admin_salf_login"), InlineKeyboardButton("🚪 خروج سلف", callback_data="admin_salf_logout")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_settings_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_mention = f"@{query.from_user.username}" if query.from_user.username else query.from_user.first_name
    text = (
        f"<b>⚡ درود {user_mention} به بخش تنظیمات پنل مدیریت خوش آمدید.</b>\n\n"
        "<b>🛠️ در این بخش میتوانید تمامی تنظیمات و مدیریت ربات را انجام دهید.</b>\n\n"
        "<b>🔄 لطفا از منوی زیر یکی از گزینه‌های مورد نظر خود را انتخاب نمایید.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("➕ ساختن کد سلف", callback_data="admin_create_code"), InlineKeyboardButton("❌ باطل کردن کد سلف", callback_data="admin_cancel_code")],
        [InlineKeyboardButton("🚫 مسدود کردن کاربر", callback_data="admin_block_user"), InlineKeyboardButton("✅ آزاد کردن کاربر", callback_data="admin_unblock_user")],
        [InlineKeyboardButton("📤 انتقال انقضا", callback_data="admin_transfer_credit"), InlineKeyboardButton("📉 کسر انقضا", callback_data="admin_deduct_credit")],
        [InlineKeyboardButton("🔑 ورود سلف", callback_data="admin_salf_login"), InlineKeyboardButton("🚪 خروج سلف", callback_data="admin_salf_logout")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id in user_states:
        del user_states[user_id]
    if user_id in admin_salf_data:
        del admin_salf_data[user_id]
    
    user_mention = f"@{query.from_user.username}" if query.from_user.username else query.from_user.first_name
    
    text = (
        f"<b>⚡ درود {user_mention} به پنل ریپر سلف خوش آمدید.</b>\n\n"
        "<b>🛠️ در این پنل میتوانید ربات را کنترل و مدیریت کنید.</b>\n\n"
        "<b>🔄 لطفا از منوی زیر انتخاب نمایید.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings")],
        [InlineKeyboardButton("📊 آمار کل", callback_data="admin_stats")],
        [InlineKeyboardButton("📡 بررسی پینگ", callback_data="admin_ping"), InlineKeyboardButton("⏳ اعتبار هاست", callback_data="admin_host")],
        [InlineKeyboardButton("👥 منوی کاربران", callback_data="admin_users_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

# ==================== USER FUNCTIONS ====================

async def rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "<b>⚡ نرخ سلف عبارت است از:</b>\n\n"
        "<b>💰 ماهانه: 100,000 هزار تومان</b>\n\n"
        "<b>💰 دو ماهه: 150,000 هزار تومان</b>\n\n"
        "<b>💰 سه ماهه: 200,000 هزار تومان</b>\n\n"
        "<b>💰 چهار ماهه: 250,000 هزار تومان</b>\n\n"
        "<b>💰 پنج ماهه: 300,000 هزار تومان</b>\n\n"
        "<b>💰 شش ماهه: 350,000 هزار تومان</b>\n\n"
        "<b>⚠️ سلف فقط بر روی اکانت‌هایی که با شماره ایران هستند نصب میشود.</b>\n\n"
        "<b>📍 @ReaperSelfChannel</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def what_is_self(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "<b>🤖 سلف به رباتی گفته میشود که روی اکانت شما نصب میشود و امکانات خاصی را در اختیار شما میگذارد.</b>\n\n"
        "<b>✨ از جمله امکانات:</b>\n"
        "<b>⏰ گذاشتن ساعت با فونت‌های مختلف روی بیو و اسم</b>\n"
        "<b>👁️ قابلیت تنظیم حالت خوانده شدن خودکار پیام‌ها</b>\n"
        "<b>🤖 تنظیم حالت پاسخ خودکار</b>\n"
        "<b>💬 جواب دادن به شخصی که به شما توهین میکند</b>\n"
        "<b>🎬 پیام انیمیشنی</b>\n"
        "<b>🧠 منشی هوشمند</b>\n"
        "<b>📱 دریافت پنل و تنظیمات اکانت هوشمند</b>\n"
        "<b>🌍 دو زبانه بودن دستورات و جواب‌ها</b>\n"
        "<b>📁 تغییر نام و کاور فایل‌ها</b>\n"
        "<b>🔔 اعلان پیام ادیت و حذف شده در پیوی</b>\n"
        "<b>👤 ذخیره پروفایل‌های جدید و اعلان حذف پروفایل مخاطبین</b>\n\n"
        "<b>📍 @ReaperSelfChannel</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def buy_with_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "waiting_for_activation_code"
    text = "<b>💶 لطفا کد انقضای خریداری شده خود را ارسال کنید.</b>"
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def handle_activation_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_activation_code":
        return
    code = update.message.text.strip().upper() if update.message.text else ""
    if not code:
        await update.message.reply_text("<b>❌ لطفا کد را وارد کنید!</b>", parse_mode='HTML')
        return
    code_data, error = validate_code(code)
    if code_data is None:
        await update.message.reply_text(f"<b>{error}</b>", parse_mode='HTML')
        return
    if use_code(code, user_id):
        days = code_data['days']
        remaining_days = get_remaining_days(user_id)
        expiry_date = get_expiry_date(user_id)
        text = (
            f"<b>✅ کد با موفقیت فعال شد!</b>\n\n"
            f"<b>📅 {days} روز به اشتراک شما اضافه شد.</b>\n"
            f"<b>📅 تاریخ انقضا: {expiry_date}</b>\n"
            f"<b>⏳ روزهای باقی‌مانده: {remaining_days} روز</b>\n\n"
            "<b>🔑 اکنون میتوانید از دکمه ورود سلف استفاده کنید.</b>"
        )
        keyboard = [
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        del user_states[user_id]
    else:
        await update.message.reply_text("<b>❌ خطا در فعال‌سازی کد!</b>", parse_mode='HTML')

async def buy_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if not is_user_verified(user_id):
        text = "<b>⚠️ برای خرید اشتراک سلف، ابتدا باید احراز هویت کنید.</b>"
        keyboard = [
            [InlineKeyboardButton("✔️ احراز هویت", callback_data="verify")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
        return
    
    text = "<b>💰 لطفا از گزینه‌های زیر انتخاب نمایید که میخواهید ریپر سلف را برای چند ماه خریداری کنید.</b>"
    keyboard = [
        [InlineKeyboardButton("📆 (1) ماه معادل 100 هزار (تومان)", callback_data="buy_1_month")],
        [InlineKeyboardButton("📆 (2) ماه معادل 150 هزار (تومان)", callback_data="buy_2_month")],
        [InlineKeyboardButton("📆 (3) ماه معادل 200 هزار (تومان)", callback_data="buy_3_month")],
        [InlineKeyboardButton("📆 (4) ماه معادل 250 هزار (تومان)", callback_data="buy_4_month")],
        [InlineKeyboardButton("📆 (5) ماه معادل 300 هزار (تومان)", callback_data="buy_5_month")],
        [InlineKeyboardButton("📆 (6) ماه معادل 350 هزار (تومان)", callback_data="buy_6_month")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if is_user_verified(user_id):
        await query.answer("✅ شما قبلاً احراز هویت شده اید!", show_alert=True)
        return
    
    text = (
        "<b>🔐 به منوی احراز هویت خوش آمدید.</b>\n\n"
        "<b>🔄 لطفا یکی از گزینه‌های زیر را انتخاب نمایید:</b>"
    )
    keyboard = [
        [InlineKeyboardButton("❌ حذف کارت", callback_data="delete_card")],
        [InlineKeyboardButton("➕ کارت جدید", callback_data="new_card")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def verified_already(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer("✅ شما قبلاً احراز هویت شده اید!", show_alert=True)

async def delete_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer("❌ کارت شما با موفقیت حذف شد!", show_alert=True)

async def new_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "waiting_for_verify_photo"
    text = (
        "<b>🔐 به بخش احراز هویت خوش آمدید.</b>\n\n"
        "<b>⚠️ نکات مهم:</b>\n"
        "<b>1️⃣ شماره کارت و نام صاحب کارت باید کاملا مشخص و خوانا باشد.</b>\n"
        "<b>2️⃣ لطفا تاریخ اعتبار و Cvv2 کارت خود را بپوشانید.</b>\n"
        "<b>3️⃣ فقط با کارتی که احراز هویت میکنید میتوانید خرید انجام دهید.</b>\n"
        "<b>4️⃣ در صورتی که توانایی ارسال عکس از کارت را ندارید، تنها راه حل ارسال عکس از کارت ملی یا شناسنامه صاحب کارت است.</b>\n\n"
        "<b>📸 لطفا عکس از کارتی که میخواهید با آن خرید انجام دهید ارسال کنید.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_verify")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def handle_verify_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_verify_photo":
        return
    if not update.message.photo:
        await update.message.reply_text("<b>❌ لطفا فقط عکس ارسال کنید!</b>", parse_mode='HTML')
        return
    
    photo = update.message.photo[-1]
    pending_verify[user_id] = {'photo_id': photo.file_id}
    user_states[user_id] = "waiting_for_card_number"
    
    await update.message.reply_text(
        "<b>✅ عکس شما با موفقیت دریافت شد.</b>\n"
        "<b>🔢 لطفا شماره کارت خود را به صورت اعداد انگلیسی وارد کنید.</b>",
        parse_mode='HTML'
    )

async def handle_verify_card_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_card_number":
        return
    
    card_number = update.message.text.strip()
    card_number = re.sub(r'[^0-9]', '', card_number)
    
    if len(card_number) != 16:
        await update.message.reply_text(
            "<b>❌ شماره کارت باید 16 رقم باشد.</b>\n"
            "<b>🔢 لطفا شماره کارت خود را بدون فاصله و کاراکتر اضافی وارد کنید.</b>",
            parse_mode='HTML'
        )
        return
    
    user = update.effective_user
    user_mention = f"@{user.username}" if user.username else user.first_name
    user_id_str = str(user_id)
    
    photo_id = pending_verify.get(user_id, {}).get('photo_id')
    
    request_id = db_add_verify_request(user_id, user_mention, card_number, photo_id)
    
    iran_tz = pytz.timezone('Asia/Tehran')
    iran_time = datetime.now(iran_tz)
    time_str = iran_time.strftime('%H:%M')
    date_str = iran_time.strftime('%Y-%m-%d')
    
    for admin_id in ADMIN_IDS:
        try:
            admin_text = (
                f"<b>🆔 درخواست جدید احراز هویت</b>\n\n"
                f"<b>🆔 شماره درخواست: {request_id}</b>\n"
                f"<b>👤 نام کاربر: {user_mention}</b>\n"
                f"<b>🆔 آیدی عددی: {user_id_str}</b>\n"
                f"<b>💳 شماره کارت: <code>{card_number}</code></b>\n\n"
                f"<b>🕐 ساعت: {time_str}</b>\n"
                f"<b>📅 تاریخ: {date_str}</b>"
            )
            
            keyboard = [
                [InlineKeyboardButton("✅ پذیرفتن", callback_data=f"accept_verify_{request_id}")],
                [InlineKeyboardButton("❌ نپذیرفتن", callback_data=f"reject_verify_{request_id}")],
                [InlineKeyboardButton("🚫 مسدود کردن کاربر", callback_data=f"block_{user_id}")],
                [InlineKeyboardButton("💬 پاسخ به کاربر", callback_data=f"reply_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if photo_id:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo_id,
                    caption=admin_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
        except Exception as e:
            print(f"خطا در ارسال به ادمین: {e}")
    
    await update.message.reply_text(
        "<b>✅ درخواست احراز هویت شما با موفقیت به تیم پشتیبانی ارسال شد.</b>\n"
        "<b>⏳ لطفا صبور باشید و منتظر تایید از سوی تیم پشتیبانی بمانید.</b>\n"
        "<b>⚠️ از ارسال درخواست‌های تکراری خودداری فرمایید.</b>",
        parse_mode='HTML'
    )
    
    if user_id in pending_verify:
        del pending_verify[user_id]
    if user_id in user_states:
        del user_states[user_id]

async def accept_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    request_id = int(query.data.split("_")[2])
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username FROM verify_requests WHERE id = ?', (request_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        user_id = result[0]
        username = result[1]
        if not is_user_verified(user_id):
            verify_user(user_id)
            db_update_verify_request(request_id, 'accepted', 'تایید شد')
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="<b>✅ درخواست احراز هویت شما با موفقیت توسط مدیریت پذیرفته شد.</b>\n\n<b>🎉 تبریک! شما اکنون احراز هویت شده اید.</b>",
                    parse_mode='HTML'
                )
            except:
                pass
            await query.edit_message_text(
                f"<b>✅ درخواست احراز هویت کاربر {username} با موفقیت پذیرفته شد.</b>\n"
                f"<b>🎉 کاربر احراز هویت شد.</b>\n\n"
                f"<b>🔙 برای بازگشت کلیک کنید:</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_back")]]),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                f"<b>⚠️ کاربر قبلاً احراز هویت شده است!</b>",
                parse_mode='HTML'
            )
    else:
        await query.edit_message_text("<b>❌ درخواست یافت نشد!</b>", parse_mode='HTML')

async def reject_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    request_id = int(query.data.split("_")[2])
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username FROM verify_requests WHERE id = ?', (request_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        user_id = result[0]
        username = result[1]
        db_update_verify_request(request_id, 'rejected', 'رد شد')
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="<b>❌ درخواست احراز هویت شما توسط مدیریت پذیرفته نشد.</b>\n\n<b>🔄 لطفا دوباره تلاش کنید و اطلاعات صحیح را ارسال نمایید.</b>",
                parse_mode='HTML'
            )
        except:
            pass
        await query.edit_message_text(
            f"<b>❌ درخواست احراز هویت کاربر {username} با موفقیت رد شد.</b>\n"
            f"<b>⛔ کاربر تایید نشد.</b>\n\n"
            f"<b>🔙 برای بازگشت کلیک کنید:</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_back")]]),
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text("<b>❌ درخواست یافت نشد!</b>", parse_mode='HTML')

async def back_to_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id in user_states:
        del user_states[query.from_user.id]
    await verify(update, context)

async def back_from_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await show_user_menu(update, context, query)

# ==================== BLOCK/UNBLOCK ====================

async def admin_block_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "waiting_for_block_user"
    text = (
        "<b>🚫 مسدود کردن کاربر</b>\n\n"
        "<b>🔢 لطفا آیدی عددی کاربر مورد نظر برای مسدود سازی را وارد کنید.</b>\n"
        "<b>⚠️ پس از مسدود شدن، کاربر قادر به استفاده از ربات نخواهد بود.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "waiting_for_unblock_user"
    text = (
        "<b>✅ آزاد کردن کاربر</b>\n\n"
        "<b>🔢 لطفا آیدی عددی کاربر مورد نظر برای آزاد سازی از مسدودیت را وارد کنید.</b>\n"
        "<b>⚠️ پس از آزاد سازی، کاربر دوباره میتواند از ربات استفاده کند.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def handle_block_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_block_user":
        return
    target_id = update.message.text.strip()
    try:
        target_id_int = int(target_id)
    except:
        await update.message.reply_text("<b>❌ آیدی وارد شده صحیح نیست! لطفا یک عدد معتبر وارد کنید.</b>", parse_mode='HTML')
        return
    if target_id_int in ADMIN_IDS:
        await update.message.reply_text("<b>❌ شما نمی‌توانید یک ادمین را مسدود کنید!</b>", parse_mode='HTML')
        return
    if not is_user_banned(target_id_int):
        ban_user(target_id_int)
        try:
            await context.bot.send_message(
                chat_id=target_id_int,
                text="<b>🚫 شما از طرف مدیریت مسدود شده اید!</b>\n<b>💠 در صورت نیاز با پشتیبانی تماس بگیرید.</b>",
                parse_mode='HTML'
            )
        except:
            pass
        await update.message.reply_text(
            f"<b>✅ کاربر با آیدی {target_id_int} با موفقیت مسدود شد.</b>\n"
            "<b>🔒 کاربر دیگر نمیتواند از ربات استفاده کند.</b>",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            f"<b>⚠️ کاربر با آیدی {target_id_int} قبلاً در لیست مسدودین قرار دارد.</b>",
            parse_mode='HTML'
        )
    del user_states[user_id]

async def handle_unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_unblock_user":
        return
    target_id = update.message.text.strip()
    try:
        target_id_int = int(target_id)
    except:
        await update.message.reply_text("<b>❌ آیدی وارد شده صحیح نیست! لطفا یک عدد معتبر وارد کنید.</b>", parse_mode='HTML')
        return
    if is_user_banned(target_id_int):
        unban_user(target_id_int)
        try:
            await context.bot.send_message(
                chat_id=target_id_int,
                text="<b>✅ تبریک! شما از طرف مدیریت از مسدودیت آزاد شدید.</b>\n<b>💠 ضمن پوزش از شما، خوشحالیم که دوباره به جمع ما برگشتید.</b>",
                parse_mode='HTML'
            )
        except:
            pass
        await update.message.reply_text(
            f"<b>✅ کاربر با آیدی {target_id_int} با موفقیت از مسدودیت آزاد شد.</b>\n"
            "<b>🔓 کاربر دوباره میتواند از ربات استفاده کند.</b>",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            f"<b>⚠️ کاربر با آیدی {target_id_int} در لیست مسدودین وجود ندارد.</b>",
            parse_mode='HTML'
        )
    del user_states[user_id]

# ==================== CODE MANAGEMENT ====================

async def admin_create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "waiting_for_code_days"
    text = (
        "<b>➕ ساختن کد سلف جدید</b>\n\n"
        "<b>🔢 لطفا تعداد روز انقضا را به صورت عدد وارد کنید.</b>\n"
        "<b>⚠️ عدد وارد شده باید بین 1 تا 100000 باشد.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_cancel_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "waiting_for_cancel_code"
    text = (
        "<b>❌ باطل کردن کد سلف</b>\n\n"
        "<b>🔢 لطفا کد سلف مورد نظر برای باطل شدن را وارد کنید.</b>\n"
        "<b>⚠️ کدهایی که قبلاً استفاده شده اند قابل باطل کردن نیستند.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def handle_code_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_code_days":
        return
    try:
        days = int(update.message.text.strip())
        if days < 1 or days > 100000:
            await update.message.reply_text("<b>❌ عدد باید بین 1 تا 100000 باشد!</b>", parse_mode='HTML')
            return
        new_code, expiry_date = create_new_code(days)
        text = (
            "<b>✅ کد سلف شما با موفقیت ساخته شد</b>\n\n"
            f"<b>📝 کد سلف: <code>{new_code}</code></b>\n\n"
            f"<b>📅 تاریخ انقضا: {expiry_date.strftime('%Y-%m-%d')}</b>\n"
            f"<b>⏱️ مدت اعتبار: {days} روز</b>\n\n"
            "<b>💡 برای کپی کردن روی کد کلیک کنید.</b>"
        )
        keyboard = [
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        del user_states[user_id]
    except ValueError:
        await update.message.reply_text("<b>❌ لطفا یک عدد معتبر وارد کنید!</b>", parse_mode='HTML')

async def handle_cancel_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_cancel_code":
        return
    code = update.message.text.strip().upper()
    existing = db_get_code(code)
    if not existing:
        await update.message.reply_text("<b>❌ کد وارد شده صحیح نیست!</b>", parse_mode='HTML')
        return
    if existing[4] == 1:
        await update.message.reply_text("<b>❌ این کد قبلاً استفاده شده و قابل باطل کردن نیست!</b>", parse_mode='HTML')
    else:
        db_delete_code(code)
        await update.message.reply_text(f"<b>✅ کد <code>{code}</code> با موفقیت باطل شد!</b>", parse_mode='HTML')
    del user_states[user_id]

# ==================== CREDIT MANAGEMENT ====================

async def admin_transfer_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "waiting_for_transfer_credit"
    text = (
        "<b>📤 انتقال انقضا</b>\n\n"
        "<b>🔢 لطفا آیدی عددی کاربر مبدا، آیدی عددی کاربر مقصد و مقدار روز را وارد کنید.</b>\n"
        "<b>⚠️ این عملیات غیرقابل بازگشت است.</b>\n"
        "<b>📝 مثال: 123456789 987654321 30</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_deduct_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "waiting_for_deduct_credit"
    text = (
        "<b>📉 کسر انقضا</b>\n\n"
        "<b>🔢 لطفا آیدی عددی کاربر و مقدار روز مورد نظر برای کسر را وارد کنید.</b>\n"
        "<b>⚠️ این عملیات غیرقابل بازگشت است.</b>\n"
        "<b>📝 مثال: 123456789 10</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def handle_transfer_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_transfer_credit":
        return
    parts = update.message.text.strip().split()
    if len(parts) != 3:
        await update.message.reply_text(
            "<b>❌ فرمت وارد شده صحیح نیست!</b>\n"
            "<b>📝 لطفا به این صورت وارد کنید: آیدی_مبدا آیدی_مقصد تعداد_روز</b>",
            parse_mode='HTML'
        )
        return
    try:
        from_id = int(parts[0])
        to_id = int(parts[1])
        days = int(parts[2])
    except:
        await update.message.reply_text("<b>❌ مقادیر وارد شده صحیح نیست! لطفا اعداد معتبر وارد کنید.</b>", parse_mode='HTML')
        return
    if days <= 0:
        await update.message.reply_text("<b>❌ تعداد روز باید بیشتر از صفر باشد.</b>", parse_mode='HTML')
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT remaining_days, expiry_date FROM users WHERE user_id = ?', (from_id,))
    from_data = cursor.fetchone()
    if not from_data or from_data[0] is None or from_data[0] < days:
        conn.close()
        await update.message.reply_text(
            f"<b>⚠️ کاربر با آیدی {from_id} به اندازه {days} روز اشتراک فعال ندارد!</b>",
            parse_mode='HTML'
        )
        return
    new_from_days = from_data[0] - days
    if new_from_days == 0:
        new_from_expiry = None
    else:
        new_from_expiry = (datetime.now() + timedelta(days=new_from_days)).isoformat()
    cursor.execute('UPDATE users SET remaining_days = ?, expiry_date = ? WHERE user_id = ?', 
                   (new_from_days, new_from_expiry, from_id))
    cursor.execute('SELECT remaining_days, expiry_date FROM users WHERE user_id = ?', (to_id,))
    to_data = cursor.fetchone()
    if to_data and to_data[0] is not None:
        new_to_days = to_data[0] + days
        new_to_expiry = (datetime.now() + timedelta(days=new_to_days)).isoformat()
        cursor.execute('UPDATE users SET remaining_days = ?, expiry_date = ? WHERE user_id = ?',
                       (new_to_days, new_to_expiry, to_id))
    else:
        new_to_days = days
        new_to_expiry = (datetime.now() + timedelta(days=days)).isoformat()
        cursor.execute('INSERT INTO users (user_id, remaining_days, expiry_date) VALUES (?, ?, ?)',
                       (to_id, new_to_days, new_to_expiry))
    conn.commit()
    conn.close()
    try:
        await context.bot.send_message(
            chat_id=from_id,
            text=f"<b>📤 از طرف مدیریت، {days} روز از اشتراک شما کسر شد.</b>\n<b>📅 انقضای جدید: {new_from_expiry.split('T')[0] if new_from_expiry else 'اشتراک شما به پایان رسید'}</b>",
            parse_mode='HTML'
        )
    except:
        pass
    try:
        await context.bot.send_message(
            chat_id=to_id,
            text=f"<b>📤 از طرف مدیریت، {days} روز به اشتراک شما اضافه شد.</b>\n<b>📅 انقضای جدید: {new_to_expiry.split('T')[0]}</b>",
            parse_mode='HTML'
        )
    except:
        pass
    await update.message.reply_text(
        f"<b>✅ انتقال {days} روز از کاربر {from_id} به کاربر {to_id} با موفقیت انجام شد.</b>",
        parse_mode='HTML'
    )
    del user_states[user_id]

async def handle_deduct_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_for_deduct_credit":
        return
    parts = update.message.text.strip().split()
    if len(parts) != 2:
        await update.message.reply_text(
            "<b>❌ فرمت وارد شده صحیح نیست!</b>\n"
            "<b>📝 لطفا به این صورت وارد کنید: آیدی_کاربر تعداد_روز</b>",
            parse_mode='HTML'
        )
        return
    try:
        target_id = int(parts[0])
        days = int(parts[1])
    except:
        await update.message.reply_text("<b>❌ مقادیر وارد شده صحیح نیست! لطفا اعداد معتبر وارد کنید.</b>", parse_mode='HTML')
        return
    if days <= 0:
        await update.message.reply_text("<b>❌ تعداد روز باید بیشتر از صفر باشد.</b>", parse_mode='HTML')
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT remaining_days, expiry_date FROM users WHERE user_id = ?', (target_id,))
    target_data = cursor.fetchone()
    if not target_data or target_data[0] is None or target_data[0] == 0:
        conn.close()
        await update.message.reply_text(
            f"<b>⚠️ کاربر با آیدی {target_id} اشتراک فعالی ندارد!</b>",
            parse_mode='HTML'
        )
        return
    new_days = max(0, target_data[0] - days)
    if new_days == 0:
        new_expiry = None
    else:
        new_expiry = (datetime.now() + timedelta(days=new_days)).isoformat()
    cursor.execute('UPDATE users SET remaining_days = ?, expiry_date = ? WHERE user_id = ?',
                   (new_days, new_expiry, target_id))
    conn.commit()
    conn.close()
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"<b>📉 از طرف مدیریت، {days} روز از اشتراک شما کسر شد.</b>\n<b>📅 انقضای جدید: {new_expiry.split('T')[0] if new_expiry else 'اشتراک شما به پایان رسید'}</b>",
            parse_mode='HTML'
        )
    except:
        pass
    await update.message.reply_text(
        f"<b>✅ {days} روز از اشتراک کاربر {target_id} با موفقیت کسر شد.</b>",
        parse_mode='HTML'
    )
    del user_states[user_id]

# ==================== ADMIN SELF LOGIN ====================

async def admin_salf_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    admin_salf_data[query.from_user.id] = {}
    user_states[query.from_user.id] = "admin_waiting_phone"
    text = (
        "<b>🔑 ورود سلف (مدیریت)</b>\n\n"
        "<b>📱 لطفا شماره موبایل کاربر را با کد کشور وارد کنید.</b>\n"
        "<b>📝 مثال: +989123456789</b>\n\n"
        "<b>⚠️ این بخش مخصوص ورود سلف به اکانت کاربران دیگر توسط مدیریت است.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_salf_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "admin_waiting_logout_phone"
    text = (
        "<b>🚪 خروج سلف</b>\n\n"
        "<b>📱 لطفا شماره تلفن مورد نظر برای خروج سلف را وارد کنید.</b>\n"
        "<b>⚠️ پس از خروج، سلف از اکانت کاربر خارج خواهد شد و ساعت از اسم او حذف میشود.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def admin_handle_salf_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "admin_waiting_phone":
        return
    phone = update.message.text.strip()
    if not phone or not re.match(r'^\+?[0-9]{10,15}$', phone):
        await update.message.reply_text(
            "<b>❌ شماره وارد شده صحیح نیست! لطفا با کد کشور وارد کنید.</b>\n"
            "<b>📝 مثال: +989123456789</b>",
            parse_mode='HTML'
        )
        return
    admin_salf_data[user_id]['phone'] = phone
    user_states[user_id] = "admin_waiting_user_id"
    text = "<b>🔑 مرحله 2 از 5</b>\n\n<b>🔢 لطفا آیدی عددی کاربر مورد نظر را وارد کنید.</b>"
    await update.message.reply_text(text, parse_mode='HTML')

async def admin_handle_salf_logout_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "admin_waiting_logout_phone":
        return
    phone = update.message.text.strip()
    if not phone or not re.match(r'^\+?[0-9]{10,15}$', phone):
        await update.message.reply_text(
            "<b>❌ شماره وارد شده صحیح نیست! لطفا با کد کشور وارد کنید.</b>\n"
            "<b>📝 مثال: +989123456789</b>",
            parse_mode='HTML'
        )
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, api_hash, api_id FROM sessions WHERE phone = ?', (phone,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        await update.message.reply_text("<b>❌ هیچ کاربری با این شماره در سلف یافت نشد!</b>", parse_mode='HTML')
        del user_states[user_id]
        return
    target_user_id = result[0]
    api_hash = result[1]
    api_id = result[2]
    try:
        client = TelegramClient(
            f"sessions/user_{target_user_id}",
            api_id,
            api_hash
        )
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            first_name = me.first_name if me.first_name else ""
            last_name = me.last_name if me.last_name else ""
            current_name = f"{first_name} {last_name}".strip()
            if not current_name:
                current_name = me.username if me.username else "کاربر"
            clean_name = re.sub(r'\s*\d{2}:\d{2}$', '', current_name).strip()
            if clean_name != current_name:
                try:
                    await client(UpdateProfileRequest(first_name=clean_name))
                except:
                    pass
        await client.disconnect()
    except:
        pass
    delete_user_session(target_user_id)
    set_clock_status(target_user_id, False)
    if target_user_id in salf_clients:
        try:
            await salf_clients[target_user_id].disconnect()
        except:
            pass
        del salf_clients[target_user_id]
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text="<b>🚪 ریپر سلف از اکانت شما خارج شد.</b>\n\n<b>⏰ ساعت از روی اسم شما حذف شد.</b>\n<b>🔑 در صورت نیاز مجدداً وارد شوید.</b>",
            parse_mode='HTML'
        )
    except:
        pass
    await update.message.reply_text(
        f"<b>✅ خروج سلف از اکانت کاربر {target_user_id} با موفقیت انجام شد.</b>\n"
        f"<b>📱 شماره: {phone}</b>\n"
        "<b>⏰ ساعت از اسم کاربر حذف شد.</b>",
        parse_mode='HTML'
    )
    del user_states[user_id]

async def admin_handle_salf_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "admin_waiting_user_id":
        return
    target_id = update.message.text.strip()
    try:
        target_id_int = int(target_id)
    except:
        await update.message.reply_text("<b>❌ آیدی وارد شده صحیح نیست! لطفا یک عدد معتبر وارد کنید.</b>", parse_mode='HTML')
        return
    admin_salf_data[user_id]['target_user_id'] = target_id_int
    user_states[user_id] = "admin_waiting_api_id"
    text = (
        "<b>🔑 مرحله 3 از 5</b>\n\n"
        "<b>🔢 لطفا آیپی عددی (API ID) را وارد کنید.</b>\n"
        "<b>⚠️ API ID باید عددی بین 1 تا 2147483647 باشد.</b>"
    )
    await update.message.reply_text(text, parse_mode='HTML')

async def admin_handle_salf_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "admin_waiting_api_id":
        return
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("<b>❌ لطفا یک عدد وارد کنید!</b>", parse_mode='HTML')
        return
    try:
        api_id = int(text)
        if api_id > 2147483647:
            await update.message.reply_text(
                "<b>❌ عدد وارد شده خیلی بزرگ است!</b>\n"
                "<b>⚠️ API ID باید عددی بین 1 تا 2147483647 باشد.</b>",
                parse_mode='HTML'
            )
            return
    except:
        await update.message.reply_text("<b>❌ آیپی عددی باید عدد باشد!</b>", parse_mode='HTML')
        return
    admin_salf_data[user_id]['api_id'] = api_id
    user_states[user_id] = "admin_waiting_api_hash"
    text = "<b>🔑 مرحله 4 از 5</b>\n\n<b>🔑 لطفا آیپی هش (API Hash) را وارد کنید.</b>"
    await update.message.reply_text(text, parse_mode='HTML')

async def admin_handle_salf_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "admin_waiting_api_hash":
        return
    api_hash = update.message.text.strip()
    if not api_hash or len(api_hash) < 20:
        await update.message.reply_text(
            "<b>❌ آیپی هش وارد شده صحیح نیست! لطفا دوباره وارد کنید.</b>",
            parse_mode='HTML'
        )
        return
    admin_salf_data[user_id]['api_hash'] = api_hash
    user_states[user_id] = "admin_waiting_code"
    try:
        data = admin_salf_data[user_id]
        session_path = f"sessions/admin_{user_id}"
        client = TelegramClient(session_path, data['api_id'], data['api_hash'])
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(data['phone'])
            admin_salf_data[user_id]['client'] = client
            text = (
                "<b>🔑 مرحله 5 از 5</b>\n\n"
                f"<b>✅ کد تایید به شماره {data['phone']} ارسال شد.</b>\n"
                "<b>📝 لطفا کد را به این صورت بفرستید: <code>1.2.3.4.5</code></b>"
            )
            await update.message.reply_text(text, parse_mode='HTML')
        else:
            await client.disconnect()
            await update.message.reply_text("<b>❌ این شماره قبلاً در سلف ثبت شده است!</b>", parse_mode='HTML')
            del user_states[user_id]
            del admin_salf_data[user_id]
    except PhoneNumberInvalidError:
        await update.message.reply_text("<b>❌ شماره وارد شده معتبر نیست!</b>", parse_mode='HTML')
        del user_states[user_id]
        del admin_salf_data[user_id]
    except Exception as e:
        await update.message.reply_text(f"<b>❌ خطا در ارسال کد تایید: {str(e)}</b>", parse_mode='HTML')
        del user_states[user_id]
        del admin_salf_data[user_id]

async def admin_handle_salf_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "admin_waiting_code":
        return
    code_input = update.message.text.strip()
    code = code_input.replace('.', '').replace(' ', '').strip()
    if not code or not code.isdigit():
        await update.message.reply_text(
            "<b>❌ لطفا کد را به این صورت بفرستید: <code>1.2.3.4.5</code></b>",
            parse_mode='HTML'
        )
        return
    try:
        data = admin_salf_data[user_id]
        client = data.get('client')
        if not client:
            await update.message.reply_text("<b>❌ خطا در اتصال! لطفا دوباره تلاش کنید.</b>", parse_mode='HTML')
            del user_states[user_id]
            del admin_salf_data[user_id]
            return
        try:
            await client.sign_in(data['phone'], code)
            me = await client.get_me()
            first_name = me.first_name if me.first_name else ""
            last_name = me.last_name if me.last_name else ""
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                full_name = me.username if me.username else "کاربر"
            iran_tz = pytz.timezone('Asia/Tehran')
            iran_time = datetime.now(iran_tz)
            time_str = iran_time.strftime('%H:%M')
            session_string = client.session.save()
            save_user_session(data['target_user_id'], session_string, data['phone'], data['api_hash'], data['api_id'])
            await client.disconnect()
            set_clock_status(data['target_user_id'], True)
            asyncio.create_task(start_salf_client(data['target_user_id']))
            text = (
                "<b>✅ ورود سلف به اکانت کاربر با موفقیت انجام شد!</b>\n\n"
                f"<b>👤 نام اکانت: {full_name}</b>\n"
                f"<b>📱 شماره: {data['phone']}</b>\n"
                f"<b>🕐 ساعت ورود: {time_str}</b>\n"
                f"<b>📅 تاریخ ورود: {iran_time.strftime('%Y-%m-%d')}</b>\n"
                f"<b>🆔 آیدی کاربر: {data['target_user_id']}</b>\n\n"
                "<b>⏰ ساعت روی اسم اکانت کاربر فعال شد!</b>\n"
                "<b>✅ پنل سلف فعال شد (با نوشتن کلمه \"پنل\" در هر جایی)</b>"
            )
            keyboard = [
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
            del user_states[user_id]
            del admin_salf_data[user_id]
            return
        except PhoneCodeExpiredError:
            await update.message.reply_text("<b>⏳ کد منقضی شده بود، در حال ارسال کد جدید...</b>", parse_mode='HTML')
            await client.send_code_request(data['phone'])
            await update.message.reply_text(
                "<b>✅ کد جدید به شماره شما ارسال شد.</b>\n"
                "<b>📝 لطفا کد را به این صورت بفرستید: <code>1.2.3.4.5</code></b>",
                parse_mode='HTML'
            )
            return
        except PhoneCodeInvalidError:
            await update.message.reply_text(
                "<b>❌ کد وارد شده صحیح نیست! لطفا دوباره تلاش کنید.</b>\n"
                "<b>📝 لطفا کد را به این صورت بفرستید: <code>1.2.3.4.5</code></b>",
                parse_mode='HTML'
            )
            return
    except SessionPasswordNeededError:
        user_states[user_id] = "admin_waiting_password"
        await update.message.reply_text(
            "<b>⚠️ اکانت کاربر دارای سیستم تایید دو مرحله‌ای (2FA) است.</b>\n"
            "<b>🗝 لطفاً رمز عبور اختصاصی را وارد کنید:</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        await update.message.reply_text(f"<b>❌ خطا در ورود: {str(e)}</b>", parse_mode='HTML')
        del user_states[user_id]
        del admin_salf_data[user_id]

async def admin_handle_salf_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "admin_waiting_password":
        return
    password = update.message.text.strip()
    if not password:
        await update.message.reply_text("<b>❌ لطفا پسورد را وارد کنید!</b>", parse_mode='HTML')
        return
    try:
        data = admin_salf_data[user_id]
        client = data.get('client')
        if not client:
            await update.message.reply_text("<b>❌ خطا در اتصال! لطفا دوباره تلاش کنید.</b>", parse_mode='HTML')
            del user_states[user_id]
            del admin_salf_data[user_id]
            return
        await client.sign_in(password=password)
        me = await client.get_me()
        first_name = me.first_name if me.first_name else ""
        last_name = me.last_name if me.last_name else ""
        full_name = f"{first_name} {last_name}".strip()
        if not full_name:
            full_name = me.username if me.username else "کاربر"
        iran_tz = pytz.timezone('Asia/Tehran')
        iran_time = datetime.now(iran_tz)
        time_str = iran_time.strftime('%H:%M')
        session_string = client.session.save()
        save_user_session(data['target_user_id'], session_string, data['phone'], data['api_hash'], data['api_id'])
        await client.disconnect()
        set_clock_status(data['target_user_id'], True)
        asyncio.create_task(start_salf_client(data['target_user_id']))
        text = (
            "<b>✅ ورود سلف به اکانت کاربر با موفقیت انجام شد!</b>\n\n"
            f"<b>👤 نام اکانت: {full_name}</b>\n"
            f"<b>📱 شماره: {data['phone']}</b>\n"
            f"<b>🕐 ساعت ورود: {time_str}</b>\n"
            f"<b>📅 تاریخ ورود: {iran_time.strftime('%Y-%m-%d')}</b>\n"
            f"<b>🆔 آیدی کاربر: {data['target_user_id']}</b>\n\n"
            "<b>⏰ ساعت روی اسم اکانت کاربر فعال شد!</b>\n"
            "<b>✅ پنل سلف فعال شد (با نوشتن کلمه \"پنل\" در هر جایی)</b>"
        )
        keyboard = [
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        del user_states[user_id]
        del admin_salf_data[user_id]
    except Exception as e:
        await update.message.reply_text(f"<b>❌ خطا در ورود با پسورد: {str(e)}</b>", parse_mode='HTML')
        del user_states[user_id]
        del admin_salf_data[user_id]

# ==================== USER SELF LOGIN ====================

async def salf_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if is_user_banned(user_id):
        await query.answer("🚫 شما مسدود شده اید!", show_alert=True)
        return
    if not has_active_subscription(user_id):
        await query.answer("❌ شما اشتراک فعال ندارید!", show_alert=True)
        return
    existing_session = get_user_session(user_id)
    if existing_session:
        await query.answer("🔑 شما قبلاً وارد سلف شده اید!", show_alert=True)
        return
    user_states[user_id] = "waiting_salf_phone"
    salf_login_data[user_id] = {}
    text = (
        "<b>🔑 ورود به سلف</b>\n\n"
        "<b>📱 لطفا شماره موبایل خود را با کد کشور وارد کنید.</b>\n"
        "<b>📝 مثال: +989123456789</b>\n\n"
        "<b>🔙 در صورتی که منصرف شده‌اید دکمه زیر را کلیک کنید.</b>"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def handle_salf_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_salf_phone":
        return
    phone = update.message.text.strip() if update.message.text else ""
    if not phone or not re.match(r'^\+?[0-9]{10,15}$', phone):
        await update.message.reply_text(
            "<b>❌ شماره وارد شده صحیح نیست! لطفا با کد کشور وارد کنید.</b>\n"
            "<b>📝 مثال: +989123456789</b>",
            parse_mode='HTML'
        )
        return
    salf_login_data[user_id]['phone'] = phone
    user_states[user_id] = "waiting_salf_api_id"
    await update.message.reply_text(
        "<b>🔑 مرحله 2 از 4</b>\n\n"
        "<b>🔢 لطفا آیپی عددی (API ID) خود را وارد کنید.</b>\n"
        "<b>⚠️ API ID باید عددی بین 1 تا 2147483647 باشد.</b>",
        parse_mode='HTML'
    )

async def handle_salf_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_salf_api_id":
        return
    text = update.message.text.strip() if update.message.text else ""
    if not text:
        await update.message.reply_text("<b>❌ لطفا یک عدد وارد کنید!</b>", parse_mode='HTML')
        return
    try:
        api_id = int(text)
        if api_id > 2147483647:
            await update.message.reply_text(
                "<b>❌ عدد وارد شده خیلی بزرگ است!</b>\n"
                "<b>⚠️ API ID باید عددی بین 1 تا 2147483647 باشد.</b>\n"
                "<b>🔄 لطفا دوباره وارد کنید:</b>",
                parse_mode='HTML'
            )
            return
    except:
        await update.message.reply_text("<b>❌ آیپی عددی باید عدد باشد! لطفا دوباره وارد کنید.</b>", parse_mode='HTML')
        return
    salf_login_data[user_id]['api_id'] = api_id
    user_states[user_id] = "waiting_salf_api_hash"
    await update.message.reply_text(
        "<b>🔑 مرحله 3 از 4</b>\n\n"
        "<b>🔑 لطفا آیپی هش (API Hash) خود را وارد کنید.</b>",
        parse_mode='HTML'
    )

async def handle_salf_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_salf_api_hash":
        return
    api_hash = update.message.text.strip() if update.message.text else ""
    if not api_hash or len(api_hash) < 20:
        await update.message.reply_text(
            "<b>❌ آیپی هش وارد شده صحیح نیست! لطفا دوباره وارد کنید.</b>",
            parse_mode='HTML'
        )
        return
    salf_login_data[user_id]['api_hash'] = api_hash
    user_states[user_id] = "waiting_salf_code"
    try:
        data = salf_login_data[user_id]
        session_path = f"sessions/user_{user_id}"
        client = TelegramClient(session_path, data['api_id'], data['api_hash'])
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(data['phone'])
            salf_login_data[user_id]['client'] = client
            await update.message.reply_text(
                "<b>🔑 مرحله 4 از 4</b>\n\n"
                "<b>✅ کد تایید به شماره شما ارسال شد.</b>\n"
                "<b>📝 لطفا کد را به این صورت بفرستید: <code>1.2.3.4.5</code></b>",
                parse_mode='HTML'
            )
        else:
            await client.disconnect()
            await update.message.reply_text("<b>❌ این شماره قبلاً در سلف ثبت شده است!</b>", parse_mode='HTML')
            del user_states[user_id]
            del salf_login_data[user_id]
    except PhoneNumberInvalidError:
        await update.message.reply_text("<b>❌ شماره وارد شده معتبر نیست!</b>", parse_mode='HTML')
        del user_states[user_id]
        del salf_login_data[user_id]
    except FloodWaitError as e:
        await update.message.reply_text(f"<b>⏳ لطفا {e.seconds} ثانیه صبر کنید و دوباره تلاش کنید.</b>", parse_mode='HTML')
        del user_states[user_id]
        del salf_login_data[user_id]
    except Exception as e:
        await update.message.reply_text(f"<b>❌ خطا در ارسال کد تایید: {str(e)}</b>", parse_mode='HTML')
        if 'client' in salf_login_data.get(user_id, {}):
            try:
                await salf_login_data[user_id]['client'].disconnect()
            except:
                pass
        del user_states[user_id]
        del salf_login_data[user_id]

async def handle_salf_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_salf_code":
        return
    code_input = update.message.text.strip() if update.message.text else ""
    code = code_input.replace('.', '').replace(' ', '').strip()
    if not code or not code.isdigit():
        await update.message.reply_text(
            "<b>❌ لطفا کد را به این صورت بفرستید: <code>1.2.3.4.5</code></b>",
            parse_mode='HTML'
        )
        return
    try:
        data = salf_login_data[user_id]
        client = data.get('client')
        if not client:
            await update.message.reply_text("<b>❌ خطا در اتصال! لطفا دوباره تلاش کنید.</b>", parse_mode='HTML')
            del user_states[user_id]
            del salf_login_data[user_id]
            return
        try:
            await client.sign_in(data['phone'], code)
            me = await client.get_me()
            first_name = me.first_name if me.first_name else ""
            last_name = me.last_name if me.last_name else ""
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                full_name = me.username if me.username else "کاربر"
            iran_tz = pytz.timezone('Asia/Tehran')
            iran_time = datetime.now(iran_tz)
            time_str = iran_time.strftime('%H:%M')
            session_string = client.session.save()
            save_user_session(user_id, session_string, data['phone'], data['api_hash'], data['api_id'])
            set_clock_status(user_id, True)
            asyncio.create_task(start_salf_client(user_id))
            text = (
                "<b>✅ ورود سلف به اکانت شما با موفقیت انجام شد.</b>\n\n"
                "<b>📌 سلف برای شما نصب شد.</b>\n"
                "<b>🔑 برای استفاده از سلف، کلمه \"پنل\" را در هر جایی بنویسید.</b>\n"
                "<b>💠 در صورت بروز مشکل با پشتیبانی تماس بگیرید.</b>"
            )
            keyboard = [
                [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
            del user_states[user_id]
            del salf_login_data[user_id]
            return
        except PhoneCodeExpiredError:
            await update.message.reply_text("<b>⏳ کد منقضی شده بود، در حال ارسال کد جدید...</b>", parse_mode='HTML')
            await client.send_code_request(data['phone'])
            await update.message.reply_text(
                "<b>✅ کد جدید به شماره شما ارسال شد.</b>\n"
                "<b>📝 لطفا کد را به این صورت بفرستید: <code>1.2.3.4.5</code></b>",
                parse_mode='HTML'
            )
            return
        except PhoneCodeInvalidError:
            await update.message.reply_text(
                "<b>❌ کد وارد شده صحیح نیست! لطفا دوباره تلاش کنید.</b>\n"
                "<b>📝 لطفا کد را به این صورت بفرستید: <code>1.2.3.4.5</code></b>",
                parse_mode='HTML'
            )
            return
    except SessionPasswordNeededError:
        user_states[user_id] = "waiting_salf_password"
        await update.message.reply_text(
            "<b>⚠️ اکانت شما دارای سیستم تایید دو مرحله‌ای (2FA) است.</b>\n"
            "<b>🗝 لطفاً رمز عبور اختصاصی خود را وارد کنید :</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        await update.message.reply_text(f"<b>❌ خطا در ورود: {str(e)}</b>", parse_mode='HTML')
        del user_states[user_id]
        del salf_login_data[user_id]

async def handle_salf_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id] != "waiting_salf_password":
        return
    password = update.message.text.strip() if update.message.text else ""
    if not password:
        await update.message.reply_text("<b>❌ لطفا پسورد را وارد کنید!</b>", parse_mode='HTML')
        return
    try:
        data = salf_login_data[user_id]
        client = data.get('client')
        if not client:
            await update.message.reply_text("<b>❌ خطا در اتصال! لطفا دوباره تلاش کنید.</b>", parse_mode='HTML')
            del user_states[user_id]
            del salf_login_data[user_id]
            return
        await client.sign_in(password=password)
        me = await client.get_me()
        first_name = me.first_name if me.first_name else ""
        last_name = me.last_name if me.last_name else ""
        full_name = f"{first_name} {last_name}".strip()
        if not full_name:
            full_name = me.username if me.username else "کاربر"
        iran_tz = pytz.timezone('Asia/Tehran')
        iran_time = datetime.now(iran_tz)
        time_str = iran_time.strftime('%H:%M')
        session_string = client.session.save()
        save_user_session(user_id, session_string, data['phone'], data['api_hash'], data['api_id'])
        set_clock_status(user_id, True)
        asyncio.create_task(start_salf_client(user_id))
        text = (
            "<b>✅ ورود سلف به اکانت شما با موفقیت انجام شد.</b>\n\n"
            "<b>📌 سلف برای شما نصب شد.</b>\n"
            "<b>🔑 برای استفاده از سلف، کلمه \"پنل\" را در هر جایی بنویسید.</b>\n"
            "<b>💠 در صورت بروز مشکل با پشتیبانی تماس بگیرید.</b>"
        )
        keyboard = [
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_from_user_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        del user_states[user_id]
        del salf_login_data[user_id]
    except Exception as e:
        await update.message.reply_text(f"<b>❌ خطا در ورود با پسورد: {str(e)}</b>", parse_mode='HTML')
        del user_states[user_id]
        del salf_login_data[user_id]

# ==================== NAVIGATION ====================

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if is_user_banned(user_id):
        await query.edit_message_text(
            "<b>🚫 شما از طرف مدیریت مسدود شده اید!</b>\n<b>💠 در صورت نیاز با پشتیبانی تماس بگیرید.</b>",
            parse_mode='HTML'
        )
        return
    if user_id in support_mode:
        del support_mode[user_id]
    user_mention = f"@{query.from_user.username}" if query.from_user.username else query.from_user.first_name
    if user_id in user_states:
        del user_states[user_id]
    if user_id in salf_login_data:
        del salf_login_data[user_id]
    
    if is_admin(user_id):
        text = (
            f"<b>⚡ درود {user_mention} به پنل ریپر سلف خوش آمدید.</b>\n\n"
            "<b>🛠️ در این پنل میتوانید ربات را کنترل و مدیریت کنید.</b>\n\n"
            "<b>🔄 لطفا از منوی زیر انتخاب نمایید.</b>"
        )
        keyboard = [
            [InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings")],
            [InlineKeyboardButton("📊 آمار کل", callback_data="admin_stats")],
            [InlineKeyboardButton("📡 بررسی پینگ", callback_data="admin_ping"), InlineKeyboardButton("⏳ اعتبار هاست", callback_data="admin_host")],
            [InlineKeyboardButton("👥 منوی کاربران", callback_data="admin_users_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
        return
    remaining_days = get_remaining_days(user_id)
    has_subscription = has_active_subscription(user_id)
    is_verified = is_user_verified(user_id)
    session_data = get_user_session(user_id)
    is_logged_in = session_data is not None
    expiry_date = get_expiry_date(user_id)
    text = (
        f"<b>⚡ سلام {user_mention} به ربات ریپر سلف خوش آمدید!</b>\n\n"
        "<b>🎯 در این ربات میتوانید از پشتیبانی، خرید، نصب ربات سلف بهره ببرید!</b>\n\n"
        "<b>💠 اگر سوالی دارید از بخش پشتیبانی استفاده کنید.</b>"
    )
    keyboard = []
    keyboard.append([InlineKeyboardButton("👨‍💻 پشتیبانی", callback_data="support")])
    keyboard.append([InlineKeyboardButton("🤔 سلف چیست؟", callback_data="what_is_self"), InlineKeyboardButton("📣 کانال ما", url="https://t.me/ReaperSelfChannel")])
    keyboard.append([InlineKeyboardButton(f"📅 انقضا شما: ({remaining_days} روز)", callback_data="expiry")])
    if is_verified:
        keyboard.append([InlineKeyboardButton("✅ احراز هویت شده", callback_data="verified_already")])
    else:
        keyboard.append([InlineKeyboardButton("✔️ احراز هویت", callback_data="verify")])
    keyboard.append([InlineKeyboardButton("💳 خرید اشتراک", callback_data="buy_subscription")])
    keyboard.append([InlineKeyboardButton("💶 خرید با کد", callback_data="buy_with_code")])
    if has_subscription:
        keyboard.append([InlineKeyboardButton("🔑 ورود سلف", callback_data="salf_login")])
    keyboard.append([InlineKeyboardButton("💎 نرخ", callback_data="rate")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def buy_1_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer("💳 لطفا مبلغ 100 هزار تومان را واریز کنید!", show_alert=True)

async def buy_2_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer("💳 لطفا مبلغ 150 هزار تومان را واریز کنید!", show_alert=True)

async def buy_3_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer("💳 لطفا مبلغ 200 هزار تومان را واریز کنید!", show_alert=True)

async def buy_4_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer("💳 لطفا مبلغ 250 هزار تومان را واریز کنید!", show_alert=True)

async def buy_5_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer("💳 لطفا مبلغ 300 هزار تومان را واریز کنید!", show_alert=True)

async def buy_6_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer("💳 لطفا مبلغ 350 هزار تومان را واریز کنید!", show_alert=True)

async def expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    remaining_days = get_remaining_days(user_id)
    expiry_date = get_expiry_date(user_id)
    if remaining_days > 0:
        await query.answer(f"📅 انقضا: {expiry_date} ({remaining_days} روز باقی مانده)", show_alert=True)
    else:
        await query.answer("⏳ اشتراک شما فعال نمیباشد!", show_alert=True)

# ==================== MESSAGE HANDLER ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text(
            "<b>🚫 شما از طرف مدیریت مسدود شده اید!</b>\n<b>💠 در صورت نیاز با پشتیبانی تماس بگیرید.</b>",
            parse_mode='HTML'
        )
        return
    
    if user_id in user_states and str(user_states[user_id]).startswith("replying_to_"):
        await handle_admin_reply_message(update, context)
        return
    if user_id in support_mode:
        await handle_support_message(update, context)
        return
    if user_id in user_states:
        state = user_states[user_id]
        if state == "waiting_for_verify_photo":
            await handle_verify_photo(update, context)
            return
        elif state == "waiting_for_card_number":
            await handle_verify_card_number(update, context)
            return
        elif state == "waiting_for_activation_code":
            await handle_activation_code(update, context)
            return
        elif state == "waiting_salf_phone":
            await handle_salf_phone(update, context)
            return
        elif state == "waiting_salf_api_id":
            await handle_salf_api_id(update, context)
            return
        elif state == "waiting_salf_api_hash":
            await handle_salf_api_hash(update, context)
            return
        elif state == "waiting_salf_code":
            await handle_salf_code(update, context)
            return
        elif state == "waiting_salf_password":
            await handle_salf_password(update, context)
            return
        elif state == "waiting_for_block_user":
            await handle_block_user(update, context)
            return
        elif state == "waiting_for_unblock_user":
            await handle_unblock_user(update, context)
            return
        elif state == "waiting_for_transfer_credit":
            await handle_transfer_credit(update, context)
            return
        elif state == "waiting_for_deduct_credit":
            await handle_deduct_credit(update, context)
            return
        elif state == "waiting_for_code_days":
            await handle_code_days(update, context)
            return
        elif state == "waiting_for_cancel_code":
            await handle_cancel_code(update, context)
            return
        elif state == "admin_waiting_phone":
            await admin_handle_salf_phone(update, context)
            return
        elif state == "admin_waiting_user_id":
            await admin_handle_salf_user_id(update, context)
            return
        elif state == "admin_waiting_api_id":
            await admin_handle_salf_api_id(update, context)
            return
        elif state == "admin_waiting_api_hash":
            await admin_handle_salf_api_hash(update, context)
            return
        elif state == "admin_waiting_code":
            await admin_handle_salf_code(update, context)
            return
        elif state == "admin_waiting_password":
            await admin_handle_salf_password(update, context)
            return
        elif state == "admin_waiting_logout_phone":
            await admin_handle_salf_logout_phone(update, context)
            return

# ==================== MAIN ====================

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_membership, pattern="check_membership"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="admin_stats"))
    app.add_handler(CallbackQueryHandler(admin_ping, pattern="admin_ping"))
    app.add_handler(CallbackQueryHandler(admin_host, pattern="admin_host"))
    app.add_handler(CallbackQueryHandler(admin_users_menu, pattern="admin_users_menu"))
    app.add_handler(CallbackQueryHandler(admin_settings, pattern="admin_settings"))
    app.add_handler(CallbackQueryHandler(admin_settings_back, pattern="admin_settings_back"))
    app.add_handler(CallbackQueryHandler(admin_create_code, pattern="admin_create_code"))
    app.add_handler(CallbackQueryHandler(admin_cancel_code, pattern="admin_cancel_code"))
    app.add_handler(CallbackQueryHandler(admin_block_user, pattern="admin_block_user"))
    app.add_handler(CallbackQueryHandler(admin_unblock_user, pattern="admin_unblock_user"))
    app.add_handler(CallbackQueryHandler(admin_transfer_credit, pattern="admin_transfer_credit"))
    app.add_handler(CallbackQueryHandler(admin_deduct_credit, pattern="admin_deduct_credit"))
    app.add_handler(CallbackQueryHandler(admin_salf_login, pattern="admin_salf_login"))
    app.add_handler(CallbackQueryHandler(admin_salf_logout, pattern="admin_salf_logout"))
    app.add_handler(CallbackQueryHandler(admin_back, pattern="admin_back"))
    app.add_handler(CallbackQueryHandler(support, pattern="support"))
    app.add_handler(CallbackQueryHandler(disconnect_support, pattern="disconnect_support"))
    app.add_handler(CallbackQueryHandler(what_is_self, pattern="what_is_self"))
    app.add_handler(CallbackQueryHandler(verify, pattern="verify"))
    app.add_handler(CallbackQueryHandler(verified_already, pattern="verified_already"))
    app.add_handler(CallbackQueryHandler(delete_card, pattern="delete_card"))
    app.add_handler(CallbackQueryHandler(new_card, pattern="new_card"))
    app.add_handler(CallbackQueryHandler(back_to_verify, pattern="back_to_verify"))
    app.add_handler(CallbackQueryHandler(buy_subscription, pattern="buy_subscription"))
    app.add_handler(CallbackQueryHandler(buy_with_code, pattern="buy_with_code"))
    app.add_handler(CallbackQueryHandler(salf_login, pattern="salf_login"))
    app.add_handler(CallbackQueryHandler(main_menu, pattern="main_menu"))
    app.add_handler(CallbackQueryHandler(back_from_user_menu, pattern="back_from_user_menu"))
    app.add_handler(CallbackQueryHandler(rate, pattern="rate"))
    app.add_handler(CallbackQueryHandler(expiry, pattern="expiry"))
    app.add_handler(CallbackQueryHandler(buy_1_month, pattern="buy_1_month"))
    app.add_handler(CallbackQueryHandler(buy_2_month, pattern="buy_2_month"))
    app.add_handler(CallbackQueryHandler(buy_3_month, pattern="buy_3_month"))
    app.add_handler(CallbackQueryHandler(buy_4_month, pattern="buy_4_month"))
    app.add_handler(CallbackQueryHandler(buy_5_month, pattern="buy_5_month"))
    app.add_handler(CallbackQueryHandler(buy_6_month, pattern="buy_6_month"))
    app.add_handler(CallbackQueryHandler(handle_admin_reply, pattern="^reply_"))
    app.add_handler(CallbackQueryHandler(handle_admin_reply, pattern="^block_"))
    app.add_handler(CallbackQueryHandler(accept_verify, pattern="^accept_verify_"))
    app.add_handler(CallbackQueryHandler(reject_verify, pattern="^reject_verify_"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND | filters.Document.ALL | filters.VIDEO, handle_message))
    
    print("🤖 ربات در حال اجراست...")
    app.run_polling()

if __name__ == "__main__":
    main()
