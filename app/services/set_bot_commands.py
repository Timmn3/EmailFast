from aiogram import types, Bot
from app.dependencies import ADMINS


async def set_default_commands(bot: Bot):
    user_commands = [
        types.BotCommand(command="start", description="Перезапустить"),
        types.BotCommand(command="get_sms", description="Принять SMS"),
        types.BotCommand(command="get_email", description="Принять Email"),
        types.BotCommand(command="account", description="Аккаунт")
    ]
    await bot.set_my_commands(user_commands,
                              scope=types.BotCommandScopeAllPrivateChats())

    admin_commands = [
        types.BotCommand(command="stat", description="Статистика"),
        types.BotCommand(command="freemoney", description="Создать ссылку для пополнения"),
        types.BotCommand(command="send", description="Рассылка"),
    ]

    for chat_id in ADMINS:
        try:
            await bot.set_my_commands(admin_commands,
                                      scope=types.BotCommandScopeChat(chat_id=chat_id))

        except:
            pass
