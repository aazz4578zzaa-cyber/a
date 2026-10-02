from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from db_helpers import (
    db_add_template, db_get_templates, db_get_template,
    db_delete_template, db_get_settings, db_update_setting
)
from keyboards import back_kb, template_kb, Colors
from config import WAIT_NEW_TEMPLATE, WAIT_TEMPLATE_TEXT, MY_USER_ID


def is_owner(user_id):
    return user_id == MY_USER_ID


async def menu_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    templates = await db_get_templates(q.from_user.id)

    if not templates:
        text = "هنوز قالبی نساختی.\n\nیه قالب بساز تا زیر پروفایل‌ها بیاد."
    else:
        lines = ["قالب‌های موجود:\n"]
        for i, t in enumerate(templates, 1):
            preview = t[3][:80].replace('\n', ' ')
            lines.append(f"{i}. {t[2]}\n   {preview}...")
        lines.append("\nروی هرکدوم کلیک کن.")
        text = "\n".join(lines)

    kb = []
    for t in templates:
        kb.append([InlineKeyboardButton(f"{Colors.PURPLE} {t[2]}", callback_data=f"tmpl_view_{t[0]}")])
    kb.append([InlineKeyboardButton(f"{Colors.SUCCESS} قالب جدید", callback_data="tmpl_add")])
    kb.append([InlineKeyboardButton(f"{Colors.NEUTRAL} بازگشت", callback_data="menu_main")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def tmpl_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    await q.edit_message_text(
        "قالب جدید\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "عنوان قالب رو بفرست:\nمثال: چهره‌های زیبا\n\n"
        "برای انصراف /cancel بزن.",
        reply_markup=back_kb("menu_templates")
    )
    return WAIT_NEW_TEMPLATE


async def tmpl_wait_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['new_tmpl_title'] = update.message.text.strip()
    await update.message.reply_text(
        "حالا متن قالب رو بفرست:\n\n"
        "متغیرهای قابل استفاده:\n"
        "{name}  →  نام کامل\n"
        "{username}  →  یوزرنیم\n"
        "{id}  →  شناسه عددی\n"
        "{first_name}  →  اسم کوچک\n"
        "{last_name}  →  فامیل"
    )
    return WAIT_TEMPLATE_TEXT


async def tmpl_wait_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text
    title = context.user_data.pop('new_tmpl_title', 'بدون عنوان')
    tid = await db_add_template(MY_USER_ID, title, text)
    await update.message.reply_text(
        f"قالب {title} ذخیره شد. شناسه: {tid}",
        reply_markup=back_kb("menu_templates")
    )
    return ConversationHandler.END


async def tmpl_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    await q.answer()
    tid = int(q.data.split('_')[2])
    t = await db_get_template(tid)
    if not t:
        await q.answer("پیدا نشد", show_alert=True)
        return

    settings = await db_get_settings(q.from_user.id)
    is_current = settings and settings[2] == tid

    text = (
        "اطلاعات قالب\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"عنوان: {t[2]}\n\n"
        f"متن:\n{t[3]}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{'این قالب فعاله' if is_current else 'فعال نیست'}"
    )
    await q.edit_message_text(text, reply_markup=template_kb(tid, is_current))


async def tmpl_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    tid = int(q.data.split('_')[2])
    await db_update_setting(q.from_user.id, current_template_id=tid)
    await q.answer("انتخاب شد")
    await tmpl_view(update, context)


async def tmpl_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(q.from_user.id):
        await q.answer()
        return
    tid = int(q.data.split('_')[2])
    await db_delete_template(tid)
    settings = await db_get_settings(q.from_user.id)
    if settings and settings[2] == tid:
        await db_update_setting(q.from_user.id, current_template_id=None)
    await q.answer("حذف شد")
    await menu_templates(update, context)
