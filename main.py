import logging
from telegram import Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, filters
)
from config import (
    TOKEN, WAIT_PHONE, WAIT_API_ID, WAIT_API_HASH, WAIT_CODE, WAIT_PASSWORD,
    WAIT_TARGET_INPUT, WAIT_TEMPLATE_TEXT, WAIT_NEW_TEMPLATE,
    WAIT_CHANNEL_LINK, WAIT_MULTI_IDS, WAIT_GROUP_LINK
)
from database import init_database
from handlers.start import start, menu_main
from handlers.accounts import (
    menu_accounts, acc_add_start, acc_wait_phone, acc_wait_api_id,
    acc_wait_api_hash, acc_wait_code, acc_wait_password,
    acc_view, acc_use, acc_del
)
from handlers.templates import (
    menu_templates, tmpl_add_start, tmpl_wait_title, tmpl_wait_text,
    tmpl_view, tmpl_use, tmpl_del
)
from handlers.profile import menu_get_profile, handle_target_input
from handlers.multi import menu_multi, handle_multi_ids
from handlers.group import menu_group, handle_group_link
from handlers.channel import menu_channel, handle_channel_input, channel_disconnect

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def post_init(app):
    await init_database()
    logger.info("دیتابیس آماده شد")


async def cancel(update: Update, context):
    from config import MY_USER_ID
    if update.effective_user.id != MY_USER_ID:
        return ConversationHandler.END
    context.user_data.clear()
    from keyboards import back_kb
    await update.message.reply_text("لغو شد.", reply_markup=back_kb("menu_main"))
    return ConversationHandler.END


def main():
    if not TOKEN or "PASTE" in TOKEN:
        print("توکن تنظیم نشده.")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    # Start
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    # افزودن اکانت
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(acc_add_start, pattern="^acc_add$")],
        states={
            WAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_phone)],
            WAIT_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_api_id)],
            WAIT_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_api_hash)],
            WAIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_code)],
            WAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, acc_wait_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # افزودن قالب
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(tmpl_add_start, pattern="^tmpl_add$")],
        states={
            WAIT_NEW_TEMPLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_wait_title)],
            WAIT_TEMPLATE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_wait_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # پروفایل
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_get_profile, pattern="^menu_get_profile$")],
        states={
            WAIT_TARGET_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_target_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # چند آیدی
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_multi, pattern="^menu_multi$")],
        states={
            WAIT_MULTI_IDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_multi_ids)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # گروه
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_group, pattern="^menu_group$")],
        states={
            WAIT_GROUP_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # کانال
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_channel, pattern="^menu_channel$")],
        states={
            WAIT_CHANNEL_LINK: [
                MessageHandler(filters.FORWARDED, handle_channel_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channel_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    ))

    # Callback ها
    app.add_handler(CallbackQueryHandler(menu_main, pattern="^menu_main$"))
    app.add_handler(CallbackQueryHandler(menu_accounts, pattern="^menu_accounts$"))
    app.add_handler(CallbackQueryHandler(menu_templates, pattern="^menu_templates$"))
    app.add_handler(CallbackQueryHandler(acc_view, pattern="^acc_view_"))
    app.add_handler(CallbackQueryHandler(acc_use, pattern="^acc_use_"))
    app.add_handler(CallbackQueryHandler(acc_del, pattern="^acc_del_"))
    app.add_handler(CallbackQueryHandler(tmpl_view, pattern="^tmpl_view_"))
    app.add_handler(CallbackQueryHandler(tmpl_use, pattern="^tmpl_use_"))
    app.add_handler(CallbackQueryHandler(tmpl_del, pattern="^tmpl_del_"))
    app.add_handler(CallbackQueryHandler(channel_disconnect, pattern="^channel_disconnect$"))

    # منوی تنظیمات
    async def menu_settings(update, context):
        q = update.callback_query
        await q.answer()
        from keyboards import back_kb
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await q.edit_message_text(
            "تنظیمات\n━━━━━━━━━━━━━━━━━━\n\nبخش تنظیمات.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔵 بازگشت", callback_data="menu_main")]
            ])
        )
    app.add_handler(CallbackQueryHandler(menu_settings, pattern="^menu_settings$"))

    print("ربات در حال اجراست...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
