# План: подтверждение при смене Email

## Context

Сейчас при нажатии «Сменить Email» ящик меняется **мгновенно**, без подтверждения. Юзеры случайно тыкают кнопку и теряют адрес (а для бесплатного FirstMail это ещё и списание 50₽). Нужно вставить экран подтверждения «Вы действительно хотите сменить Email?» с кнопками **«Сменить Email»** / **«Отмена»** во все три точки смены.

Решения, согласованные с пользователем:
- Подтверждение **редактирует текущую карточку** в вопрос (не отдельное сообщение).
- После успешной смены — alert «✅ Почта изменена» + **карточка обновляется новым email** (текущее поведение сохраняется).
- «Отмена» возвращает исходную карточку.
- Применить ко **всем трём** точкам смены.

## Три точки смены (текущее состояние)

| Точка | Хендлер | Файл | Возврат карточки |
|---|---|---|---|
| Аренда FirstMail (бесплатно для юзера, cooldown 24ч) | `change_rental_email:{lease_id}` | [start_handler.py:583](app/handlers/start_handler.py#L583) | `mail:{lease_id}` → [mail_info:270](app/handlers/start_handler.py#L270) |
| Бесплатный FirstMail (смена платная 50₽) | `change_free_firstmail:{assignment_id}` | [get_email_handler.py:322](app/handlers/get_email_handler.py#L322) | билдеры `_build_free_firstmail_text/_markup` |
| Legacy mail.tm аренда | `change_email:{mail_id}` | [start_handler.py:703](app/handlers/start_handler.py#L703) | карточка строится инлайн в теле хендлера |

## Подход

Единый паттерн для каждой точки: **кнопка карточки остаётся с тем же callback_data**, но её хендлер теперь показывает подтверждение. Реальная смена переезжает в новый `confirm_*` хендлер. «Отмена» перерисовывает карточку.

Рендеры карточек (`mail_info`, `_build_free_firstmail_*`, тело legacy) **не трогаем** — callback_data кнопок «Сменить Email» не меняются.

### 1. Тексты — [app/services/bot_texts.py](app/services/bot_texts.py)

Добавить константу:
```python
CONFIRM_CHANGE_EMAIL = "Вы действительно хотите сменить Email?"
```
Кнопки «Сменить Email» / «Отмена» задаём инлайн (отдельные константы не обязательны).

### 2. Аренда FirstMail — [app/handlers/start_handler.py](app/handlers/start_handler.py)

- `change_rental_email:{lease_id}` (текущий [:583](app/handlers/start_handler.py#L583)) → **переделать в показ подтверждения**:
  - проверить user + lease + **cooldown** (как сейчас, [:623](app/handlers/start_handler.py#L623)) — если cooldown, сразу alert, вопрос не показываем;
  - `edit_text(CONFIRM_CHANGE_EMAIL, ...)` с клавиатурой:
    - «Сменить Email» → `confirm_change_rental_email:{lease_id}`
    - «Отмена» → `mail:{lease_id}` (переиспользуем `mail_info`, ноль дублирования)
- Новый хендлер `confirm_change_rental_email:{lease_id}` → **текущее тело смены** ([:636-672](app/handlers/start_handler.py#L636)): `change_rental_email_lease(...)`, обновление карточки новым email, `call.answer("✅ Почта успешно изменена")`. Cooldown-проверку оставить и здесь (защита от устаревшей кнопки).

### 3. Бесплатный FirstMail (50₽) — [app/handlers/get_email_handler.py](app/handlers/get_email_handler.py)

- `change_free_firstmail:{assignment_id}` (текущий [:322](app/handlers/get_email_handler.py#L322)) → **переделать в показ подтверждения**:
  - проверить user + assignment активен;
  - `edit_text(CONFIRM_CHANGE_EMAIL, ...)` с клавиатурой:
    - «Сменить Email» → `confirm_change_free_firstmail:{assignment_id}`
    - «Отмена» → `cancel_change_free_firstmail:{assignment_id}`
- Новый `confirm_change_free_firstmail:{assignment_id}` → **текущее тело** ([:362-420](app/handlers/get_email_handler.py#L362)): проверка баланса (→ платёжный flow если мало), списание 50₽, `change_free_firstmail_assignment(...)`, обновление карточки, alert «✅».
- Новый `cancel_change_free_firstmail:{assignment_id}` → перерисовать карточку через `_build_free_firstmail_text(assignment)` + `_build_free_firstmail_markup(assignment_id, show_back=True)` + `edit_text`.

### 4. Legacy mail.tm — [app/handlers/start_handler.py](app/handlers/start_handler.py)

- `change_email:{mail_id}` (текущий [:703](app/handlers/start_handler.py#L703)) → **переделать в показ подтверждения**:
  - проверить user + old_mail (`is_paid_mail=True, is_active=True`);
  - `edit_text(CONFIRM_CHANGE_EMAIL, ...)` с клавиатурой:
    - «Сменить Email» → `confirm_change_email:{mail_id}`
    - «Отмена» → `cancel_change_email:{mail_id}`
- Новый `confirm_change_email:{mail_id}` → **текущее тело смены** ([:719-814](app/handlers/start_handler.py#L719)): `create_mail()`, транзакция, обновление карточки, alert «✅».
- Новый `cancel_change_email:{mail_id}` → перерисовать legacy-карточку (та же клавиатура `EXTEND_EMAIL_BTN`/`CHANGE_EMAIL_BTN`/`my_rent_emails`, что строится после смены в [:793-811](app/handlers/start_handler.py#L793)).

## Итоговый flow (для всех трёх)

```
[ карточка ящика ]
   │ жмёт «Сменить Email»  (callback не меняется)
   ▼
[ Вы действительно хотите сменить Email? ]
   [Сменить Email]  [Отмена]
       │                │
  confirm_*         cancel_* / mail:{id}
       │                │
 смена → alert ✅   возврат карточки
 + карточка с
 новым email
```

## Проверка

- `python3 -m py_compile app/handlers/start_handler.py app/handlers/get_email_handler.py`
- `python3 -c "import yaml" ` не нужен (правки только .py).
- Ручной прогон в боте по каждой из трёх точек:
  1. «Сменить Email» → появляется вопрос (карточка превратилась в вопрос).
  2. «Отмена» → вернулась исходная карточка с тем же email.
  3. «Сменить Email» (в вопросе) → alert «✅», карточка с **новым** email.
  4. Для free firstmail при балансе < 50₽ после подтверждения открывается оплата (поведение сохранено).
  5. Для аренды при активном cooldown — alert про cooldown, вопрос не показывается.
- Проверить, что callback_data старых кнопок на карточках не изменились (обратная совместимость с уже отправленными сообщениями).
