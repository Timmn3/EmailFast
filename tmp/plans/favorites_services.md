# План: Избранные сервисы в EmailFast

## Контекст
Пользователь устал каждый раз листать список из 344 сервисов. Нужна система избранных для быстрого доступа. Кнопка "Избранные сервисы" появится на месте Telegram (Telegram уходит на 2-ю страницу). После выбора сервиса и перехода к выбору страны — появляется кнопка добавления/удаления сервиса из избранного.

---

## Навигационный флоу (полная схема)

```
[Выбор сервиса]
  ├── 🏷 Избранные сервисы  ──► [Окно избранных]  ──► ← К списку сервисов
  │                               (только избранные)       (switch_to select_service)
  ├── ScrollingGroup (все сервисы, telegram на 2-й стр.)
  ├── 🔍 Поиск сервиса       ──► [Ввод названия]
  └── ← Назад

[Клик на сервис] (из любого окна: основного или избранных)
  └──► [Выбор страны]
         ├── ScrollingGroup (страны с ценами)
         ├── 🏷 Добавить/Удалить [сервис] из избранного  ← новая кнопка
         ├── 🔍 Поиск страны
         └── ← Назад
```

---

## Критические файлы

- `app/db/models.py` — добавить модель FavoriteService
- `app/dialogs/receive_sms/windows.py` — новое окно + правки в существующих
- `app/dialogs/receive_sms/getters.py` — get_services_2(), get_favorites(), get_countries()
- `app/dialogs/receive_sms/selected.py` — новые хендлеры
- `app/dialogs/receive_sms/states.py` — новый state ServiceMenu.favorites
- `app/dialogs/receive_sms/__init__.py` — подключить новое окно в Dialog
- `app/services/bot_texts.py` — константы текстов

---

## Шаг 1: БД — новая таблица

### Модель Tortoise ORM (добавить в models.py):
```python
class FavoriteService(Model):
    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField('models.User', related_name='favorite_services', on_delete=fields.CASCADE)
    service_code = fields.CharField(max_length=50)
    service_name = fields.CharField(max_length=255)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "favorite_services"
        unique_together = (("user", "service_code"),)
```

### SQL миграция (выполнить вручную):
```sql
CREATE TABLE IF NOT EXISTS favorite_services (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service_code VARCHAR(50) NOT NULL,
    service_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (user_id, service_code)
);

CREATE INDEX idx_favorite_services_user_id ON favorite_services(user_id);
```

---

## Шаг 2: Тексты (bot_texts.py)

Добавить константы:
```python
FAVORITES_SERVICES_BTN = "🏷 Избранные сервисы"
ADD_TO_FAVORITES_BTN = "🏷 Добавить {name} в избранное"     # .format(name=...)
REMOVE_FROM_FAVORITES_BTN = "🏷 Удалить {name} из избранных" # .format(name=...)
SELECT_FAVORITES_TITLE = "⭐ Избранные сервисы"
BACK_TO_SERVICES_BTN = "← К списку сервисов"
NO_FAVORITES_POPUP = "У вас пока нет избранных сервисов"
```

Custom emoji (в HTML через parse_mode=HTML):
- Добавить: `<tg-emoji emoji-id="5296348778012361146">🏷</tg-emoji>`
- Удалить:  `<tg-emoji emoji-id="5296678515536581003">🏷</tg-emoji>`

---

## Шаг 3: States — новый state (states.py)

```python
class ServiceMenu(StatesGroup):
    select_service = State()   # уже есть
    service_info = State()     # уже есть
    enter_service = State()    # уже есть
    enter_service_error = State()  # уже есть
    not_enough_balance = State()   # уже есть
    favorites = State()            # НОВЫЙ — окно избранных сервисов
```

---

## Шаг 4: Окно ИЗБРАННЫХ СЕРВИСОВ (windows.py)

Новая функция `select_favorites_window()`:
```python
Window(
    Const("⭐ Избранные сервисы"),
    ScrollingGroup(
        Select(
            Format("{item[name]}"),
            id="favorites_select",
            item_id_getter=operator.itemgetter("code"),
            items="services",
            on_click=on_select_service,   # тот же хендлер что и в основном окне
        ),
        id="favorites_scroll",
        width=2,
        height=5,
    ),
    Button(Const("← К списку сервисов"), id="back_to_services", on_click=on_back_to_services),
    state=states.ServiceMenu.favorites,
    getter=get_favorites,   # отдельный getter — только избранные
)
```

---

## Шаг 5: Изменения в окне выбора СЕРВИСА (windows.py)

В `select_service_window()` добавить кнопку "🏷 Избранные сервисы" перед ScrollingGroup:
```python
Button(
    Const(bt.FAVORITES_SERVICES_BTN),
    id="show_favorites",
    on_click=on_show_favorites,
),
ScrollingGroup(...)    # без изменений, telegram просто уйдёт на 2-ю стр.
Button(Const(bt.SEARCH_SERVICE_BTN), ...)
Button(Const(bt.BACK_BTN), ...)
```

---

## Шаг 6: Изменения в окне выбора СТРАНЫ (windows.py)

