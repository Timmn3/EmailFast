from aiogram import Router, F
from aiogram.filters import ChatMemberUpdatedFilter, KICKED, LEFT, RESTRICTED, MEMBER, ADMINISTRATOR, CREATOR, \
    IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import ChatMemberUpdated

from app import dependencies
from app.db import models

router = Router()


@router.chat_member(
    ChatMemberUpdatedFilter(
        member_status_changed=IS_NOT_MEMBER >> IS_MEMBER
    )
)
async def user_subscribe(event: ChatMemberUpdated):
    user_id = event.from_user.id
    if str(event.chat.id) != dependencies.CHANNEL_ID:
        return

    user = await models.User.get_user(user_id)
    if user is None:
        return

    user.in_channel = True
    await user.save()


@router.chat_member(
    ChatMemberUpdatedFilter(
        member_status_changed=IS_MEMBER >> IS_NOT_MEMBER
    )
)
async def user_unsubscribe(event: ChatMemberUpdated):
    if str(event.chat.id) != dependencies.CHANNEL_ID:
        return

    user_id = event.from_user.id
    user = await models.User.get_user(user_id)
    if user is None:
        return
    user.in_channel = False
    user.last_check_in = None
    await user.save()
