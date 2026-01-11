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

import time

@router.message(Command("rent_number"))
@router.message(F.text == bt.RENT_NUMBER)
async def rent_number(message: types.Message, dialog_manager: DialogManager):
    """
    📞Арендовать номер
    """
    user_id = message.from_user.id
    logger.bind(user_id=user_id, action='rent_number').log("USER_ACTION", "Пользователь начал процесс аренды номера")

    t0 = time.perf_counter()
    try:
        user = await models.User.get_user(user_id)
        t1 = time.perf_counter()
        logger.bind(user_id=user_id, action='rent_number').log(
            "USER_ACTION",
            f"PERF: get_user={(t1 - t0):.3f}s"
        )

        logger.bind(user_id=user_id, action='rent_number').log(
            "USER_ACTION",
            f"Запрос к БД: получение данных пользователя {user_id}, баланс = {user.balance} ₽"
        )

        # Проверяем подписку
        sub = await check_subscribe(user)
        t2 = time.perf_counter()
        logger.bind(user_id=user_id, action='rent_number').log(
            "USER_ACTION",
            f"PERF: check_subscribe={(t2 - t1):.3f}s"
        )

        if not sub:
            logger.bind(user_id=user_id, action='rent_number').log(
                "USER_ACTION",
                f"Подписка неактивна для пользователя {user_id}, баланс = {user.balance} ₽"
            )
            await send_subscribe_msg(user)
            return

        # Проверяем аренды пользователя
        activation_list = await models.Rent.get_active_rent(user.id)
        t3 = time.perf_counter()
        logger.bind(user_id=user_id, action='rent_number').log(
            "USER_ACTION",
            f"PERF: get_active_rent={(t3 - t2):.3f}s total_before_ui={(t3 - t0):.3f}s"
        )

        logger.bind(user_id=user_id, action='rent_number').log(
            "USER_ACTION",
            f"Запрос к БД: получение активных аренд для пользователя {user_id}, баланс = {user.balance} ₽"
        )
        logger.bind(user_id=user_id, action='rent_number').log(
            "USER_ACTION",
            f"Результат из БД: найдено активных аренд - {len(activation_list) if activation_list else 0}"
        )

        # Если нет активных арендных номеров, то предлагаем
        if activation_list is None or not activation_list:
            logger.bind(user_id=user_id, action='rent_number').log(
                "USER_ACTION",
                f"Нет активных аренд, перенаправляем в выбор страны для пользователя {user_id}, баланс = {user.balance} ₽"
            )
            await dialog_manager.start(RentCountryMenu.select_country, mode=StartMode.RESET_STACK)
            return

        # Отправляем меню аренды
        await send_rent_menu(user, message=message)

        t4 = time.perf_counter()
        logger.bind(user_id=user_id, action='rent_number').log(
            "USER_ACTION",
            f"PERF: total_full={(t4 - t0):.3f}s"
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /rent_number: {e}")


async def send_rent_menu(user: "User", message: types.Message = None, callback_query: types.CallbackQuery = None):
    """
    Вспомогательная функция для отправки меню аренды номеров.
    """
    user_id = user.telegram_id
    logger.bind(user_id=user_id, action='send_rent_menu').log(
        "USER_ACTION",
        f"Формирование меню аренды для пользователя {user_id}, баланс = {user.balance} ₽"
    )

    t0 = time.perf_counter()
    try:
        # Проверяем аренды пользователя
        activation_list = await models.Rent.get_active_rent(user.id)
        t1 = time.perf_counter()

        logger.bind(user_id=user_id, action='send_rent_menu').log(
            "USER_ACTION",
            f"PERF: get_active_rent={(t1 - t0):.3f}s"
        )

        logger.bind(user_id=user_id, action='send_rent_menu').log(
            "USER_ACTION",
            f"Запрос к БД: получение активных аренд для формирования меню"
        )
        logger.bind(user_id=user_id, action='send_rent_menu').log(
            "USER_ACTION",
            f"Результат из БД: найдено активных аренд - {len(activation_list) if activation_list else 0}"
        )

        # Создаем список для вывода информации
        rent_details = ["<i>Ваши арендованные номера⤵️</i>\n"]
        # Создаем inline клавиатуру
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])

        fetch_related_total = 0.0
        count = 0

        # Проходим по всем арендам
        for activation in activation_list:
            # Если аренда отменена, пропускаем ее
            if activation.is_canceled:
                continue

            count += 1
            t_rel0 = time.perf_counter()
            await activation.fetch_related('country')
            fetch_related_total += (time.perf_counter() - t_rel0)

            country = activation.country.name
            flag = country_flags.get(country, "")
            phone_number = activation.phone_number

            button_text = f"{flag} +{phone_number}"
            callback_data = f"number_{activation.id}"
            keyboard.inline_keyboard.append(
                [types.InlineKeyboardButton(text=button_text, callback_data=callback_data)]
            )

        t2 = time.perf_counter()
        logger.bind(user_id=user_id, action='send_rent_menu').log(
            "USER_ACTION",
            f"PERF: fetch_related_total={fetch_related_total:.3f}s rents_count={count} build_ui={(t2 - t1):.3f}s"
        )

        # Добавляем кнопку для аренды нового номера
        keyboard.inline_keyboard.append(
            [types.InlineKeyboardButton(text=bt.RENT_NEW_ROOM, callback_data="new_number")]
        )

        # Отправляем сообщение с inline клавиатурой
        t_send0 = time.perf_counter()
        if message:
            await message.answer("\n".join(rent_details), reply_markup=keyboard)
        else:
            await callback_query.message.edit_text("\n".join(rent_details), reply_markup=keyboard)
        t_send1 = time.perf_counter()

        logger.bind(user_id=user_id, action='send_rent_menu').log(
            "USER_ACTION",
            f"PERF: telegram_send={(t_send1 - t_send0):.3f}s total={(t_send1 - t0):.3f}s"
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в send_rent_menu: {e}")



@router.callback_query(F.data == "back_to_rent_menu")
async def back_to_rent_menu(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка кнопки 'Назад', возвращающая в меню аренды номеров.
    """
    user_id = callback_query.from_user.id
    logger.bind(user_id=user_id, action='back_to_rent_menu').log("USER_ACTION", f"Пользователь вернулся в меню аренды")
    try:
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action='back_to_rent_menu').log("USER_ACTION",
                                                                    f"Запрос к БД: получение данных пользователя {user_id}, баланс = {user.balance} ₽")
        # Отправляем меню аренды
        await send_rent_menu(user, callback_query=callback_query)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /back_to_rent_menu: {e}")


# Обработчик для нажатия на кнопку арендованного номера
@router.callback_query(F.data.startswith('number_'))
async def rent_number_selected(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка выбора арендованного номера
    """
    user_id = callback_query.from_user.id
    user = await models.User.get_user(user_id)
    logger.bind(user_id=user_id, action='rent_number_selected').log("USER_ACTION", f"Пользователь выбрал арендованный номер, баланс = {user.balance} ₽")
    try:
        rent_id = int(callback_query.data.split('_')[1])  # Извлекаем id аренды из callback_data
        logger.bind(user_id=user_id, action='rent_number_selected').log("USER_ACTION",
                                                                      f"Выбрана аренда с ID={rent_id}")
        rented = await models.Rent.get_rent(id=rent_id)  # Получаем аренду по id
        logger.bind(user_id=user_id, action='rent_number_selected').log("USER_ACTION",
                                                                      f"Запрос к БД: получение аренды с ID={rent_id}")
        if rented:
            # Проверяем, отменена ли аренда
            if rented.is_canceled:
                await callback_query.answer("Эта аренда была отменена.", show_alert=True)
                logger.bind(user_id=user_id, action='rent_number_selected').log("USER_ACTION",
                                                                              f"Аренда ID={rent_id} отменена, баланс = {user.balance} ₽")
                return
            # Формируем информацию о номере
            country = await models.Rent.get_country_by_rent(id=rent_id)
            phone_number = rented.phone_number
            expiry_date = rented.rent_expire_at.strftime("%d.%m.%y %H:%M")  # Пример формата даты
            flag = country_flags.get(country, "")  # Получаем флаг по имени страны
            sms = rented.sms_text
            # Текст для отправки пользователю
            rent_details = bt.RENT_DETAILS.format(
                phone_number=phone_number,
                country=country,
                expiry_date=expiry_date,
                flag=flag
            )
            if sms:
                # Форматируем сообщения для вывода
                formatted_sms = "\n".join(
                    [f"• <b>{service}:</b> {text}" for line in sms.split("\n") if
                     (split_line := line.split(": ", 1)) and len(split_line) == 2 and (service := split_line[0]) and (
                         text := split_line[1])]
                )
                # Добавляем блок с сообщениями
                rent_details += f"\n<b>Ваши сообщения:</b>\n{formatted_sms}"

                # Логируем завершение обработки и текущий баланс
                logger.bind(user_id=user_id, action='rent_number_selected').log(
                    "USER_ACTION",
                    f"Отображение аренды: номер={phone_number}, страна={country}, истекает={expiry_date}, есть_sms={bool(sms)}, баланс = {user.balance} ₽"
                )

            # Создаем inline клавиатуру
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
            # Кнопки для управления автопродлением
            if rented.autorenew:
                keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="✅ Автопродление включено",
                                                                           callback_data=f"auto_renew_{rent_id}")])
            else:
                keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="❌Автопродление выключено",
                                                                           callback_data=f"auto_renew_{rent_id}")])
            # Кнопки для продления и отмены аренды
            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(text="🔄 Продлить аренду", callback_data=f"extend_rent_{rent_id}"),
                types.InlineKeyboardButton(text="🚫 Отменить аренду", callback_data=f"cancel_rent_{rent_id}")
            ])
            # Кнопка для возврата
            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_rent_menu")
            ])
            # Отправляем информацию о номере и клавиатуру
            await callback_query.message.edit_text(rent_details, reply_markup=keyboard)
        else:
            logger.bind(user_id=user_id, action='rent_number_selected').log("USER_ACTION",
                                                                          f"Аренда ID={rent_id} не найдена, баланс = {user.balance} ₽")
            await callback_query.answer("Номер не найден.")
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /rent_number_selected: {e}")


