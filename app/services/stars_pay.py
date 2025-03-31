""" Оплата Telegram Stars """

from aiogram.types import LabeledPrice
from aiogram_dialog import DialogManager, StartMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import types
from aiogram_dialog.widgets.kbd import Button
from aiogram.types import PreCheckoutQuery
from app.services.bot_texts import PAYMENT
from tortoise import timezone
from math import floor
from app.db import models
from loguru import logger
from app.dialogs.personal_cabinet.states import PersonalMenu
from aiogram.exceptions import TelegramBadRequest

from app.services.periodic_tasks import send_coder, balance_replenishment_notification


def payment_keyboard(amount):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Оплатить {amount} ⭐️", pay=True)

    return builder.as_markup()


async def send_invoice_handler_stars(c: types.CallbackQuery, button: Button, manager: DialogManager):
    ctx = manager.current_context()
    amount = ctx.dialog_data.get('stars', 0)
    stars = int(round(amount) / 2)
    prices = [LabeledPrice(label="XTR", amount=stars)]

    try:
        await c.message.answer_invoice(
            title=PAYMENT,
            description=f"💰 Сумма: {amount} ₽",
            prices=prices,
            provider_token="",
            payload="payment_in_stars",
            currency="XTR",
            reply_markup=payment_keyboard(stars),
        )
        await manager.start(PersonalMenu.user_info, mode=StartMode.RESET_STACK)
    except TelegramBadRequest as e:
        if "total price must be positive" in str(e):
            await c.message.answer(
                "К сожалению, у вас недостаточно Telegram Stars для этой операции. Пожалуйста, пополните баланс и попробуйте снова.")
        else:
            # Обработка других потенциальных ошибок TelegramBadRequest.
            await c.message.answer(
                "Произошла ошибка при создании счета. Пожалуйста, попробуйте позже или обратитесь в поддержку.")

        # При желании вы можете зарегистрировать ошибку в целях отладки.
        logger.error(f"Ошибка в send_invoice_handler_stars: {e}")

    # Убедитесь, что мы всегда отвечаем на запрос обратного вызова, чтобы избежать состояния «загрузки» в пользовательском интерфейсе.
    await c.answer()


async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    """
    Обрабатывает PreCheckoutQuery (предварительный запрос на оплату) от пользователя.

    Этот обработчик вызывается при получении предварительного запроса на оплату.
    Он выполняет следующие действия:
    1. Получает пользователя по его идентификатору.
    2. Извлекает сумму последнего платежа пользователя.
    3. Сохраняет информацию о платеже в базу данных.
    4. Отвечает на предварительный запрос с положительным результатом.

    :param pre_checkout_query: Объект PreCheckoutQuery, содержащий информацию о запросе на оплату.
    :param dialog_manager: DialogManager
    """
    # Получаем объект пользователя по его Telegram ID
    user = await models.User.get_user(pre_checkout_query.from_user.id)
    # Извлекаем сумму последнего платежа пользователя
    amount = await models.Payment.get_last_payment_amount(user.id)
    # Сохраняем информацию о платеже в базу данных
    await save_payment_to_database(user, amount)
    # Отправляем подтверждение успешного предварительного запроса на оплату
    await pre_checkout_query.answer(ok=True)


async def save_payment_to_database(user, amount):
    # Проверяем, есть ли у пользователя активный бонус и его срок не истек.
    if user.bonus_end_at and user.bonus_end_at > timezone.now():
        # Если бонус активен, увеличиваем сумму платежа на 10%.
        amount = floor(amount * 1.1)
        # Сбрасываем срок действия бонуса.
        user.bonus_end_at = None

    # Увеличиваем баланс пользователя на сумму платежа (с учетом бонуса, если он был).
    user.balance += amount
    msg_text = (f'💲Пополнение stars⭐️\n'
                f'пользователь {user.mention}\n'
                f'id {user.telegram_id}\n'
                f'сумма {amount}\n'
                f'баланс: {user.balance}')
    await send_coder(msg_text)
    await user.save()

