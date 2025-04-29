from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram_dialog import DialogManager, StartMode
from pyonlinesim import OnlineSMS
from aiogram.exceptions import TelegramBadRequest
from app.db import models
from app.dependencies import API_KEY_ONLINESIM, bot
from app.dialogs.receive_sms.getters import service_is_smsactivate
from app.dialogs.receive_sms.selected import send_service_info_with_keyboard
from app.dialogs.receive_sms.states import ServiceMenu
from app.services import bot_texts as bt
from app.services.bot_texts import SERVICES_TRANSLATION
from app.services.mail.receive_messages import fetch_full_message
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from app.services.sms_receive import SmsReceive
from loguru import logger
import html
from bs4 import BeautifulSoup
import re

router = Router()


def log_exceptions(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в обработчике {func.__name__}: {e}")
            raise  # Возможно, чтобы повторно вызвать ошибку и не скрывать её

    return wrapper


@router.message(Command("get_sms"))
@router.message(F.text == bt.RECEIVE_SMS_BTN)
async def receive_sms(message: types.Message, dialog_manager: DialogManager):
    user = await models.User.get_user(message.from_user.id)

    activation = await models.Activation.get_active_activation(user.id)

    if activation is None:
        sub = await check_subscribe(user)
        if not sub:
            await send_subscribe_msg(user)
            return

        await dialog_manager.start(ServiceMenu.select_service, mode=StartMode.RESET_STACK)
    else:
        await activation.fetch_related('country')
        country = activation.country.name
        service = await models.ServicesSmsActivate.get_service_name_by_id(service_id=activation.service_id)
        await send_service_info_with_keyboard(message=message, activation=activation, service=service, country=country)


# Обрабатываем кнопку принять смс для другого сервиса
@router.callback_query(F.data.startswith('receive_sms_for_another_service'))
async def receive_sms_for_another_service(call: types.CallbackQuery, dialog_manager: DialogManager):
    await dialog_manager.reset_stack()
    await dialog_manager.start(ServiceMenu.select_service, mode=StartMode.RESET_STACK)


# Обработчик колл бека для принятия смс повторно, пока не используется
@router.callback_query(F.data.startswith('request_code:'))
@log_exceptions
async def request_code(call: types.CallbackQuery, **kwargs):
    # Извлечение id активации из данных колл бека
    activation_id = int(call.data.split(':')[1])

    # Поиск объекта активации по id
    activation = await models.Activation.get_or_none(id=activation_id).prefetch_related('service')

    # Если активация не найдена, завершить обработку колл бека
    if not activation:
        await call.answer()
        return

    # если активация относится к onlinesim
    try:
        service = activation.service_2.code
    except AttributeError:
        return
    if service in SERVICES_TRANSLATION:
        client = OnlineSMS(api_key=API_KEY_ONLINESIM)
        try:
            revise_response = await client.revise_order(operation_id=activation_id)
            if revise_response.get("response") == '1':
                await call.answer(text='ожидание повторной отправки смс', show_alert=True)
                return
            else:
                await call.answer(text='Попробуйте позже')
                return
        except Exception as e:
            logger.warning(f'Повторный запрос смс onlinesim {e}')
            return

    else:
        # Создание объекта для получения смс
        sms = SmsReceive()

        # Получение текущего статуса активации
        status = str(await sms.get_activation_status(activation.activation_id))

        # Проверка статуса активации на ожидание кода
        if status:  # == models.StatusResponse.STATUS_WAIT_CODE.name and activation.status == models.StatusResponse.STATUS_WAIT_CODE:

            request_status = str(await sms.set_activation_status(activation_id=activation.activation_id,
                                                                 status=models.ActivationCode.RETRY_GET))
            # Проверка если статус изменен на ожидание повторной отправки смс
            if request_status == "STATUS_WAIT_RETRY ":
                await call.answer(text='ожидание повторной отправки смс', show_alert=True)
                return

            # Если статус изменен на ожидание кода
            if request_status != "STATUS_WAIT_CODE":
                await call.answer(text='ожидание смс')
                return

            # Если статус изменен на отмену
            if request_status != "STATUS_CANCEL":
                await call.answer(text='активация отменена')
                return

            # Если статус изменен на успешное получение кода
            if request_status != "STATUS_OK":
                await call.answer(text='код получен')
                return


from aiogram.exceptions import TelegramAPIError
from datetime import datetime

@router.callback_query(F.data.startswith('cancel_service:'))
async def cancel_service(call: types.CallbackQuery, **kwargs):
    try:
        # Извлекаем идентификатор активации из данных callback
        activation_id = int(call.data.split(':')[1])

        # Пытаемся получить объект активации из базы данных по идентификатору
        if await service_is_smsactivate():
            activation = await models.Activation.get_or_none(id=activation_id).prefetch_related('service')
        else:
            activation = await models.Activation.get_or_none(id=activation_id).prefetch_related('service_2')

        # Если активация не найдена, просто отвечаем на callback и выходим
        if not activation:
            await call.answer(text='Номер автоматически отменится через 10 минут', show_alert=True)
            await call.answer()
            return

        formatted_time = activation.activation_expire_at.strftime("%H:%M")

        # Проверяем, относится ли активация к service_onlinesim
        try:
            if await service_is_smsactivate():
                service = activation.service.code
            else:
                service = activation.service_2.code
        except AttributeError:
            await call.answer(text=f'Номер автоматически отменится в {formatted_time}', show_alert=True)
            return
        cancellation_successful = False
        # Выбор API клиента в зависимости от типа услуги
        if service in SERVICES_TRANSLATION or not await service_is_smsactivate():
            client = OnlineSMS(api_key=API_KEY_ONLINESIM)
            cancel_status = await client.finish_order(operation_id=activation.activation_id, ban=False)
            cancellation_successful = cancel_status.get("response") == 1
            # Unable to finish order - когда менее 2 минут
            # Wrong operation ID - отмена не доступна
        else:
            sms = SmsReceive()
            status = str(await sms.set_activation_status(activation_id=activation.activation_id,
                                                         status=models.ActivationCode.CANCEL))
            if status == 'STATUS_WAIT_CODE':
                await call.answer(text='Ожидание смс', show_alert=True)
                return

            if status == 'EARLY_CANCEL_DENIED':
                await call.answer(text='Нельзя отменить в первые 2 минуты', show_alert=True)
                return

            if status == "ACCESS_CANCEL":
                cancellation_successful = True

        if cancellation_successful:
            activation.activation_expire_at = None
            activation.status = models.StatusResponse.STATUS_CANCEL
            await activation.save()

            user = await models.User.get_user(telegram_id=call.from_user.id)
            if activation.sms_text is None:
                user.balance += activation.cost
                await user.save()
                msg_text = bt.SERVICE_CANCEL_MONEY_RETURNED.strip()
            else:
                msg_text = bt.SERVICE_CANCEL.strip()

            # Проверяем, изменился ли текст или клавиатура, и выполняем изменения только при необходимости
            if call.message.text.strip() != msg_text or call.message.reply_markup is not None:
                try:
                    await call.message.edit_text(text=msg_text)
                    await call.message.edit_reply_markup(reply_markup=None)
                except TelegramAPIError as e:
                    pass
        else:
            await call.answer(text='Отмена больше не доступна', show_alert=True)


    except TelegramAPIError as e:
        logger.warning(f"Telegram server error: {e}")
    except Exception as e:
        error_text = str(e)
        if error_text == 'Unable to finish order':
            await call.answer(text='Нельзя отменить в первые 2 минуты', show_alert=True)
        elif error_text == 'Wrong operation ID':
            activation.activation_expire_at = None
            activation.status = models.StatusResponse.STATUS_CANCEL
            await activation.save()
            await call.answer(text='Отмена больше не доступна', show_alert=True)
        elif error_text == 'Try again later':
            await call.answer(text='Повторите попытку позже', show_alert=True)
        else:
            text = error_text[0].upper() + error_text[1:] if error_text else "Неизвестная ошибка"
            logger.error(f"Необработанное исключение: {text}")
            await call.answer(text=f'Ошибка при отмене номера. \n{text}', show_alert=True)


@router.callback_query(F.data.startswith('full_unread_message|'))
@log_exceptions
async def unread_message(call: types.CallbackQuery, **kwargs):
    _, message_id, mail_id = call.data.split("|")
    mail_id = int(mail_id)

    mail = await models.Mail.get_or_none(id=mail_id).prefetch_related("user")
    if not mail:
        print(f"Ошибка: Mail с id={mail_id} не найден")
        return

    text = await fetch_full_message(mail.token, message_id)

    # Удаляем HTML-теги <a> и <img>
    soup = BeautifulSoup(text, "html.parser")
    for a in soup.find_all("a"):
        a.decompose()
    for img in soup.find_all("img"):
        img.decompose()

    # Получаем очищенный текст
    cleaned_text = soup.get_text()

    # Удаляем ссылки вида "https://example.com"
    cleaned_text = re.sub(r"https?://\S+", "", cleaned_text)

    # Удаляем ссылки в формате [text](https://example.com)
    cleaned_text = re.sub(r"\[.*?\]\(https?://\S+\)", "", cleaned_text)

    # Экранируем HTML
    cleaned_text = html.escape(cleaned_text)

    msg_text = (
        f'📩<b>Полный текст сообщения</b> на почту: <b>{mail.email}</b>\n\n'
        f'{cleaned_text}'
    )

    if len(msg_text) <= 4096:
        await bot.send_message(chat_id=mail.user.telegram_id, text=msg_text, parse_mode="HTML")
    else:
        parts = await split_message(msg_text, 4096)
        for part in parts:
            await bot.send_message(chat_id=mail.user.telegram_id, text=part, parse_mode="HTML")



async def split_message(text: str, max_length: int) -> list:
    """Разбивает длинное сообщение на части, не превышающие max_length."""
    lines = text.split('\n')
    parts = []
    current_part = ""

    for line in lines:
        if len(current_part) + len(line) + 1 > max_length:
            parts.append(current_part)
            current_part = ""
        current_part += line + '\n'

    if current_part:
        parts.append(current_part)

    return parts
