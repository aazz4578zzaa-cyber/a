import asyncio
from io import BytesIO
import cv2
import numpy as np
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.photos import GetUserPhotosRequest
from db_helpers import (
    db_get_account, db_get_template, db_get_settings
)
from face_filter import is_human_face
from keyboards import back_kb, Colors
from config import WAIT_TARGET_INPUT, MY_USER_ID


def is_owner(user_id):
    return user_id == MY_USER_ID


async def menu_get_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    a = await db_get_account(settings[1])
    t_text = "انتخاب نشده"
    if settings[2]:
        t = await db_get_template(settings[2])
        if t:
            t_text = t[2]
    ch_text = settings[4] if settings[3] else "متصل نشده"

    await q.edit_message_text(
        "گرفتن پروفایل\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"اکانت: {a[6]}\n"
        f"قالب: {t_text}\n"
        f"کانال: {ch_text}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "شناسه طرف رو بفرست:\n\n"
        "- آیدی عددی:  123456789\n"
        "- یوزرنیم:  @username\n\n"
        "فقط چهره‌های انسانی فیلتر می‌شن.",
        reply_markup=back_kb("menu_main")
    )
    return WAIT_TARGET_INPUT


async def handle_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END

    raw = update.message.text.strip()
    owner_id = update.effective_user.id

    settings = await db_get_settings(owner_id)
    if not settings or not settings[1]:
        await update.message.reply_text("اکانت فعال نداری.")
        return ConversationHandler.END

    a = await db_get_account(settings[1])
    if not a:
        await update.message.reply_text("اکانت پیدا نشد.")
        return ConversationHandler.END

    status_msg = await update.message.reply_text(f"در حال پردازش {raw}...")

    asyncio.create_task(_process_target(
        bot=context.bot,
        owner_id=owner_id,
        account_row=a,
        target_raw=raw,
        template_id=settings[2],
        channel_id=settings[3],
        status_message_id=status_msg.message_id,
        chat_id=update.effective_chat.id
    ))
    return ConversationHandler.END


async def _resolve_entity(client, raw):
    raw = raw.strip()
    try:
        if raw.startswith('@'):
            raw = raw[1:]
        if raw.isdigit():
            return await client.get_entity(int(raw))
        return await client.get_entity(raw)
    except Exception as e:
        print(f"resolve error: {e}")
        return None


async def _process_target(bot, owner_id, account_row, target_raw,
                          template_id, channel_id, status_message_id, chat_id):
    client = None
    try:
        _, _, phone, api_id, api_hash, session_str, acc_name, _, _, _ = account_row
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await bot.edit_message_text(
                "سشن منقضی شده.",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        target = await _resolve_entity(client, target_raw)
        if not target:
            await bot.edit_message_text(
                f"کاربر {target_raw} پیدا نشد.",
                chat_id=chat_id, message_id=status_message_id
            )
            return

        await _process_user_profile(
            bot, client, owner_id, target,
            template_id, channel_id, status_message_id, chat_id
        )
    except Exception as e:
        print(f"error: {e}")
        try:
            await bot.edit_message_text(
                f"خطا: {str(e)[:200]}",
                chat_id=chat_id, message_id=status_message_id
            )
        except:
            pass
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def _process_user_profile(bot, client, owner_id, target,
                                template_id, channel_id,
                                status_message_id, chat_id):
    target_id = target.id
    first_name = target.first_name or ""
    last_name = target.last_name or ""
    full_name = (first_name + " " + last_name).strip() or "بدون نام"
    username = target.username or ""
    user_id_str = str(target.id)

    try:
        photos = await client(GetUserPhotosRequest(
            user_id=target_id, offset=0, max_id=0, limit=200
        ))
        photo_list = photos.photos if hasattr(photos, 'photos') else []
    except Exception as e:
        await bot.edit_message_text(
            f"خطا در دریافت پروفایل‌ها: {str(e)[:150]}",
            chat_id=chat_id, message_id=status_message_id
        )
        return

    total = len(photo_list)
    if total == 0:
        await bot.edit_message_text(
            f"کاربر {full_name} پروفایلی نداره.",
            chat_id=chat_id, message_id=status_message_id
        )
        return

    await bot.edit_message_text(
        f"در حال پردازش...\n\nکاربر: {full_name}\nتعداد: {total}",
        chat_id=chat_id, message_id=status_message_id
    )

    human_faces = []
    for i, photo in enumerate(photo_list):
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
                human_faces.append(data)

            if (i + 1) % 5 == 0:
                try:
                    await bot.edit_message_text(
                        f"در حال پردازش...\n\n{i+1}/{total}\nچهره: {len(human_faces)}",
                        chat_id=chat_id, message_id=status_message_id
                    )
                except:
                    pass
        except:
            continue

    # متن قالب
    footer = ""
    if template_id:
        t = await db_get_template(template_id)
        if t:
            footer = t[3]
            footer = footer.replace("{name}", full_name)
            footer = footer.replace("{username}", username or "")
            footer = footer.replace("{first_name}", first_name or "")
            footer = footer.replace("{last_name}", last_name or "")
            footer = footer.replace("{id}", user_id_str)

    sent_owner = 0
    sent_channel = 0
    for idx, data in enumerate(human_faces):
        caption = footer if idx == 0 else None
        try:
            await bot.send_photo(chat_id=owner_id, photo=data, caption=caption)
            sent_owner += 1
        except:
            pass
        if channel_id:
            try:
                await bot.send_photo(chat_id=channel_id, photo=data, caption=caption)
                sent_channel += 1
            except:
                pass
        await asyncio.sleep(0.3)

    text = (
        "تکمیل شد\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"کاربر: {full_name}\n"
        f"یوزرنیم: @{username or 'ندارد'}\n"
        f"شناسه: {user_id_str}\n\n"
        f"کل: {total}\n"
        f"ارسال شده: {sent_owner}\n"
        f"فیلتر: {total - sent_owner}"
    )
    if channel_id:
        text += f"\nکانال: {sent_channel}"

    await bot.edit_message_text(
        text, chat_id=chat_id, message_id=status_message_id,
        reply_markup=back_kb("menu_main")
    )
