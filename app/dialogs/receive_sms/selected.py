from datetime import datetime, timedelta
import pytz
import math
import asyncio
from loguru import logger
from aiogram import types
from aiogram_dialog import DialogManager, StartMode
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Select, Button
from tortoise.transactions import in_transaction
from app.services.sms_fast.smsfast_client import get_smsfast_client
from pyonlinesim import OnlineSMS
from aiogram.exceptions import TelegramBadRequest

from app.db import models
from app.db.models import PriceOnlinesim
from app.dependencies import API_KEY_ONLINESIM, ADMINS, bot, SMSFAST_SERVICE_MAP, CODER
from app.dialogs.receive_sms.getters import service_is_smsactivate
from app.dialogs.receive_sms.states import ServiceMenu, CountryMenu
from app.dialogs.rent_sms.states import RentCountryMenu
from app.services.bot_texts import country_flags, sort_countries, SERVICES_TRANSLATION, \
    REVERSE_SERVICES_TRANSLATION, NUMBER_REQUEST_SENT, PLEASE_WAIT_SECONDS, DOLLAR_ONLINESIM, DOLLAR_SMS_ACTIVATE, \
    SMS_ACTIVATE_SERVICE_CODES_AT_ONLINESIM, NOT_NUMBERS_ALERT, list_for_sorting_countries_for_telegram, \
    EXCLUDED_COUNTRIES, INTEREST, COUNTRY_PRIORITY_LIST_BY_SERVICE
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.onlinesim.get_tariffs import fetch_tariffs
from app.services.sms_receive import SmsReceive
from app.services import bot_texts as bt

