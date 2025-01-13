from datetime import timedelta
from typing import Union

import asyncio
from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram_dialog import DialogManager, StartMode
from app.db import models
from app.dialogs.receive_email.states import ReceiveEmailMenu
from app.services import bot_texts as bt
from app.services.bot_texts import RENT_EMAIL_WEEK, RENT_EMAIL_MONTH, RENT_EMAIL_TWO_MONTHS, RENT_EMAIL_SIX_MONTHS, \
    RENT_EMAIL_YEAR
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.mail.temp_mail_tm import create_mail
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from loguru import logger
router = Router()


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

        try:
            email, token = await create_mail()
        except Exception as e:
            logger.error(e)
            await message.answer("В настоящее время сервис недоступен, попробуйте позже🙎‍♂️")
            return

        mail = await models.Mail.add_mail(user, email, token)

        # Удаляем временное сообщение о создании письма
        await temp_mail.delete()

    # Запускаем диалоговый менеджер для обработки действий с получением письма
    await dialog_manager.start(ReceiveEmailMenu.receive_email,
                               data={"mail_id": mail.id},
                               mode=StartMode.RESET_STACK)


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
