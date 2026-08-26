# Конфигурация

### Конфигурация

1) Скопируйте пример `app/config.yaml` и заполните **своими** значениями (никогда не коммитьте реальные токены):

```yaml
# app/config.yaml (пример)
API_TOKEN: "000000:TELEGRAM_BOT_TOKEN"

DB_HOST: "127.0.0.1"
DB_PORT: 5432
DB_USER: "postgres"
DB_PASS: "postgres"
DB_NAME: "REDACTED"

# Платежи - включайте только те, что используете
YOOMONEY_ID: ""
YOOMONEY_TOKEN: ""
YOOMONEY_RECEIVER: ""

FK_SHOP_ID: ""
FK_SECRET_KEY: ""
FK_API_KEY: ""

ANY_PAY_ID: ""
ANY_PAY_API_KEY: ""
ANY_PAY_PROJECT_ID: ""

STORE_ID: ""         # streampay
PUBLIC_KEY: ""
PRIVATE_KEY: ""
API_URL: ""

API_LOGIN_CKASSA: ""
API_KEY_CKASSA: ""
SERV_CODE_CKASSA: ""

CRYPTOMUS_API_KEY: ""
CRYPTOMUS_API_KEY_PAYOUT: ""
CRYPTOMUS_MERCHANT_ID: ""

# Провайдеры SMS
SMS_ACTIVATE_KEY: ""
API_KEY_ONLINESIM: ""

# Прочее
REF_BONUS: 10
WITHDRAW_CHAT_ID: ""
SUPPORT_URL: "https://t.me/your_support_bot"
ADMINS: [123456789]
CHANNEL_ID: ""
CHECK_CHANNEL: false
CODER: 0
USER_BOT: 0
ON_SCHEDULE: true            # включает фоновые задачи
REFERRAL_PREFIX: 0
USER_ACCESS_TO_THE_COMMAND: 0
```

> Примечание: `aerich.ini` содержит плейсхолдер `%(db_url)s`. При старте он автоматически заменяется на строку подключения из `app/dependencies.py`. При необходимости проверьте права доступа на запись в корень проекта.

### Миграции БД (Aerich)

Инициализация и создание схемы (один раз на чистой БД):

```bash
aerich init -t app.dependencies.DB_CONFIG
aerich init-db
```

Дальше - стандартные команды:
```bash
aerich migrate -n "init"
aerich upgrade
```

### Запуск

```bash
python main.py
```

Бот поднимет `Dispatcher`, зарегистрирует диалоги/роутеры, выполнит инициализацию БД, настроит команды и запустит **polling**. Для продакшена используйте **systemd**/supervisor/Docker по вашему стандарту.
