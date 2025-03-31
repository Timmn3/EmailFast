from app.dependencies import API_TOKEN, DB_CONFIG
from celery_worker.celery_config import celery_app
from app.db import models
from aiogram import Bot
import asyncio
from loguru import logger
from tortoise import Tortoise

test = True

ADMINS = [7099582423, 5097159804, 808667695, 1089138631]

async def init_db():
    await Tortoise.init(config=DB_CONFIG)
    await Tortoise.generate_schemas()

@celery_app.task
def send_message_batch(campaign_id: int):
    print("Celery таска запущена")
    asyncio.run(async_send_message(campaign_id))  # Запускаем асинхронную задачу


async def async_send_message(campaign_id: int):
    await init_db()
    bot = Bot(token=API_TOKEN)

    campaign = await models.BroadcastCampaign.get(id=campaign_id)
    users = ADMINS if test else await models.User.all()
    sent_users = {b.sent_to for b in await models.Broadcast.filter(campaign=campaign)}

    sent_count = 0
    blocked_count = 0

    for user_id in users:
        if user_id in sent_users:
            continue
        try:
            await bot.send_message(chat_id=user_id, text=campaign.message_text)
            await models.Broadcast.create(campaign=campaign, sent_to=user_id)
            sent_count += 1
        except Exception as e:
            error_message = str(e)
            if "chat not found" in error_message:
                blocked_count += 1
            else:
                logger.error(f"Ошибка при отправке {user_id}: {e}")
        await asyncio.sleep(0.5)

    text = (f'Рассылка завершена!\n'
            f'✅ Успешно отправлено: {sent_count}\n'
            f'🚫 Заблокировали бота: {blocked_count}')

    await bot.send_message(chat_id=campaign.sent_by_admin_id, text=text)

    await bot.session.close()
    await Tortoise.close_connections()
