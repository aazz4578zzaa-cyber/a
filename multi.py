import asyncio
from io import BytesIO
import cv2
import numpy as np
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.photos import GetUserPhotosRequest
from db_helpers import db_get_account, db_get_template, db_get_settings
from face_filter import is_human_face
from keyboards import back_kb, Colors
from config import WAIT_MULTI_IDS, MY_USER_ID
from handlers.profile import _resolve_entity


def is_owner(user_id):
    return user_id == MY_USER_ID


async def menu_multi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()

    settings = await db_get_settings(q.from_user.id)
    if not settings or not settings[1]:
        await q.edit_message_text(
            "اول یه اکانت انتخاب کن.",
            reply_markup=back_kb("menu_main")
        )
        return

    await q.edit_message_text(
        "چند آیدی همزمان\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "شناسه‌ها رو یکی در هر خط بفرست:\n\n"
        "123456789\n"
        "@username1\n"
        "@username2\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb("menu_main")
    )
    return WAIT_MULTI_IDS


async def handle_multi_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    raw = update.message.text.strip()
    lines = [l.strip() for l in raw.split('\n') if l.strip()]

    if not lines:
        await update.message.reply_text("چیزی نفرستادی.")
        return WAIT_MULTI_IDS

    owner_id = update.effective_user.id
    settings = await db_get_settings(owner_id)
    if not settings or not settings[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = await db_get_account(settings[1])
    status_msg = await update.message.reply_text(f"در حال پردازش {len(lines)} شناسه...")

    asyncio.create_task(_process_multi(
        bot=context.bot, owner_id=owner_id, account_row=a,
        targets=lines, template_id=settings[2], channel_id=settings[3],
        status_message_id=status_msg.message_id, chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def _process_multi(bot, owner_id, account_row, targets,
                         template_id, channel_id, status_message_id, chat_id):
    client = None
    try:
        _, _, phone, api_id, api_hash, session_str, acc_name, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text("سشن منقضی شده.", chat_id=chat_id, message_id=status_message_id)
            return

        total = len(targets)
        success = 0
        failed = 0

        for idx, raw in enumerate(targets, 1):
            try:
                await bot.edit_message_text(
                    f"پردازش {idx}/{total}...\nشناسه: {raw}\nموفق: {success}  ناموفق: {failed}",
                    chat_id=chat_id, message_id=status_message_id
                )
            except:
                pass

            target = await _resolve_entity(client, raw)
            if not target:
                failed += 1
                continue
            try:
                await _process_silent(bot, client, owner_id, target, template_id, channel_id)
                success += 1
            except:
                failed += 1
            await asyncio.sleep(1)

        await bot.edit_message_text(
            f"تکمیل شد\nکل: {total}\nموفق: {success}\nناموفق: {failed}",
            chat_id=chat_id, message_id=status_message_id,
            reply_markup=back_kb("menu_main")
        )
    except Exception as e:
        print(f"multi error: {e}")
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def _process_silent(bot, client, owner_id, target, template_id, channel_id):
    target_id = target.id
    first_name = target.first_name or ""
    last_name = target.last_name or ""
    full_name = (first_name + " " + last_name).strip() or "بدون نام"
    username = target.username or ""
    user_id_str = str(target.id)

    try:
        photos = await client(GetUserPhotosRequest(user_id=target_id, offset=0, max_id=0, limit=200))
        photo_list = photos.photos if hasattr(photos, 'photos') else []
    except:
        return
    if not photo_list:
        return

    footer = ""
    if template_id:
        t = await db_get_template(template_id)
        if t:
            footer = t[3]
            footer = footer.replace("{name}", full_name).replace("{username}", username or "")
            footer = footer.replace("{first_name}", first_name).replace("{last_name}", last_name)
            footer = footer.replace("{id}", user_id_str)

    sent = 0
    for photo in photo_list:
        try:
            buf = BytesIO()
            await client.download_media(photo, buf)
            buf.seek(0)
            data = buf.getvalue()
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if is_human_face(img):
                caption = footer if sent == 0 else None
                try:
                    await bot.send_photo(chat_id=owner_id, photo=data, caption=caption)
                except:
                    pass
                if channel_id:
                    try:
                        await bot.send_photo(chat_id=channel_id, photo=data, caption=caption)
                    except:
                        pass
                sent += 1
                await asyncio.sleep(0.3)
        except:
            continue
