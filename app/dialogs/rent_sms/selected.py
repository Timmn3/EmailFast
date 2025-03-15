from aiogram import types
from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Select, Button
from app.db import models
from app.dialogs.rent_sms.getters import get_day_string
from app.dialogs.rent_sms.states import RentCountryMenu
from app.services.bot_texts import DOLLAR_ONLINESIM, NUMBER_REQUEST_SENT, PLEASE_WAIT_SECONDS, country_flags
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.onlinesim.rent_number import OnlineSimRentAPI
from datetime import datetime, timedelta
import pytz
from app.services import bot_texts as bt
import asyncio
from typing import Optional

# Функция для обработки нажатия кнопки поиска страны
async def rent_on_search_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.switch_to(RentCountryMenu.enter_country)


async def rent_on_deposit(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.switch_to(RentCountryMenu.deposit)


async def rent_back_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.switch_to(RentCountryMenu.select_country)


# Функция для обработки результата поиска страны
async def rent_on_result_country(m: types.Message, widget: TextInput, manager: DialogManager, country_name: str):
    """
    Обрабатывает результат поиска страны по введенному названию.

    :param m: Объект Message от aiogram.
    :param widget: Виджет TextInput от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param country_name: Название страны, введенное пользователем.
    """
    country_names = await models.CountriesOnlinesim.search_countries(country_name.lower())
    if len(country_names) == 0:
        await manager.switch_to(RentCountryMenu.enter_country_error)
        return

    ctx = manager.current_context()
    ctx.dialog_data['search_name'] = country_names[0]
    await manager.switch_to(RentCountryMenu.select_country)


# Функция для обработки нажатия кнопки поиска страны
async def on_search_rent_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обрабатывает нажатие кнопки поиска страны и переводит на меню ввода страны.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Button от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    """
    await manager.switch_to(RentCountryMenu.enter_country)


async def rent_on_select_country_new(c: types.CallbackQuery, widget: Select, manager: DialogManager,
                                     country_index: str):
    """
    Обрабатывает выбор страны для аренды.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Select от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param country_index: Индекс выбранной страны.
    """
    # Получаем список стран из текущего контекста
    rent_countries = manager.dialog_data.get("rent_countries", [])

    # Находим выбранную страну
    selected_country = next((country for country in rent_countries if country["id"] == country_index), None)

    if not selected_country:
        await c.answer("Страна не найдена.", show_alert=True)
        return

    # Извлекаем тарифы для выбранной страны
    tariffs = selected_country["tariffs"].get(country_index, {})

    # Преобразуем тарифы: умножаем цены на DOLLAR_RATE
    updated_tariffs = {days: round(price * DOLLAR_ONLINESIM) for days, price in tariffs.items()}
    # Сохраняем данные выбранной страны и тарифы в dialog_data
    manager.dialog_data["selected_country"] = {
        "rent_country_code": country_index,
        "country": selected_country["country"],
        "tariffs": updated_tariffs,  # Сохраняем только нужные тарифы
    }

    # Переходим к окну с деталями
    await manager.switch_to(RentCountryMenu.country_details)


async def rent_number_in_days(c: types.CallbackQuery, widget: Select, manager: DialogManager, day_index: str,
                              selected_country: Optional[dict] = None):
    """
    Обрабатывает выбор количества дней для аренды или продления аренды номера страны.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Select от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param day_index: Индекс выбранного количества дней.
    :param selected_country: selected_country.
    """
    tzid = None

    if selected_country is None:
        # Получаем данные о выбранной стране
        selected_country = manager.dialog_data.get("selected_country")
        if selected_country is None:
            selected_country = manager.start_data.get("selected_country")
            tzid = selected_country["tzid"]

        if not selected_country:
            await c.answer("Ошибка: данные о стране отсутствуют.", show_alert=True)
            return

    # Преобразуем индекс дней в число
    days = int(day_index.split()[0])

    price = selected_country['tariffs'].get(str(days))
    # Получаем информацию о пользователе
    user = await models.User.get_user(c.from_user.id)
    country_code = selected_country["rent_country_code"]  # Код страны из context

    # Проверяем, достаточно ли у пользователя средств на балансе
    if user.balance < price:

        missing_amount = max(price - user.balance, 50.0) if user.balance < price else 0.0
        manager.current_context().dialog_data.update({'day_index': day_index, 'selected_country': selected_country,
                                                      'rent_country_code': country_code, 'price': missing_amount})
        from app.dialogs.personal_cabinet.selected import send_payment_keyboard
        await send_payment_keyboard(m=c, manager=manager, price=missing_amount)

        # await manager.switch_to(RentCountryMenu.deposit)
        return


    await c.message.answer(text=NUMBER_REQUEST_SENT)

    # Проверяем, прошло ли 10 секунд с последнего запроса
    if user.last_request_time is not None and (
            datetime.now(pytz.utc) - user.last_request_time.astimezone(pytz.utc)).total_seconds() < 5:
        await c.answer(text=PLEASE_WAIT_SECONDS, show_alert=True)
        return

    # Получаем текущее время в московском часовом поясе
    current_time = datetime.now(pytz.timezone('Europe/Moscow'))
    # Обновляем время последнего запроса
    user.last_request_time = current_time.astimezone(pytz.utc)
    await user.save(update_fields=['last_request_time'])
    # Создаем экземпляр API клиента и делаем запрос аренды
    api_client = OnlineSimRentAPI()

    try:
        if tzid is None:
            rent_result = await api_client.rent_number(country=int(country_code), days=days) # Запрос номера
        else:
            rent_result = await api_client.extend_rent_state(tzid=tzid, days=days) # Продление аренды
    except Exception as e:
        await c.answer(f"Ошибка при аренде: {str(e)}", show_alert=True)
        return

    # Выводим результат аренды
    if rent_result is None:
        await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
        return

        # Извлекаем данные активации
    rent_id = int(rent_result.get("tzid", 0))
    phone_number = rent_result.get("number", None)
    country = await models.CountriesOnlinesim.get_country_onlinesim(country_id=country_code)
    minutes = int(rent_result.get("time", 0))

    if phone_number is None:
        await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
        return

    # Добавляем запись об активации в базу данных
    activation = await models.Rent.add_rent(
        user=user,
        rent_id=rent_id,
        country=country,
        cost=price,
        phone_number=f"{country_code}{phone_number}",
        rent_expire_at=datetime.now(pytz.timezone("Europe/Moscow")).replace(microsecond=0) + timedelta(minutes=minutes),
        days=days
    )

    # обновляем количество покупок номеров
    activation.purchase_count += 1
    await activation.save(update_fields=["purchase_count"])

    # Отправляем пользователю сообщение о номере телефона
    await send_message_country_number(message=c.message, activation=activation, country=activation.country.name, days=days)

    # Списываем средства с баланса пользователя
    user.balance -= price
    await user.save(update_fields=['balance'])

    # Проверяем, низкий ли баланс у пользователя после списания средств
    low_balance = await check_low_balance(user, price)
    # Ждем 1 секунду перед отправкой уведомления о низком балансе, если это необходимо
    await asyncio.sleep(1)
    if low_balance:
        await send_low_balance_alert(user)


async def send_message_country_number(message: types.Message, activation, country, days):
    """
    Отправляет пользователю о номер телефона.

    :param country: Страна
    :param message: Сообщение для отправки.
    :param activation: Объект активации.
    :param days: Количество арендованных дней.
    """

    # Получаем флаг из словаря
    country = country.strip()
    flag = country_flags.get(country, "")  # Получаем флаг, если страны нет в словаре, возвращается пустая строка
    flag_and_country = f"{flag} {country}"

    # Сообщение о количестве дней аренды
    days_text = get_day_string(days)
    await message.answer(
        text=bt.RENT_SUCCESS_MESSAGE.format(days=days_text)
    )

    await message.answer(
        text=bt.NUMBER_INFO.format(
            country=flag_and_country,
            phone=activation.phone_number,
        )
    )

