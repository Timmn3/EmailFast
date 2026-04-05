from aiogram import Router
from aiogram.types import Message
from aiogram.filters.command import Command

health_check_router = Router()

@health_check_router.message(Command("ping"))
async def ping_handler(message: Message):
    await message.answer("OK")
