import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.types import ChannelParticipantsSearch
from db_helpers import db_get_account, db_get_settings
from keyboards import back_kb, Colors
from config import WAIT_GROUP_LINK, MY_USER_ID
from handlers.multi import _process_silent


def is_owner(user_id):
    return user_id == MY_USER_ID


async def menu_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    settings = await db_get_settings(q.from_user.id)
    if not settings or not settings[1]:
        await q.edit_message_text("اول یه اکانت انتخاب کن.", reply_markup=back_kb("menu_main"))
        return

    await q.edit_message_text(
        "گروه یا کانال\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "لینک گروه یا کانال رو بفرست:\n\n"
        "- https://t.me/groupname\n"
        "- https://t.me/+AbCdEf123\n"
        "- @groupname\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb("menu_main")
    )
    return WAIT_GROUP_LINK


async def handle_group_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    link = update.message.text.strip()
    owner_id = update.effective_user.id

    settings = await db_get_settings(owner_id)
    if not settings or not settings[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = await db_get_account(settings[1])
    status_msg = await update.message.reply_text(f"در حال پردازش گروه...")

    asyncio.create_task(_process_group(
        bot=context.bot, owner_id=owner_id, account_row=a, link=link,
        template_id=settings[2], channel_id=settings[3],
        status_message_id=status_msg.message_id, chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def _process_group(bot, owner_id, account_row, link,
                         template_id, channel_id, status_message_id, chat_id):
    client = None
    try:
        _, _, phone, api_id, api_hash, session_str, acc_name, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text("سشن منقضی شده.", chat_id=chat_id, message_id=status_message_id)
            return

        entity = None
        try:
            if '+' in link:
                hash_part = link.split('+')[-1].split('/')[0]
                try:
                    result = await client(ImportChatInviteRequest(hash_part))
                    entity = result.chats[0]
                except:
                    result = await client(CheckChatInviteRequest(hash_part))
                    entity = result.chat
            else:
                name = link.split('/')[-1]
                if name.startswith('@'):
                    name = name[1:]
                entity = await client.get_entity(name)
        except Exception as e:
            await bot.edit_message_text(f"گروه پیدا نشد.\n{str(e)[:200]}", chat_id=chat_id, message_id=status_message_id)
            return

        group_title = getattr(entity, 'title', 'بدون نام')
        await bot.edit_message_text(f"گروه: {group_title}\nدر حال دریافت اعضا...", chat_id=chat_id, message_id=status_message_id)

        participants = []
        offset = 0
        limit = 100
        while True:
            try:
                result = await client(GetParticipantsRequest(
                    channel=entity, filter=ChannelParticipantsSearch(''),
                    offset=offset, limit=limit, hash=0
                ))
                if not result.users:
                    break
                participants.extend(result.users)
                offset += len(result.users)
                if len(result.users) < limit or len(participants) >= 500:
                    break
                await asyncio.sleep(0.5)
            except:
                break

        total = len(participants)
        if total == 0:
            await bot.edit_message_text("عضوی پیدا نشد.", chat_id=chat_id, message_id=status_message_id)
            return

        await bot.edit_message_text(f"اعضا: {total}\nپردازش...", chat_id=chat_id, message_id=status_message_id)

        success = 0
        for idx, user in enumerate(participants, 1):
            if user.bot or user.deleted:
                continue
            try:
                await bot.edit_message_text(
                    f"پردازش {idx}/{total}...\nموفق: {success}",
                    chat_id=chat_id, message_id=status_message_id
                )
            except:
                pass
            try:
                await _process_silent(bot, client, owner_id, user, template_id, channel_id)
                success += 1
            except:
                pass
            await asyncio.sleep(0.5)

        await bot.edit_message_text(
            f"تکمیل شد\nگروه: {group_title}\nکل: {total}\nپردازش شده: {success}",
            chat_id=chat_id, message_id=status_message_id,
            reply_markup=back_kb("menu_main")
        )
    except Exception as e:
        print(f"group error: {e}")
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass
