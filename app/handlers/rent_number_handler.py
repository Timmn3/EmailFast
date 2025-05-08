from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram_dialog import DialogManager, StartMode
from app.db import models
from app.db.models import StatusResponse
from app.dialogs.rent_sms.states import RentCountryMenu
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.services.bot_texts import country_flags, DOLLAR_ONLINESIM
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from app.services import bot_texts as bt
from app.services.onlinesim.rent_number import OnlineSimRentAPI
from loguru import logger

router = Router()


@router.message(Command("rent_number"))
@router.message(F.text == bt.RENT_NUMBER)
async def rent_number(message: types.Message, dialog_manager: DialogManager):
    """
    📞Арендовать номер
    """
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", "Пользователь запросил аренду номера")
        logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", f"Результат из БД: пользователь найден={user is not None}")

        if not user:
            return

        logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", "Проверка подписки")
        sub = await check_subscribe(user)
        if not sub:
            logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", "Подписка неактивна, отправляем сообщение")
            await send_subscribe_msg(user)
            return

        logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", f"Запрос к БД: получение активных аренд для пользователя {user_id}")
        activation_list = await models.Rent.get_active_rent(user.id)
        logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", f"Результат из БД: найдено аренд={len(activation_list) if activation_list else 0}")

        if activation_list is None or not activation_list:
            logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", "Нет активных аренд, запуск диалога выбора страны")
            await dialog_manager.start(RentCountryMenu.select_country, mode=StartMode.RESET_STACK)
            return

        logger.bind(user_id=user_id, action="rent_number").log("USER_ACTION", "Отправка меню аренды")
        await send_rent_menu(user, message=message)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /rent_number: {e}")


async def send_rent_menu(user: "User", message: types.Message = None, callback_query: types.CallbackQuery = None):
    """
    Вспомогательная функция для отправки меню аренды номеров.
    """
    try:
        user_id = user.telegram_id
        logger.bind(user_id=user_id, action="send_rent_menu").log("USER_ACTION", "Формирование меню арендованных номеров")

        logger.bind(user_id=user_id, action="send_rent_menu").log("USER_ACTION", f"Запрос к БД: получение активных аренд для пользователя {user_id}")
        activation_list = await models.Rent.get_active_rent(user.id)
        logger.bind(user_id=user_id, action="send_rent_menu").log("USER_ACTION", f"Результат из БД: найдено аренд={len(activation_list) if activation_list else 0}")

        # Создаем список для вывода информации
        rent_details = ["<i>Ваши арендованные номера⤵️</i>"]
        # Создаем inline клавиатуру
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])

        for activation in activation_list:
            if activation.is_canceled:
                continue

            await activation.fetch_related('country')
            country = activation.country.name
            flag = country_flags.get(country, "")
            phone_number = activation.phone_number
            button_text = f"{flag} +{phone_number}"
            callback_data = f"number_{activation.id}"
            keyboard.inline_keyboard.append([types.InlineKeyboardButton(text=button_text, callback_data=callback_data)])

        keyboard.inline_keyboard.append([types.InlineKeyboardButton(text=bt.RENT_NEW_ROOM, callback_data="new_number")])

        if message:
            logger.bind(user_id=user_id, action="send_rent_menu").log("USER_ACTION", "Отправка нового сообщения с меню аренды")
            await message.answer("\n".join(rent_details), reply_markup=keyboard)
        else:
            logger.bind(user_id=user_id, action="send_rent_menu").log("USER_ACTION", "Обновление текущего сообщения с меню аренды")
            await callback_query.message.edit_text("\n".join(rent_details), reply_markup=keyboard)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в функции /send_rent_menu: {e}")


