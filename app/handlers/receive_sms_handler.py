from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram_dialog import DialogManager, StartMode
from pyonlinesim import OnlineSMS

from app.db import models
from app.dependencies import API_KEY_ONLINESIM
from app.dialogs.receive_sms.selected import send_service_info_with_keyboard
from app.dialogs.receive_sms.states import ServiceMenu
from app.services import bot_texts as bt
from app.services.bot_texts import SERVICES_TRANSLATION
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from app.services.sms_receive import SmsReceive
from loguru import logger

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
        service = await models.Service.get_service_name_by_id(service_id=activation.service_id)
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

    # если активация относится к service_onlinesim
    try:
        service = activation.service.code
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


@router.callback_query(F.data.startswith('cancel_service:'))
async def cancel_service(call: types.CallbackQuery, **kwargs):
    try:
        # Извлекаем идентификатор активации из данных callback
        activation_id = int(call.data.split(':')[1])

        # Пытаемся получить объект активации из базы данных по идентификатору
        activation = await models.Activation.get_or_none(id=activation_id).prefetch_related('service')

        # Если активация не найдена, просто отвечаем на callback и выходим
        if not activation:
            await call.answer()
            return

        # Проверяем, относится ли активация к service_onlinesim
        try:
            service = activation.service.code
        except AttributeError:
            return
        cancellation_successful = False
        # Выбор API клиента в зависимости от типа услуги
        if service in SERVICES_TRANSLATION:
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
        if str(e) == 'Unable to finish order':
            await call.answer(text='Нельзя отменить в первые 2 минуты', show_alert=True)
        elif str(e) == 'Wrong operation ID':
            activation.activation_expire_at = None
            activation.status = models.StatusResponse.STATUS_CANCEL
            await activation.save()
            await call.answer(text='Отмена больше не доступна', show_alert=True)
        elif str(e) == 'Try again later':
            await call.answer(text='Повторите попытку позже', show_alert=True)
        else:
            logger.error(f"Необработанное исключение: {e}")
            await call.answer(text='Ошибка при отмене номера', show_alert=True)
