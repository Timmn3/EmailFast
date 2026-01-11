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
from loguru import logger
from app.dependencies import bot

# Функция для обработки нажатия кнопки поиска страны
async def rent_on_search_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='rent_on_search_country').log(
            "USER_ACTION",
            "Переход к поиску страны для аренды"
        )
        await manager.switch_to(RentCountryMenu.enter_country)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в rent_on_search_country: {e}")


async def rent_on_deposit(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='rent_on_deposit').log(
            "USER_ACTION",
            "Переход к пополнению баланса"
        )
        await manager.switch_to(RentCountryMenu.deposit)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в rent_on_deposit: {e}")


async def rent_back_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='rent_back_country').log(
            "USER_ACTION",
            "Возврат к выбору страны"
        )
        await manager.switch_to(RentCountryMenu.select_country)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в rent_back_country: {e}")


# Функция для обработки результата поиска страны
async def rent_on_result_country(m: types.Message, widget: TextInput, manager: DialogManager, country_name: str):
    """
    Обрабатывает результат поиска страны по введенному названию.

    :param m: Объект Message от aiogram.
    :param widget: Виджет TextInput от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param country_name: Название страны, введенное пользователем.
    """
    try:
        user_id = m.from_user.id
        logger.bind(user_id=user_id, action='rent_on_result_country').log(
            "USER_ACTION",
            f"Поиск страны: {country_name}"
        )

        country_names = await models.CountriesOnlinesim.search_countries(country_name.lower())
        if len(country_names) == 0:
            logger.bind(user_id=user_id, action='rent_on_result_country').log(
                "USER_ACTION",
                "Страна не найдена"
            )
            await manager.switch_to(RentCountryMenu.enter_country_error)
            return

        ctx = manager.current_context()
        ctx.dialog_data['search_name'] = country_names[0]
        logger.bind(user_id=user_id, action='rent_on_result_country').log(
            "USER_ACTION",
            f"Найдена страна: {country_names[0]}"
        )
        await manager.switch_to(RentCountryMenu.select_country)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в rent_on_result_country: {e}")


# Функция для обработки нажатия кнопки поиска страны
async def on_search_rent_country(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обрабатывает нажатие кнопки поиска страны и переводит на меню ввода страны.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Button от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_search_rent_country').log(
            "USER_ACTION",
            "Переход к поиску страны для аренды"
        )
        await manager.switch_to(RentCountryMenu.enter_country)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_search_rent_country: {e}")


