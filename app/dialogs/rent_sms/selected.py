from aiogram import types
from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Select, Button
from app.db import models
from app.dialogs.rent_sms.states import RentCountryMenu
from app.services.bot_texts import DOLLAR_RATE
from app.services.onlinesim.rent_number import OnlineSimRentAPI


# Функция для обработки нажатия кнопки поиска страны
async def rent_on_search_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обрабатывает нажатие кнопки поиска страны и переводит на меню ввода страны.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Button от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    """
    await manager.switch_to(RentCountryMenu.enter_country)


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
    country_names = await models.CountryOnlinesim.search_countries(country_name.lower())
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


async def rent_on_select_country_new(c: types.CallbackQuery, widget: Select, manager: DialogManager, country_index: str):
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
    updated_tariffs = {days: round(price * DOLLAR_RATE) for days, price in tariffs.items()}

    # Сохраняем данные выбранной страны и тарифы в dialog_data
    manager.dialog_data["selected_country"] = {
        "rent_country_code": country_index,
        "country": selected_country["country"],
        "tariffs": updated_tariffs,  # Сохраняем только нужные тарифы
    }

    # Переходим к окну с деталями
    await manager.switch_to(RentCountryMenu.country_details)


async def rent_number_in_days(c: types.CallbackQuery, widget: Select, manager: DialogManager, day_index: str):
    """
    Обрабатывает выбор количества дней для аренды номера страны.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Select от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param day_index: Индекс выбранного количества дней.
    """
    # Получаем данные о выбранной стране
    selected_country = manager.dialog_data.get("selected_country")

    if not selected_country:
        await c.answer("Ошибка: данные о стране отсутствуют.", show_alert=True)
        return

    # Преобразуем индекс дней в число
    days = int(day_index.split()[0])
    country_code = selected_country["rent_country_code"]  # Код страны из context

    # Создаем экземпляр API клиента и делаем запрос аренды
    api_client = OnlineSimRentAPI()
    try:
        rent_result = await api_client.rent_number(country=int(country_code), days=days)
    except Exception as e:
        await c.answer(f"Ошибка при аренде: {str(e)}", show_alert=True)
        return

    # Выводим результат аренды
    if rent_result:
        number = f"+{country_code}{number}" if (number := rent_result.get("number")) else "Неизвестно"
        await c.answer(f"Номер: {number}", show_alert=True)
    else:
        await c.answer("Ошибка: не удалось арендовать номер.", show_alert=True)
