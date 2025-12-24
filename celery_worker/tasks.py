from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from loguru import logger
from tortoise import Tortoise

from app.db import models
from app.dependencies import API_TOKEN, DB_CONFIG
from celery_worker.celery_config import celery_app

test = True
LIMITED_USERS = [7099582423, 5097159804, 808667695, 1089138631]


async def init_db() -> None:
    await Tortoise.init(config=DB_CONFIG)
    # Оставляю как было, чтобы не ломать текущий деплой.
    await Tortoise.generate_schemas()


async def _copy_with_retry(
    bot: Bot,
    *,
    chat_id: int,
    from_chat_id: int,
    message_id: int,
    max_network_attempts: int = 3,
) -> None:
    network_attempt = 0
    while True:
        try:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            return

        except TelegramRetryAfter as e:
            wait_s = int(getattr(e, "retry_after", 1)) + 1
            logger.warning(f"FloodWait для {chat_id}: ждём {wait_s} сек")
            await asyncio.sleep(wait_s)

        except TelegramNetworkError as e:
            network_attempt += 1
            if network_attempt >= max_network_attempts:
                raise
            wait_s = 1 + network_attempt
            logger.warning(
                f"NetworkError для {chat_id}: {e}. Повтор через {wait_s} сек "
                f"(attempt {network_attempt}/{max_network_attempts})"
            )
            await asyncio.sleep(wait_s)


@celery_app.task
def send_message_batch(campaign_id: int) -> None:
    print(f"Celery таска #{campaign_id} запущена")
    asyncio.run(async_send_message(campaign_id))


async def async_send_message(campaign_id: int) -> None:
    await init_db()
    bot = Bot(token=API_TOKEN)

    try:
        campaign = await models.BroadcastCampaign.get(id=campaign_id)

        if test:
            users: list[int] = LIMITED_USERS
        else:
            users = await models.User.filter(telegram_id__isnull=False).values_list("telegram_id", flat=True)

        sent_users = set(
            await models.Broadcast.filter(campaign=campaign).values_list("sent_to", flat=True)
        )

        sent_count = 0
        skipped_count = 0

        blocked_count = 0
        chat_not_found_count = 0
        deactivated_count = 0
        bad_request_other_count = 0
        network_error_count = 0
        other_error_count = 0

        for user_id in users:
            user_id = int(user_id)

            if user_id in sent_users:
                skipped_count += 1
                continue

            try:
                await _copy_with_retry(
                    bot,
                    chat_id=user_id,
                    from_chat_id=int(campaign.sent_by_admin_id),
                    message_id=int(campaign.message_id),
                )

                await models.Broadcast.create(campaign=campaign, sent_to=user_id)
                sent_count += 1

            except TelegramForbiddenError as e:
                # bot was blocked by the user / user is deactivated / etc.
                blocked_count += 1
                logger.info(f"Forbidden для {user_id}: {e}")

            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "chat not found" in msg:
                    chat_not_found_count += 1
                elif "user is deactivated" in msg:
                    deactivated_count += 1
                else:
                    bad_request_other_count += 1
                logger.info(f"BadRequest для {user_id}: {e}")

            except TelegramNetworkError as e:
                network_error_count += 1
                logger.warning(f"NetworkError для {user_id}: {e}")

            except Exception as e:
                other_error_count += 1
                logger.error(f"Ошибка при отправке {user_id}: {e}")

            await asyncio.sleep(0.5)

        user_error_count = (
                blocked_count
                + chat_not_found_count
                + deactivated_count
                + bad_request_other_count
        )

        network_total_count = network_error_count + other_error_count

        text = (
            "Рассылка завершена!\n"
            f"✅ Успешно отправлено: {sent_count}\n"
            f"🚫 Заблокировали бота: {user_error_count}\n"
            f"🌐 NetworkError: {network_total_count}"
        )

        await bot.send_message(chat_id=int(campaign.sent_by_admin_id), text=text)

    finally:
        await bot.session.close()
        await Tortoise.close_connections()
