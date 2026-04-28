"""
Хендлеры для уведомлений о неактивности и незавершённых платежах.
"""
import datetime
import pytz

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from loguru import logger

from app.db import models
from app.dependencies import bot
from app.services.payments.freekassa import generate_fk_link
from app.services.payments.cryptomus import link_to_heleket
from app.services.payments.lava import LavaApi
from app.services.payments.ckassa import create_invoice_ckassa
from app.services.payments.streampay import create_payment_streampay

router = Router()

# ──────────────────────────────────────────────────────────────────────────────
# Кнопка "Получить скидку" (уведомление о неактивности)
# ──────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "get_inactivity_discount")
async def handle_get_inactivity_discount(c: CallbackQuery) -> None:
    user = await models.User.get_user(c.from_user.id)
    now = datetime.datetime.now(pytz.utc)

    # Скидка уже активна — не перезаписываем
    if (
        user.inactivity_discount_end_at is not None
        and user.inactivity_discount_end_at.replace(tzinfo=pytz.utc) > now
    ):
        remaining = user.inactivity_discount_end_at.replace(tzinfo=pytz.utc) - now
        hours_left = int(remaining.total_seconds() // 3600)
        await c.answer(
            f"Скидка уже активна! Осталось ~{hours_left} ч.",
            show_alert=True,
        )
        return

    user.inactivity_discount_end_at = now + datetime.timedelta(hours=24)
    await user.save(update_fields=["inactivity_discount_end_at"])

    logger.bind(user_id=c.from_user.id, action="get_inactivity_discount").info(
        "Пользователь активировал скидку 15% за возврат"
    )

    await c.answer("✅ Скидка 15% активирована на 24 часа!", show_alert=True)

    # Обновляем сообщение — убираем кнопку
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Кнопка "Получить номер" (напоминание о незавершённой оплате)
# ──────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "resume_sms_payment")
async def handle_resume_sms_payment(c: CallbackQuery) -> None:
    user = await models.User.get_user(c.from_user.id)

    # Ищем последний незавершённый платёж с данными SMS-покупки
    payment = (
        await models.Payment.filter(
            user_id=user.id,
            is_success=False,
        )
        .order_by("-created_at")
        .first()
    )

    if not payment or not payment.continue_data or "service_code" not in payment.continue_data:
        await c.answer("Активных счетов не найдено. Выбери номер заново.", show_alert=True)
        return

    amount = float(payment.amount)
    continue_data = payment.continue_data

    await c.answer()

    # Удаляем старую кнопку
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Создаём новые платёжные ссылки и отправляем клавиатуру
    await _send_standalone_payment_keyboard(
        chat_id=c.from_user.id,
        user=user,
        amount=amount,
        continue_data=continue_data,
    )


async def _send_standalone_payment_keyboard(
    chat_id: int,
    user: models.User,
    amount: float,
    continue_data: dict,
) -> None:
    """Создаёт платёжные записи и отправляет inline-клавиатуру с ссылками напрямую."""

    buttons: list[list[InlineKeyboardButton]] = []

    # FreeKassa — всегда
    try:
        p_fk = await models.Payment.create_payment(
            user=user,
            method=models.PaymentMethod.FREEKASSA,
            amount=amount,
            continue_data=continue_data,
        )
        fk_url = generate_fk_link(amount, p_fk.id)
        if fk_url:
            buttons.append([InlineKeyboardButton(text="💳 Оплатить (карта/СБП)", url=fk_url)])
    except Exception as e:
        logger.warning(f"_send_standalone_payment_keyboard FK: {e}")

    # Lava
    try:
        lava = LavaApi()
        p_lava = await models.Payment.create_payment(
            user=user,
            method=models.PaymentMethod.LAVA,
            amount=amount,
            continue_data=continue_data,
        )
        order_id = f"sms_email_:{p_lava.id}"
        resp = await lava.create_invoice(amount, order_id=order_id)
        lava_url = resp["data"]["url"]
        p_lava.invoice_id = resp["data"]["id"]
        p_lava.order_id = order_id
        await p_lava.save()
        buttons.append([InlineKeyboardButton(text="⚡ Lava", url=lava_url)])
    except Exception as e:
        logger.warning(f"_send_standalone_payment_keyboard Lava: {e}")

    # Crypto (heleket/cryptomus)
    try:
        p_crypto = await models.Payment.create_payment(
            user=user,
            method=models.PaymentMethod.CRYPTOMUS,
            amount=amount,
            continue_data=continue_data,
        )
        crypto_url = link_to_heleket(amount, p_crypto.id)
        if crypto_url:
            buttons.append([InlineKeyboardButton(text="🔐 Криптовалюта", url=crypto_url)])
    except Exception as e:
        logger.warning(f"_send_standalone_payment_keyboard Crypto: {e}")

    # CKassa (от 50₽)
    if amount >= 50:
        try:
            p_ckassa = await models.Payment.create_payment(
                user=user,
                method=models.PaymentMethod.CKASSA,
                amount=amount,
                continue_data=continue_data,
            )
            p_ckassa.invoice_id, ckassa_url = await create_invoice_ckassa(
                amount, str(user.telegram_id)
            )
            await p_ckassa.save()
            if ckassa_url:
                buttons.append([InlineKeyboardButton(text="🏦 CKassa", url=ckassa_url)])
        except Exception as e:
            logger.warning(f"_send_standalone_payment_keyboard CKassa: {e}")

    # StreamPay (от 300₽)
    if amount >= 300:
        try:
            p_stream = await models.Payment.create_payment(
                user=user,
                method=models.PaymentMethod.STREAMPAY,
                amount=amount,
                continue_data=continue_data,
            )
            external_id = f"user_{user.id}"
            p_stream.invoice_id, stream_url = await create_payment_streampay(amount, external_id)
            await p_stream.save()
            if stream_url:
                buttons.append([InlineKeyboardButton(text="🏧 Банковская карта", url=stream_url)])
        except Exception as e:
            logger.warning(f"_send_standalone_payment_keyboard StreamPay: {e}")

    if not buttons:
        await bot.send_message(
            chat_id=chat_id,
            text="Не удалось сформировать ссылки на оплату. Попробуй открыть меню заново.",
        )
        return

    service_name = continue_data.get("service_name", "")
    header = f"💳 Оплата: <b>{service_name}</b>\n" if service_name else "💳 Оплата номера\n"
    header += f"Сумма: <b>{int(amount)} ₽</b>"

    await bot.send_message(
        chat_id=chat_id,
        text=header,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
