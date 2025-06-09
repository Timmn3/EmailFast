from typing import Union
import logging
from aiogram import types, F, Router
from aiogram.filters import Command, CommandObject
from aiogram_dialog import DialogManager, StartMode
from app.db import models
from app.dependencies import bot, REFERRAL_PREFIX
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.dialogs.receive_email.states import ReceiveEmailMenu
from app.dialogs.receive_sms.selected import send_country_info
from app.services import bot_texts as bt
from app.services.keyboards import start_kb
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from loguru import logger
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.db.models import ReferralLink

from app.services.periodic_tasks import balance_replenishment_notification

router = Router()


@router.message(F.text == '/id')
async def get_id(message: types.Message):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='get_id').log(
            "USER_ACTION",
            f"Пользователь запросил свой ID"
        )
        await message.answer(text=str(message.chat.id))
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /id: {e}")


@router.callback_query(F.data == 'start')
@router.message(Command('start'))
async def start(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager,
                command: CommandObject):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='start').log(
            "USER_ACTION",
            f"Пользователь начал взаимодействие с ботом"
        )

        user = await models.User.get_user(user_id)
        if not user:
            refer_id = command.args

            # --- Добавлено: обработка персональных ссылок Petr (1939379478) ---
            if refer_id and refer_id.startswith(f"{REFERRAL_PREFIX}_"):
                link_code = refer_id
                petr_user = await models.User.get_or_none(telegram_id=REFERRAL_PREFIX)
                if petr_user:
                    referral_link = await ReferralLink.get_or_create_link(user=petr_user, link_code=link_code)

                    # добавляем нового пользователя в таблицу users
                    user = await models.User.add_user(
                        message.from_user,
                        refer=petr_user,
                        referral_link_code=link_code  # ✅ передаём персональную ссылку
                    )

                    # увеличиваем total_starts (если пользователь первый раз)
                    referral_link.total_starts += 1
                    await referral_link.save()

                    # отправляем уведомление Petr, если уведомления не отключены
                    if petr_user and not petr_user.disable_ref_notifications:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔕 Отключить уведомление",
                                                  callback_data=f"disable_notify:{petr_user.telegram_id}")]
                        ])
                        await bot.send_message(
                            chat_id=petr_user.telegram_id,
                            text=f"📈 У Вас новый реферал (https://t.me/emailfastbot?start={link_code})\n└ Аккаунт: {user.telegram_id}",
                            reply_markup=keyboard
                        )

            # --- Стандартная обработка обычных реферальных ID ---
            elif refer_id and refer_id.isdigit():
                logger.bind(user_id=user_id, action='start').log(
                    "USER_ACTION",
                    f"Обнаружен реферальный ID: {refer_id}"
                )
                refer_id = int(refer_id)
                refer = await models.User.get_or_none(telegram_id=refer_id)
                user = await models.User.add_user(message.from_user, refer)

                if refer and not refer.disable_ref_notifications:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔕 Отключить уведомление",
                                              callback_data=f"disable_notify:{refer.telegram_id}")]
                    ])
                    await bot.send_message(
                        chat_id=refer.telegram_id,
                        text=f"📈 У Вас новый реферал\n└ Аккаунт: {user.telegram_id}",
                        reply_markup=keyboard
                    )

            else:
                logger.bind(user_id=user_id, action='start').log(
                    "USER_ACTION",
                    f"Реферальный ID отсутствует или некорректен"
                )
                user = await models.User.add_user(message.from_user)

        if isinstance(message, types.CallbackQuery):
            logger.bind(user_id=user_id, action='start').log(
                "USER_ACTION",
                f"Удаление сообщения после нажатия кнопки 'start'"
            )
            await message.message.delete()

        sub = await check_subscribe(user)
        if not sub:
            logger.bind(user_id=user_id, action='start').log(
                "USER_ACTION",
                f"Подписка не оформлена"
            )
            await send_subscribe_msg(user)
            return

        logger.bind(user_id=user_id, action='start').log(
            "USER_ACTION",
            f"Отправка главного меню"
        )
        await message.answer(text=bt.MAIN_MENU, reply_markup=start_kb())
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /start: {e}")