async def _safe_cb_answer(c: types.CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """
    Безопасный ответ на callback.
    Если callback уже протух (часто после оплаты, когда продолжение идет из scheduler),
    не падаем, а при наличии текста пытаемся отправить обычное сообщение.
    """
    try:
        await c.answer(text=text, show_alert=show_alert)
    except TelegramBadRequest:
        # callback "too old" / invalid query id
        if text:
            try:
                if getattr(c, "message", None):
                    await c.message.answer(text)
            except Exception:
                pass

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

        # Убираем из поиска псевдо-сервис "Любой другой"
        services = [
            s for s in services
            if (getattr(s, "name", None) or "").strip() != "Любой другой"
        ]

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
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_select_country_new').log(
            "USER_ACTION", f"Выбор страны по индексу: {country_index}"
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
            # Определяем флаги провайдера
            smsfast_active = (await models.AdminSettings.get_setting_value("sms_rental_service") == "SMS_Fast")
            # Флаг частичного использования SMSFast
            smsfast_enabled_val = await models.AdminSettings.get_setting_value("smsfast_enabled")
            smsfast_enabled = str(smsfast_enabled_val or "").strip().lower() in ("1", "true", "yes", "y", "on", "enable", "enabled")
            use_smsfast = False
            smsfast_service_code = None
            if not smsfast_active and smsfast_enabled:
                # Проверяем, входит ли выбранный сервис в список разрешённых для SMSFast
                if service_code in SMSFAST_SERVICE_MAP:
                    smsfast_service_code = SMSFAST_SERVICE_MAP[service_code]
                elif service_code in SMSFAST_SERVICE_MAP.values():
                    smsfast_service_code = service_code
                if smsfast_service_code:
                    use_smsfast = True
            if smsfast_active or use_smsfast:
                # Используем провайдера SMSFast
                country_obj = await models.CountriesSmsFast.get_or_none(name=country_name)
                if not country_obj:
                    # Страна недоступна у SMSFast
                    await _safe_cb_answer(c, text="⚠️ Эта страна сейчас недоступна у провайдера SMSFast.", show_alert=True)
                    await manager.switch_to(CountryMenu.select_country)
                    return
                country_id = int(country_obj.country_id)
                # Если partial SMSFast режим, сохраняем код сервиса SMSFast для дальнейшего использования
                if use_smsfast and smsfast_service_code:
                    # Заменяем код сервиса на SMSFast-код для дальнейшей обработки
                    service_code = smsfast_service_code
            else:
                # Стандартное поведение: выбор провайдера по free_price_map (None -> OnlineSim, not None -> SMSActivate)
                if free_price_map is None:
                    country_id = await models.CountriesOnlinesim.get_country_id_by_name(country_name)
                    if await service_is_smsactivate():
                        # Если основной провайдер SMSActivate, маппим код сервиса на код для OnlineSim
                        service_code = SERVICES_TRANSLATION.get(service_code, service_code)
                else:
                    country_id = await models.CountriesSmsActivate.get_country_id_by_name(country_name)
            retail_price = selected_country.get('retail_price')
            logger.bind(user_id=user_id, action='on_select_country_new').log(
                "USER_ACTION", f"Выбрана страна: {country_name}, цена: {price}, сервис: {service_code}"
            )
            # Переходим к обработке активации номера с выбранными параметрами
            await send_service_on_country(
                country_id=country_id,
                country_name=country_name,
                service_code=service_code,
                price=price,
                retail_price=retail_price,
                free_price_map=free_price_map,
                c=c,
                manager=manager
            )
        else:
            logger.bind(user_id=user_id, action='on_select_country_new').log(
                "USER_ACTION", f"Неверный индекс страны: {country_index}"
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
async def send_service_on_country(country_id: int, country_name: str, service_code: str, price: float, retail_price, free_price_map,
                                  c: types.CallbackQuery, manager: DialogManager = None):
    """
    Отправляет информацию о сервисе пользователю и обрабатывает активацию номера.
    """
    try:
        user_id = c.from_user.id

        # Функция для безопасного ответа/отправки сообщения
        async def _reply(text: str, **kwargs):
            try:
                if getattr(c, "message", None):
                    return await c.message.answer(text=text, **kwargs)
                return await bot.send_message(chat_id=user_id, text=text, **kwargs)
            except Exception as e:
                logger.bind(user_id=user_id, action='send_service_on_country').warning(
                    f"Не удалось отправить сообщение пользователю: {e}"
                )
                return None

        # Снимаем "часики" с кнопки (если callback уже протух — не уронит логику)
        await _safe_cb_answer(c)

        logger.bind(user_id=user_id, action='send_service_on_country').log(
            "USER_ACTION", f"Запрос на активацию сервиса: страна={country_id}, сервис={service_code}, цена={price}"
        )

        # Получаем информацию о пользователе
        user = await models.User.get_user(user_id)
        if not user:
            logger.bind(user_id=user_id, action='send_service_on_country').warning("Пользователь не найден в базе")
            return

        # Лимитер запросов (не чаще одного запроса за 5 сек)
        if user.last_request_time is not None and (
            datetime.now(pytz.utc) - user.last_request_time.astimezone(pytz.utc)
        ).total_seconds() < 5:
            await _safe_cb_answer(c, text=PLEASE_WAIT_SECONDS, show_alert=True)
            return

        current_time = datetime.now(pytz.timezone('Europe/Moscow'))
        user.last_request_time = current_time.astimezone(pytz.utc)
        await user.save(update_fields=['last_request_time'])

        # Проверяем глобальный флаг основного провайдера
        is_smsactivate = await service_is_smsactivate()

        # Определяем провайдер для выдачи номера (с учётом SMSFast)
        smsfast_flag_val = await models.AdminSettings.get_setting_value("smsfast_enabled")
        smsfast_enabled_flag = str(smsfast_flag_val or "").strip().lower() in (
            "1", "true", "yes", "y", "on", "enable", "enabled"
        )

        smsfast_condition = False
        smsfast_service_code = None
        if smsfast_enabled_flag:
            if service_code in SMSFAST_SERVICE_MAP:
                smsfast_service_code = SMSFAST_SERVICE_MAP[service_code]
            elif service_code in SMSFAST_SERVICE_MAP.values():
                smsfast_service_code = service_code

        # Условие использования SMSFast: либо глобально выбран SMSFast, либо флаг включён и сервис разрешён
        smsfast_active = (await models.AdminSettings.get_setting_value("sms_rental_service") == "SMS_Fast")
        if smsfast_active or (smsfast_enabled_flag and smsfast_service_code):
            smsfast_condition = True

        # Флаги провайдеров
        provider_is_smsfast = smsfast_condition
        provider_is_smsactivate = (free_price_map is not None) and not smsfast_condition

        # Флаг достаточности баланса пользователя
        has_enough_balance = float(user.balance or 0.0) >= float(price)

        # Предварительная проверка наличия номеров у провайдера
        if provider_is_smsfast:
            # Проверяем через кэш цен SMSFast наличие доступных номеров
            has_numbers = True
            try:
                record = await models.PriceSmsFast.get_or_none(
                    country=country_id,
                    service_code=smsfast_service_code or service_code
                )
                if record and record.count is not None:
                    if int(record.count) <= 0:
                        has_numbers = False
            except Exception as e:
                # В случае ошибки кэша пропускаем строгую проверку
                logger.bind(user_id=user_id, action='send_service_on_country').warning(
                    f"Не удалось проверить наличие номеров у SMSFast: {e}"
                )

            if not has_numbers:
                await _reply(text=NOT_NUMBERS_ALERT)
                return

        elif not provider_is_smsactivate:
            # Провайдер OnlineSim:
            # - если у пользователя ХВАТАЕТ баланса, precheck пропускаем и сразу покупаем номер ниже
            # - если баланса НЕ хватает, оставляем precheck как раньше
            if has_enough_balance:
                logger.bind(user_id=user_id, action='send_service_on_country').log(
                    "USER_ACTION",
                    "OnlineSim: precheck пропущен, так как у пользователя достаточно баланса"
                )
            else:
                client_check = OnlineSMS(api_key=API_KEY_ONLINESIM)
                try:
                    await client_check.order_number(service=service_code, country=country_id)
                    logger.bind(user_id=user_id, action='send_service_on_country').log(
                        "USER_ACTION",
                        "OnlineSim: precheck выполнен, номер у провайдера есть"
                    )
                except Exception as e:
                    await _reply(text=NOT_NUMBERS_ALERT)
                    logger.bind(user_id=user_id, action='send_service_on_country').log(
                        "USER_ACTION", f"Нет доступных номеров OnlineSim: {e}"
                    )
                    return

        else:
            # Провайдер SMSActivate – проверяем наличие номеров через API
            sms_client = SmsReceive()
            has_numbers = False
            try:
                services_in_country = await sms_client.get_services_by_country_id(country_id=country_id)
                for svc in services_in_country:
                    if svc.get("code") == service_code and int(svc.get("count", 0)) > 0:
                        has_numbers = True
                        break
            except Exception as e:
                logger.bind(user_id=user_id, action='send_service_on_country').warning(
                    f"Не удалось проверить наличие номеров у SMSActivate: {e}"
                )
                has_numbers = True  # при ошибке считаем, что номера могут быть

            if not has_numbers:
                await _reply(text=NOT_NUMBERS_ALERT)
                return

        # Проверяем баланс пользователя
        if float(user.balance or 0.0) < float(price):
            missing_amount = max(float(price) - float(user.balance or 0.0), 50.0)
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

        # Сообщаем пользователю, что запрос номера отправлен
        sent_message = await _reply(text=NUMBER_REQUEST_SENT)
        sent_message_id = sent_message.message_id if sent_message else None

        activation_id = None
        phone_number = None
        country_obj = None
        service_obj = None

        # === Получение номера от провайдера ===
        if provider_is_smsfast:
            # Заказываем номер через SMSFast
            smsfast_client = get_smsfast_client()
            try:
                logger.bind(user_id=user_id, action='send_service_on_country').log("USER_ACTION", "SMSFast")

                # Определяем код сервиса для SMSFast API
                api_service_code = smsfast_service_code or service_code
                max_price_val = math.ceil(float(retail_price) if retail_price is not None else float(price))
                order_response = await smsfast_client._call(
                    action="getNumber",
                    service=api_service_code,
                    country=country_id
                )

                request_reply = f'service={api_service_code}, country={country_id} Ответ SMSFast {order_response}'
                logger.bind(user_id=user_id, action='send_service_on_country').log("USER_ACTION", request_reply)

                if user_id == CODER:
                    await bot.send_message(
                        chat_id=user_id,
                        text=request_reply
                    )

                # ✅ Если нет денег на балансе SMSFast (может прийти строкой или dict)
                smsfast_error = None
                if isinstance(order_response, dict):
                    smsfast_error = order_response.get("error")
                elif isinstance(order_response, str):
                    smsfast_error = order_response.strip()

                if smsfast_error == "NO_BALANCE":
                    for admin_id in ADMINS:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=(
                                "🚨 *Внимание, администратор!*\n"
                                "❌ На сервисе *SMSFast* недостаточно средств для выполнения операции.\n"
                                f"💬 *Ошибка*: `{smsfast_error}`\n"
                            ),
                            parse_mode="Markdown"
                        )

                    await _reply(text=NOT_NUMBERS_ALERT)
                    await manager.switch_to(CountryMenu.select_country)
                    return

                # Разбираем ответ
                if isinstance(order_response, str):
                    # Ожидаемый формат: "ACCESS_NUMBER:ID:NUMBER"
                    parts = order_response.split(':')
                    if parts and parts[0].startswith("ACCESS") and len(parts) >= 3:
                        activation_id = int(parts[1]) if parts[1].isdigit() else None
                        phone_number = parts[2].lstrip('+') if parts[2] else None
                elif isinstance(order_response, dict):
                    # Ожидаемый формат JSON: {"activationId": ..., "phoneNumber": ...}
                    if order_response.get("activationId"):
                        activation_id = int(order_response["activationId"])
                        phone_number = str(order_response.get("phoneNumber") or order_response.get("number") or "").lstrip('+')

                # Проверяем, удалось ли получить номер
                if not activation_id or not phone_number:
                    await _reply(text=NOT_NUMBERS_ALERT)
                    await manager.switch_to(CountryMenu.select_country)
                    return

                # Маппим страну SMSFast -> объект CountriesSmsActivate для сохранения в активации
                country_obj = await models.map_smsfast_country_to_smsactivate(country_id)
                if not country_obj:
                    # Если не удалось отобразить страну, отменяем активацию у SMSFast и сообщаем об ошибке
                    try:
                        await smsfast_client._call(action="setStatus", id=activation_id, status=8)
                    except Exception as e:
                        logger.warning(f"Не удалось отменить активацию SMSFast #{activation_id}: {e}")

                    await _reply(text=NOT_NUMBERS_ALERT)
                    await manager.switch_to(CountryMenu.select_country)
                    return

                # Определяем объект сервиса для SMSFast (используем таблицу SMSActivate)
                api_code = smsfast_service_code or service_code
                service_obj = await models.ServicesSmsActivate.get_or_none(code=api_code)

            except Exception as e:
                await _reply(text=NOT_NUMBERS_ALERT)
                logger.bind(user_id=user_id, action='send_service_on_country').error(
                    f"Ошибка при получении номера SMSFast: {e}"
                )
                if user_id == CODER:
                    await bot.send_message(
                        chat_id=user_id,
                        text=f'Ошибка: {e}'
                    )
                await manager.switch_to(CountryMenu.select_country)
                return

        elif not provider_is_smsactivate:
            # Заказываем номер через OnlineSim
            client = OnlineSMS(api_key=API_KEY_ONLINESIM)
            try:
                logger.bind(user_id=user_id, action='send_service_on_country').log("USER_ACTION", "OnlineSim")
                order_number_response = await client.order_number(service=service_code, country=country_id)

                try:
                    if user_id == CODER:
                        await bot.send_message(chat_id=user_id, text=f'Ответ OnlineSim: {order_number_response}')
                except Exception:
                    pass

                activation_id = order_number_response.get('tzid')
                info = await client.get_order_info(operation_id=activation_id)

                try:
                    if user_id == CODER:
                        await bot.send_message(chat_id=user_id, text=f'Ответ OnlineSim info: {info}')
                except Exception:
                    pass

                phone_number = info[0].get('number').lstrip('+') if info else None
                if not phone_number:
                    await _reply(text=NOT_NUMBERS_ALERT)
                    await manager.switch_to(CountryMenu.select_country)
                    return

                # Маппим страну OnlineSim -> CountriesSmsActivate
                country_obj = await models.CountriesOnlinesim.get_country_from_country_by_id(country_id=country_id)

                # Определяем сервис для сохранения: если глобальный провайдер SMSActivate, используем код SMSActivate
                if is_smsactivate:
                    key = REVERSE_SERVICES_TRANSLATION.get(service_code, service_code)
                    service_obj = await models.ServicesSmsActivate.get_or_none(code=key)
                else:
                    service_obj = await models.ServicesOnlinesim.get_or_none(code=service_code)

            except Exception as e:
                await _reply(text=NOT_NUMBERS_ALERT)
                error_message = str(e)
                error_message_lower = error_message.lower()

                logger.bind(user_id=user_id, action='send_service_on_country').log(
                    "USER_ACTION", f"Ошибка при получении номера OnlineSim: {error_message}"
                )

                if "not enough funds" in error_message_lower:
                    # Оповещение администраторов о нехватке средств на OnlineSim
                    for admin_id in ADMINS:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=(
                                "🚨 *Внимание, администратор!*\n"
                                "❌ На сервисе *OnlineSim* недостаточно средств для выполнения операции.\n"
                                f"💬 *Ошибка*: `{error_message}`\n"
                            ),
                            parse_mode="Markdown"
                        )

                elif "exceeded concurrent operations for your account" in error_message_lower:
                    # Оповещение администраторов о достижении лимита одновременных операций на OnlineSim
                    for admin_id in ADMINS:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=(
                                "🚨 *Внимание, администратор!*\n"
                                "⚠️ На сервисе *OnlineSim* достигнут лимит операций по аккаунту.\n"
                                "ℹ️ Провайдер временно ограничил новые выдачи номеров.\n"
                                f"💬 *Ошибка*: `{error_message}`\n"
                            ),
                            parse_mode="Markdown"
                        )

                await manager.switch_to(CountryMenu.select_country)
                return

        else:
            # Заказываем номер через SMSActivate
            sms_client = SmsReceive()
            try:
                logger.bind(user_id=user_id, action='send_service_on_country').log("USER_ACTION", "SMSActivate")
                max_price_val = math.ceil(float(retail_price) if retail_price is not None else float(price))
                phone_data = await sms_client.get_phone_number(
                    country_id=country_id,
                    service_code=service_code,
                    max_price=max_price_val
                )

                if 'activationId' not in phone_data:
                    # При первом запросе не получили номер, пробуем с +5% цены
                    max_price_val = math.ceil(float(retail_price or 0) * 1.05)
                    phone_data = await sms_client.get_phone_number(
                        country_id=country_id,
                        service_code=service_code,
                        max_price=max_price_val
                    )

                if 'activationId' not in phone_data:
                    await _safe_cb_answer(c, text=NOT_NUMBERS_ALERT, show_alert=True)
                    await manager.switch_to(CountryMenu.select_country)
                    return

                activation_id = int(phone_data['activationId'])
                phone_number = phone_data['phoneNumber']
                country_obj = await models.CountriesSmsActivate.get_country_by_id(country_id=country_id)
                service_obj = await models.ServicesSmsActivate.get_or_none(code=service_code)

            except Exception as e:
                await _reply(text=NOT_NUMBERS_ALERT)
                logger.bind(user_id=user_id, action='send_service_on_country').error(
                    f"Ошибка при получении номера SMSActivate: {e}"
                )
                await manager.switch_to(CountryMenu.select_country)
                return

        # Атомарно: создаём активацию и списываем баланс
        expire_at = datetime.now(pytz.timezone('Europe/Moscow')).replace(microsecond=0) + timedelta(minutes=14)
        async with in_transaction() as conn:
            user_locked = await models.User.filter(id=user.id).using_db(conn).select_for_update().first()
            if not user_locked:
                raise RuntimeError("Пользователь не найден при блокировке")
            if float(user_locked.balance or 0.0) < float(price):
                raise RuntimeError("Недостаточно средств после блокировки пользователя")

            if provider_is_smsfast:
                activation = await models.Activation.create(
                    user=user_locked,
                    provider="smsfast",
                    activation_id=activation_id,
                    country=country_obj,
                    service=service_obj,
                    cost=float(price),
                    phone_number=phone_number,
                    activation_expire_at=expire_at,
                    using_db=conn,
                )
                service_name = service_obj.name if service_obj else str(service_code)

            elif provider_is_smsactivate:
                activation = await models.Activation.create(
                    user=user_locked,
                    provider="smsactivate",
                    activation_id=activation_id,
                    country=country_obj,
                    service=service_obj,
                    cost=float(price),
                    phone_number=phone_number,
                    activation_expire_at=expire_at,
                    using_db=conn,
                )
                service_name = service_obj.name if service_obj else str(service_code)

            else:
                activation = await models.Activation.create(
                    user=user_locked,
                    provider="onlinesim",
                    activation_id=activation_id,
                    country=country_obj,
                    service_2=service_obj,
                    cost=float(price),
                    phone_number=phone_number,
                    activation_expire_at=expire_at,
                    using_db=conn,
                )
                service_name = service_obj.name if service_obj else str(service_code)

            # Списываем баланс
            user_locked.balance = float(user_locked.balance or 0.0) - float(price)
            await user_locked.save(using_db=conn, update_fields=["balance"])

        # Обновляем объект user (актуальный баланс)
        await user.refresh_from_db(fields=["balance"])
        low_balance = await check_low_balance(user, price)

        # Удаляем служебное сообщение "запрос отправлен"
        if sent_message_id:
            try:
                await bot.delete_message(chat_id=user_id, message_id=sent_message_id)
            except Exception as e:
                logger.bind(user_id=user_id, action='send_service_on_country').warning(
                    f"Не удалось удалить служебное сообщение: {e}"
                )

        # Готовим данные для ответа пользователю
        try:
            country_name_result = activation.country.name
        except Exception:
            country_name_result = country_name  # на случай, если country не связан

        base_message = c.message if getattr(c, "message", None) else sent_message
        if base_message is None:
            # Если нет контекста сообщения, отправляем текстом и затем используем base_message
            await _reply(text="✅ Номер получен. Открываю детали…")
            base_message = c.message or sent_message

        # Отправляем информацию о полученном номере и кнопки управления
        await send_service_info_with_keyboard(
            message=base_message,
            activation=activation,
            service=service_name,
            country=country_name_result
        )

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

    # ✅ Показываем кнопку "повторное SMS" по провайдеру, а не по длине activation_id
    provider = (getattr(activation, "provider", None) or "").strip().lower()

    # Fallback для старых записей, где provider мог не выставляться
    if not provider:
        provider = "onlinesim" if getattr(activation, "service_2_id", None) else "smsactivate"

    # Кнопка "📩Принять новое SMS..." должна быть и для smsfast тоже
    if provider in ("smsfast", "onlinesim") and getattr(activation, "activation_id", None) is not None:
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
        sent = await message.answer(
            text=bt.SERVICE_INFO.format(
                country=flag_and_country,
                service=service,
                phone=activation.phone_number,
            ),
            reply_markup=mk
        )
    else:
        sent = await message.answer(
            text=bt.SERVICE_INFO_TELEGRAM.format(
                country=flag_and_country,
                service=service,
                phone=activation.phone_number,
            ),
            reply_markup=mk
        )

    # ⬇️ Сохраняем message_id для последующего удаления
    try:
        activation.service_msg_id = sent.message_id
        await activation.save(update_fields=["service_msg_id"])
    except Exception as e:
        logger.opt(exception=e).error("Не удалось сохранить service_msg_id у активации")

    # Лог
    user_id = message.from_user.id
    logger.bind(user_id=user_id, action='send_service_info_with_keyboard').log(
        "USER_ACTION",
        f"Ваш номер: {activation.phone_number}"
    )


