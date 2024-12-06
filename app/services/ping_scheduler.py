from app.dependencies import USER_BOT, bot

async def userbot_ping():
    await bot.send_message(chat_id=USER_BOT, text="ping")