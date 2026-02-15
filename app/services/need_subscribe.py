from datetime import timedelta

from aiogram import types
from tortoise import timezone
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from loguru import logger

from app import dependencies
from app.db import models
from app.dependencies import bot, CHECK_CHANNEL
from app.services import bot_texts as bt


async def check_subscribe(user: models.User):
    """
    Проверяет подписку на канал (если CHECK_CHANNEL=True).

    ⚠️ Важно: для новых пользователей (пока они не получили первое SMS или не создали временную почту)
    проверка подписки отключена, чтобы не ломать онбординг.
    """
    if not CHECK_CHANNEL:
        return True

    # ✅ Не требуем подписку до первого фактического использования сервиса
    if not getattr(user, "channel_gate_enabled", True):
        return True

    if user.in_channel:
        return True

    if user.last_check_in is None:
        user.last_check_in = timezone.now() - timedelta(minutes=31)
        await user.save()
        return False

    if user.last_check_in < timezone.now() - timedelta(minutes=30):
        # Приводим CHANNEL_ID к корректному виду:
        # - "@channel" оставляем строкой
        # - "-100..." (строка) превращаем в int
        chat_id = dependencies.CHANNEL_ID
        if isinstance(chat_id, str):
            chat_id = chat_id.strip()
            if not chat_id.startswith("@") and chat_id.lstrip("-").isdigit():
                chat_id = int(chat_id)

        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user.telegram_id)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            # Чтобы бот не падал из-за неверной конфигурации/прав канала
            logger.bind(user_id=user.telegram_id, action="check_subscribe").error(
                f"Не удалось проверить подписку: chat_id={chat_id!r}. Ошибка: {e}"
            )
            # Fail-open: сервис не ложится, но в логах будет точная причина
            return True
        except Exception as e:
            logger.bind(user_id=user.telegram_id, action="check_subscribe").error(
                f"Неожиданная ошибка проверки подписки: chat_id={chat_id!r}. Ошибка: {e}"
            )
            return True

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