async def send_country_info(service_code: str, c: types.CallbackQuery, manager: DialogManager = None):
    """
    Отправляет информацию о сервисе пользователю и обрабатывает активацию номера.

    :param service_code: Код сервиса.
    :param c: Объект CallbackQuery от aiogram.
    :param manager: Менеджер диалогов от aiogram_dialog (опционально).
    """
    try:
        user_id = c.from_user.id

        # Определяем активного провайдера на основе настроек
        smsactivate_active = await service_is_smsactivate()
        smsfast_active = (await models.AdminSettings.get_setting_value("sms_rental_service") == "SMS_Fast")
        smsfast_enabled_val = await models.AdminSettings.get_setting_value("smsfast_enabled")
        smsfast_enabled = str(smsfast_enabled_val or "").strip().lower() in ("1", "true", "yes", "y", "on", "enable", "enabled")

        # Выбираем провайдера для логирования (с учетом частичного SMSFast)
        if smsactivate_active:
            provider = "SMSActivate"
        elif smsfast_active or (not smsfast_active and smsfast_enabled and (service_code in SMSFAST_SERVICE_MAP or service_code in SMSFAST_SERVICE_MAP.values())):
            provider = "SMSFast"
        else:
            provider = "OnlineSim"
        logger.bind(user_id=user_id, action='send_country_info').log(
            "USER_ACTION",
            f"Запрос информации о сервисе: {service_code}, провайдер: {provider}"
        )

        if provider == "SMSActivate":
            # Ветка работы через SMSActivate (без изменений)
            if service_code in SERVICES_TRANSLATION:
                code_onlinesim = SERVICES_TRANSLATION[service_code]
                services = await models.PriceOnlinesim.get_service_data(code_onlinesim)
                sorted_countries_with_prices = [
                    {
                        "country": country,
                        "price": math.ceil(float(price) * DOLLAR_ONLINESIM),
                        "retail_price": int(float(price)),
                        "freePriceMap": None
                    }
                    for country, price in services.items()
                ]
            else:
                sms = SmsReceive()
                services = await sms.get_top_country(service=service_code)
                if not services:
                    await c.answer("Извините, информация о сервисе недоступна.")
                    return
                countries_with_prices = [
                    {
                        "country": svc.get("country"),
                        "price": math.ceil(float(svc.get("retail_price")) * DOLLAR_SMS_ACTIVATE),
                        "retail_price": svc.get("retail_price"),
                        "freePriceMap": svc.get("freePriceMap") or {}
                    }
                    for svc in services.values()
                ]
                country_name_mapping = await models.CountriesSmsActivate.get_country_name_mapping()
                for item in countries_with_prices:
                    raw_id = item["country"]
                    if isinstance(raw_id, int) or str(raw_id).isdigit():
                        item["country"] = country_name_mapping.get(int(raw_id), "Unknown Country")
                sorted_countries_with_prices = sort_countries_by_dict(countries_with_prices)

        elif provider == "SMSFast":
            # Ветка работы через SMSFast (полностью или частично)
            smsfast_service_code = service_code
            if service_code in SMSFAST_SERVICE_MAP:
                smsfast_service_code = SMSFAST_SERVICE_MAP[service_code]
            elif service_code in SMSFAST_SERVICE_MAP.values():
                smsfast_service_code = service_code

            # Берём список стран из справочника (countries_smsfast), а цены/кол-во — из кеша price_smsfast
            rows = await models.CountriesSmsFast.all().order_by("id")
            if not rows:
                logger.bind(user_id=user_id, action="send_country_info", provider=provider).log(
                    "USER_ACTION",
                    "Справочник стран SMSFast пуст (countries_smsfast)"
                )
                await c.answer("Извините, список стран временно недоступен.")
                return

            price_rows = await models.PriceSmsFast.filter(
                service_code=smsfast_service_code
            ).values("country", "price", "count")

            # print(f'price_rows {price_rows}')

            price_by_country_id: dict[int, dict] = {}
            for pr in price_rows:
                try:
                    cid = int(pr.get("country"))
                except (TypeError, ValueError):
                    continue
                price_by_country_id[cid] = {
                    "price": pr.get("price"),
                    "count": pr.get("count"),
                }

            countries_with_prices: list[dict] = []
            for r in rows:
                cid = int(r.country_id)
                country_name = str(r.name or "").strip()

                # ✅ 3) Явно исключаем "США (виртуальные)"
                if country_name == "США (виртуальные)":
                    continue

                raw = price_by_country_id.get(cid) or {}
                raw_price = raw.get("price")
                raw_count = raw.get("count")

                # ✅ Нет цены в кеше — страну НЕ показываем
                if raw_price is None:
                    continue

                # ✅ Если count известен и 0 — номеров нет, страну НЕ показываем
                if raw_count is not None:
                    try:
                        if int(raw_count) <= 0:
                            continue
                    except (TypeError, ValueError):
                        pass

                try:
                    retail_price_float = float(raw_price)
                except (TypeError, ValueError):
                    # ✅ Цена битая/не парсится — тоже НЕ показываем
                    continue

                # ✅ 2) Фильтрация retail_price == 1 (как ты просил)
                # Важно: это эвристика. Если увидишь что "дешёвые, но реальные" страны пропали — убери этот блок.
                # if retail_price_float == 1.0:
                #     continue

                # retail_price — сырой прайс провайдера; price — наша цена (с наценкой)
                retail_price = retail_price_float
                price = math.ceil(retail_price_float * INTEREST)

                countries_with_prices.append(
                    {
                        "country": country_name,
                        "price": price,
                        "retail_price": retail_price,
                        # не None, чтобы старая логика воспринимала как smsactivate-подобный поток
                        "freePriceMap": {},
                        # важно для SMSFast
                        "smsfast_country_id": cid,
                        "smsfast_service_code": smsfast_service_code,
                        # опционально: пригодится для дебага/логов
                        "smsfast_count": raw_count,
                    }
                )

            sorted_countries_with_prices = sort_countries_by_dict(countries_with_prices)

        else:
            # Ветка работы через OnlineSim (как было ранее)
            if service_code not in SMS_ACTIVATE_SERVICE_CODES_AT_ONLINESIM:
                services = await models.PriceOnlinesim.get_service_data(service_code)
                sorted_countries_with_prices = [
                    {
                        "country": country,
                        "price": math.ceil(float(price) * DOLLAR_ONLINESIM),
                        "retail_price": int(float(price)),
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
                    await c.answer("Извините, информация о сервисе недоступна в данный момент.")
                    return
                countries_with_prices = [
                    {
                        "country": svc.get("country"),
                        "price": math.ceil(float(svc.get("retail_price")) * DOLLAR_SMS_ACTIVATE),
                        "retail_price": svc.get("retail_price"),
                        "freePriceMap": svc.get("freePriceMap") or {}
                    }
                    for svc in services.values()
                ]
                country_name_mapping = await models.CountriesSmsActivate.get_country_name_mapping()
                for item in countries_with_prices:
                    raw_id = item["country"]
                    if isinstance(raw_id, int) or str(raw_id).isdigit():
                        item["country"] = country_name_mapping.get(int(raw_id), "Unknown Country")
                sorted_countries_with_prices = sort_countries_by_dict(countries_with_prices)

        # Фильтрация исключённых стран и доп. сортировка (общая для всех провайдеров)
        excluded_countries = set(EXCLUDED_COUNTRIES)
        excluded_countries.add("США (виртуальные)")

        sorted_countries_with_prices = [
            item for item in sorted_countries_with_prices
            if str(item.get("country", "")).strip() not in excluded_countries
        ]

        print(service_code)
        priority_list = COUNTRY_PRIORITY_LIST_BY_SERVICE.get(service_code)
        if priority_list:
            sorted_countries_with_prices = await sort_countries_tg(
                sorted_countries_with_prices,
                priority_list,
            )

        # print(f'sorted_countries_with_prices {sorted_countries_with_prices}')

        # Передаем данные в диалог выбора страны
        await manager.start(
            CountryMenu.select_country,
            mode=StartMode.NORMAL,
            data={"countries_with_prices": sorted_countries_with_prices, "service_code": service_code}
        )

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


@logger.catch()
async def on_smsfast_other_service(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик кнопки "Любой другой" (только для SMSFast).

    Переводим пользователя в выбор страны, используя service_code="ot".
    Дальше логика уже существующая: send_country_info -> CountryMenu.select_country.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action="on_smsfast_other_service").log(
            "USER_ACTION",
            "Пользователь выбрал 'Любой другой' (SMSFast, service_code=ot)"
        )

        # ВАЖНО: send_country_info уже умеет ветвиться на SMSFast по настройкам/флагам.
        await send_country_info("ot", c, manager)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_smsfast_other_service: {e}")
        await _safe_cb_answer(c, text="Ошибка. Попробуйте позже.", show_alert=True)