# Обработчик изменения состояния автопродления
@router.callback_query(F.data.startswith('auto_renew_'))
async def toggle_autorenew(callback_query: types.CallbackQuery):
    """
    Обработка переключения состояния автопродления.
    """
    user_id = callback_query.from_user.id
    user = await models.User.get_user(user_id)
    logger.bind(user_id=user_id, action='toggle_autorenew').log("USER_ACTION", f"Пользователь изменяет состояние автопродления, баланс = {user.balance} ₽")
    try:
        rent_id = int(callback_query.data.split('_')[2])  # Извлекаем id аренды из callback_data
        logger.bind(user_id=user_id, action='toggle_autorenew').log("USER_ACTION",
                                                                   f"Обновление автопродления для аренды ID={rent_id}")
        # Получаем аренду по id
        rented = await models.Rent.get_rent(id=rent_id)
        logger.bind(user_id=user_id, action='toggle_autorenew').log("USER_ACTION",
                                                                   f"Запрос к БД: получение аренды с ID={rent_id}")
        if not rented:
            logger.bind(user_id=user_id, action='toggle_autorenew').log("USER_ACTION",
                                                                       f"Аренда ID={rent_id} не найдена")
            await callback_query.answer("Аренда не найдена.", show_alert=True)
            return
        # Переключаем значение autorenew
        old_state = rented.autorenew
        rented.autorenew = not rented.autorenew
        # Обновляем статус уведомления
        rented.is_notified = False
        await rented.save()
        logger.bind(user_id=user_id, action='toggle_autorenew').log("USER_ACTION",
                                                                   f"Результат из БД: автопродление для аренды ID={rent_id} изменено с {old_state} на {rented.autorenew}, баланс = {user.balance} ₽")
        # Сообщаем пользователю о новом состоянии
        new_state = "включено" if rented.autorenew else "выключено"
        await callback_query.answer(f"Автопродление {new_state}.")
        # Формируем информацию о номере
        country = await models.Rent.get_country_by_rent(id=rent_id)
        phone_number = rented.phone_number
        expiry_date = rented.rent_expire_at.strftime("%d.%m.%y %H:%M")  # Пример формата даты
        flag = country_flags.get(country, "")  # Получаем флаг по имени страны
        sms = rented.sms_text
        # Текст для отправки пользователю
        rent_details = bt.RENT_DETAILS.format(
            phone_number=phone_number,
            country=country,
            expiry_date=expiry_date,
            flag=flag
        )
        if sms:
            # Форматируем сообщения для вывода
            formatted_sms = "\n".join(
                [f"• <b>{service}:</b> {text}" for line in sms.split("\n") if
                 (split_line := line.split(": ", 1)) and len(split_line) == 2 and (service := split_line[0]) and (
                     text := split_line[1])]
            )
            # Добавляем блок с сообщениями
            rent_details += f"\n<b>Ваши сообщения:</b>\n{formatted_sms}"
        # Формируем обновленные кнопки
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
        # Кнопка автопродления
        if rented.autorenew:
            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(
                    text="✅ Автопродление включено", callback_data=f"auto_renew_{rent_id}"
                )
            ])
        else:
            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(
                    text="❌ Автопродление выключено", callback_data=f"auto_renew_{rent_id}"
                )
            ])
        # Кнопки для продления и отмены аренды
        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(
                text="🔄 Продлить аренду", callback_data=f"extend_rent_{rent_id}"
            ),
            types.InlineKeyboardButton(
                text="🚫 Отменить аренду", callback_data=f"cancel_rent_{rent_id}"
            )
        ])
        # Кнопка для возврата
        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(
                text="🔙 Назад", callback_data="back_to_rent_menu"
            )
        ])
        await callback_query.message.edit_text(rent_details, reply_markup=keyboard)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /toggle_autorenew: {e}")


