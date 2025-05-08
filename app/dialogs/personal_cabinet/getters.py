from aiogram_dialog import DialogManager
from tortoise import timezone

from app.db import models
from app.services.sms_receive import SmsReceive
from app.services import bot_texts as bt
from loguru import logger


async def get_user_info(dialog_manager: DialogManager, **middleware_data):
    """
    Получает информацию о пользователе для отображения в интерфейсе.
    :param dialog_manager: Объект DialogManager.
    :param middleware_data: Дополнительные данные из middleware.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        logger.bind(user_id=user_id, action='get_user_info').log(
            "USER_ACTION",
            "Запрос информации о пользователе"
        )

        user = await models.User.get_user(user_id)
        if not user:
            logger.bind(user_id=user_id, action='get_user_info').log(
                "USER_ACTION",
                "Пользователь не найден"
            )
            return {}

        logger.bind(user_id=user_id, action='get_user_info').log(
            "USER_ACTION",
            f"Информация о пользователе получена: баланс={user.balance}, ref_balance={user.ref_balance}"
        )
        return {
            'user_id': user.telegram_id,
            'balance': user.balance,
            'ref_balance': int(user.ref_balance),
        }
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_user_info: {e}")
        return {}


async def get_deposit_prices(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список доступных сумм пополнения и проверяет наличие бонуса у пользователя.
    :param dialog_manager: Объект DialogManager.
    :param middleware_data: Дополнительные данные из middleware.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        if user_id is None:
            user_id = dialog_manager.start_data.get("user_id")

        logger.bind(user_id=user_id, action='get_deposit_prices').log(
            "USER_ACTION",
            "Запрос списка цен на пополнение"
        )

        user = await models.User.get_user(user_id)

        bonus_active = False
        if user and user.bonus_end_at and user.bonus_end_at > timezone.now():
            bonus_active = True
            logger.bind(user_id=user_id, action='get_deposit_prices').log(
                "USER_ACTION",
                f"Бонус активен до {user.bonus_end_at}"
            )

        logger.bind(user_id=user_id, action='get_deposit_prices').log(
            "USER_ACTION",
            "Список цен успешно подготовлен"
        )
        return {
            'prices': bt.prices_data,
            'bonus': bonus_active
        }
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_deposit_prices: {e}")
        return {}