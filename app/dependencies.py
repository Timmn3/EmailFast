from pathlib import Path
import yaml
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.bot import DefaultBotProperties


def read_config(path, default={}):
    if path.exists() is False:
        # print(f"WARNING: {path} not found")
        return default
    else:
        with path.open('r') as ymlfile:
            return yaml.safe_load(ymlfile)


BAS_DIR = Path(__file__).parent
config = read_config(BAS_DIR / 'config.yaml')

# Database
DB_USER = config.get("DB_USER")
DB_PASS = config.get("DB_PASS")
DB_HOST = config.get("DB_HOST")
DB_PORT = config.get("DB_PORT")
DB_NAME = config.get("DB_NAME")

SMS_ACTIVATE_KEY = config.get("SMS_ACTIVATE_KEY")
SMSFAST_API_KEY = config.get("SMSFAST_API_KEY")
SMSFAST_API_URL = config.get("SMSFAST_API_URL", "https://api.smsfast.com/stubs/handler_api.php")

REF_BONUS = config.get("REF_BONUS")
WITHDRAW_CHAT_ID = config.get("WITHDRAW_CHAT_ID")
SUPPORT_URL = config.get("SUPPORT_URL")
CHANNEL_ID = config.get("CHANNEL_ID")

DATABASE_DATA = f"{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
DATABASE_URL = f'postgresql+asyncpg://{DATABASE_DATA}'
DATABASE_URL_SYNC = f'postgresql://{DATABASE_DATA}'

DB_CONFIG = {
    "connections": {
        "default": {
            "engine": "tortoise.backends.asyncpg",
            "credentials": {
                "host": f"{DB_HOST}",
                "port": f"{DB_PORT}",
                "user": f"{DB_USER}",
                "password": f"{DB_PASS}",
                "database": f"{DB_NAME}",

                # ✅ Настройки пула asyncpg (уменьшают риск "протухших" / оборванных коннектов)
                # docs: tortoise -> PostgreSQL optional parameters (pass-through to driver)
                "minsize": 1,
                "maxsize": 10,
                "max_queries": 10000,
                "max_inactive_connection_lifetime": 120.0,
            },
        },
    },
    "apps": {
        "models": {
            "models": ["app.db.models", "aerich.models"],
            "default_connection": "default",
        },
    },
}


# --- Aerich: безопасно подставляем DATABASE_URL, не завися от Working Directory ---
AERICH_INI_PATH = (BAS_DIR.parent / "aerich.ini").resolve()

try:
    if AERICH_INI_PATH.exists():
        aerich_config = AERICH_INI_PATH.read_text(encoding="utf-8")

        # Заменяем только если реально есть плейсхолдер.
        # Если плейсхолдера нет — файл не трогаем.
        if "%(db_url)s" in aerich_config:
            aerich_config = aerich_config.replace("%(db_url)s", DATABASE_URL)
            AERICH_INI_PATH.write_text(aerich_config, encoding="utf-8")
except Exception:
    # В некоторых окружениях (скрипты/CI/read-only FS) не должны падать из-за Aerich ini
    pass


API_TOKEN = config.get('API_TOKEN')
ADMINS = config.get('ADMINS', [])
CODER = config.get('CODER')
PROJECT_MANAGER = config.get('PROJECT_MANAGER')
USER_BOT = config.get('USER_BOT')


LAVA_SHOP_ID = config.get('LAVA_SHOP_ID')
LAVA_SECRET_KEY = config.get('LAVA_SECRET_KEY')

FK_SHOP_ID = config.get('FK_SHOP_ID')
FK_SECRET_KEY = config.get('FK_SECRET_KEY')
FK_FK_API_KEY = config.get('FK_API_KEY')

YOOMONEY_ID = config.get('YOOMONEY_ID')
YOOMONEY_TOKEN = config.get('YOOMONEY_TOKEN')
YOOMONEY_RECEIVER = config.get('YOOMONEY_RECEIVER')


ANY_PAY_ID = config.get('ANY_PAY_ID')
ANY_PAY_API_KEY = config.get('ANY_PAY_API_KEY')
ANY_PAY_PROJECT_ID = config.get('ANY_PAY_PROJECT_ID')

STORE_ID = config.get('STORE_ID')
PUBLIC_KEY = config.get('PUBLIC_KEY')
PRIVATE_KEY = config.get('PRIVATE_KEY')
API_URL = config.get('API_URL')

API_LOGIN_CKASSA = config.get('API_LOGIN_CKASSA')
API_KEY_CKASSA = config.get('API_KEY_CKASSA')
SERV_CODE_CKASSA = config.get('SERV_CODE_CKASSA')

CRYPTOMUS_API_KEY = config.get('CRYPTOMUS_API_KEY')
CRYPTOMUS_API_KEY_PAYOUT = config.get('CRYPTOMUS_API_KEY_PAYOUT')
CRYPTOMUS_MERCHANT_ID = config.get('CRYPTOMUS_MERCHANT_ID')

API_KEY_ONLINESIM = config.get('API_KEY_ONLINESIM')

ON_SCHEDULE = config.get('ON_SCHEDULE')

CHECK_CHANNEL = config.get('CHECK_CHANNEL')\

REFERRAL_PREFIX = config.get('REFERRAL_PREFIX')

REFERRAL_PREFIX_WHODI = config.get('REFERRAL_PREFIX_WHODI')

USER_ACCESS_TO_THE_COMMAND = config.get('USER_ACCESS_TO_THE_COMMAND')

USER_ACCESS_TO_THE_COMMAND_USER_REPORT_AND_ADD_BALANCE = int(config.get('USER_ACCESS_TO_THE_COMMAND_USER_REPORT_AND_ADD_BALANCE'))

REFERRAL_PREFIX_SILOBUS = config.get('REFERRAL_PREFIX_SILOBUS')

SMSFAST_RPS = float(config.get("SMSFAST_RPS", 1.0))

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode='HTML', link_preview_is_disabled=True)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# Сопоставление сервисов для SMSFast: название сервиса -> код для SMSFast
SMSFAST_SERVICE_MAP = {
    "telegram": "tg",
    "vkcom":    "vk",
    "google":   "go",
    "tiktok":   "lf",
    "amazon":   "am",
    "claude":   "acz",
    "ot":       "ot",   # "Любой другой"
}