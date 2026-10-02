from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from db_helpers import db_get_account, db_get_settings, db_update_setting
from keyboards import back_kb, Colors
from config import WAIT_CHANNEL_LINK, MY_USER_ID


def is_owner(user_id):
    return user_id == MY_USER_ID


async def menu_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    settings = await db_get_settings(q.from_user.id)
    current = settings[3] if settings and settings[3] else None
    title = settings[4] if settings and settings[4] else "متصل نشده"

    text = (
        "اتصال به کانال\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"کانال فعلی: {title}\n\n"
        "پروفایل‌ها همزمان به این کانال هم ارسال می‌شن.\n\n"
        "برای اتصال:\n"
        "1. ربات رو به کانال اضافه کن\n"
        "2. یه پیام از کانال به ربات فوروارد کن\n"
        "3. یا یوزرنیم بفرست: @channel\n\n"
        "برای انصراف /cancel بزن."
    )

    kb = []
    if current:
        kb.append([InlineKeyboardButton(f"{Colors.DANGER} قطع اتصال", callback_data="channel_disconnect")])
    kb.append([InlineKeyboardButton(f"{Colors.NEUTRAL} بازگشت", callback_data="menu_main")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return WAIT_CHANNEL_LINK


async def handle_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    owner_id = update.effective_user.id

    # فوروارد
    if update.message.forward_from_chat:
        chat = update.message.forward_from_chat
        await db_update_setting(owner_id, channel_id=str(chat.id), channel_title=chat.title or "کانال")
        await update.message.reply_text(
            f"کانال متصل شد.\nنام: {chat.title}\nشناسه: {chat.id}",
            reply_markup=back_kb("menu_main")
        )
        return ConversationHandler.END

    text = update.message.text.strip() if update.message.text else ""
    if text.startswith('@') or text.startswith('https://t.me/'):
        name = text.split('/')[-1]
        if name.startswith('@'):
            name = name[1:]

        settings = await db_get_settings(owner_id)
        if not settings or not settings[1]:
            await update.message.reply_text("اول یه اکانت انتخاب کن.")
            return ConversationHandler.END

        a = await db_get_account(settings[1])
        client = None
        try:
            client = TelegramClient(StringSession(a[5]), a[3], a[4])
            await client.connect()
            entity = await client.get_entity(name)
            await db_update_setting(owner_id, channel_id=str(entity.id), channel_title=getattr(entity, 'title', name))
            await update.message.reply_text(
                f"کانال متصل شد.\nنام: {getattr(entity, 'title', name)}\nشناسه: {entity.id}",
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

    await update.message.reply_text("ورودی نامعتبر.")
    return WAIT_CHANNEL_LINK


async def channel_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    await db_update_setting(q.from_user.id, channel_id=None, channel_title=None)
    from handlers.start import menu_main
    await menu_main(update, context)
