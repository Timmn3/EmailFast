from aiogram import Router, F
from aiogram.filters import ChatMemberUpdatedFilter, KICKED, LEFT, RESTRICTED, MEMBER, ADMINISTRATOR, CREATOR, \
    IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import ChatMemberUpdated

from app import dependencies
from app.db import models
from loguru import logger


router = Router()


@router.chat_member(
    ChatMemberUpdatedFilter(
        member_status_changed=IS_NOT_MEMBER >> IS_MEMBER
    )
)
async def user_subscribe(event: ChatMemberUpdated):
    try:
        user_id = event.from_user.id
        # logger.bind(user_id=user_id, action="user_subscribe").log("USER_ACTION", "Пользователь подписался на канал")
        if str(event.chat.id) != dependencies.CHANNEL_ID:
            return

        # logger.bind(user_id=user_id, action="user_subscribe").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        # logger.bind(user_id=user_id, action="user_subscribe").log("USER_ACTION", f"Результат из БД: пользователь найден={user is not None}")

        if user is None:
            return

        user.in_channel = True
        await user.save()
        logger.bind(user_id=user_id, action="user_subscribe").log("USER_ACTION", "Статус in_channel обновлён: True")
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /user_subscribe: {e}")


@router.chat_member(
    ChatMemberUpdatedFilter(
        member_status_changed=IS_MEMBER >> IS_NOT_MEMBER
    )
)
async def user_unsubscribe(event: ChatMemberUpdated):
    try:
        user_id = event.from_user.id
        if str(event.chat.id) != dependencies.CHANNEL_ID:
            return

        logger.bind(user_id=user_id, action="user_unsubscribe").log("USER_ACTION", "Пользователь отписался от канала")
        # logger.bind(user_id=user_id, action="user_unsubscribe").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        # logger.bind(user_id=user_id, action="user_unsubscribe").log("USER_ACTION", f"Результат из БД: пользователь найден={user is not None}")

        if user is None:
            return

        user.in_channel = False
        user.last_check_in = None
        await user.save()
        logger.bind(user_id=user_id, action="user_unsubscribe").log("USER_ACTION", "Статус in_channel обновлён: False, last_check_in сброшен")
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /user_unsubscribe: {e}")