@router.callback_query(F.data.startswith("disable_notify:"))
async def disable_notify_callback(callback: types.CallbackQuery):
    try:
        _, user_id = callback.data.split(":")
        user = await models.User.get_or_none(telegram_id=int(user_id))
        if user:
            user.disable_ref_notifications = True
            await user.save()
            await callback.message.edit_reply_markup()
            await callback.answer("Уведомления о рефералах отключены.", show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error("Ошибка при отключении уведомлений")


@router.callback_query(F.data.startswith("enable_ref_notify:"))
async def enable_ref_notify(callback: types.CallbackQuery):
    print("Уведомления о рефералах включены.")
    try:
        _, user_id = callback.data.split(":")
        user = await models.User.get_or_none(telegram_id=int(user_id))
        if user:
            user.disable_ref_notifications = False
            await user.save()
            await callback.answer("Уведомления о рефералах включены.", show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error("Ошибка при включении уведомлений")



@router.callback_query(F.data == 'check_subscribe')
async def check_subscribe_handler(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        # logger.bind(user_id=user_id, action='check_subscribe').log(
        #     "USER_ACTION",
        #     f"Проверка подписки пользователя"
        # )

        user = await models.User.get_user(user_id)
        sub = await check_subscribe(user)
        if sub:
            # logger.bind(user_id=user_id, action='check_subscribe').log(
            #     "USER_ACTION",
            #     f"Подписка подтверждена"
            # )
            await call.message.delete()
            await call.message.answer(text=bt.MAIN_MENU, reply_markup=start_kb())
        else:
            # logger.bind(user_id=user_id, action='check_subscribe').log(
            #     "USER_ACTION",
            #     f"Подписка не найдена"
            # )
            await call.answer(text='Вы не подписаны на канал', show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере check_subscribe: {e}")


@router.message(Command("account"))
@router.message(F.text == bt.PERSONAL_CABINET_BTN)
async def personal_cabinet(message: types.Message, dialog_manager: DialogManager):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='personal_cabinet').log(
            "USER_ACTION",
            f"Переход в личный кабинет"
        )

        user = await models.User.get_user(user_id)
        sub = await check_subscribe(user)
        if not sub:
            logger.bind(user_id=user_id, action='personal_cabinet').log(
                "USER_ACTION",
                f"Подписка не оформлена"
            )
            await send_subscribe_msg(user)
            return

        logger.bind(user_id=user_id, action='personal_cabinet').log(
            "USER_ACTION",
            f"Запуск диалога PersonalMenu.user_info"
        )
        await dialog_manager.start(PersonalMenu.user_info, mode=StartMode.RESET_STACK)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере personal_cabinet: {e}")


@router.callback_query(F.data.startswith('mail:'))
async def mail_info(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action='mail_info').log(
            "USER_ACTION",
            f"Пользователь запросил информацию о почте с ID={mail_id}"
        )

        mail = await models.Mail.get_or_none(id=mail_id)
        if not mail:
            logger.bind(user_id=user_id, action='mail_info').log(
                "USER_ACTION",
                f"Почта с ID={mail_id} не найдена"
            )
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
        logger.bind(user_id=user_id, action='mail_info').log(
            "USER_ACTION",
            f"Отображение информации о почте '{mail.email}'"
        )
        await call.message.edit_text(text=msg_text, reply_markup=mk)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере mail_info: {e}")


@router.callback_query(F.data.startswith('continue_payment:'))
async def continue_payment(call: types.CallbackQuery, dialog_manager: DialogManager):
    try:
        user_id = call.from_user.id
        payment_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action='continue_payment').log(
            "USER_ACTION",
            f"Продолжение оплаты для платежа ID={payment_id}"
        )

        payment = await models.Payment.get_or_none(id=payment_id)
        if not payment:
            logger.bind(user_id=user_id, action='continue_payment').log(
                "USER_ACTION",
                f"Платёж с ID={payment_id} не найден"
            )
            await call.answer()
            return

        if 'email' in payment.continue_data:
            logger.bind(user_id=user_id, action='continue_payment').log(
                "USER_ACTION",
                f"Старт диалога ReceiveEmailMenu.rent_email_confirm"
            )
            await dialog_manager.start(ReceiveEmailMenu.rent_email_confirm, data=payment.continue_data,
                                       mode=StartMode.RESET_STACK)
        else:
            logger.bind(user_id=user_id, action='continue_payment').log(
                "USER_ACTION",
                f"Вызов send_country_info для продолжения оплаты"
            )
            await send_country_info(payment.continue_data['service_code'], call, dialog_manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере continue_payment: {e}")


@router.callback_query(F.data.startswith('bonus_price:'))
async def bonus_price(call: types.CallbackQuery, dialog_manager: DialogManager):
    try:
        user_id = call.from_user.id
        price = call.data.split(':')[1]
        logger.bind(user_id=user_id, action='bonus_price').log(
            "USER_ACTION",
            f"Выбрана сумма бонуса: {price}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            logger.bind(user_id=user_id, action='bonus_price').log(
                "USER_ACTION",
                f"Пользователь не найден"
            )
            return

        if price == 'other':
            logger.bind(user_id=user_id, action='bonus_price').log(
                "USER_ACTION",
                f"Запрос ввода произвольной суммы"
            )
            await call.message.edit_reply_markup()
            await dialog_manager.start(PersonalMenu.enter_amount, mode=StartMode.RESET_STACK)
        else:
            logger.bind(user_id=user_id, action='bonus_price').log(
                "USER_ACTION",
                f"Отправка клавиатуры оплаты на сумму {price}"
            )
            from app.dialogs.personal_cabinet.selected import send_payment_keyboard
            await send_payment_keyboard(call, price=float(price))
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере bonus_price: {e}")


@router.message(Command('rent'))
async def rent(message: types.Message, dialog_manager: DialogManager):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='rent').log(
            "USER_ACTION",
            f"Пользователь начал процесс аренды"
        )
        from app.dialogs.receive_email.selected import on_rent_email_check_discount
        await on_rent_email_check_discount(message, dialog_manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /rent: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith('rental'))
async def process_rent_callback(message: types.Message, dialog_manager: DialogManager):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='process_rent_callback').log(
            "USER_ACTION",
            f"Обработка inline-кнопки 'Продлить аренду'"
        )
        from app.dialogs.receive_email.selected import on_rent_email_check_discount
        await on_rent_email_check_discount(message, dialog_manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере process_rent_callback: {e}")


@router.message(Command("test_notify"))
async def test_balance_notify(m: types.Message):
    user = await models.User.get_or_none(telegram_id=m.from_user.id)
    if not user:
        await m.answer("Пользователь не найден в базе.")
        return

    # Пример: создаём тестовый платеж (не сохраняем в БД, можно мокнуть)
    class DummyPayment:
        def __init__(self, user):
            self.user = user
            self.amount = 123.45

    payment = DummyPayment(user=user)

    # Название сервиса может быть "LAVA", "YOOMONEY", и т.д.
    await balance_replenishment_notification(payment, service="LAVA")
    await m.answer("Тестовое уведомление отправлено.")