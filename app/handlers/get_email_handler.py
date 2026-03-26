from datetime import timedelta
from typing import Union
import html
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
from app.services.rental_email_pool import (
    build_firstmail_change_cooldown_message,
    change_rental_email_lease,
    get_firstmail_change_cooldown_remaining,
    pull_rental_email_messages,
)
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.mail.temp_mail_tm import create_mail
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from app.services.mail.firstmail_imap import fetch_firstmail_messages_async
from loguru import logger


router = Router()


@router.message(Command("get_email"))  # Обработка команды /get_email
@router.message(F.text == bt.RECEIVE_EMAIL_BTN)  # кнопка '📩Принять Email'
@router.callback_query(F.data == 'receive_email')  # Обработка коллбэк-запросов с данными 'receive_email'
async def receive_email(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", "Пользователь запросил получение email")

        user = await models.User.get_user(user_id)

        if not user:
            return

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

            # ✅ После первого созданного email включаем обязательную проверку подписки
            if not getattr(user, "channel_gate_enabled", True):
                user.channel_gate_enabled = True
                await user.save(update_fields=["channel_gate_enabled"])

            await temp_mail.delete()

        logger.bind(user_id=user_id, action="receive_email").log("USER_ACTION", f"Запуск диалога с mail_id={mail.id}")
        await dialog_manager.start(
            ReceiveEmailMenu.receive_email,
            data={"mail_id": mail.id},
            mode=StartMode.RESET_STACK
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_email: {e}")


@router.callback_query(F.data == 'my_rent_emails')
async def my_rent_emails(call: types.CallbackQuery):
    """
    Показывает список активных арендованных FirstMail-ящиков пользователя.

    Важно:
    - используем новую модель RentalEmailLease;
    - callback_data оставляем в формате `mail:{lease_id}`,
      чтобы не ломать существующую навигацию.
    """
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="my_rent_emails").log(
            "USER_ACTION",
            "Пользователь открыл список арендованных почт"
        )

        user = await models.User.get_user(user_id)
        if not user:
            return

        leases = await models.RentalEmailLease.filter(
            user=user,
            is_active=True
        ).order_by("-id").all()

        logger.bind(user_id=user_id, action="my_rent_emails").log(
            "USER_ACTION",
            f"Результат из БД: найдено арендованных почт={len(leases)}"
        )

        if len(leases) == 0:
            logger.bind(user_id=user_id, action="my_rent_emails").log(
                "USER_ACTION",
                "Нет арендованных почт"
            )
            await call.answer(text='У вас нет арендованных почтовых ящиков', show_alert=True)
            return

        builder = InlineKeyboardBuilder()
        for lease in leases:
            builder.add(
                types.InlineKeyboardButton(
                    text=lease.email,
                    callback_data=f'mail:{lease.id}'
                )
            )

        builder.button(text=bt.BACK_BTN, callback_data='receive_email')
        builder.adjust(1)

        await call.message.edit_text(
            text='Выберите почтовый ящик',
            reply_markup=builder.as_markup()
        )

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /my_rent_emails: {e}")


@router.callback_query(F.data.startswith('receive_my_mail:'))
async def receive_my_mail(call: types.CallbackQuery):
    """
    Ручное получение новых писем для арендованного FirstMail-ящика.

    Важно:
    - используем общий helper, чтобы ручная проверка и scheduler
      не дублировали письма внутри одного процесса;
    - если ящик ещё не был инициализирован, helper выполнит
      тихую инициализацию через old_messages_id / is_initialized.
    """
    try:
        user_id = call.from_user.id
        lease_id = int(call.data.split(':')[1])

        logger.bind(user_id=user_id, action="receive_my_mail").log(
            "USER_ACTION",
            f"Ручная проверка FirstMail lease_id={lease_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        lease = await models.RentalEmailLease.get_or_none(
            id=lease_id,
            user=user,
            is_active=True
        )

        if not lease:
            await call.answer("Арендованный ящик не найден.", show_alert=True)
            return

        lease, messages = await pull_rental_email_messages(
            lease_id=lease.id,
            limit=5,
        )

        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=bt.BACK_BTN,
                        callback_data=f'mail:{lease.id}'
                    )
                ]
            ]
        )

        base_text = bt.MY_RENT_EMAIL.format(
            email=lease.email,
            expire_at=lease.expire_at.strftime('%d.%m.%Y')
        )

        if not messages:
            base_text += (
                "\n\n"
                "<i>Новых писем пока нет.</i>"
            )
        else:
            base_text += (
                "\n\n"
                f"<b>Найдено новых писем:</b> {len(messages)}"
            )

        await call.message.edit_text(
            text=base_text,
            reply_markup=mk
        )

        for message_obj in messages:
            from_text = html.escape(message_obj.from_header or "-")
            subject_text = html.escape(message_obj.subject or "(без темы)")
            content_text = html.escape((message_obj.content or "").strip() or "Нет текста в сообщении.")

            if len(content_text) > 3500:
                content_text = content_text[:3500] + "\n\n...[обрезано]"

            msg_text = (
                f"📩<b>Новое сообщение</b> на почту: <b>{html.escape(lease.email)}</b>\n\n"
                f"<b>От кого:</b> {from_text}\n"
                f"<b>Тема:</b> {subject_text}\n\n"
                f"{content_text}"
            )

            await call.message.answer(msg_text)

        logger.bind(user_id=user_id, action="receive_my_mail").log(
            "USER_ACTION",
            f"Проверка FirstMail завершена | lease_id={lease_id} new_messages={len(messages)}"
        )

    except ValueError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_my_mail: {e}")
        await call.answer(str(e), show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_my_mail: {e}")
        await call.answer("Не удалось получить письма. Попробуйте ещё раз позже.", show_alert=True)

@router.callback_query(F.data.startswith('extend_email:'))
async def extend_email(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(':')[1])
        user = await models.User.get_user(user_id)

        logger.bind(user_id=user_id, action="extend_email").log("USER_ACTION", f"Пользователь начал продление почты ID={mail_id}")

        mail = await models.Mail.filter(user=user).order_by('-id').first()

        keyboard = get_extend_email_kb(mail_id, False)
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

@router.callback_query(F.data.startswith("change_rental_email:"))
async def change_rental_email(call: types.CallbackQuery):
    """
    Меняет именно арендованный FirstMail-ящик на новый аккаунт из пула.

    Важно:
    - mail.tm здесь не используется;
    - cooldown 24 часа действует глобально на пользователя;
    - если cooldown ещё не закончился, сразу показываем понятный alert;
    - новый аккаунт инициализируется внутри сервисного слоя;
    - если инициализация не удалась, старая аренда восстанавливается.
    """
    try:
        user_id = call.from_user.id
        lease_id = int(call.data.split(":", 1)[1])

        logger.bind(user_id=user_id, action="change_rental_email").log(
            "USER_ACTION",
            f"Запрос смены FirstMail lease_id={lease_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        lease = await models.RentalEmailLease.get_or_none(
            id=lease_id,
            user=user,
            is_active=True,
        )

        if not lease:
            await call.answer("Арендованный ящик не найден.", show_alert=True)
            return

        # Сначала мягкая UX-проверка cooldown в хэндлере,
        # чтобы пользователь сразу получил понятное сообщение.
        cooldown_remaining = await get_firstmail_change_cooldown_remaining(user)
        if cooldown_remaining:
            cooldown_message = build_firstmail_change_cooldown_message(cooldown_remaining)

            logger.bind(user_id=user_id, action="change_rental_email").log(
                "USER_ACTION",
                f"Смена FirstMail заблокирована cooldown: lease_id={lease_id} "
                f"remaining_seconds={int(cooldown_remaining.total_seconds())}"
            )

            await call.answer(cooldown_message, show_alert=True)
            return

        await call.answer("Подбираю новый почтовый ящик…", show_alert=False)

        new_lease = await change_rental_email_lease(
            lease_id=lease.id,
            user=user,
        )

        msg_text = bt.PAID_EMAIL_INFO.format(
            email=new_lease.email,
            expire_at=new_lease.expire_at.strftime("%d.%m.%Y")
        )
        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=bt.RECEIVE_MY_EMAIL_BTN,
                        callback_data=f"receive_my_mail:{new_lease.id}",
                    ),
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.EXTEND_EMAIL_BTN,
                        callback_data=f"extend_rental_email:{new_lease.id}",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.CHANGE_EMAIL_BTN,
                        callback_data=f"change_rental_email:{new_lease.id}",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.BACK_BTN,
                        callback_data="my_rent_emails"
                    )
                ],
            ]
        )

        await call.message.edit_text(text=msg_text, reply_markup=mk)
        await call.answer("✅ Почта успешно изменена", show_alert=False)

    except ValueError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере change_rental_email: {e}")
        await call.answer(str(e), show_alert=True)
    except RuntimeError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере change_rental_email: {e}")
        await call.answer(str(e), show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере change_rental_email: {e}")
        try:
            await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        except Exception:
            pass

@router.callback_query(F.data.startswith('extend_email_'))
async def extend_email_confirm(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        callback_data = call.data.split(':')  # ['extend_email_month', '6901']
        data_key = callback_data[0]  # например, 'extend_email_month'

        rent_data = {
            'extend_email_week': RENT_EMAIL_WEEK,
            'extend_email_month': RENT_EMAIL_MONTH,
            'extend_email_six_months': RENT_EMAIL_SIX_MONTHS,
            'extend_email_year': RENT_EMAIL_YEAR,
        }

        if data_key not in rent_data:
            logger.bind(user_id=user_id, action="extend_email_confirm").log(
                "USER_ACTION", f"Ошибка: неизвестный ключ срока аренды: {data_key}"
            )
            await call.answer("Неверный формат запроса.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="extend_email_confirm").log(
            "USER_ACTION", f"Пользователь выбрал срок: {data_key}"
        )

        user = await models.User.get_user(user_id)

        price, _, rent_text = rent_data[data_key]
        if user.balance < price:
            logger.bind(user_id=user_id, action="extend_email_confirm").log(
                "USER_ACTION", "Ошибка: недостаточно средств"
            )
            await call.answer(text='Недостаточно средств', show_alert=True)
            return

        mail_id = int(callback_data[1])
        logger.bind(user_id=user_id, action="extend_email_confirm").log(
            "USER_ACTION", f"Запрос к БД: получение почты ID={mail_id}"
        )
        mail = await models.Mail.get_or_none(id=mail_id)

        if not mail:
            logger.bind(user_id=user_id, action="extend_email_confirm").log(
                "USER_ACTION", f"Ошибка: почта с ID={mail_id} не найдена"
            )
            await call.answer("Почта не найдена.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="extend_email_confirm").log(
            "USER_ACTION", f"Результат из БД: почта={mail.email}"
        )

        msg_text = bt.CONFIRM_EXTEND_EMAIL.format(
            email=mail.email,
            rent_text=rent_text,
            cost=price
        )

        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=bt.CONFIRM_BTN,
                        callback_data=f'confirm_{data_key}:{mail_id}'
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.BACK_BTN,
                        callback_data=f'mail:{mail_id}'
                    )
                ]
            ]
        )

        await call.message.edit_text(text=msg_text, reply_markup=mk)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_email_confirm: {e}")
        await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)



