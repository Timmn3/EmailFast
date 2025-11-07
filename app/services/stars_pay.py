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
    Обрабатывает PreCheckoutQuery (предварительный запрос на оплату) от Telegram.

    ВАЖНО:
    - Здесь НИЧЕГО не начисляем пользователю и не создаём запись платежа.
    - Только проверяем базовые параметры и говорим Telegram «ok=True»,
      чтобы он завершил списание звёзд.
    - Реальное пополнение баланса делаем уже в обработчике successful_payment.
    """
    try:
        # Базовая валидация, чтобы не принять левый инвойс
        if pre_checkout_query.currency != "XTR":
            await pre_checkout_query.answer(
                ok=False,
                error_message="Неверная валюта платежа."
            )
            return

        # Проверяем, что payload наш (старый формат тоже поддерживаем)
        payload = pre_checkout_query.invoice_payload or ""
        if payload and not payload.startswith("payment_in_stars"):
            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректные данные заказа."
            )
            return

        logger.bind(
            user_id=pre_checkout_query.from_user.id,
            action="pre_checkout_stars"
        ).log(
            "USER_ACTION",
            f"PreCheckout Stars: amount={pre_checkout_query.total_amount}, payload={payload}"
        )

        # Здесь только подтверждаем предзапрос — без начислений
        await pre_checkout_query.answer(ok=True)

    except Exception as e:
        logger.opt(exception=e).error("Ошибка в pre_checkout_handler (Stars)")
        # В случае ошибки лучше не подтверждать платёж
        await pre_checkout_query.answer(
            ok=False,
            error_message="Ошибка при обработке платежа. Попробуйте ещё раз."
        )



async def save_payment_to_database(user, amount):
    # Проверяем, есть ли у пользователя активный бонус и его срок не истек.
    if user.bonus_end_at and user.bonus_end_at > timezone.now():
        # Если бонус активен, увеличиваем сумму платежа на 10%.
        amount = floor(amount * 1.1)
        # Сбрасываем срок действия бонуса.
        user.bonus_end_at = None

    payment = await models.Payment.create_payment(
        user=user,
        method=models.PaymentMethod.STARS,
        amount=amount,
        continue_data=None
    )
    payment.is_success = True
    await payment.save()
    # Увеличиваем баланс пользователя на сумму платежа (с учетом бонуса, если он был).
    user.balance += amount
    msg_text = (f'💲Пополнение stars⭐️\n'
                f'пользователь {user.mention}\n'
                f'id {user.telegram_id}\n'
                f'сумма {amount}\n'
                f'баланс: {user.balance}')
    await send_coder(msg_text)
    await user.save()

async def successful_payment_handler(message: types.Message):
    """
    Обрабатывает сообщение об успешной оплате (в т.ч. Telegram Stars).

    ВАЖНО:
    - Сюда Telegram присылает событие ТОЛЬКО после того, как платёж реально прошёл.
    - Здесь фиксируем платёж в базе и начисляем баланс.
    - Обрабатываем только наши инвойсы в валюте XTR (Telegram Stars).
    """
    successful_payment = message.successful_payment

    # Подстраховка: если по какой-то причине нет объекта платежа — выходим
    if not successful_payment:
        logger.warning("successful_payment_handler вызван без successful_payment")
        return

    # Обрабатываем только платежи в Stars
    if successful_payment.currency != "XTR":
        return

    payload = successful_payment.invoice_payload or ""
    # Обрабатываем только наши счета c payload "payment_in_stars"
    if payload and not payload.startswith("payment_in_stars"):
        return

    # Получаем пользователя
    user = await models.User.get_user(message.from_user.id)

    # Telegram передаёт количество Stars в total_amount
    stars_paid = successful_payment.total_amount

    # В send_invoice_handler_stars было:
    #   stars = int(round(amount) / 2)
    # => amount (рубли) ≈ stars * 2
    amount_rub = stars_paid * 2

    logger.bind(
        user_id=message.from_user.id,
        action="successful_payment_stars"
    ).log(
        "USER_ACTION",
        f"Успешная оплата Stars: {stars_paid}⭐ (~{amount_rub} ₽), payload={payload}"
    )

    # Фактическая запись платежа и начисление баланса (с учётом бонуса)
    await save_payment_to_database(user, amount_rub)

    # Сообщение пользователю
    await message.answer(
        f"✅ Оплата прошла успешно!\n"
        f"💳 Зачислено: {amount_rub} ₽"
    )
