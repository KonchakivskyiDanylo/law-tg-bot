import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Timezone offset in hours
TIMEZONE_OFFSET_HOURS = 3

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o").split("#")[0].strip()

# MongoDB Configuration
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME")
MONGODB_COLLECTION_NAME = os.getenv("MONGODB_COLLECTION_NAME")

# YOOKASSA config
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
BOT_URL = "t.me/{YourBotName}_bot"

# Subscription Settings
TARIFF_PRICES = {
    "basic": "149.00",
    "premium": "759.00"
}

# Subscription duration in days
SUBSCRIPTION_DURATION_DAYS = 30

MAX_TELEGRAM_MESSAGE_LENGTH = 4096
