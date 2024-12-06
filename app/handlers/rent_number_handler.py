from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram_dialog import DialogManager, StartMode
from app.db import models
from app.dialogs.rent_sms import states
from app.dialogs.rent_sms.states import RentCountryMenu
from app.services import bot_texts as bt
from app.services.bot_texts import country_flags
from app.services.need_subscribe import check_subscribe, send_subscribe_msg

router = Router()


@router.message(Command("rent_number"))
@router.message(F.text == bt.RENT_NUMBER)
async def rent_number(message: types.Message, dialog_manager: DialogManager):
    """
    📞Арендовать номер
    """
    user = await models.User.get_user(message.from_user.id)

    # Проверяем подписку
    sub = await check_subscribe(user)
    if not sub:
        await send_subscribe_msg(user)
        return

    # Проверяем аренды пользователя
    activation_list = await models.Rent.get_active_rent(user.id)

    # если нет активных арендных номеров, то предлагаем
    if activation_list is None:
        await dialog_manager.start(RentCountryMenu.select_country, mode=StartMode.RESET_STACK)
        return

    # Отправляем меню аренды
    await send_rent_menu(user, message=message)


async def send_rent_menu(user: "User", message: types.Message = None, callback_query: types.CallbackQuery = None):
    """
    Вспомогательная функция для отправки меню аренды номеров.
    """
    # Проверяем аренды пользователя
    activation_list = await models.Rent.get_active_rent(user.id)

    # Создаем список для вывода информации
    rent_details = ["<i>Ваши арендованные номера⤵️</i>\n"]

    # Создаем inline клавиатуру
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])

    # Проходим по всем арендам
    for activation in activation_list:
        # Загружаем связанные данные о стране
        await activation.fetch_related('country')
        country = activation.country.name
        # Формируем строку с флагом и номером
        flag = country_flags.get(country, "")  # Получаем флаг по имени страны
        phone_number = activation.phone_number

        # Формируем текст кнопки (флаг + номер)
        button_text = f"{flag} +{phone_number}"

        # Создаем кнопку с уникальным callback_data для каждого номера
        callback_data = f"number_{activation.id}"

        # Добавляем кнопку в клавиатуру
        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(text=button_text, callback_data=callback_data)
        ])

    # Добавляем кнопку для аренды нового номера
    keyboard.inline_keyboard.append([
        types.InlineKeyboardButton(text="📞Арендовать новый номер", callback_data="new_number")
    ])

    # Отправляем сообщение с inline клавиатурой
    if message:
        await message.answer("\n".join(rent_details), reply_markup=keyboard)
    else:
        await callback_query.message.edit_text("\n".join(rent_details), reply_markup=keyboard)


@router.callback_query(F.data == "back_to_rent_menu")
async def back_to_rent_menu(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка кнопки 'Назад', возвращающая в меню аренды номеров.
    """
    user = await models.User.get_user(callback_query.from_user.id)

    # Отправляем меню аренды
    await send_rent_menu(user, callback_query=callback_query)


# Обработчик для нажатия на кнопку арендованного номера
@router.callback_query(F.data.startswith('number_'))
async def rent_number_selected(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Обработка выбора арендованного номера
    """
    rent_id = int(callback_query.data.split('_')[1])  # Извлекаем id аренды из callback_data
    # Логика обработки выбранного номера
    rented = await models.Rent.get_rent(id=rent_id)  # Получаем аренду по id
    if rented:
        # Формируем информацию о номере
        country = await models.Rent.get_country_by_rent(id=rent_id)
        phone_number = rented.phone_number
        expiry_date = rented.rent_expire_at.strftime("%d.%m.%y %H:%M")  # Пример формата даты
        flag = country_flags.get(country, "")  # Получаем флаг по имени страны

        # Текст для отправки пользователю
        rent_details = (
            f"<i>Ваш арендованный номер:</i> {flag} +{phone_number}\n"
            f"<i>Страна:</i> {flag}{country}\n"
            f"<i>Срок аренды до:</i> {expiry_date}\n"
        )

        # Создаем inline клавиатуру
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])

        # Проверяем статус автопродления
        autorenew_enabled = await models.Rent.is_autorenew_enabled(rent_id)

        # Кнопки для управления автопродлением
        if autorenew_enabled:
            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(
                    text="✅ Автопродление включено", callback_data=f"auto_renew_{rent_id}"
                )
            ])
        else:
            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(
                    text="❌Автопродление выключено", callback_data=f"auto_renew_{rent_id}"
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

        # Отправляем информацию о номере и клавиатуру
        await callback_query.message.edit_text(rent_details, reply_markup=keyboard)

    else:
        await callback_query.answer("Номер не найден.")


@router.callback_query(F.data.startswith('auto_renew_'))
async def toggle_autorenew(callback_query: types.CallbackQuery):
    """
    Обработка переключения состояния автопродления.
    """
    rent_id = int(callback_query.data.split('_')[2])  # Извлекаем id аренды из callback_data
    # Получаем аренду по id
    rented = await models.Rent.get_rent(id=rent_id)
    if not rented:
        await callback_query.answer("Аренда не найдена.", show_alert=True)
        return

    # Переключаем значение autorenew
    rented.autorenew = not rented.autorenew
    await rented.save()

    # Сообщаем пользователю о новом состоянии
    new_state = "включено" if rented.autorenew else "выключено"
    await callback_query.answer(f"Автопродление {new_state}.")

    # Формируем информацию о номере
    country = await models.Rent.get_country_by_rent(id=rent_id)
    phone_number = rented.phone_number
    expiry_date = rented.rent_expire_at.strftime("%d.%m.%y %H:%M")  # Пример формата даты
    flag = country_flags.get(country, "")  # Получаем флаг по имени страны
    # Текст для отправки пользователю
    rent_details = (
        f"<i>Ваш арендованный номер:</i> {flag} +{phone_number}\n"
        f"<i>Страна:</i> {flag}{country}\n"
        f"<i>Срок аренды до:</i> {expiry_date}\n"
    )

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


@router.callback_query(F.data.startswith('new_number'))
async def rent_new_number(callback_query: types.CallbackQuery, dialog_manager: DialogManager):
    # Передаем только необходимые данные для восстановления
    context_data = {
        'chat_id': callback_query.message.chat.id,
        'message_id': callback_query.message.message_id
    }

    # Завершаем текущий диалог или возвращаем в предыдущий
    await dialog_manager.start(
        states.RentCountryMenu.select_country,  # Состояние для выбора страны
        context_data  # Передаем только нужные данные
    )
