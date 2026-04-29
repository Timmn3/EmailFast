"""
Хендлеры для уведомлений о неактивности и незавершённых платежах.
"""
import datetime
import pytz

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram_dialog import DialogManager, StartMode
from loguru import logger

from app.db import models
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.services.keyboards import send_main_menu
from app.services import bot_texts as bt

router = Router()

# ──────────────────────────────────────────────────────────────────────────────
# Кнопка "Получить скидку" (уведомление о неактивности)
# ──────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "get_inactivity_discount")
async def handle_get_inactivity_discount(c: CallbackQuery) -> None:
    user = await models.User.get_user(c.from_user.id)
    now = datetime.datetime.now(pytz.utc)

    if (
        user.inactivity_discount_end_at is not None
        and user.inactivity_discount_end_at.replace(tzinfo=pytz.utc) > now
    ):
        remaining = user.inactivity_discount_end_at.replace(tzinfo=pytz.utc) - now
        hours_left = int(remaining.total_seconds() // 3600)
        await c.answer(f"Скидка уже активна! Осталось ~{hours_left} ч.", show_alert=True)
        return

    user.inactivity_discount_end_at = now + datetime.timedelta(hours=24)
    await user.save(update_fields=["inactivity_discount_end_at"])

    logger.bind(user_id=c.from_user.id, action="get_inactivity_discount").info(
        "Пользователь активировал скидку 15% за возврат"
    )

    await c.answer("✅ Скидка 15% активирована на 24 часа!", show_alert=True)

    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await send_main_menu(c.message, bt.MAIN_MENU, parse_mode="HTML")


# ──────────────────────────────────────────────────────────────────────────────
# Кнопка "Получить номер" (напоминание о незавершённой оплате)
# ──────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "resume_sms_payment")
async def handle_resume_sms_payment(c: CallbackQuery, dialog_manager: DialogManager) -> None:
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

    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Запускаем PersonalMenu как базовый диалог.
    # continue_data передаётся как start_data — send_payment_keyboard читает его оттуда
    # и сохраняет в новых Payment записях, чтобы после оплаты покупка номера продолжилась.
    await dialog_manager.start(
        PersonalMenu.user_info,
        mode=StartMode.RESET_STACK,
        data=continue_data,
    )

    ctx = dialog_manager.current_context()
    ctx.dialog_data["price"] = amount

    from app.dialogs.personal_cabinet.selected import send_payment_keyboard
    await send_payment_keyboard(c, manager=dialog_manager, price=amount)

    logger.bind(user_id=c.from_user.id, action="resume_sms_payment").info(
        f"Открыта платёжная клавиатура для возобновления оплаты: amount={amount}"
    )
