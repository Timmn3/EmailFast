from datetime import timedelta
from typing import Union

import asyncio
from aiogram import types, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram_dialog import DialogManager, StartMode
from pyonlinesim import OnlineSMS

from app.db import models
from app.dependencies import API_KEY_ONLINESIM
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.dialogs.receive_email.states import ReceiveEmailMenu
from app.dialogs.receive_sms.selected import send_country_info, send_service_info_with_keyboard
from app.dialogs.receive_sms.states import ServiceMenu
from app.services import bot_texts as bt
from app.services.bot_texts import RENT_EMAIL_WEEK, RENT_EMAIL_MONTH, RENT_EMAIL_TWO_MONTHS, RENT_EMAIL_SIX_MONTHS, \
    RENT_EMAIL_YEAR, SERVICES_TRANSLATION
from app.services.keyboards import start_kb
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from app.services.sms_receive import SmsReceive
from app.services.temp_mail import TempMail
from loguru import logger

def log_exceptions(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в обработчике {func.__name__}: {e}")
            raise  # Возможно, чтобы повторно вызвать ошибку и не скрывать её
    return wrapper

router = Router()


@router.message(F.text == '/id')
async def get_id(message: types.Message):
    await message.answer(text=str(message.chat.id))


@router.callback_query(F.data == 'start')
@router.message(Command('start'))
async def start(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager,
                command: CommandObject):
    user = await models.User.get_user(message.from_user.id)
    if not user:
        refer_id = command.args
        if refer_id and refer_id.isdigit():
            refer_id = int(refer_id)
            refer = await models.User.get_or_none(telegram_id=refer_id)
            user = await models.User.add_user(message.from_user, refer)
        else:
            user = await models.User.add_user(message.from_user)

    if isinstance(message, types.CallbackQuery):
        await message.message.delete()

    # else:
    #     start_arg = command.args
    #     if start_arg and start_arg.startswith('free_'):
    #         payment_link = await models.PaymentLink.get_payment_link(start_arg)
    #         if payment_link:
    #             if len(payment_link.user_id_list) >= payment_link.limit:
    #                 await message.answer(text='Лимит активаций исчерпан')
    #                 return
    #
    #             elif message.from_user.id in payment_link.user_id_list:
    #                 await message.answer(text='Вы уже активировали эту ссылку')
    #                 return
    #
    #             payment_link.user_id_list.append(user.telegram_id)
    #             await payment_link.save(update_fields=['user_id_list'])
    #             user.balance += payment_link.amount
    #             await user.save(update_fields=['balance'])
    #             await message.answer(text=f'Вам начислено {payment_link.amount}₽')
    #             return

    sub = await check_subscribe(user)
    if not sub:
        await send_subscribe_msg(user)
        return

    await message.answer(text=bt.MAIN_MENU, reply_markup=start_kb())


@router.callback_query(F.data == 'check_subscribe')
async def check_subscribe_handler(call: types.CallbackQuery):
    user = await models.User.get_user(call.from_user.id)
    sub = await check_subscribe(user)
    if sub:
        await call.message.delete()
        await call.message.answer(text=bt.MAIN_MENU, reply_markup=start_kb())
    else:
        await call.answer(text='Вы не подписаны на канал', show_alert=True)


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


@router.message(Command("get_email"))  # Обработка команды /get_email
@router.message(F.text == bt.RECEIVE_EMAIL_BTN)  # кнопка '📩Принять Email'
@router.callback_query(F.data == 'receive_email')  # Обработка коллбэк-запросов с данными 'receive_email'
async def receive_email(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager):
    # Получаем пользователя из базы данных
    user = await models.User.get_user(message.from_user.id)

    # Если пользователь не найден, прекращаем выполнение функции
    if not user:
        return

    # Проверяем подписку пользователя
    sub = await check_subscribe(user)

    # Если подписка отсутствует, отправляем сообщение о необходимости подписки и завершаем функцию
    if not sub:
        await send_subscribe_msg(user)
        return

    # Получаем все активные письма пользователя, которые неоплачены
    mails = await models.Mail.filter(user=user, is_paid_mail=False, is_active=True).all()

    # Если у пользователя есть непрочитанные письма, выбираем последнее
    if len(mails) > 0:
        mail = mails[-1]
    else:
        # Если у пользователя нет непрочитанных писем, создаем временное сообщение для отображения процесса создания письма
        if isinstance(message, types.CallbackQuery):
            temp_mail = message.message
            await temp_mail.edit_text(text=bt.CREATING_EMAIL)
        else:
            temp_mail = await message.answer(text=bt.CREATING_EMAIL)

        # Генерируем временный почтовый адрес и добавляем новое письмо в базу данных
        tm = TempMail()
        email = await tm.generate_email()
        mail = await models.Mail.add_mail(user, email)

        # Удаляем временное сообщение о создании письма
        await temp_mail.delete()

    # Запускаем диалоговый менеджер для обработки действий с получением письма
    await dialog_manager.start(ReceiveEmailMenu.receive_email,
                               data={"mail_id": mail.id},
                               mode=StartMode.RESET_STACK)


@router.message(Command("account"))
@router.message(F.text == bt.PERSONAL_CABINET_BTN)
async def personal_cabinet(message: types.Message, dialog_manager: DialogManager):
    user = await models.User.get_user(message.from_user.id)
    sub = await check_subscribe(user)
    if not sub:
        await send_subscribe_msg(user)
        return

    await dialog_manager.start(PersonalMenu.user_info, mode=StartMode.RESET_STACK)


@router.callback_query(F.data.startswith('mail:'))
async def mail_info(call: types.CallbackQuery):
    mail_id = int(call.data.split(':')[1])
    mail = await models.Mail.get_or_none(id=mail_id)
    if not mail:
        await call.answer()
        return

    msg_text = bt.PAID_EMAIL_INFO.format(email=mail.email, expire_at=mail.expire_at.strftime('%d.%m.%Y'))
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.RECEIVE_MY_EMAIL_BTN, callback_data=f'receive_my_mail:{mail.id}'),
            ],
            [
                types.InlineKeyboardButton(text=bt.EXTEND_EMAIL_BTN, callback_data=f'extend_email:{mail.id}')
            ],
            [
                types.InlineKeyboardButton(text=bt.BACK_BTN, callback_data='my_rent_emails')
            ]
        ]
    )
    await call.message.edit_text(text=msg_text, reply_markup=mk)


