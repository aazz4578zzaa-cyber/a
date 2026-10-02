import re
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, PhoneCodeExpiredError, PhoneCodeInvalidError,
    PhoneNumberInvalidError, SessionPasswordNeededError
)
from db_helpers import (
    db_add_account, db_get_accounts, db_get_account,
    db_delete_account, db_get_settings, db_update_setting
)
from keyboards import back_kb, account_kb, Colors
from config import (
    WAIT_PHONE, WAIT_API_ID, WAIT_API_HASH, WAIT_CODE, WAIT_PASSWORD,
    MY_USER_ID
)


def is_owner(user_id):
    return user_id == MY_USER_ID


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
        lines.append("\nبرای مدیریت، روی هرکدوم کلیک کن.")
        text = "\n".join(lines)

    kb = []
    for a in accounts:
        label = f"{Colors.PRIMARY} {a[6]}  |  {a[2][-4:]}"
        kb.append([__import__('telegram').InlineKeyboardButton(label, callback_data=f"acc_view_{a[0]}")])
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb.append([InlineKeyboardButton(f"{Colors.SUCCESS} افزودن اکانت جدید", callback_data="acc_add")])
    kb.append([InlineKeyboardButton(f"{Colors.NEUTRAL} بازگشت", callback_data="menu_main")])

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
            "کد رو بفرست:\n1.2.3.4.5  یا  12345"
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

        acc_id = await db_add_account(
            MY_USER_ID, phone, api_id, api_hash, session_str, acc_name, username
        )
        await client.disconnect()

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await update.message.reply_text(
            "اکانت اضافه شد\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"نام: {acc_name}\n"
            f"یوزرنیم: @{username or 'ندارد'}\n"
            f"شماره: {phone}\n"
            f"شناسه: {acc_id}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{Colors.SUCCESS} مدیریت اکانت‌ها", callback_data="menu_accounts")]
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

    settings = await db_get_settings(q.from_user.id)
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
        f"{'این اکانت فعاله' if is_current else 'فعال نیست'}"
    )
    await q.edit_message_text(text, reply_markup=account_kb(acc_id, is_current))


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
    settings = await db_get_settings(q.from_user.id)
    if settings and settings[1] == acc_id:
        await db_update_setting(q.from_user.id, current_account_id=None)
    await q.answer("حذف شد")
    await menu_accounts(update, context)
