from aiogram import Bot
from app.dependencies import ADMINS


async def notify_wakeup_bot(bot: Bot):
    try:
        await bot.send_message(chat_id=ADMINS[0], text='Успешный запуск бота!')
    except:
        pass