@router.callback_query(F.data == 'my_rent_emails')
async def my_rent_emails(call: types.CallbackQuery):
    user = await models.User.get_user(call.from_user.id)
    if not user:
        return

    mails = await models.Mail.filter(user=user, is_paid_mail=True).all()
    if len(mails) == 0:
        await call.answer(text='У вас нет арендованных почтовых ящиков', show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for mail in mails:
        builder.add(types.InlineKeyboardButton(text=mail.email, callback_data=f'mail:{mail.id}'))

    builder.button(text=bt.BACK_BTN, callback_data='receive_email')
    builder.adjust(1)

    await call.message.edit_text(text='Выберите почтовый ящик', reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith('receive_my_mail:'))
async def receive_my_mail(call: types.CallbackQuery):
    mail_id = int(call.data.split(':')[1])
    mail = await models.Mail.get_or_none(id=mail_id)
    if not mail:
        await call.answer()
        return

    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.BACK_BTN, callback_data=f'mail:{mail.id}')
            ]
        ]
    )
    msg_text = bt.MY_RENT_EMAIL.format(email=mail.email, expire_at=mail.expire_at.strftime('%d.%m.%Y'))
    await call.message.edit_text(text=msg_text, reply_markup=mk)


@router.callback_query(F.data.startswith('extend_email:'))
async def extend_email(call: types.CallbackQuery):
    mail_id = int(call.data.split(':')[1])

    builder = InlineKeyboardBuilder()
    builder.button(text=bt.RENT_EMAIL_WEEK_BTN, callback_data=f'extend_email_week:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_MONTH_BTN, callback_data=f'extend_email_month:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_TWO_MONTHS_BTN, callback_data=f'extend_email_two_months:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_SIX_MONTHS_BTN, callback_data=f'extend_email_six_months:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_YEAR_BTN, callback_data=f'extend_email_year:{mail_id}')
    builder.button(text=bt.BACK_BTN, callback_data=f'mail:{mail_id}')
    builder.adjust(1)
    await call.message.edit_reply_markup(reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith('extend_email_'))
