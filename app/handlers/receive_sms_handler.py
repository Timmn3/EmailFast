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
            logger.opt(exception=e).error(f"Ошибка в обработчике {func.__name__}: {e}")
            raise
    return wrapper


@router.message(Command("get_sms"))
@router.message(F.text == bt.RECEIVE_SMS_BTN)
async def receive_sms(message: types.Message, dialog_manager: DialogManager):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Пользователь запросил получение SMS")
        logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", f"Результат из БД: пользователь найден={user is not None}, баланс = {user.balance} ₽")

        if not user:
            return

        logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Проверка активации")
        activation = await models.Activation.get_active_activation(user.id)

        if activation is None:
            logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Проверка подписки")
            sub = await check_subscribe(user)
            if not sub:
                logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Подписка неактивна, отправляем сообщение")
                await send_subscribe_msg(user)
                return
            logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Запуск диалога выбора сервиса")
            await dialog_manager.start(ServiceMenu.select_service, mode=StartMode.RESET_STACK)
        else:
            logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Получение информации о текущей активации")
            await activation.fetch_related('country')
            country = activation.country.name
            service = await models.ServicesSmsActivate.get_service_name_by_id(service_id=activation.service_id)
            if service is None:
                service = await models.ServicesOnlinesim.get_service_name_by_id(service_id=activation.service_2_id)
            logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", f"Текущая активация: сервис={service}, страна={country}")
            await send_service_info_with_keyboard(message=message, activation=activation, service=service, country=country)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_sms: {e}")


@router.callback_query(F.data == 'receive_sms_for_another_service')
async def receive_sms_for_another_service(call: types.CallbackQuery, dialog_manager: DialogManager):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="receive_sms_for_another_service").log("USER_ACTION", "Принять SMS для другого сервиса")
        await dialog_manager.reset_stack()
        await dialog_manager.start(ServiceMenu.select_service, mode=StartMode.NORMAL)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_sms_for_another_service: {e}")


@router.callback_query(F.data.startswith('request_code:'))
@log_exceptions
async def request_code(call: types.CallbackQuery, **kwargs):
    try:
        user_id = call.from_user.id
        activation_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", f"Запрос к БД: получение активации ID={activation_id}")
        activation = await models.Activation.get_or_none(id=activation_id).prefetch_related('service')
        logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", f"Результат из БД: активация найдена={activation is not None}")

        if not activation:
            await call.answer()
            return

        try:
            service = activation.service_2.code
        except AttributeError:
            logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", "Ошибка: сервис не найден")
            return

        if service in SERVICES_TRANSLATION:
            logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", "Используется сервис Onlinesim")
            client = OnlineSMS(api_key=API_KEY_ONLINESIM)
            try:
                revise_response = await client.revise_order(operation_id=activation_id)
                if revise_response.get("response") == '1':
                    logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", "Ожидание повторной отправки SMS")
                    await call.answer(text='ожидание повторной отправки смс', show_alert=True)
                    return
                else:
                    logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", "Ошибка: повторная отправка недоступна")
                    await call.answer(text='Попробуйте позже')
                    return
            except Exception as e:
                logger.opt(exception=e).warning(f'Повторный запрос смс onlinesim {e}')
                return
        else:
            logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", "Используется другой сервис")
            sms = SmsReceive()
            status = str(await sms.get_activation_status(activation.activation_id))
            logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", f"Текущий статус активации: {status}")

            if status:
                request_status = str(await sms.set_activation_status(activation_id=activation.activation_id,
                                                                     status=models.ActivationCode.RETRY_GET))
                logger.bind(user_id=user_id, action="request_code").log("USER_ACTION", f"Новый статус: {request_status}")

                if request_status == "STATUS_WAIT_RETRY":
                    await call.answer(text='ожидание повторной отправки смс', show_alert=True)
                    return
                elif request_status != "STATUS_WAIT_CODE":
                    await call.answer(text='ожидание смс')
                    return
                elif request_status != "STATUS_CANCEL":
                    await call.answer(text='активация отменена')
                    return
                elif request_status != "STATUS_OK":
                    await call.answer(text='код получен')
                    return
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /request_code: {e}")


