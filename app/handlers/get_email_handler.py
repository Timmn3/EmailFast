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
from app.services.bot_texts import RENT_EMAIL_WEEK, RENT_EMAIL_MONTH, RENT_EMAIL_SIX_MONTHS, \
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
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", "Пользователь запросил получение email")

        # logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        # logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", f"Результат из БД: пользователь найден={user is not None}")

        if not user:
            return

        # logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", "Проверка подписки")
        sub = await check_subscribe(user)

        if not sub:
            logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", "Подписка неактивна, отправляем сообщение")
            await send_subscribe_msg(user)
            return

        logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", f"Запрос к БД: получение непрочитанных писем для {user_id}")
        mails = await models.Mail.filter(user=user, is_paid_mail=False, is_active=True).all()
        logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", f"Результат из БД: найдено писем={len(mails)}")

        if len(mails) > 0:
            mail = mails[-1]
        else:
            if isinstance(message, types.CallbackQuery):
                temp_mail = message.message
                await temp_mail.edit_text(text=bt.CREATING_EMAIL)
            else:
                temp_mail = await message.answer(text=bt.CREATING_EMAIL)

            logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", "Создание нового временного email")
            try:
                email, token = await create_mail()
            except Exception as e:
                logger.opt(exception=e).error(f"Ошибка при создании email в /receive_email: {e}")
                await message.answer("В настоящее время сервис недоступен, попробуйте позже🙎‍♂️")
                return

            mail = await models.Mail.add_mail(user, email, token)
            logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", f"Новый email создан: {email}")

            await temp_mail.delete()

        logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", f"Запуск диалога с mail_id={mail.id}")
        await dialog_manager.start(ReceiveEmailMenu.receive_email,
                                   data={"mail_id": mail.id},
                                   mode=StartMode.RESET_STACK)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_email: {e}")


@router.callback_query(F.data == 'my_rent_emails')
async def my_rent_emails(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="my_rent_emails").log("USER_ACTION", "Пользователь открыл список арендованных почт")

        # logger.bind(user_id=user_id, action="my_rent_emails").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        # logger.bind(user_id=user_id, action="my_rent_emails").log("USER_ACTION", f"Результат из БД: пользователь найден={user is not None}")

        if not user:
            return

        logger.bind(user_id=user_id, action="my_rent_emails").log("USER_ACTION", f"Запрос к БД: получение арендованных почт для {user_id}")
        mails = await models.Mail.filter(user=user, is_paid_mail=True, is_active=True).all()
        logger.bind(user_id=user_id, action="my_rent_emails").log("USER_ACTION", f"Результат из БД: найдено почт={len(mails)}")

        if len(mails) == 0:
            logger.bind(user_id=user_id, action="my_rent_emails").log("USER_ACTION", "Нет арендованных почт")
            await call.answer(text='У вас нет арендованных почтовых ящиков', show_alert=True)
            return

        builder = InlineKeyboardBuilder()
        for mail in mails:
            builder.add(types.InlineKeyboardButton(text=mail.email, callback_data=f'mail:{mail.id}'))

        builder.button(text=bt.BACK_BTN, callback_data='receive_email')
        builder.adjust(1)

        await call.message.edit_text(text='Выберите почтовый ящик', reply_markup=builder.as_markup())
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /my_rent_emails: {e}")


@router.callback_query(F.data.startswith('receive_my_mail:'))
async def receive_my_mail(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="receive_my_mail").log("USER_ACTION", f"Пользователь открыл почту ID={mail_id}")

        logger.bind(user_id=user_id, action="receive_my_mail").log("USER_ACTION", f"Запрос к БД: получение почты ID={mail_id}")
        mail = await models.Mail.get_or_none(id=mail_id)
        logger.bind(user_id=user_id, action="receive_my_mail").log("USER_ACTION", f"Результат из БД: почта найдена={mail is not None}")

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
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_my_mail: {e}")


@router.callback_query(F.data.startswith('extend_email:'))
async def extend_email(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(':')[1])
        user = await models.User.get_user(user_id)

        logger.bind(user_id=user_id, action="extend_email").log("USER_ACTION", f"Пользователь начал продление почты ID={mail_id}")

        mail = await models.Mail.filter(user=user).order_by('-id').first()

        keyboard = get_extend_email_kb(mail_id, mail.is_free_week)
        await call.message.edit_reply_markup(reply_markup=keyboard)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_email: {e}")



