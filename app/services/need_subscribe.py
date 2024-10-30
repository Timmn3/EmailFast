from datetime import timedelta

from aiogram import types
from tortoise import timezone

from app import dependencies
from app.db import models
from app.dependencies import bot
from app.services import bot_texts as bt


async def check_subscribe(user: models.User):
    if user.in_channel:
        return True

    if user.last_check_in is None:
        user.last_check_in = timezone.now() - timedelta(minutes=31)
        await user.save()
        return False

    if user.last_check_in < timezone.now() - timedelta(minutes=30):
        member = await bot.get_chat_member(chat_id=dependencies.CHANNEL_ID, user_id=user.telegram_id)
        if member.status in ("creator", "administrator", "member"):
            user.in_channel = True
            user.last_check_in = timezone.now()
            await user.save()
            return True

        return False

    return True


async def send_subscribe_msg(user: models.User):
    msg_text = bt.SUBSCRIBE_CHANNEL
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.SUBSCRIBE_CHANNEL_BTN, url=bt.CHANNEL_LINK)
            ],
            [
                types.InlineKeyboardButton(text=bt.READY_SUBSCRIBE_CHANNEL_BTN, callback_data="check_subscribe")
            ]
        ]
    )
    await bot.send_message(chat_id=user.telegram_id, text=msg_text, reply_markup=mk)
