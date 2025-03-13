from aiogram_dialog import DialogManager
from tortoise import timezone

from app.db import models
from app.services.sms_receive import SmsReceive
from app.services import bot_texts as bt


async def get_user_info(dialog_manager: DialogManager, **middleware_data):
    user = await models.User.get_user(dialog_manager.event.from_user.id)
    if not user:
        return

    return {
        'user_id': user.telegram_id,
        'balance': user.balance,
        'ref_balance': user.ref_balance,
    }


import math

async def get_deposit_prices(dialog_manager: DialogManager, **middleware_data):
    user_id = dialog_manager.event.from_user.id
    if user_id is None:
        user_id = dialog_manager.start_data.get("user_id")

    user = await models.User.get_user(user_id)
    balance = user.balance if user else 0.0

    service_price = dialog_manager.dialog_data.get("service_price", 0)

    # Рассчитываем недостающую сумму
    missing_amount = math.ceil(max(service_price - balance, 50)) if balance < service_price else 0

    # Копируем стандартные цены
    dynamic_prices = []

    # Если недостающая сумма больше 0, добавляем кнопку первой
    if missing_amount > 0:
        dynamic_prices.append({'id': 5, 'price': int(missing_amount)})

    # Добавляем стандартные цены
    dynamic_prices.extend(bt.prices_data)

    return {
        'prices': dynamic_prices,
        'bonus': True if user and user.bonus_end_at and user.bonus_end_at > timezone.now() else False
    }