def get_extend_email_kb(mail_id: int, is_free_week: bool):
    builder = InlineKeyboardBuilder()

    if is_free_week:
        builder.button(text=bt.RENT_EMAIL_WEEK_BTN, callback_data=f'extend_email_week:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_MONTH_BTN, callback_data=f'extend_email_month:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_SIX_MONTHS_BTN, callback_data=f'extend_email_six_months:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_YEAR_BTN, callback_data=f'extend_email_year:{mail_id}')
    builder.button(text=bt.BACK_BTN, callback_data=f'mail:{mail_id}')

    builder.adjust(1)
    return builder.as_markup()

@router.callback_query(F.data.startswith('extend_email_'))
async def extend_email_confirm(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        data = call.data.split(':')[0]
        rent_data = {
            'rent_email_week': RENT_EMAIL_WEEK,
            'rent_email_month': RENT_EMAIL_MONTH,
            'rent_email_six_months': RENT_EMAIL_SIX_MONTHS,
            'rent_email_year': RENT_EMAIL_YEAR,
        }
        logger.bind(user_id=user_id, action="extend_email_confirm").log("USER_ACTION", f"Пользователь выбрал срок: {data}")

        # logger.bind(user_id=user_id, action="extend_email_confirm").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        # logger.bind(user_id=user_id, action="extend_email_confirm").log("USER_ACTION", f"Результат из БД: баланс={user.balance}")

        if user.balance < rent_data[data][0]:
            logger.bind(user_id=user_id, action="extend_email_confirm").log("USER_ACTION", "Ошибка: недостаточно средств")
            await call.answer(text='Недостаточно средств', show_alert=True)
            return

        mail_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="extend_email_confirm").log("USER_ACTION", f"Запрос к БД: получение почты ID={mail_id}")
        mail = await models.Mail.get_or_none(id=mail_id)
        logger.bind(user_id=user_id, action="extend_email_confirm").log("USER_ACTION", f"Результат из БД: почта={mail.email}")

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
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_email_confirm: {e}")


@router.callback_query(F.data.startswith('confirm_extend_email_'))
async def confirm_extend_email(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", f"Подтверждение продления почты ID={mail_id}")

        logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", f"Запрос к БД: получение почты ID={mail_id}")
        mail = await models.Mail.get_or_none(id=mail_id)
        logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", f"Результат из БД: почта={mail.email}")

        if not mail:
            await call.answer()
            return

        # logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", f"Результат из БД: баланс={user.balance}")

        if not user:
            return

        data = call.data.split(':')[0]
        rent_data = {
            'rent_email_week': RENT_EMAIL_WEEK,
            'rent_email_month': RENT_EMAIL_MONTH,
            'rent_email_six_months': RENT_EMAIL_SIX_MONTHS,
            'rent_email_year': RENT_EMAIL_YEAR,
        }

        if user.balance < rent_data[data][0]:
            logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", "Ошибка: недостаточно средств")
            await call.answer(text='Недостаточно средств', show_alert=True)
            return

        logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", f"Обновление срока аренды: +{rent_data[data][1]} дней")
        mail.expire_at += timedelta(days=rent_data[data][1])
        await mail.save(update_fields=['expire_at'])

        low_balance = await check_low_balance(user, rent_data[data][0])
        user.balance -= rent_data[data][0]
        await user.save(update_fields=['balance'])
        logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", f"Баланс обновлён: новый баланс={user.balance}")

        msg_text = bt.EXTEND_EMAIL_SUCCESS.format(
            email=mail.email,
            rent_text=rent_data[data][2]
        )
        await call.message.edit_text(text=msg_text)
        await call.answer()
        await asyncio.sleep(2)

        if low_balance:
            logger.bind(user_id=user_id, action="confirm_extend_email").log("USER_ACTION", "Отправка уведомления о низком балансе")
            await send_low_balance_alert(user)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /confirm_extend_email: {e}")