В `select_country_window()` добавить динамическую кнопку ДО кнопки поиска:
```python
Button(
    Format("{favorite_btn_text}"),
    id="toggle_favorite",
    on_click=on_toggle_favorite,
    when="show_favorite_btn",
),
Button(Const(bt.SEARCH_COUNTRY_BTN), id="search_country", on_click=on_search_country),
```

---

## Шаг 7: Getter — get_favorites() (getters.py)

Новая функция для окна избранных:
```python
async def get_favorites(dialog_manager: DialogManager, **middleware_data):
    user = middleware_data.get('user')  # или через telegram_id из middleware
    favs = await models.FavoriteService.filter(user=user)\
        .order_by('created_at')\
        .values('service_code', 'service_name')
    return {
        "services": [{"code": f["service_code"], "name": f["service_name"]} for f in favs]
    }
```

---

## Шаг 8: Getter — get_services_2() (getters.py)

Единственное изменение — убрать `telegram` из priority_codes:
```python
# Было:
priority_codes = {"telegram", "google", "vkcom", "whatsapp"}
# Стало:
priority_codes = {"google", "vkcom", "whatsapp"}
```
Telegram сам попадёт на 2-ю страницу по алфавитному/дефолтному порядку из БД.

---

## Шаг 9: Getter — get_countries() (getters.py)

Добавить в возвращаемые данные (получить выбранный сервис и проверить избранное):
```python
selected_code = dialog_manager.dialog_data.get("selected_service_code")
selected_name = dialog_manager.dialog_data.get("selected_service_name")

is_fav = False
if selected_code:
    user = middleware_data.get('user')
    is_fav = await models.FavoriteService.filter(
        user=user, service_code=selected_code
    ).exists()

return {
    ...существующие данные...,
    "show_favorite_btn": bool(selected_code),
    "favorite_btn_text": (
        bt.REMOVE_FROM_FAVORITES_BTN.format(name=selected_name) if is_fav
        else bt.ADD_TO_FAVORITES_BTN.format(name=selected_name)
    ),
}
```

---

## Шаг 10: Хендлеры (selected.py)

### on_show_favorites() — кнопка "🏷 Избранные сервисы"
```python
async def on_show_favorites(c: CallbackQuery, button: Button, manager: DialogManager):
    user = ...  # из middleware
    has_favs = await models.FavoriteService.filter(user=user).exists()
    if not has_favs:
        await c.answer(bt.NO_FAVORITES_POPUP, show_alert=True)
        return
    await manager.switch_to(ServiceMenu.favorites)
```

### on_back_to_services() — кнопка "← К списку сервисов"
```python
async def on_back_to_services(c: CallbackQuery, button: Button, manager: DialogManager):
    await manager.switch_to(ServiceMenu.select_service)
```

### on_toggle_favorite() — добавить/удалить из избранного (из окна стран)
```python
async def on_toggle_favorite(c: CallbackQuery, button: Button, manager: DialogManager):
    user = ...
    code = manager.dialog_data.get("selected_service_code")
    name = manager.dialog_data.get("selected_service_name")

    fav = await models.FavoriteService.filter(user=user, service_code=code).first()
    if fav:
        await fav.delete()
        await c.answer(f"'{name}' удалён из избранных")
    else:
        await models.FavoriteService.create(user=user, service_code=code, service_name=name)
        await c.answer(f"'{name}' добавлен в избранное")

    await manager.update({})   # перерендерить окно стран — кнопка сменит текст
```

### Изменить on_select_service() — сохранять выбранный сервис перед переходом
```python
async def on_select_service(c, widget, manager, code):
    # Найти название из текущих данных окна
    services = (await manager.find("services_select").get_value()) or []
    name = next((s["name"] for s in services if s["code"] == code), code)
    manager.dialog_data["selected_service_code"] = code
    manager.dialog_data["selected_service_name"] = name
    await send_country_info(code, c, manager)
```
> Примечание: точный способ достать items зависит от версии aiogram_dialog. Альтернатива — брать название из БД по code прямым запросом.

---

## Шаг 11: Подключить новое окно в Dialog (__init__.py)

```python
def select_services_dialogs():
    return [
        Dialog(
            windows.select_service_window(),
            windows.select_favorites_window(),   # НОВОЕ
            windows.enter_service_window(),
            windows.enter_service_error_window(),
        )
    ]
```

---

## Верификация

1. SQL создаёт таблицу без ошибок
2. Кнопка "🏷 Избранные сервисы" видна в начале (перед ScrollingGroup сервисов)
3. Telegram попал на 2-ю страницу (позиция 11+)
4. При пустых избранных — popup "У вас пока нет избранных сервисов"
5. При непустых избранных → открывается окно с их списком + кнопка "← К списку сервисов"
6. "← К списку сервисов" возвращает к полному списку сервисов
7. Клик на избранный сервис → нормально переходит на выбор страны
8. В окне стран — кнопка "🏷 Добавить [сервис] в избранное" видна
9. После нажатия — текст меняется на "🏷 Удалить [сервис] из избранных"
10. Удалённый сервис пропадает из окна избранных
