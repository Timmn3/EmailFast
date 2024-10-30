from datetime import timedelta

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from tortoise import timezone

from app.db import models
from app.dependencies import bot
from app.services import bot_texts as bt


async def check_low_balance(user: models.User, amount: float):
    if user.balance >= 50 > user.balance - amount:
        return True

    return False


async def send_low_balance_alert(user: models.User):
    user.bonus_end_at = timezone.now() + timedelta(days=1)
    await user.save()
    builder = InlineKeyboardBuilder()
    for price_dict in bt.prices_data:
        builder.button(
            text=str(price_dict['price']) + '₽ (+10%)',
            callback_data=f'bonus_price:{price_dict["price"]}'
        )

    builder.adjust(2)
    builder.row(
        types.InlineKeyboardButton(text=bt.OTHER_DEPOSIT_PRICE_BTN + ' (+10%)', callback_data='bonus_price:other')
    )
    await bot.send_message(
        chat_id=user.telegram_id,
        text=bt.LOW_BALANCE_ALERT,
        reply_markup=builder.as_markup()
    )
