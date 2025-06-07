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
from app.services.bot_texts import country_flags, sort_countries, SERVICES_TRANSLATION, \
    REVERSE_SERVICES_TRANSLATION, NUMBER_REQUEST_SENT, PLEASE_WAIT_SECONDS, DOLLAR_ONLINESIM, DOLLAR_SMS_ACTIVATE, \
    SMS_ACTIVATE_SERVICE_CODES_AT_ONLINESIM, NOT_NUMBERS_ALERT, list_for_sorting_countries_for_telegram
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.onlinesim.get_tariffs import fetch_tariffs
from app.services.sms_receive import SmsReceive
from app.services import bot_texts as bt


@logger.catch()
async def on_select_service(c: types.CallbackQuery, widget: Select, manager: DialogManager, code: str):
    """
    Обрабатывает выбор сервиса пользователем и отправляет информацию о сервисе.
    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Select от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param code: Код выбранного сервиса.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_select_service').log(
            "USER_ACTION",
            f"Выбран сервис: {code}"
        )
        await send_country_info(code, c, manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_select_service: {e}")


@logger.catch()
async def on_search_service(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обрабатывает нажатие кнопки поиска сервиса и переводит на меню ввода сервиса.
    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Button от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_search_service').log(
            "USER_ACTION",
            "Переход к поиску сервиса"
        )
        await manager.switch_to(ServiceMenu.enter_service)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_search_service: {e}")


@logger.catch()
async def on_result_service(m: types.Message, widget: TextInput, manager: DialogManager, service_name: str):
    """
    Обрабатывает результат поиска сервиса по введенному названию.
    :param m: Объект Message от aiogram.
    :param widget: Виджет TextInput от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param service_name: Название сервиса, введенное пользователем.
    """
    try:
        user_id = m.from_user.id
        logger.bind(user_id=user_id, action='on_result_service').log(
            "USER_ACTION",
            f"Поиск сервиса: {service_name}"
        )

        if await service_is_smsactivate():
            services = await models.ServicesSmsActivate.search_service(service_name.lower())
        else:
            services = await models.ServicesOnlinesim.search_service(service_name.lower())

        if not services:
            logger.bind(user_id=user_id, action='on_result_service').log(
                "USER_ACTION",
                "Сервис не найден"
            )
            await manager.switch_to(ServiceMenu.enter_service_error)
            return

        services_data = [{'code': service.code, 'name': service.name} for service in services]
        ctx = manager.current_context()
        ctx.dialog_data["services"] = {'services': services_data}

        logger.bind(user_id=user_id, action='on_result_service').log(
            "USER_ACTION",
            f"Найдено сервисов: {len(services)}"
        )
        await manager.switch_to(ServiceMenu.select_service)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_result_service: {e}")


@logger.catch()
async def on_select_country_new(c: types.CallbackQuery, widget: Select, manager: DialogManager, country_index: str):
    """
    Обрабатывает выбор страны и сервиса.
    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Select от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param country_index: Индекс выбранной страны.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_select_country_new').log(
            "USER_ACTION",
            f"Выбор страны по индексу: {country_index}"
        )

        ctx = manager.current_context()
        service_code = ctx.start_data.get('service_code')
        countries_with_prices = ctx.start_data.get('countries_with_prices', [])
        country_index = int(country_index)

        if 0 <= country_index < len(countries_with_prices):
            selected_country = countries_with_prices[country_index]
            country_name = selected_country['country']
            price = selected_country['price']
            free_price_map = selected_country.get('freePriceMap')
            if free_price_map is None:
                country_id = await models.CountriesOnlinesim.get_country_id_by_name(country_name)
                if await service_is_smsactivate():
                    service_code = SERVICES_TRANSLATION[service_code]
            else:
                country_id = await models.CountriesSmsActivate.get_country_id_by_name(country_name)

            retail_price = selected_country.get('retail_price')

            logger.bind(user_id=user_id, action='on_select_country_new').log(
                "USER_ACTION",
                f"Выбрана страна: {country_name}, цена: {price}, сервис: {service_code}"
            )

            await send_service_on_country(
                country_id=country_id,
                service_code=service_code,
                price=price,
                retail_price=retail_price,
                free_price_map=free_price_map,
                c=c,
                manager=manager
            )
        else:
            logger.bind(user_id=user_id, action='on_select_country_new').log(
                "USER_ACTION",
                f"Неверный индекс страны: {country_index}"
            )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_select_country_new: {e}")


@logger.catch()
async def on_search_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обрабатывает нажатие кнопки поиска страны и переводит на меню ввода страны.
    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Button от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_search_country').log(
            "USER_ACTION",
            "Переход к поиску страны"
        )
        await manager.switch_to(CountryMenu.enter_country)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_search_country: {e}")


