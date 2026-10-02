import os

# ==================== تنظیمات اصلی ====================
TOKEN = os.environ.get(
    "TOKEN",
    "8816493813:AAHSSd5Xz1i4jCbZ-jW9QrW8QcRAZi41BzQ"
)

MY_USER_ID = int(os.environ.get("MY_USER_ID", "7803165903"))

# ==================== PostgreSQL ====================
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# اگه DATABASE_URL نبود، fallback به SQLite
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///private_bot.db"

# ==================== اتصال به کانال ====================
DEFAULT_CHANNEL_ID = os.environ.get("DEFAULT_CHANNEL_ID", "")

# ==================== تنظیمات AI ====================
FACE_DETECTION_MIN_SIZE = 40  # حداقل سایز چهره به پیکسل
FACE_DETECTION_NEIGHBORS = 6  # حساسیت تشخیص

# ==================== Conversation States ====================
(
    WAIT_PHONE, WAIT_API_ID, WAIT_API_HASH, WAIT_CODE, WAIT_PASSWORD,
    WAIT_TARGET_INPUT, WAIT_TEMPLATE_TEXT, WAIT_NEW_TEMPLATE,
    WAIT_CHANNEL_LINK, WAIT_MULTI_IDS, WAIT_GROUP_LINK
) = range(11)
