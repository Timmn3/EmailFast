from datetime import datetime, timedelta
import pytz
import math
import asyncio
from loguru import logger
from aiogram import types
from aiogram_dialog import DialogManager, StartMode
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Select, Button
from pyonlinesim import OnlineSMS
from app.db import models
from app.db.models import PriceOnlinesim
from app.dependencies import API_KEY_ONLINESIM, ADMINS, bot
from app.dialogs.receive_sms.getters import service_is_smsactivate
from app.dialogs.receive_sms.states import ServiceMenu, CountryMenu
from app.dialogs.rent_sms.states import RentCountryMenu
from app.services.bot_texts import INTEREST, country_flags, sort_countries, SERVICES_TRANSLATION, \
    REVERSE_SERVICES_TRANSLATION, NUMBER_REQUEST_SENT, PLEASE_WAIT_SECONDS, DOLLAR_ONLINESIM, DOLLAR_SMS_ACTIVATE
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.sms_receive import SmsReceive
from app.services import bot_texts as bt


# Функция для обработки выбора сервиса
async def on_select_service(c: types.CallbackQuery, widget: Select, manager: DialogManager, code: str):
    """
    Обрабатывает выбор сервиса пользователем и отправляет информацию о сервисе.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Select от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param code: Код выбранного сервиса.
    """
    # await manager.start(ServiceMenu.select_service, data={"code": code})
    await send_country_info(code, c, manager)