@logger.catch()
async def on_result_country(m: types.Message, widget: TextInput, manager: DialogManager, country_name: str):
    """
    Обрабатывает результат поиска страны по введенному названию.
    :param m: Объект Message от aiogram.
    :param widget: Виджет TextInput от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param country_name: Название страны, введенное пользователем.
    """
    try:
        user_id = m.from_user.id
        logger.bind(user_id=user_id, action='on_result_country').log(
            "USER_ACTION",
            f"Поиск страны: {country_name}"
        )

        country_names = await models.CountriesSmsActivate.search_countries(country_name.lower())
        if len(country_names) == 0:
            logger.bind(user_id=user_id, action='on_result_country').log(
                "USER_ACTION",
                "Страна не найдена"
            )
            await manager.switch_to(CountryMenu.enter_country_error)
            return

        ctx = manager.current_context()
        ctx.dialog_data['search_name'] = country_names[0]
        await manager.switch_to(CountryMenu.select_country)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_result_country: {e}")


@logger.catch()
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
    try:
        await c.answer()
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='send_service_on_country').log(
            "USER_ACTION",
            f"Запрос на активацию сервиса: страна={country_id}, сервис={service_code}, цена={price}"
        )

        # Получаем информацию о пользователе
        user = await models.User.get_user(user_id)

        # Проверяем, прошло ли 10 секунд с последнего запроса
        if user.last_request_time is not None and (
                datetime.now(pytz.utc) - user.last_request_time.astimezone(pytz.utc)).total_seconds() < 5:
            await c.answer(text=PLEASE_WAIT_SECONDS, show_alert=True)
            return

        current_time = datetime.now(pytz.timezone('Europe/Moscow'))
        user.last_request_time = current_time.astimezone(pytz.utc)
        await user.save(update_fields=['last_request_time'])

        # если onlinesim
        if not await service_is_smsactivate():
            tariffs = await fetch_tariffs(country_id, service_code)
            if tariffs is None:
                logger.bind(user_id=user_id, action='send_service_on_country').log(
                    "USER_ACTION",
                    "Нет доступных номеров OnlineSim"
                )
                await c.message.answer(text=NOT_NUMBERS_ALERT)
                return

        # Проверяем, достаточно ли средств на балансе
        if user.balance < price:
            missing_amount = max(price - user.balance, 50.0) if user.balance < price else 0.0
            manager.current_context().dialog_data.update({
                'country_id': country_id,
                'service_code': service_code,
                'retail_price': retail_price,
                'service_price': price,
                'price': missing_amount
            })
            from app.dialogs.personal_cabinet.selected import send_payment_keyboard
            await send_payment_keyboard(m=c, manager=manager, price=missing_amount)
            return

        sent_message = await c.message.answer(text=NUMBER_REQUEST_SENT)
        sent_message_id = sent_message.message_id

        if service_code not in SMS_ACTIVATE_SERVICE_CODES_AT_ONLINESIM:
            client = OnlineSMS(api_key=API_KEY_ONLINESIM)
            try:
                logger.bind(user_id=user_id, action='send_service_on_country').log(
                    "USER_ACTION",
                    f"OnlineSim"
                )
                order_number_response = await client.order_number(service=service_code, country=country_id)
                activation_id = order_number_response.get('tzid')
                phone_number = (await client.get_order_info(operation_id=activation_id))[0].get('number').lstrip('+')
                country = await models.CountriesOnlinesim.get_country_from_country_by_id(country_id=country_id)
                if await service_is_smsactivate():
                    key = REVERSE_SERVICES_TRANSLATION.get(service_code)
                    service = await models.ServicesSmsActivate.get_service(code=key)
                else:
                    service = await models.ServicesOnlinesim.get_service(code=service_code)
            except Exception as e:
                await c.message.answer(text=bt.NOT_NUMBERS_ALERT)
                error_message = str(e)
                logger.bind(user_id=user_id, action='send_service_on_country').log(
                    "USER_ACTION",
                    f"Ошибка при получении номера сервиса OnlineSim: {error_message}"
                )
                if "No available numbers for this service" in error_message:
                    await manager.switch_to(CountryMenu.select_country)
                    return
                if "Not enough funds" in error_message:
                    for admin_id in ADMINS:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=(
                                "🚨 *Внимание, администратор!*\n"
                                "❌ На сервисе *OnlineSim* недостаточно средств для выполнения операции.\n"
                                f"📅 *Время*: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"💬 *Описание ошибки*: {error_message}"
                            ),
                            parse_mode="Markdown"
                        )
                await manager.switch_to(CountryMenu.select_country)
                return
        else:
            sms = SmsReceive()
            max_price = math.ceil(retail_price)
            phone_number_data = await sms.get_phone_number(country_id=country_id, service_code=service_code,
                                                           max_price=max_price)
            logger.bind(user_id=user_id, action='send_service_on_country').log(
                "USER_ACTION",
                f"SMSActivate"
            )
            if 'activationId' not in phone_number_data:
                max_price = math.ceil(retail_price * 1.05)
                phone_number_data = await sms.get_phone_number(country_id=country_id, service_code=service_code,
                                                             max_price=max_price)
                if 'activationId' not in phone_number_data:
                    await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
                    await manager.switch_to(CountryMenu.select_country)
                    return

            activation_id = int(phone_number_data['activationId'])
            phone_number = phone_number_data['phoneNumber']
            country = await models.CountriesSmsActivate.get_country_by_id(country_id=country_id)
            service = await models.ServicesSmsActivate.get_service(code=service_code)

        if await service_is_smsactivate() or service_code in SMS_ACTIVATE_SERVICE_CODES_AT_ONLINESIM:
            activation = await models.Activation.add_activation_sms_activate(
                user=user,
                activation_id=activation_id,
                country=country,
                service=service,
                cost=price,
                phone_number=phone_number,
                activation_expire_at=datetime.now(pytz.timezone("Europe/Moscow")).replace(microsecond=0) + timedelta(minutes=10)
            )
            service = activation.service.name
        else:
            activation = await models.Activation.add_activation_onlinesim(
                user=user,
                activation_id=activation_id,
                country=country,
                service_2=service,
                cost=price,
                phone_number=phone_number,
                activation_expire_at=datetime.now(pytz.timezone("Europe/Moscow")).replace(microsecond=0) + timedelta(minutes=10)
            )
            service = activation.service_2.name

        user.balance -= price
        await user.save(update_fields=['balance'])

        await bot.delete_message(chat_id=c.from_user.id, message_id=sent_message_id)
        try:
            country = activation.country.name
        except Exception as e:
            country = None
            logger.opt(exception=e).error(f"country = None")

        await send_service_info_with_keyboard(message=c.message, activation=activation, service=service, country=country)

        low_balance = await check_low_balance(user, price)
        await asyncio.sleep(1)
        if low_balance:
            await send_low_balance_alert(user)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в send_service_on_country: {e}")


