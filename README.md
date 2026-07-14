# EmailFast — Telegram-бот для приёма SMS и аренды e‑mail

Сервис на базе **Aiogram 3** для приёма SMS/кодов подтверждения и аренды временных e‑mail‑ящиков. Есть пополнение баланса через платёжные системы, партнёрская программа, планировщик фоновых задач и удобная админ‑статистика.

> Демо/связь: [@emailfastbot](https://telegram.me/emailfastbot) • Доп. бот: [@NeuronAgentBot](https://telegram.me/NeuronAgentBot)

---

## Содержание
- [Возможности](#возможности)
- [Технологии](#технологии)
- [Структура](#структура)
- [Быстрый старт](#быстрый-старт)
  - [Требования](#требования)
  - [Установка](#установка)
  - [Конфигурация](#конфигурация)
  - [Миграции БД (Aerich)](#миграции-бд-aerich)
  - [Запуск](#запуск)
- [Планировщик задач](#планировщик-задач)
- [Платёжные провайдеры](#платёжные-провайдеры)
- [Журналы логирования](#журналы-логирования)
- [Частые вопросы](#частые-вопросы)
- [Лицензия](#лицензия)

---

## Возможности

- Приём **SMS** и аренда номеров (интеграции со сторонними провайдерами).
- Аренда **временных e‑mail** (создание, продление, авто‑уведомления об окончании).
- Пополнение баланса через несколько платёжных систем + **Telegram Stars**.
- **Партнёрская программа** с учётом приглашений и вознаграждений.
- Автоматические фоновые проверки и уведомления (**APScheduler**).
- Админ‑статистика: пользователи, SMS/e‑mail, аренды, пополнения и пр.
- Современный стек: **Aiogram 3**, **aiogram_dialog**, **Tortoise ORM + asyncpg**, **Loguru**.

## Технологии

- **Python 3.12**, **Aiogram 3**, **aiogram_dialog**
- **APScheduler** — фоновые задания
- **Tortoise ORM** + **PostgreSQL (asyncpg)**, **Aerich** — миграции
- **Loguru** — структурированное логирование
- Внешние сервисы: SMS‑провайдеры, платёжные системы

## Структура

```
app/
  db/
    models.py          # модели Tortoise ORM
    database.py        # подключение к БД
  dialogs/             # диалоги aiogram_dialog
  services/            # интеграции (платежи, SMS/e-mail и т.д.)
  scheduler_instance.py# экземпляр APScheduler
  dependencies.py      # конфиг и DI (бот, dp, DB_CONFIG)
  config.yaml          # основной конфиг (не коммитить секреты!)
aerich.ini             # конфиг для Aerich (db_url подставляется динамически)
requirements.txt
main.py                # точка входа в приложение
```

## Быстрый старт

### Требования

- Python **3.12+**
- PostgreSQL **13+**
- Токен Telegram‑бота
- Ключи платёжных систем (по необходимости)

### Установка

```bash
git clone <your-repo-url> emailfast
cd emailfast
python -m venv .venv
. .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

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

# Платежи — включайте только те, что используете
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
SUPPORT_URL: "https://telegram.me/your_support_bot"
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

Дальше — стандартные команды:
```bash
aerich migrate -n "init"
aerich upgrade
```

### Запуск

```bash
python main.py
```

Бот поднимет `Dispatcher`, зарегистрирует диалоги/роутеры, выполнит инициализацию БД, настроит команды и запустит **polling**. Для продакшена используйте **systemd**/supervisor/Docker по вашему стандарту.

## Планировщик задач

В фоне выполняется ряд периодических задач (включаются флагом `ON_SCHEDULE: true`):

- Проверка SMS — каждые **10 сек**
- Проверка e‑mail — каждые **30 сек**
- Проверка платежей: **CKassa (25s)**, **StreamPay (28s)**, **FreeKassa (33s)**, **AnyPay (45s)**, **Cryptomus (50s)**
- Обновление справочников сервисов — **каждый день в 03:00**
- Пинг служебного бота — каждые **300 сек**
- Уведомления/проверки e‑mail аренды — **каждые 10–20 мин**
- Аренда SMS: проверка активных, напоминания, авто‑продление, завершение — **1 мин**/35 сек циклы

## Платёжные провайдеры

Поддерживаются (подключайте выборочно):
- **YooMoney**, **FreeKassa**, **AnyPay**, **StreamPay**, **CKassa**, **Cryptomus**, **Lava**, **PayOK**, а также **Telegram Stars** (pre_checkout обработчик).

Каждый провайдер настраивается ключами/ID в `app/config.yaml`. Добавьте только то, что реально используете, и реализуйте необходимые вебхуки/проверки в соответствующих сервисах.

## Журналы логирования

`Loguru` пишет логи в папки:
```
logs/errors    # ошибки с traceback (с ротацией/архивацией)
logs/users     # действия пользователей (отдельный уровень USER_ACTION)
logs/general   # общая информация
```

Консольный вывод форматирован и цветной. Папки создаются автоматически при старте.

## Частые вопросы

**Q:** Можно хранить секреты в репозитории?  
**A:** Нет. Держите реальные токены вне Git (env/secret‑хранилища, CI/CD), в `config.yaml` — только плейсхолдеры.

**Q:** Что с миграциями?  
**A:** Используется `Aerich` для моделей Tortoise ORM. См. раздел «Миграции БД».

**Q:** Как отключить фоновые задачи?  
**A:** Поставьте `ON_SCHEDULE: false` в `app/config.yaml` и перезапустите.

## Лицензия

MIT.

---

**Автор/контакты:** TG: [@Timmn3](https://telegram.me/emailfastbot)