async def extend_email_confirm(call: types.CallbackQuery, state: FSMContext):
    data = call.data.split(':')[0]
    rent_data = {
        'rent_email_week': RENT_EMAIL_WEEK,
        'rent_email_month': RENT_EMAIL_MONTH,
        'rent_email_two_months': RENT_EMAIL_TWO_MONTHS,
        'rent_email_six_months': RENT_EMAIL_SIX_MONTHS,
        'rent_email_year': RENT_EMAIL_YEAR,
    }
    user = await models.User.get_user(call.from_user.id)
    if user.balance < rent_data[data][0]:
        await call.answer(text='Недостаточно средств', show_alert=True)
        return

    mail_id = int(call.data.split(':')[1])
    mail = await models.Mail.get_or_none(id=mail_id)
    msg_text = bt.CONFIRM_EXTEND_EMAIL.format(
        email=mail.email,
        rent_text=rent_data[data][2],
        cost=rent_data[data][0]
    )
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.CONFIRM_BTN, callback_data=f'confirm_{data}:{mail_id}')
            ],
            [
                types.InlineKeyboardButton(text=bt.BACK_BTN, callback_data=f'mail:{mail_id}')
            ]
        ]
    )
    await call.message.edit_text(text=msg_text, reply_markup=mk)


@router.callback_query(F.data.startswith('confirm_extend_email_'))
async def confirm_extend_email(call: types.CallbackQuery):
    mail_id = int(call.data.split(':')[1])
    mail = await models.Mail.get_or_none(id=mail_id)
    if not mail:
        await call.answer()
        return

    user = await models.User.get_user(call.from_user.id)
    if not user:
        return

    data = call.data.split(':')[0]
    rent_data = {
        'rent_email_week': RENT_EMAIL_WEEK,
        'rent_email_month': RENT_EMAIL_MONTH,
        'rent_email_two_months': RENT_EMAIL_TWO_MONTHS,
        'rent_email_six_months': RENT_EMAIL_SIX_MONTHS,
        'rent_email_year': RENT_EMAIL_YEAR,
    }
    if user.balance < rent_data[data][0]:
        await call.answer(text='Недостаточно средств', show_alert=True)
        return

    mail.expire_at += timedelta(days=rent_data[data][1])
    await mail.save(update_fields=['expire_at'])

    low_balance = await check_low_balance(user, rent_data[data][0])
    user.balance -= rent_data[data][0]
    await user.save(update_fields=['balance'])
    msg_text = bt.EXTEND_EMAIL_SUCCESS.format(
        email=mail.email,
        rent_text=rent_data[data][2]
    )
    await call.message.edit_text(text=msg_text)
    await call.answer()
    await asyncio.sleep(2)
    if low_balance:
        await send_low_balance_alert(user)


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
        else:
            logger.error(f"Необработанное исключение: {e}")
            await call.answer(text='Ошибка при отмене номера', show_alert=True)


@router.callback_query(F.data.startswith('continue_payment:'))
async def continue_payment(call: types.CallbackQuery, dialog_manager: DialogManager):
    payment_id = int(call.data.split(':')[1])
    payment = await models.Payment.get_or_none(id=payment_id)
    if not payment:
        await call.answer()
        return

    if 'email' in payment.continue_data:
        await dialog_manager.start(ReceiveEmailMenu.rent_email_confirm, data=payment.continue_data,
                                   mode=StartMode.RESET_STACK)

    else:  # было (payment.continue_data['country_id'], payment.continue_data['service_code'], call, dialog_manager)
        await send_country_info(payment.continue_data['service_code'], call,
                                dialog_manager)


@router.callback_query(F.data.startswith('bonus_price:'))
async def bonus_price(call: types.CallbackQuery, dialog_manager: DialogManager):
    from app.dialogs.personal_cabinet.selected import send_payment_keyboard
    price = call.data.split(':')[1]
    user = await models.User.get_user(call.from_user.id)
    if not user:
        return

    if price == 'other':
        await call.message.edit_reply_markup()
        await dialog_manager.start(PersonalMenu.enter_amount, mode=StartMode.RESET_STACK)

    else:
        await send_payment_keyboard(call, price=float(price))


@router.message(Command('rent'))
async def rent(message: types.Message, dialog_manager: DialogManager):
    from app.dialogs.receive_email.selected import on_rent_email_check_discount
    await on_rent_email_check_discount(message, dialog_manager)


# Обработчик для inline-кнопки "Продлить аренду"
@router.callback_query(lambda c: c.data and c.data.startswith('rental'))
async def process_rent_callback(message: types.Message, dialog_manager: DialogManager):
    # Получаем пользователя и вызываем функцию on_rent_email_check_discount
    from app.dialogs.receive_email.selected import on_rent_email_check_discount
    await on_rent_email_check_discount(message, dialog_manager)
