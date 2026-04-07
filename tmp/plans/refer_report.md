# План: Детальная статистика рефовода `/refer_report`

## Context
В боте есть ТОП-20 рефоводов (`/stat → Рефералы`). Нужно добавить команду `/refer_report [telegram_id]` для просмотра детальной статистики конкретного рефовода в HTML-формате. Статистика включает: переходы за сутки/неделю/месяц и помесячный дневной дашборд.

---

## Файлы для изменения

- `c:\PycharmProjects\ON_server\EmailFast\app\handlers\admin_handler.py` — добавить новый хендлер команды

---

## Реализация

### Команда: `/refer_report <telegram_id>`

**Хендлер:** `@router.message(Command("refer_report"))` в `admin_handler.py`

Только для ADMINS. Парсим `telegram_id` из аргументов команды.

### Данные для сбора

1. **Базовая инфо рефовода** — из `User` по `telegram_id`:
   - `full_name`, `username`, `telegram_id`, `ref_balance`, `total_ref_earnings`
   - Внутренний `user.id` нужен для join с таблицей рефералов

2. **Рефералы** (`User.refer_id = referrer.id`):
   - За сутки: `created_at >= now - 24h`
   - За неделю: `created_at >= now - 7d`
   - За месяц: `created_at >= now - 30d`
   - Всего

3. **Оплаты рефералов** (`Payment.user__refer_id = referrer.id`, `is_success=True`):
   - За сутки / неделю / месяц / всего
   - Суммы за те же периоды

4. **Дневной дашборд текущего месяца** — группировка по дням:
   - Для каждого дня: количество новых рефералов + количество оплат + сумма оплат
   - SQL через `annotate` + `group_by` с truncate по дате

### SQL-запросы (Tortoise ORM)

```python
from tortoise.functions import Count, Sum
from tortoise.expressions import RawSQL
from datetime import datetime, timedelta, timezone

now = datetime.now(tz=timezone.utc)
today = now.replace(hour=0, minute=0, second=0, microsecond=0)
month_start = today.replace(day=1)

# Рефоводский user объект
referrer = await models.User.get_or_none(telegram_id=target_tg_id)

# Рефералы по периодам (COUNT)
refs_day   = await models.User.filter(refer_id=referrer.id, created_at__gte=now - timedelta(days=1)).count()
refs_week  = await models.User.filter(refer_id=referrer.id, created_at__gte=now - timedelta(days=7)).count()
refs_month = await models.User.filter(refer_id=referrer.id, created_at__gte=now - timedelta(days=30)).count()
refs_total = await models.User.filter(refer_id=referrer.id).count()

# Оплаты по периодам
base_pay = models.Payment.filter(is_success=True, user__refer_id=referrer.id)
pays_day   = await base_pay.filter(created_at__gte=now - timedelta(days=1))
pays_week  = await base_pay.filter(created_at__gte=now - timedelta(days=7))
pays_month = await base_pay.filter(created_at__gte=now - timedelta(days=30))
# Annotate count + sum через .annotate(cnt=Count("id"), total=Sum("amount")).values(...)

# Дневной дашборд текущего месяца — через RawSQL group by date
daily_refs = await models.User.filter(
    refer_id=referrer.id,
    created_at__gte=month_start
).annotate(
    day=RawSQL("DATE(created_at)")
).group_by("day").annotate(cnt=Count("id")).values("day", "cnt")

daily_pays = await models.Payment.filter(
    is_success=True,
    user__refer_id=referrer.id,
    created_at__gte=month_start
).annotate(
    day=RawSQL("DATE(created_at)")
).group_by("day").annotate(cnt=Count("id"), total=Sum("amount")).values("day", "cnt", "total")
```

### Формат вывода (HTML, parse_mode="HTML")

```
📊 <b>Детальный отчёт рефовода</b>
👤 <b>Имя:</b> Иван Иванов
🆔 <b>TG ID:</b> <code>123456789</code>
🔗 <b>Username:</b> @username

💰 <b>Реф. баланс:</b> 1 234.50 ₽
💎 <b>Всего заработано:</b> 5 000.00 ₽

─────────────────────────
📈 <b>Переходы по рефссылке</b>

⏱ За сутки:  <b>12</b>
📅 За неделю: <b>87</b>
🗓 За месяц:  <b>234</b>
∞  Всего:     <b>1 518</b>

─────────────────────────
💳 <b>Оплаты рефералов</b>

⏱ За сутки:  <b>3</b> шт · <b>450.00 ₽</b>
📅 За неделю: <b>21</b> шт · <b>3 150.00 ₽</b>
🗓 За месяц:  <b>87</b> шт · <b>13 050.00 ₽</b>
∞  Всего:     <b>531</b> шт · <b>79 650.00 ₽</b>

─────────────────────────
📆 <b>Апрель 2026 — по дням</b>

<code>
Дата       Переходы  Оплат  Сумма
──────────────────────────────────
01.04        8        3     450 ₽
02.04        5        2     300 ₽
03.04       12        7   1 050 ₽
...
</code>
```

Таблица по дням — через `<code>` блок с выравниванием (monospace).

---

## Добавление кнопки в ТОП-20

В `admin_referrals_top` рядом с каждым рефоводом добавить подсказку:
```
/refer_report 123456789
```
(просто текст в конце каждой строки, кликабельная команда через `<code>`)

---

## Порядок реализации

1. Написать хендлер `refer_report` в `admin_handler.py`
2. Собрать все запросы (базовая инфо, периоды, дашборд)
3. Сформировать HTML-текст
4. Добавить `/refer_report {telegram_id}` как подсказку в ТОП-20

---

## Верификация

- `python3 -m py_compile app/handlers/admin_handler.py` — синтаксис
- Запустить бота, выполнить `/refer_report <real_tg_id>` — убедиться что данные корректны
- Проверить `/stat → Рефералы` — ТОП-20 должен содержать подсказку по команде
- Проверить несуществующий ID — должно выводить ошибку "Рефовод не найден"