@router.callback_query(F.data == "back_to_rent_menu")
async def back_to_rent_menu(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка кнопки 'Назад', возвращающая в меню аренды номеров.
    """
    try:
        user_id = callback_query.from_user.id
        logger.bind(user_id=user_id, action="back_to_rent_menu").log("USER_ACTION", "Пользователь вернулся в меню аренды")
        user = await models.User.get_user(user_id)
        await send_rent_menu(user, callback_query=callback_query)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /back_to_rent_menu: {e}")


# Обработчик для нажатия на кнопку арендованного номера
@router.callback_query(F.data.startswith('number_'))
async def rent_number_selected(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка выбора арендованного номера
    """
    try:
        user_id = callback_query.from_user.id
        rent_id = int(callback_query.data.split('_')[1])
        logger.bind(user_id=user_id, action="rent_number_selected").log("USER_ACTION", f"Выбрана аренда ID={rent_id}")

        logger.bind(user_id=user_id, action="rent_number_selected").log("USER_ACTION", f"Запрос к БД: получение аренды ID={rent_id}")
        rented = await models.Rent.get_rent(id=rent_id)
        logger.bind(user_id=user_id, action="rent_number_selected").log("USER_ACTION", f"Результат из БД: аренда найдена={rented is not None}")

        if not rented:
            await callback_query.answer("Номер не найден.")
            return

        if rented.is_canceled:
            logger.bind(user_id=user_id, action="rent_number_selected").log("USER_ACTION", "Ошибка: аренда отменена")
            await callback_query.answer("Эта аренда была отменена.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="rent_number_selected").log("USER_ACTION", f"Запрос к БД: получение страны для аренды ID={rent_id}")
        country = await models.Rent.get_country_by_rent(id=rent_id)
        logger.bind(user_id=user_id, action="rent_number_selected").log("USER_ACTION", f"Результат из БД: страна={country}")

        phone_number = rented.phone_number
        expiry_date = rented.rent_expire_at.strftime("%d.%m.%y %H:%M")
        flag = country_flags.get(country, "")
        sms = rented.sms_text

        rent_details = bt.RENT_DETAILS.format(
            phone_number=phone_number,
            country=country,
            expiry_date=expiry_date,
            flag=flag
        )

        if sms:
            formatted_sms = "\n".join(
                [f"• <b>{service}:</b> {text}" for line in sms.split("\n") if
                 (split_line := line.split(": ", 1)) and len(split_line) == 2 and (service := split_line[0]) and (
                     text := split_line[1])]
            )
            rent_details += f"\n<b>Ваши сообщения:</b>\n{formatted_sms}"

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])

        if rented.autorenew:
            keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="✅ Автопродление включено", callback_data=f"auto_renew_{rent_id}")])
        else:
            keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="❌Автопродление выключено", callback_data=f"auto_renew_{rent_id}")])

        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(text="🔄 Продлить аренду", callback_data=f"extend_rent_{rent_id}"),
            types.InlineKeyboardButton(text="🚫 Отменить аренду", callback_data=f"cancel_rent_{rent_id}")
        ])
        keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_rent_menu")])

        await callback_query.message.edit_text(rent_details, reply_markup=keyboard)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /rent_number_selected: {e}")