@logger.catch()
async def send_service_info_with_keyboard(message: types.Message, activation, service, country):
    """
    Отправляет пользователю информацию о сервисе и номере телефона с клавиатурой.
    :param message: Сообщение для отправки.
    :param activation: Объект активации.
    :param service: Название сервиса.
    :param country: Страна.
    """

    buttons = []

    # Добавляем первую кнопку только если длина activation_id > 9
    if len(str(activation.activation_id)) > 8:
        buttons.append([types.InlineKeyboardButton(
            text=bt.RECEIVE_ANOTHER_SMS_TO_NUMBER,
            callback_data=f"request_code:{activation.id}"
        )])

    # Вторая и третья кнопки всегда добавляются
    buttons.append([types.InlineKeyboardButton(
        text=bt.CANCEL_SERVICE_BTN,
        callback_data=f"cancel_service:{activation.id}"
    )])
    buttons.append([types.InlineKeyboardButton(
        text=bt.REQUEST_ANOTHER_CODE,
        callback_data="receive_sms_for_another_service"
    )])

    mk = types.InlineKeyboardMarkup(inline_keyboard=buttons)
    country = country.strip()
    flag = country_flags.get(country, "")
    flag_and_country = f"{flag} {country}"

    if service != "Telegram":
        await message.answer(
            text=bt.SERVICE_INFO.format(
                country=flag_and_country,
                service=service,
                phone=activation.phone_number,
            ),
            reply_markup=mk
        )
    else:
        await message.answer(
            text=bt.SERVICE_INFO_TELEGRAM.format(
                country=flag_and_country,
                service=service,
                phone=activation.phone_number,
            ),
            reply_markup=mk
        )

    user_id = message.from_user.id
    logger.bind(user_id=user_id, action='send_service_info_with_keyboard').log(
        "USER_ACTION",
        f"Ваш номер: {activation.phone_number}"
    )