@router.callback_query(F.data.startswith('cancel_service:'))
async def cancel_service(call: types.CallbackQuery, **kwargs):
    user_id = call.from_user.id
    try:
        user = await models.User.get_user(telegram_id=call.from_user.id)
        activation_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION", f"Пользователь запрашивает отмену активации ID={activation_id}, баланс = {user.balance} ₽")
        if await service_is_smsactivate():
            activation = await models.Activation.get_or_none(id=activation_id).prefetch_related('service')
        else:
            activation = await models.Activation.get_or_none(id=activation_id).prefetch_related('service_2')
        logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION", f"Результат из БД: активация найдена={activation is not None}")

        if not activation:
            logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION", "Активация не найдена")
            await call.answer(text='Номер автоматически отменится через 10 минут', show_alert=True)
            await call.answer()
            return

        formatted_time = activation.activation_expire_at.strftime("%H:%M")
        try:
            if await service_is_smsactivate():
                service = activation.service.code
            else:
                service = activation.service_2.code
        except AttributeError:
            logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION", "Ошибка: сервис не найден")
            await call.answer(text=f'Номер автоматически отменится в {formatted_time}', show_alert=True)
            return

        cancellation_successful = False
        if service in SERVICES_TRANSLATION or not await service_is_smsactivate():
            client = OnlineSMS(api_key=API_KEY_ONLINESIM)
            cancel_status = await client.finish_order(operation_id=activation.activation_id, ban=False)
            cancellation_successful = cancel_status.get("response") == 1
        else:
            sms = SmsReceive()
            status = str(await sms.set_activation_status(activation_id=activation.activation_id,
                                                         status=models.ActivationCode.CANCEL))
            if status == 'STATUS_WAIT_CODE':
                await call.answer(text='Ожидание смс', show_alert=True)
                return
            elif status == 'EARLY_CANCEL_DENIED':
                await call.answer(text='Нельзя отменить в первые 2 минуты', show_alert=True)
                return
            elif status == "ACCESS_CANCEL":
                cancellation_successful = True

        if cancellation_successful:
            activation.activation_expire_at = None
            activation.status = models.StatusResponse.STATUS_CANCEL
            await activation.save()
            user = await models.User.get_user(telegram_id=call.from_user.id)
            if activation.sms_text is None:
                user.balance += activation.cost
                await user.save()
                logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION", f"Если нет смс, возвращаем деньги, баланс = {user.balance} ₽")
                msg_text = bt.SERVICE_CANCEL_MONEY_RETURNED.strip()
            else:
                msg_text = bt.SERVICE_CANCEL.strip()
            try:
                if call.message.text.strip() != msg_text or call.message.reply_markup is not None:
                    await call.message.edit_text(text=msg_text)
                    # await call.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest as e:
                logger.opt(exception=e).warning("Не удалось изменить сообщение или клавиатуру")
        else:
            await call.answer(text='Отмена больше не доступна', show_alert=True)
            logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION",
                                                                      f"Отмена больше не доступна")
    except TelegramBadRequest as e:
        logger.opt(exception=e).warning(f"Telegram server error: {e}")
    except Exception as e:
        error_text = str(e)
        if error_text == 'Unable to finish order':
            await call.answer(text='Нельзя отменить в первые 2 минуты', show_alert=True)
            logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION",
                                                                      f"Нельзя отменить в первые 2 минуты")
        elif error_text == 'Wrong operation ID':
            activation.activation_expire_at = None
            activation.status = models.StatusResponse.STATUS_CANCEL
            await activation.save()
            await call.answer(text='Отмена больше не доступна', show_alert=True)
            logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION",
                                                                      f"Отмена больше не доступна")
        elif error_text == 'Try again later':
            await call.answer(text='Повторите попытку позже', show_alert=True)
            logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION",
                                                                      f"Повторите попытку позже")
        else:
            text = error_text[0].upper() + error_text[1:] if error_text else "Неизвестная ошибка"
            logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION",
                                                                      f"Ошибка при отмене номера.\n{text}")
            await call.answer(text=f'Ошибка при отмене номера.\n{text}', show_alert=True)


@router.callback_query(F.data.startswith('full_unread_message|'))
async def unread_message(call: types.CallbackQuery, **kwargs):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", "Пользователь запросил полный текст сообщения")
        _, message_id, mail_id = call.data.split("|")
        mail_id = int(mail_id)
        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", f"Запрос к БД: получение сообщения ID={mail_id}")
        mail = await models.Mail.get_or_none(id=mail_id).prefetch_related("user")
        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", f"Результат из БД: сообщение найдено={mail is not None}")

        if not mail:
            logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", "Ошибка: сообщение не найдено")
            return

        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", "Получение полного текста сообщения")
        text = await fetch_full_message(mail.token, message_id)

        # Удаляем HTML-теги <a> и <img>
        soup = BeautifulSoup(text, "html.parser")
        for a in soup.find_all("a"):
            a.decompose()
        for img in soup.find_all("img"):
            img.decompose()

        # Получаем очищенный текст
        cleaned_text = soup.get_text()
        # Удаляем ссылки вида "https://example.com "
        cleaned_text = re.sub(r"https?://\S+", "", cleaned_text)
        # Удаляем ссылки в формате [text](https://example.com )
        cleaned_text = re.sub(r"\[.*?\]\(https?://\S+\)", "", cleaned_text)
        # Экранируем HTML
        cleaned_text = html.escape(cleaned_text)

        msg_text = (
            f'📩<b>Полный текст сообщения</b> на почту: <b>{mail.email}</b>\n'
            f'{cleaned_text}'
        )

        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", f"Отправка сообщения пользователю {mail.user.telegram_id}")
        if len(msg_text) <= 4096:
            await bot.send_message(chat_id=mail.user.telegram_id, text=msg_text, parse_mode="HTML")
        else:
            parts = await split_message(msg_text, 4096)
            for part in parts:
                await bot.send_message(chat_id=mail.user.telegram_id, text=part, parse_mode="HTML")
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /unread_message: {e}")


async def split_message(text: str, max_length: int) -> list:
    """Разбивает длинное сообщение на части, не превышающие max_length."""
    logger.bind(action="split_message").log("USER_ACTION", f"Разделение сообщения длиной {len(text)} символов")
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
    logger.bind(action="split_message").log("USER_ACTION", f"Сообщение разделено на {len(parts)} частей")
    return parts