async def rent_on_select_country_new(c: types.CallbackQuery, widget: Select, manager: DialogManager,
                                     country_index: str):
    """
    Обрабатывает выбор страны для аренды.

    :param c: Объект CallbackQuery от aiogram.
    :param widget: Виджет Select от aiogram_dialog.
    :param manager: Менеджер диалогов от aiogram_dialog.
    :param country_index: Индекс выбранной страны.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='rent_on_select_country_new').log(
            "USER_ACTION",
            f"Выбор страны для аренды: {country_index}"
        )

        # Получаем список стран из текущего контекста
        rent_countries = manager.dialog_data.get("rent_countries", [])

        # Находим выбранную страну
        selected_country = next((country for country in rent_countries if country["id"] == country_index), None)

        if not selected_country:
            logger.bind(user_id=user_id, action='rent_on_select_country_new').log(
                "USER_ACTION",
                f"Страна с ID={country_index} не найдена"
            )
            await c.answer("Страна не найдена.", show_alert=True)
            return

        tariffs = selected_country["tariffs"].get(country_index, {})
        updated_tariffs = {days: round(price * DOLLAR_ONLINESIM) for days, price in tariffs.items()}
        manager.dialog_data["selected_country"] = {
            "rent_country_code": country_index,
            "country": selected_country["country"],
            "tariffs": updated_tariffs,
        }

        logger.bind(user_id=user_id, action='rent_on_select_country_new').log(
            "USER_ACTION",
            f"Выбрана страна: {selected_country['country']}, тарифы обновлены"
        )

        await manager.switch_to(RentCountryMenu.country_details)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в rent_on_select_country_new: {e}")


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
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='rent_number_in_days').log(
            "USER_ACTION",
            f"Запрос аренды на {day_index} дней"
        )

        tzid = None
        if selected_country is None:
            # Получаем данные о выбранной стране
            selected_country = manager.dialog_data.get("selected_country")
            if selected_country is None:
                selected_country = manager.start_data.get("selected_country")
                tzid = selected_country["tzid"]

            if not selected_country:
                await c.answer("Ошибка: данные о стране отсутствуют.", show_alert=True)
                logger.bind(user_id=user_id, action='rent_number_in_days').log(
                    "USER_ACTION",
                    "Данные о стране отсутствуют"
                )
                return

        # Преобразуем индекс дней в число
        days = int(day_index.split()[0])

        price = selected_country['tariffs'].get(str(days))
        # Получаем информацию о пользователе
        user = await models.User.get_user(c.from_user.id)
        country_code = selected_country["rent_country_code"]  # Код страны из context

        # Создаем экземпляр API клиента заранее — он нужен и для precheck, и для аренды
        api_client = OnlineSimRentAPI()

        # Проверяем, достаточно ли у пользователя средств на балансе
        if user.balance < price:
            # ✅ Сначала проверяем, есть ли вообще номера по этой стране (чтобы не просить пополнение зря)
            try:
                logger.bind(user_id=user_id, action='rent_number_in_days').log(
                    "USER_ACTION",
                    f"Precheck наличия номеров перед пополнением: страна={country_code}, дни={days}"
                )
                precheck_result = await api_client.rent_number(country=int(country_code), days=days)
            except Exception as e:
                err_text = str(e)
                # API иногда возвращает NO_NUMBER исключением — ловим это
                if "NO_NUMBER" in err_text or "NO_NUMBERS" in err_text:
                    await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
                    return
                # Любая другая ошибка (чаще всего NO_MONEY) — продолжаем к пополнению
                precheck_result = "error"

            # API иногда возвращает NO_NUMBER как None — ловим и это
            if precheck_result is None:
                await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
                return

            # Если вдруг вернулся успешный rent (не должно при нехватке средств) — пробуем закрыть, чтобы не «утек» номер
            if isinstance(precheck_result, dict) and precheck_result.get("tzid"):
                try:
                    await api_client.close_rent_num(tzid=int(precheck_result["tzid"]))
                except Exception as close_e:
                    logger.opt(exception=close_e).warning("Не удалось закрыть аренду после precheck")

            logger.bind(user_id=user_id, action='rent_number_in_days').log(
                "USER_ACTION",
                f"Недостаточно средств для аренды. Требуется: {price}, доступно: {user.balance}"
            )

            missing_amount = max(price - user.balance, 50.0)
            manager.current_context().dialog_data.update({
                'day_index': day_index,
                'selected_country': selected_country,
                'rent_country_code': country_code,
                'price': missing_amount
            })
            from app.dialogs.personal_cabinet.selected import send_payment_keyboard
            await send_payment_keyboard(m=c, manager=manager, price=missing_amount)
            return



        sent_message = await c.message.answer(text=NUMBER_REQUEST_SENT)
        sent_message_id = sent_message.message_id

        # Проверяем, прошло ли 10 секунд с последнего запроса
        if user.last_request_time is not None and (
                datetime.now(pytz.utc) - user.last_request_time.astimezone(pytz.utc)).total_seconds() < 5:
            await c.answer(text=PLEASE_WAIT_SECONDS, show_alert=True)
            logger.bind(user_id=user_id, action='rent_number_in_days').log(
                "USER_ACTION",
                "Слишком частый запрос"
            )
            return

        # Получаем текущее время в московском часовом поясе
        current_time = datetime.now(pytz.timezone('Europe/Moscow'))
        # Обновляем время последнего запроса
        user.last_request_time = current_time.astimezone(pytz.utc)
        await user.save(update_fields=['last_request_time'])

        try:
            if tzid is None:
                logger.bind(user_id=user_id, action='rent_number_in_days').log(
                    "USER_ACTION",
                    f"Запрос аренды номера: страна={country_code}, дни={days}"
                )
                rent_result = await api_client.rent_number(country=int(country_code), days=days) # Запрос номера
            else:
                logger.bind(user_id=user_id, action='rent_number_in_days').log(
                    "USER_ACTION",
                    f"Продление аренды: tzid={tzid}, дни={days}"
                )
                rent_result = await api_client.extend_rent_state(tzid=tzid, days=days)
        except Exception as e:
            logger.opt(exception=e).error(f"Ошибка при вызове API: {e}")
            await c.answer(f"Ошибка при аренде: {str(e)}", show_alert=True)
            return

        # Выводим результат аренды
        if rent_result is None:
            logger.bind(user_id=user_id, action='rent_number_in_days').log(
                "USER_ACTION",
                "Не удалось получить номер"
            )
            await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
            return

            # Извлекаем данные активации
        rent_id = int(rent_result.get("tzid", 0))
        phone_number = rent_result.get("number", None)
        country = await models.CountriesOnlinesim.get_country_onlinesim(country_id=country_code)
        minutes = int(rent_result.get("time", 0))

        if phone_number is None:
            await c.answer(text=bt.NOT_NUMBERS_ALERT, show_alert=True)
            logger.bind(user_id=user_id, action='rent_number_in_days').log(
                "USER_ACTION",
                "Номер не получен"
            )
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
        logger.bind(user_id=user_id, action='rent_number_in_days').log(
            "USER_ACTION",
            f"Успешная аренда номера: {activation.phone_number}, истекает {activation.rent_expire_at}"
        )

        await bot.delete_message(chat_id=c.from_user.id, message_id=sent_message_id)

        # Отправляем пользователю сообщение о номере телефона
        await send_message_country_number(message=c.message, activation=activation, country=activation.country.name, days=days)

        # Проверяем, низкий ли баланс у пользователя после списания средств
        low_balance = await check_low_balance(user, price)

        # Списываем средства с баланса пользователя
        user.balance -= price
        await user.save(update_fields=['balance'])


        # Ждем 1 секунду перед отправкой уведомления о низком балансе, если это необходимо
        await asyncio.sleep(1)
        if low_balance:
            await send_low_balance_alert(user)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в rent_number_in_days: {e}")


async def send_message_country_number(message: types.Message, activation, country, days):
    """
    Отправляет пользователю о номер телефона.

    :param country: Страна
    :param message: Сообщение для отправки.
    :param activation: Объект активации.
    :param days: Количество арендованных дней.
    """

    try:
        user_id = message.from_user.id
        country = country.strip()
        flag = country_flags.get(country, "")
        flag_and_country = f"{flag} {country}"

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
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в send_message_country_number: {e}")