@logger.catch()
async def send_country_info(service_code: str, c: types.CallbackQuery, manager: DialogManager = None):
    """
    Отправляет информацию о сервисе пользователю и обрабатывает активацию номера.
    :param service_code: Код сервиса.
    :param c: Объект CallbackQuery от aiogram.
    :param manager: Менеджер диалогов от aiogram_dialog (опционально).
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='send_country_info').log(
            "USER_ACTION",
            f"Запрос информации о сервисе: {service_code}"
        )

        if await service_is_smsactivate():
            if service_code in SERVICES_TRANSLATION:
                code_onlinesim = SERVICES_TRANSLATION.get(service_code)
                services = await PriceOnlinesim.get_service_data(code_onlinesim)
                sorted_countries_with_prices = [
                    {
                        "country": country,
                        "price": math.ceil(float(price) * DOLLAR_ONLINESIM),
                        "retail_price": int(price),
                        "freePriceMap": None
                    }
                    for country, price in services.items()
                ]
            else:
                sms = SmsReceive()
                services = await sms.get_top_country(service=service_code)
                if not services:
                    logger.bind(user_id=user_id, action='send_country_info').log(
                        "USER_ACTION",
                        f"Сервис не найден: {service_code}"
                    )
                    await c.answer("Извините, информация о сервисе недоступна.")
                    return

                filtered_services = services
                countries_with_prices = [
                    {
                        "country": service["country"],
                        "price": math.ceil(float(service["retail_price"]) * DOLLAR_SMS_ACTIVATE),
                        "retail_price": service.get("retail_price"),
                        "freePriceMap": service.get("freePriceMap")
                    }
                    for service in filtered_services.values()
                ]

                country_name_mapping = await models.CountriesSmsActivate.get_country_name_mapping()
                for country in countries_with_prices:
                    country_id = int(country["country"])
                    country["country"] = country_name_mapping.get(country_id, "Unknown Country")

                sorted_countries_with_prices = sort_countries_by_dict(countries_with_prices)

            await manager.start(CountryMenu.select_country, mode=StartMode.NORMAL,
                                data={"countries_with_prices": sorted_countries_with_prices,
                                      "service_code": service_code})
        else:
            if service_code not in SMS_ACTIVATE_SERVICE_CODES_AT_ONLINESIM:
                services = await PriceOnlinesim.get_service_data(service_code)

                sorted_countries_with_prices = [
                    {
                        "country": country,
                        "price": math.ceil(float(price) * DOLLAR_ONLINESIM),
                        "retail_price": int(price),
                        "freePriceMap": None
                    }
                    for country, price in services.items()
                ]
                if service_code == 'vkcom':
                    sorted_countries_with_prices = move_russia_first(sorted_countries_with_prices)
            else:
                sms = SmsReceive()
                services = await sms.get_top_country(service=service_code)
                if not services:
                    logger.bind(user_id=user_id, action='send_country_info').log(
                        "USER_ACTION",
                        f"Сервис не найден: {service_code}"
                    )
                    await c.answer("Извините, информация о сервисе недоступна в данный момент.")
                    return

                filtered_services = services
                countries_with_prices = [
                    {
                        "country": service["country"],
                        "price": math.ceil(float(service["retail_price"]) * DOLLAR_SMS_ACTIVATE),
                        "retail_price": service.get("retail_price"),
                        "freePriceMap": service.get("freePriceMap")
                    }
                    for service in filtered_services.values()
                ]

                country_name_mapping = await models.CountriesSmsActivate.get_country_name_mapping()
                for country in countries_with_prices:
                    country_id = int(country["country"])
                    country["country"] = country_name_mapping.get(country_id, "Unknown Country")

                sorted_countries_with_prices = sort_countries_by_dict(countries_with_prices)

            if service_code == 'telegram':
                sorted_countries_with_prices = await sort_countries_tg(sorted_countries_with_prices, list_for_sorting_countries_for_telegram)

            await manager.start(CountryMenu.select_country, mode=StartMode.NORMAL,
                                data={"countries_with_prices": sorted_countries_with_prices,
                                      "service_code": service_code})
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в send_country_info: {e}")

async def back_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.switch_to(CountryMenu.select_country)

def move_russia_first(country_list):
    for i, country_data in enumerate(country_list):
        if country_data.get("country") == "Россия":
            russia_entry = country_list.pop(i)
            country_list.insert(0, russia_entry)
            break
    return country_list


def sort_countries_by_dict(countries_with_prices):
    """
    Сортирует список стран с ценами в соответствии с порядком в countries_dict
    Args:
        countries_with_prices (list): Список словарей с информацией о странах и ценах
    Returns:
        list: Отсортированный список стран с ценами
    """
    order_dict = {country_id: position for position, country_id in enumerate(sort_countries.keys())}
    sorted_countries = sorted(
        countries_with_prices,
        key=lambda x: order_dict.get(x['country'], float('inf'))
    )
    return sorted_countries


def country_key(country_dict, priority_list):
    try:
        return (priority_list.index(country_dict["country"]),)
    except ValueError:
        return (len(priority_list),)


@logger.catch()
async def sort_countries_tg(data, priority_list):
    return sorted(data, key=lambda x: country_key(x, priority_list))