# Функция для обработки нажатия кнопки поиска сервиса
async def on_search_service(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обрабатывает нажатие кнопки поиска сервиса и переводит на меню ввода сервиса.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Button от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    """
    await manager.switch_to(ServiceMenu.enter_service)


# Функция для обработки результата поиска сервиса
async def on_result_service(m: types.Message, widget: TextInput, manager: DialogManager, service_name: str):
    """
    Обрабатывает результат поиска сервиса по введенному названию.

    :param m: Объект Message от aiogram.
    :param widget: Виджет TextInput от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param service_name: Название сервиса, введенное пользователем.
    """
    services = await models.ServicesSmsActivate.search_service(service_name.lower())

    if not services:
        await manager.switch_to(ServiceMenu.enter_service_error)
        return

    # Формируем список сервисов для контекста диалога
    services_data = [{'code': service.code, 'name': service.name} for service in services]

    ctx = manager.current_context()
    ctx.dialog_data["services"] = {'services': services_data}
    await manager.switch_to(ServiceMenu.select_service)


# Функция для обработки выбора страны
async def on_select_country_new(c: types.CallbackQuery, widget: Select, manager: DialogManager, country_index: str):
    """
    Обрабатывает выбор страны и сервиса.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Select от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param country_index: Индекс выбранной страны.
    """
    # Извлекаем service_code из текущего контекста
    ctx = manager.current_context()
    service_code = ctx.start_data.get('service_code')

    # Извлекаем список стран с ценами из контекста
    countries_with_prices = ctx.start_data.get('countries_with_prices', [])

    # Преобразуем индекс в целое число
    country_index = int(country_index)

    # Проверяем, что индекс находится в пределах списка
    if 0 <= country_index < len(countries_with_prices):
        selected_country = countries_with_prices[country_index]
        country_name = selected_country['country']
        price = selected_country['price']
        free_price_map = selected_country.get('freePriceMap')
        if free_price_map is None:
            country_id = await models.CountriesOnlinesim.get_country_id_by_name(country_name)
            service_code = SERVICES_TRANSLATION[service_code]
        else:
            country_id = await models.CountriesSmsActivate.get_country_id_by_name(country_name)
        retail_price = selected_country.get('retail_price')
        await send_service_on_country(country_id=country_id, service_code=service_code, price=price,
                                      retail_price=retail_price, free_price_map=free_price_map, c=c, manager=manager)

    else:
        print(f"Country with index {country_index} not found.")


# Функция для обработки нажатия кнопки поиска страны
async def on_search_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обрабатывает нажатие кнопки поиска страны и переводит на меню ввода страны.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Button от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    """
    await manager.switch_to(CountryMenu.enter_country)


# Функция для обработки результата поиска страны
async def on_result_country(m: types.Message, widget: TextInput, manager: DialogManager, country_name: str):
    """
    Обрабатывает результат поиска страны по введенному названию.

    :param m: Объект Message от aiogram.
    :param widget: Виджет TextInput от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param country_name: Название страны, введенное пользователем.
    """
    country_names = await models.CountriesSmsActivate.search_countries(country_name.lower())
    if len(country_names) == 0:
        await manager.switch_to(CountryMenu.enter_country_error)
        return

    ctx = manager.current_context()
    ctx.dialog_data['search_name'] = country_names[0]
    await manager.switch_to(CountryMenu.select_country)



# Функция для отправки информации о сервисе
async def send_service_on_country(country_id: int, service_code: str, price: float, retail_price, free_price_map,
                                  c: types.CallbackQuery, manager: DialogManager = None):
    """
    Отправляет информацию о сервисе пользователю и обрабатывает активацию номера.

    :param country_id: Идентификатор страны.
    :param service_code: Код сервиса.
    :param price: Цена для клиента.
    :param retail_price: Цена сервиса.
    :param free_price_map: Дополнительные цены.
    :param c: Объект CallbackQuery от aiogram.
    :param manager: Менеджер диалогов от aiogram_dialog (опционально).
    """

    # Получаем информацию о пользователе
    user = await models.User.get_user(c.from_user.id)

    # Проверяем, прошло ли 10 секунд с последнего запроса
    if user.last_request_time is not None and (
            datetime.now(pytz.utc) - user.last_request_time.astimezone(pytz.utc)).total_seconds() < 10:
        await c.answer(text=PLEASE_WAIT_SECONDS, show_alert=True)
        return

    # Получаем текущее время в московском часовом поясе
    current_time = datetime.now(pytz.timezone('Europe/Moscow'))
    # Обновляем время последнего запроса
    user.last_request_time = current_time.astimezone(pytz.utc)
    await user.save(update_fields=['last_request_time'])

    # Проверяем, достаточно ли у пользователя средств на балансе
    if user.balance < price:
        manager.current_context().dialog_data.update({'country_id': country_id, 'service_code': service_code,
                                                      'service_price': price})
        await manager.switch_to(CountryMenu.deposit)
        return

    await c.message.answer(text=NUMBER_REQUEST_SENT)

    if free_price_map is None:
        client = OnlineSMS(api_key=API_KEY_ONLINESIM)
        try:
            # Отправляем запрос на получение номера заказа с указанием сервиса и страны
            order_number_response = await client.order_number(service=service_code, country=country_id)
            # Извлекаем уникальный идентификатор активации activation_id из ответа
            activation_id = order_number_response.get('tzid')
            # Получаем информацию о заказе по идентификатору активации и извлекаем номер телефона
            phone_number = (await client.get_order_info(operation_id=activation_id))[0].get('number').lstrip('+')
            # Получаем объект страны из модели Country по country_id из CountryOnlinesim
            country = await models.CountriesOnlinesim.get_country_from_country_by_id(country_id=country_id)
            # Получаем название сервиса таблицы services из price_onlinesim
            # (т.е. в таблице price_onlinesim "telegram" а в services "tg")
            key = REVERSE_SERVICES_TRANSLATION.get(service_code)
            # Ищем объект сервиса в базе данных по ключу
            service = await models.ServicesSmsActivate.get_service(code=key)

        except Exception as e:
            error_message = str(e)
            if "No available numbers for this service" in error_message:
                await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
                await manager.switch_to(CountryMenu.select_country)
                return
            if "Not enough funds" in error_message:
                for admin_id in ADMINS:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=(
                            "🚨 *Внимание, администратор!*\n\n"
                            "❌ На сервисе *OnlineSim* недостаточно средств для выполнения операции.\n"
                            "📅 *Время*: {time}\n"
                            "💬 *Описание ошибки*: {error}"
                        ).format(
                            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            error=error_message
                        ),
                        parse_mode="Markdown"
                    )
            logger.warning(e)
            await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
            await manager.switch_to(CountryMenu.select_country)
            return
    else:
        sms = SmsReceive()

        # Получаем номер телефона для активации
        max_price = math.ceil(retail_price)  # Округляем в большую сторону
        phone_number_data = await sms.get_phone_number(country_id=country_id, service_code=service_code,
                                                       max_price=max_price)

        # Если не удалось получить номер телефона, смотрим можно ли получить за доп плату
        if 'activationId' not in phone_number_data:
            try:
                # увеличиваем максимальный прайс на несколько процентов
                max_price = math.ceil(retail_price * 1.05)
                phone_number_data = await sms.get_phone_number(country_id=country_id, service_code=service_code,
                                                               max_price=max_price)

                if 'activationId' not in phone_number_data:
                    try:
                        await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
                    except Exception:
                        pass
                    await manager.switch_to(CountryMenu.select_country)
                    return

            except Exception as e:
                # logger.warning(f'Не удалось получить номер телефона: {e}')
                await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
                await manager.switch_to(CountryMenu.select_country)
                return

        # Извлекаем данные активации
        activation_id = int(phone_number_data['activationId'])
        phone_number = phone_number_data['phoneNumber']
        # Получаем информацию о стране и сервисе из базы данных
        country = await models.CountriesSmsActivate.get_country_by_id(country_id=country_id)
        service = await models.ServicesSmsActivate.get_service(code=service_code)

    # Добавляем запись об активации в базу данных
    activation = await models.Activation.add_activation(
        user=user,
        activation_id=activation_id,
        country=country,
        service=service,
        cost=price,
        phone_number=phone_number,
        activation_expire_at=datetime.now(pytz.timezone("Europe/Moscow")).replace(microsecond=0) + timedelta(minutes=10)
    )
    service = activation.service.name
    country = activation.country.name
    # Отправляет пользователю информацию о сервисе и номере телефона с клавиатурой
    await send_service_info_with_keyboard(message=c.message, activation=activation, service=service, country=country)

    # Списываем средства с баланса пользователя
    user.balance -= price
    await user.save(update_fields=['balance'])

    # Проверяем, низкий ли баланс у пользователя после списания средств
    low_balance = await check_low_balance(user, price)
    # Ждем 2 секунды перед отправкой уведомления о низком балансе, если это необходимо
    await asyncio.sleep(1)
    if low_balance:
        await send_low_balance_alert(user)