# Арендовать новый номер
@router.callback_query(F.data.startswith('new_number'))
async def rent_new_number(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка аренды нового номера
    """
    user_id = callback_query.from_user.id
    logger.bind(user_id=user_id, action='rent_new_number').log("USER_ACTION", f"Пользователь начал аренду нового номера")
    try:
        # Передаем только необходимые данные для восстановления
        context_data = {
            'chat_id': callback_query.message.chat.id,
            'message_id': callback_query.message.message_id
        }
        # Завершаем текущий диалог или возвращаем в предыдущий
        logger.bind(user_id=user_id, action='rent_new_number').log("USER_ACTION",
                                                                  f"Переход к выбору страны для аренды нового номера")
        await dialog_manager.start(
            RentCountryMenu.select_country,  # Состояние для выбора страны
            context_data  # Передаем только нужные данные
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /rent_new_number: {e}")


# Отмена аренды
@router.callback_query(F.data.startswith('cancel_rent_'))
async def cancel_rent(callback_query: types.CallbackQuery):
    """
    Обработка отмены аренды номера.
    """
    user_id = callback_query.from_user.id
    user = await models.User.get_user(user_id)
    logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION", f"Пользователь начал отмену аренды, баланс = {user.balance} ₽")
    try:
        rent_id = int(callback_query.data.split('_')[2])  # Извлекаем id аренды из callback_data
        logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION",
                                                               f"Отмена аренды с ID={rent_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION",
                                                               f"Запрос к БД: получение данных пользователя {user_id}, баланс = {user.balance} ₽")
        # Проверяем, существует ли аренда и не отменена ли она
        rented = await models.Rent.get_rent(id=rent_id)
        logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION",
                                                               f"Запрос к БД: получение аренды с ID={rent_id}")
        if not rented:
            logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION",
                                                                   f"Аренда ID={rent_id} не найдена, баланс = {user.balance} ₽")
            await callback_query.answer(bt.RENT_NOT_FOUND_MSG, show_alert=True)
            await callback_query.message.delete()
            return
        if rented.is_canceled:
            logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION",
                                                                   f"Аренда ID={rent_id} уже отменена, баланс = {user.balance} ₽")
            await callback_query.answer(bt.RENT_ALREADY_CANCELED_MSG, show_alert=True)
            await callback_query.message.delete()
            return
        # Используем API для отмены аренды
        api = OnlineSimRentAPI()  # Создаем экземпляр API
        # Если статус аренды в ожидании, то возвращаем баланс
        if rented.status == StatusResponse.STATUS_WAIT_CODE and not rented.refund_processed:
            user.balance += rented.cost
            rented.refund_processed = True
            await rented.save()
            await user.save()
            logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION",
                                                                   f"Баланс пользователя увеличен на {rented.cost}, баланс = {user.balance} ₽")
        try:
            response = await api.close_rent_num(tzid=rented.rent_id)  # Передаем ID операции аренды
            if response.get("response"):
                # Успешно отменено, обновляем статус аренды в базе данных
                rented.is_canceled = True
                await rented.save()  # Сохраняем изменения в базе данных
                logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION",
                                                                       f"Аренда ID={rent_id} успешно отменена, баланс = {user.balance} ₽")
                await callback_query.answer(bt.RENT_CANCEL_SUCCESS_MSG, show_alert=True)
                # Удаляем сообщение о текущей аренде
                await callback_query.message.delete()
                # Обновляем меню аренды
                await send_rent_menu(user, callback_query=callback_query)
            else:
                # Если API вернул неизвестный ответ
                await callback_query.answer(bt.RENT_CANCEL_FAILED_MSG, show_alert=True)
                logger.error(f"Неизвестный ответ API при отмене аренды: {response}")
        except Exception as e:
            # Обрабатываем специфическую ошибку
            if "ERROR_NO_OPERATIONS" in str(e):
                logger.error(e)
                await callback_query.answer(bt.RENT_CANCEL_SUCCESS_MSG, show_alert=True)
            else:
                await callback_query.answer(bt.RENT_CANCEL_FAILED_MSG, show_alert=True)
            # Удаляем сообщение о текущей аренде
            await callback_query.message.delete()
            # В случае ошибки переводим аренду в статус отмененной
            rented.is_canceled = True
            await rented.save()  # Сохраняем изменения в базе данных
            logger.bind(user_id=user_id, action='cancel_rent').log("USER_ACTION",
                                                                   f"Аренда ID={rent_id} переведена в статус отмененной, баланс = {user.balance} ₽")
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /cancel_rent: {e}")


# Продление аренды
@router.callback_query(F.data.startswith('extend_rent_'))
async def extend_rent(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка продления аренды.
    """
    user_id = callback_query.from_user.id
    user = await models.User.get_user(user_id)
    logger.bind(user_id=user_id, action='extend_rent').log("USER_ACTION", f"Пользователь начал продление аренды, баланс = {user.balance} ₽")
    try:
        rent_id = int(callback_query.data.split('_')[2])  # Извлекаем id аренды из callback_data
        logger.bind(user_id=user_id, action='extend_rent').log("USER_ACTION",
                                                               f"Продление аренды с ID={rent_id}")
        rented = await models.Rent.get_rent(id=rent_id)  # получаем объект Rent
        logger.bind(user_id=user_id, action='extend_rent').log("USER_ACTION",
                                                               f"Запрос к БД: получение аренды с ID={rent_id}")
        # Получаем состояние аренды через OnlineSimRentAPI
        api_client = OnlineSimRentAPI()
        rent_state = await api_client.get_rent_state(tzid=rented.rent_id)
        logger.bind(user_id=user_id, action='extend_rent').log("USER_ACTION",
                                                               f"Результат API: состояние аренды {rent_state}")
        # Проверяем, если список пуст или extend отсутствует
        if not rent_state or not rent_state.get("list") or not rent_state["list"]:
            await callback_query.answer(text=bt.RENTAL_CANCELED_OR_NOT_FOUND, show_alert=True)
            return
        if "extend" not in rent_state["list"][0]:
            await callback_query.answer(text=bt.FAILED_TO_GET_AVAILABLE_DAYS, show_alert=True)
            return
        rent_country_code = rented.country.country_id
        country = rented.country.name
        # Извлекаем тарифы для выбранной страны
        data = await api_client.get_tariffs()
        try:
            tariffs = data.get(str(rent_country_code), {})
        except Exception as e:
            tariffs = None
            logger.error(e)
        # Преобразуем тарифы: умножаем цены на DOLLAR_RATE
        if tariffs:  # Проверяем, есть ли данные
            updated_tariffs = {days: round(price * DOLLAR_ONLINESIM) for days, price in tariffs.items()}
        else:
            days = rented.days
            cost = rented.cost
            updated_tariffs= {days: cost}

        context_data = {
            "selected_country": {
                "rent_country_code": rent_country_code,
                "country": country,
                "tariffs": updated_tariffs,  # Сохраняем только нужные тарифы
                "tzid": rented.rent_id,
            }}
        logger.bind(user_id=user_id, action='extend_rent').log("USER_ACTION",
                                                               f"Переход к деталям страны для продления аренды, баланс = {user.balance} ₽")
        await dialog_manager.start(
            RentCountryMenu.country_details,  # Состояние для выбора страны
            context_data  # Передаем только нужные данные
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_rent: {e}")


# Пополнение баланса
@router.callback_query(F.data.startswith('top_up_balance'))
async def top_up_balance(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка пополнения баланса
    """
    user_id = callback_query.from_user.id
    logger.bind(user_id=user_id, action='top_up_balance').log("USER_ACTION", f"Пользователь начал пополнение баланса")
    try:
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action='top_up_balance').log("USER_ACTION",
                                                                 f"Запрос к БД: получение данных пользователя {user_id}, баланс = {user.balance} ₽")
        context_data = {
            'user_id': user.telegram_id,
            'auto_renewal': True,
        }
        logger.bind(user_id=user_id, action='top_up_balance').log("USER_ACTION",
                                                                 f"Переход к окну пополнения баланса")
        await dialog_manager.start(
            PersonalMenu.deposit,  # Состояние для выбора страны
            context_data  # Передаем только нужные данные
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /top_up_balance: {e}")