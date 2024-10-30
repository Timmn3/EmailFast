from pathlib import Path
import yaml
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
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
REF_BONUS = config.get("REF_BONUS")
WITHDRAW_CHAT_ID = config.get("WITHDRAW_CHAT_ID")
SUPPORT_URL = config.get("SUPPORT_URL")
CHANNEL_ID = config.get("CHANNEL_ID")

DATABASE_DATA = f"{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
DATABASE_URL = f'postgresql+asyncpg://{DATABASE_DATA}'
DATABASE_URL_SYNC = f'postgresql://{DATABASE_DATA}'

DB_CONFIG = {
    'connections': {
        'default': {
            'engine': 'tortoise.backends.asyncpg',
            'credentials': {
                'host': f'{DB_HOST}',
                'port': f'{DB_PORT}',
                'user': f'{DB_USER}',
                'password': f'{DB_PASS}',
                'database': f'{DB_NAME}',
            },
        },
    },
    'apps': {
        'models': {
            'models': ['app.db.models', 'aerich.models'],
            'default_connection': 'default',
        },
    },
}

with open("aerich.ini", "r") as aerich_file:
    aerich_config = aerich_file.read()

aerich_config = aerich_config.replace("%(db_url)s", DATABASE_URL)

with open("aerich.ini", "w") as aerich_file:
    aerich_file.write(aerich_config)

API_TOKEN = config.get('API_TOKEN')
ADMINS = config.get('ADMINS', [])
CODER = config.get('CODER')


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

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode='HTML', link_preview_is_disabled=True)
)
storage = RedisStorage.from_url(f"redis://{DB_HOST}:6379/0", key_builder=DefaultKeyBuilder(with_destiny=True))
dp = Dispatcher(storage=storage)