@router.callback_query(F.data.startswith('auto_renew_'))
async def toggle_autorenew(callback_query: types.CallbackQuery):
    """
    Обработка переключения состояния автопродления.
    """
    try:
        user_id = callback_query.from_user.id
        rent_id = int(callback_query.data.split('_')[2])
        logger.bind(user_id=user_id, action="toggle_autorenew").log("USER_ACTION", f"Изменение автопродления для аренды ID={rent_id}")

        logger.bind(user_id=user_id, action="toggle_autorenew").log("USER_ACTION", f"Запрос к БД: получение аренды ID={rent_id}")
        rented = await models.Rent.get_rent(id=rent_id)
        logger.bind(user_id=user_id, action="toggle_autorenew").log("USER_ACTION", f"Результат из БД: аренда найдена={rented is not None}")

        if not rented:
            await callback_query.answer("Аренда не найдена.", show_alert=True)
            return

        rented.autorenew = not rented.autorenew
        rented.is_notified = False
        await rented.save()
        new_state = "включено" if rented.autorenew else "выключено"
        await callback_query.answer(f"Автопродление {new_state}.")
        country = await models.Rent.get_country_by_rent(id=rent_id)
        phone_number = rented.phone_number
        expiry_date = rented.rent_expire_at.strftime("%d.%m.%y %H:%M")
        flag = country_flags.get(country, "")
        sms = rented.sms_text

        rent_details = bt.RENT_DETAILS.format(
            phone_number=phone_number,
            country=country,
            expiry_date=expiry_date,
            flag=flag
        )

        if sms:
            formatted_sms = "\n".join(
                [f"• <b>{service}:</b> {text}" for line in sms.split("\n") if
                 (split_line := line.split(": ", 1)) and len(split_line) == 2 and (service := split_line[0]) and (
                     text := split_line[1])]
            )
            rent_details += f"\n<b>Ваши сообщения:</b>\n{formatted_sms}"

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
        if rented.autorenew:
            keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="✅ Автопродление включено", callback_data=f"auto_renew_{rent_id}")])
        else:
            keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="❌Автопродление выключено", callback_data=f"auto_renew_{rent_id}")])

        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(text="🔄 Продлить аренду", callback_data=f"extend_rent_{rent_id}"),
            types.InlineKeyboardButton(text="🚫 Отменить аренду", callback_data=f"cancel_rent_{rent_id}")
        ])

        keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_rent_menu")])
        await callback_query.message.edit_text(rent_details, reply_markup=keyboard)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /toggle_autorenew: {e}")