@router.callback_query(F.data.startswith('confirm_extend_email_'))
async def confirm_extend_email(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Подтверждение продления почты ID={mail_id}"
        )

        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Запрос к БД: получение почты ID={mail_id}"
        )
        mail = await models.Mail.get_or_none(id=mail_id)

        if not mail:
            await call.answer("Почта не найдена.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Результат из БД: почта={mail.email}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer("Пользователь не найден.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Результат из БД: баланс={user.balance}"
        )

        raw_key = call.data.split(':')[0]  # confirm_extend_email_month
        data_key = raw_key.replace('confirm_extend_', 'rent_')

        rent_data = {
            'rent_email_week': RENT_EMAIL_WEEK,
            'rent_email_month': RENT_EMAIL_MONTH,
            'rent_email_six_months': RENT_EMAIL_SIX_MONTHS,
            'rent_email_year': RENT_EMAIL_YEAR,
        }

        if data_key not in rent_data:
            logger.bind(user_id=user_id, action="confirm_extend_email").log(
                "USER_ACTION", f"Ошибка: неизвестный ключ срока аренды: {data_key}"
            )
            await call.answer("Неверный срок аренды.", show_alert=True)
            return

        price, days, rent_text = rent_data[data_key]

        if user.balance < price:
            logger.bind(user_id=user_id, action="confirm_extend_email").log(
                "USER_ACTION", "Ошибка: недостаточно средств"
            )
            await call.answer(text='Недостаточно средств', show_alert=True)
            return

        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Обновление срока аренды: +{days} дней"
        )
        mail.expire_at += timedelta(days=days)
        await mail.save(update_fields=['expire_at'])

        low_balance = await check_low_balance(user, price)
        user.balance -= price
        await user.save(update_fields=['balance'])
        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Баланс обновлён: новый баланс={user.balance}"
        )

        msg_text = bt.EXTEND_EMAIL_SUCCESS.format(
            email=mail.email,
            rent_text=rent_text
        )
        await call.message.edit_text(text=msg_text)
        await call.answer()
        await asyncio.sleep(2)

        if low_balance:
            logger.bind(user_id=user_id, action="confirm_extend_email").log(
                "USER_ACTION", "Отправка уведомления о низком балансе"
            )
            await send_low_balance_alert(user)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /confirm_extend_email: {e}")
        await call.answer("Произошла ошибка.", show_alert=True)
