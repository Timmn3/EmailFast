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


async def get_deposit_prices(dialog_manager: DialogManager, **middleware_data):
    user_id = dialog_manager.event.from_user.id
    user = await models.User.get_user(user_id)
    return {
        'prices': bt.prices_data,
        'bonus': True if user.bonus_end_at and user.bonus_end_at > timezone.now() else False
    }
