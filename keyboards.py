from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ==================== ایموجی‌های رنگی ====================
class Colors:
    PRIMARY = "🔵"   # آبی
    SUCCESS = "🟢"   # سبز
    DANGER = "🔴"    # قرمز
    WARNING = "🟡"   # زرد
    INFO = "🔷"      # آبی روشن
    NEUTRAL = "⚪"   # خاکستری
    PURPLE = "🟣"    # بنفش
    ORANGE = "🟠"    # نارنجی
    BLACK = "⚫"     # مشکی
    WHITE = "⚪"     # سفید


def main_menu_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{Colors.PRIMARY} مدیریت اکانت‌ها", callback_data="menu_accounts"),
            InlineKeyboardButton(f"{Colors.PURPLE} مدیریت قالب‌ها", callback_data="menu_templates")
        ],
        [
            InlineKeyboardButton(f"{Colors.SUCCESS} گرفتن پروفایل", callback_data="menu_get_profile"),
        ],
        [
            InlineKeyboardButton(f"{Colors.INFO} چند آیدی همزمان", callback_data="menu_multi"),
            InlineKeyboardButton(f"{Colors.INFO} گروه/کانال", callback_data="menu_group")
        ],
        [
            InlineKeyboardButton(f"{Colors.ORANGE} اتصال به کانال", callback_data="menu_channel"),
            InlineKeyboardButton(f"{Colors.NEUTRAL} تنظیمات", callback_data="menu_settings")
        ]
    ])


def back_kb(target="menu_main"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{Colors.NEUTRAL} بازگشت", callback_data=target)]
    ])


def confirm_kb(yes_cb, no_cb):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{Colors.SUCCESS} بله", callback_data=yes_cb),
            InlineKeyboardButton(f"{Colors.DANGER} انصراف", callback_data=no_cb)
        ]
    ])


def account_kb(acc_id, is_current):
    kb = []
    if not is_current:
        kb.append([InlineKeyboardButton(f"{Colors.SUCCESS} انتخاب به عنوان فعال", callback_data=f"acc_use_{acc_id}")])
    kb.append([InlineKeyboardButton(f"{Colors.DANGER} حذف", callback_data=f"acc_del_{acc_id}")])
    kb.append([InlineKeyboardButton(f"{Colors.NEUTRAL} بازگشت", callback_data="menu_accounts")])
    return InlineKeyboardMarkup(kb)


def template_kb(tid, is_current):
    kb = []
    if not is_current:
        kb.append([InlineKeyboardButton(f"{Colors.SUCCESS} انتخاب به عنوان فعال", callback_data=f"tmpl_use_{tid}")])
    kb.append([InlineKeyboardButton(f"{Colors.DANGER} حذف", callback_data=f"tmpl_del_{tid}")])
    kb.append([InlineKeyboardButton(f"{Colors.NEUTRAL} بازگشت", callback_data="menu_templates")])
    return InlineKeyboardMarkup(kb)