# 📞Арендовать новый номер
@router.callback_query(F.data.startswith('new_number'))
async def rent_new_number(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Пользователь выбрал 'Арендовать новый номер'.
    """
    try:
        user_id = callback_query.from_user.id
        logger.bind(user_id=user_id, action="rent_new_number").log("USER_ACTION", "Пользователь выбрал 'Арендовать новый номер'")
        context_data = {
            'chat_id': callback_query.message.chat.id,
            'message_id': callback_query.message.message_id
        }
        logger.bind(user_id=user_id, action="rent_new_number").log("USER_ACTION", "Запуск диалога выбора страны")
        await dialog_manager.start(RentCountryMenu.select_country, data=context_data)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /rent_new_number: {e}")


@router.callback_query(F.data.startswith('cancel_rent_'))
async def cancel_rent(callback_query: types.CallbackQuery):
    """
    Обработка отмены аренды номера.
    """
    try:
        user_id = callback_query.from_user.id
        rent_id = int(callback_query.data.split('_')[2])
        logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", f"Пользователь запросил отмену аренды ID={rent_id}")

        logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", f"Запрос к БД: получение аренды ID={rent_id}")
        rented = await models.Rent.get_rent(id=rent_id)
        logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", f"Результат из БД: аренда найдена={rented is not None}")

        if not rented:
            logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Ошибка: аренда не найдена")
            await callback_query.answer(bt.RENT_NOT_FOUND_MSG, show_alert=True)
            await callback_query.message.delete()
            return

        if rented.is_canceled:
            logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Ошибка: аренда уже отменена")
            await callback_query.answer(bt.RENT_ALREADY_CANCELED_MSG, show_alert=True)
            await callback_query.message.delete()
            return

        logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Инициализация API для отмены аренды")
        api = OnlineSimRentAPI()
        response = await api.close_rent_num(tzid=rented.rent_id)

        user = await models.User.get_user(user_id)

        if response.get("response"):
            if rented.status == StatusResponse.STATUS_WAIT_CODE:
                logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Возврат средств за ожидание кода")
                user.balance += rented.cost
                await user.save()

            rented.is_canceled = True
            await rented.save()
            logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Аренда успешно отменена")
            await callback_query.answer(bt.RENT_CANCEL_SUCCESS_MSG, show_alert=True)

            await callback_query.message.delete()
            await send_rent_menu(user, callback_query=callback_query)
        else:
            logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Ошибка при отмене аренды через API")
            await callback_query.answer(bt.RENT_CANCEL_FAILED_MSG, show_alert=True)
            logger.error(f"Неизвестный ответ API при отмене аренды: {response}")

    except Exception as e:
        error_text = str(e)
        logger.opt(exception=e).error(f"Необработанное исключение в /cancel_rent: {error_text}")

        # Обрабатываем специфическую ошибку
        if "ERROR_NO_OPERATIONS" in error_text:
            logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Специфическая ошибка: ERROR_NO_OPERATIONS")
            logger.error(e)
            await callback_query.answer(bt.RENT_CANCEL_SUCCESS_MSG, show_alert=True)
        else:
            logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Общая ошибка API")
            await callback_query.answer(bt.RENT_CANCEL_FAILED_MSG, show_alert=True)

        try:
            # Удаляем сообщение о текущей аренде
            await callback_query.message.delete()
        except Exception as msg_delete_error:
            logger.opt(exception=msg_delete_error).warning("Не удалось удалить сообщение")

        try:
            # В случае ошибки переводим аренду в статус отмененной
            rented.is_canceled = True
            await rented.save()
            logger.bind(user_id=user_id, action="cancel_rent").log("USER_ACTION", "Аренда переведена в статус 'отменена' после ошибки")
        except Exception as save_error:
            logger.opt(exception=save_error).error("Не удалось сохранить статус аренды как 'отменён'")


@router.callback_query(F.data.startswith('extend_rent_'))
async def extend_rent(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка продления аренды.
    """
    try:
        user_id = callback_query.from_user.id
        rent_id = int(callback_query.data.split('_')[2])
        logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", f"Пользователь запросил продление аренды ID={rent_id}")

        logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", f"Запрос к БД: получение аренды ID={rent_id}")
        rented = await models.Rent.get_rent(id=rent_id)
        logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", f"Результат из БД: аренда найдена={rented is not None}")

        if not rented:
            logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", "Ошибка: аренда не найдена")
            await callback_query.answer(text=bt.RENTAL_CANCELED_OR_NOT_FOUND, show_alert=True)
            return

        logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", "Инициализация API клиента")
        api_client = OnlineSimRentAPI()
        rent_state = await api_client.get_rent_state(tzid=rented.rent_id)

        if not rent_state or not rent_state.get("list") or "extend" not in rent_state["list"][0]:
            logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", "Ошибка: нет доступных дней для продления")
            await callback_query.answer(text=bt.FAILED_TO_GET_AVAILABLE_DAYS, show_alert=True)
            return

        logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", f"Запрос к БД: получение страны аренды ID={rent_id}")
        rent_country_code = rented.country.country_id
        country = rented.country.name
        logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", f"Результат из БД: страна={country}, код={rent_country_code}")

        logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", "Получение тарифов")
        data = await api_client.get_tariffs()
        tariffs = data.get(str(rent_country_code), {})

        if not tariffs:
            logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", "Ошибка: не удалось получить тарифы")
            await callback_query.answer(text=bt.FAILED_TO_GET_AVAILABLE_DAYS, show_alert=True)
            return

        updated_tariffs = {days: round(price * DOLLAR_ONLINESIM) for days, price in tariffs.items()}

        context_data = {
            "selected_country": {
                "rent_country_code": rent_country_code,
                "country": country,
                "tariffs": updated_tariffs,
                "tzid": rented.rent_id,
            }
        }

        logger.bind(user_id=user_id, action="extend_rent").log("USER_ACTION", "Запуск диалога деталей страны")
        await dialog_manager.start(RentCountryMenu.country_details, data=context_data)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_rent: {e}")


@router.callback_query(F.data.startswith('top_up_balance'))
async def top_up_balance(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка пополнения баланса
    """
    try:
        user_id = callback_query.from_user.id
        logger.bind(user_id=user_id, action="top_up_balance").log("USER_ACTION", "Пользователь начал пополнение баланса")

        logger.bind(user_id=user_id, action="top_up_balance").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="top_up_balance").log("USER_ACTION", f"Результат из БД: пользователь найден={user is not None}")

        context_data = {
            'user_id': user.telegram_id,
            'auto_renewal': True,
        }

        logger.bind(user_id=user_id, action="top_up_balance").log("USER_ACTION", "Запуск диалога пополнения баланса")
        await dialog_manager.start(PersonalMenu.deposit, data=context_data)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /top_up_balance: {e}")