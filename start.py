from telegram import Update
from telegram.ext import ContextTypes
from db_helpers import db_get_settings, db_get_account, db_get_template
from keyboards import main_menu_kb, Colors
from config import MY_USER_ID


def is_owner(user_id):
    return user_id == MY_USER_ID


def main_menu_text(settings, account, template):
    acc_name = account[6] if account else "انتخاب نشده"
    tmpl_name = template[2] if template else "انتخاب نشده"
    ch_name = settings[4] if settings and settings[3] else "متصل نشده"

    return (
        "پنل مدیریت خصوصی\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"اکانت فعال: {acc_name}\n"
        f"قالب فعال: {tmpl_name}\n"
        f"کانال مقصد: {ch_name}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "از دکمه‌های زیر انتخاب کنید:"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    uid = update.effective_user.id
    settings = await db_get_settings(uid)
    account = await db_get_account(settings[1]) if settings and settings[1] else None
    template = await db_get_template(settings[2]) if settings and settings[2] else None

    await update.message.reply_text(
        main_menu_text(settings, account, template),
        reply_markup=main_menu_kb()
    )


async def menu_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    uid = q.from_user.id
    settings = await db_get_settings(uid)
    account = await db_get_account(settings[1]) if settings and settings[1] else None
    template = await db_get_template(settings[2]) if settings and settings[2] else None

    try:
        await q.edit_message_text(
            main_menu_text(settings, account, template),
            reply_markup=main_menu_kb()
        )
    except:
        pass
