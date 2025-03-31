from app.dependencies import API_TOKEN, DB_CONFIG
from celery_worker.celery_config import celery_app
from app.db import models
from aiogram import Bot
import asyncio
from loguru import logger
from tortoise import Tortoise


async def init_db():
    await Tortoise.init(config=DB_CONFIG)
    await Tortoise.generate_schemas()


@celery_app.task
def send_message_batch(campaign_id: int):
    print("Celery таска запущена")
    asyncio.run(async_send_message(campaign_id))  # Запускаем асинхронную задачу


async def async_send_message(campaign_id: int):
    await init_db()  # Инициализация базы данных
    bot = Bot(token=API_TOKEN)

    campaign = await models.BroadcastCampaign.get(id=campaign_id)
    users = await models.User.all()
    sent_users = {b.sent_to for b in await models.Broadcast.filter(campaign=campaign)}

    sent_count = 0
    blocked_count = 0

    for user in users:
        if user.telegram_id in sent_users:
            continue  # Пропускаем, если уже отправляли
        try:
            await bot.send_message(chat_id=5635586329, text=campaign.message_text)
            await models.Broadcast.create(campaign=campaign, sent_to=user.telegram_id)
            sent_count += 1  # Увеличиваем счётчик успешных отправок
        except Exception as e:
            error_message = str(e)

            # Проверяем на конкретную ошибку "chat not found"
            if "chat not found" in error_message:
                blocked_count += 1  # Увеличиваем счётчик заблокировавших бота
            else:
                logger.error(f"Ошибка при отправке {user.telegram_id}: {e}")

        await asyncio.sleep(0.5)

    # Отправляем админу статистику по рассылке
    text = (f'Рассылка завершена!\n'
            f'✅ Успешно отправлено: {sent_count}\n'
            f'🚫 Заблокировали бота: {blocked_count}')

    await bot.send_message(chat_id=campaign.sent_by_admin_id, text=text)

    await bot.session.close()
    await Tortoise.close_connections()