async def send_service_info_with_keyboard(message: types.Message, activation, service, country):
    """
    Отправляет пользователю информацию о сервисе и номере телефона с клавиатурой.

    :param country: Страна
    :param message: Сообщение для отправки.
    :param activation: Объект активации.
    :param service: Название сервиса.
    """
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=bt.RECEIVE_ANOTHER_SMS_TO_NUMBER,
                                        callback_data=f"request_code:{activation.id}")],
            [types.InlineKeyboardButton(text=bt.CANCEL_SERVICE_BTN, callback_data=f"cancel_service:{activation.id}")],
            [types.InlineKeyboardButton(text=bt.REQUEST_ANOTHER_CODE,
                                        callback_data=f"receive_sms_for_another_service:{activation.id}")],
        ]
    )
    # Получаем флаг из словаря
    country = country.strip()
    flag = country_flags.get(country, "")  # Получаем флаг, если страны нет в словаре, возвращается пустая строка
    flag_and_country = f"{flag} {country}"
    await message.answer(
        text=bt.SERVICE_INFO.format(
            country=flag_and_country,
            service=service,
            phone=activation.phone_number,
        ),
        reply_markup=mk
    )


@logger.catch()
# Функция для отправки информации о сервисе
async def send_country_info(service_code: str, c: types.CallbackQuery, manager: DialogManager = None):
    """
    Отправляет информацию о сервисе пользователю и обрабатывает активацию номера.

    :param service_code: Код сервиса.
    :param c: Объект CallbackQuery от aiogram.
    :param manager: Менеджер диалогов от aiogram_dialog (опционально).
    """
    if await service_is_smsactivate():
        if service_code in SERVICES_TRANSLATION:
            # переводим сервисный код в код для onlinesim
            code_onlinesim = SERVICES_TRANSLATION.get(service_code)
            # Получаем список стран для сервиса
            services = await PriceOnlinesim.get_service_data(code_onlinesim)
            sorted_countries_with_prices = [
                {
                    "country": country,
                    "price": math.ceil(float(price) * INTEREST),  # Округляем цену после умножения
                    "retail_price": int(price),
                    "freePriceMap": None
                }
                for country, price in services.items()
            ]

        else:
            # Создаем экземпляр класса для получения SMS
            sms = SmsReceive()
            # Получаем список стран для указанного сервиса
            services = await sms.get_top_country(service=service_code)
            if services is None or not services:
                # logger.warning(f"Сервисы по коду сервиса не найдены: {service_code}")
                await c.answer("Извините, информация о сервисе недоступна в данный момент.")
                return

            # Фильтруем данные, убирая те, у которых count равен 0
            filtered_services = services  # [service for service in services if service['count'] > 0]

            # Извлекаем список стран с ценами, увеличиваем цену на 30% и добавляем `freePriceMap`
            countries_with_prices = [
                {
                    "country": service["country"],
                    "price": math.ceil(float(service["retail_price"]) * DOLLAR_SMS_ACTIVATE),
                    "retail_price": service.get("retail_price"),
                    "freePriceMap": service.get("freePriceMap")
                }
                for service in filtered_services.values()
            ]

            # Сортируем список стран по цене от меньшего к большему
            # sorted_countries_with_prices = sorted(countries_with_prices, key=lambda x: x['price'])

            sorted_countries_with_prices = sort_countries_by_dict(countries_with_prices)

            # Получаем словарь с именами стран
            country_name_mapping = await models.CountriesSmsActivate.get_country_name_mapping()

            for country in sorted_countries_with_prices:
                # Преобразуем идентификатор страны в int
                country_id = int(country["country"])
                country["country"] = country_name_mapping.get(country_id, "Unknown Country")

        # Передача данных через параметр `data`
        await manager.start(CountryMenu.select_country, mode=StartMode.NORMAL,
                            data={"countries_with_prices": sorted_countries_with_prices,
                                  "service_code": service_code})

    else: # если Onlinesim
        if service_code != "ot": # "любой другой"
            services = await PriceOnlinesim.get_service_data(service_code)
            sorted_countries_with_prices = [
                {
                    "country": country,
                    "price": math.ceil(float(price) * INTEREST),  # Округляем цену после умножения
                    "retail_price": int(price),
                    "freePriceMap": None
                }
                for country, price in services.items()
            ]

        else:
            # Создаем экземпляр класса для получения SMS
            sms = SmsReceive()
            # Получаем список стран для указанного сервиса
            services = await sms.get_top_country(service=service_code)
            if services is None or not services:
                # logger.warning(f"Сервисы по коду сервиса не найдены: {service_code}")
                await c.answer("Извините, информация о сервисе недоступна в данный момент.")
                return

            # Фильтруем данные, убирая те, у которых count равен 0
            filtered_services = services  # [service for service in services if service['count'] > 0]

            # Извлекаем список стран с ценами, увеличиваем цену на 30% и добавляем `freePriceMap`
            countries_with_prices = [
                {
                    "country": service["country"],
                    "price": math.ceil(float(service["retail_price"]) * DOLLAR_SMS_ACTIVATE),
                    "retail_price": service.get("retail_price"),
                    "freePriceMap": service.get("freePriceMap")
                }
                for service in filtered_services.values()
            ]

            # Сортируем список стран по цене от меньшего к большему
            # sorted_countries_with_prices = sorted(countries_with_prices, key=lambda x: x['price'])

            sorted_countries_with_prices = sort_countries_by_dict(countries_with_prices)

            # Получаем словарь с именами стран
            country_name_mapping = await models.CountriesSmsActivate.get_country_name_mapping()

            for country in sorted_countries_with_prices:
                # Преобразуем идентификатор страны в int
                country_id = int(country["country"])
                country["country"] = country_name_mapping.get(country_id, "Unknown Country")

        # Передача данных через параметр `data`
        await manager.start(CountryMenu.select_country, mode=StartMode.NORMAL,
                            data={"countries_with_prices": sorted_countries_with_prices,
                                  "service_code": service_code})


async def back_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.switch_to(CountryMenu.select_country)


def sort_countries_by_dict(countries_with_prices):
    """
    Сортирует список стран с ценами в соответствии с порядком в countries_dict

    Args:
        countries_with_prices (list): Список словарей с информацией о странах и ценах

    Returns:
        list: Отсортированный список стран с ценами
    """
    # Создаем словарь, где ключ - это country_id, а значение - позиция в countries_dict
    order_dict = {country_id: position for position, country_id in enumerate(sort_countries.keys())}

    # Сортируем список стран с ценами
    sorted_countries = sorted(
        countries_with_prices,
        key=lambda x: order_dict.get(x['country'], float('inf'))  # если страны нет в словаре, ставим её в конец
    )

    return sorted_countries
