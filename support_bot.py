import asyncio
import os
from pathlib import Path
from contextlib import suppress

import yaml
from aiogram import Bot, Dispatcher, Router
from aiogram.client.bot import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from app.db.database import init_db, close_db
from support_bot import support_forum


def _read_yaml_config() -> dict:
    cfg_path = Path(__file__).parent / "app" / "config.yaml"
    if not cfg_path.exists():
        raise RuntimeError(f"Не найден файл конфигурации: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_support_token() -> str:
    token = (os.getenv("SUPPORT_BOT_TOKEN") or "").strip()
    if token:
        return token

    cfg = _read_yaml_config()
    token = (cfg.get("SUPPORT_BOT_TOKEN") or "").strip()
    if token:
        return token

    raise RuntimeError("Не задан SUPPORT_BOT_TOKEN (env или app/config.yaml).")


def build_start_router() -> Router:
    start_router = Router()

    @start_router.message(CommandStart())
    async def start_cmd(message):
        await message.answer(
            "Здравствуйте! Операторы онлайн, расскажите что у вас случилось?"
        )

    return start_router


async def main():
    bot: Bot | None = None
    try:
        await init_db()

        bot = Bot(
            token=_get_support_token(),
            default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True),
        )

        dp = Dispatcher(storage=MemoryStorage())

        # ВАЖНО: каждый запуск создаём новый Router (чтобы не было “already attached”)
        dp.include_router(build_start_router())
        dp.include_router(support_forum.router)

        me = await bot.get_me()
        logger.info("Support bot запущен: @{}", me.username)

        await bot.delete_webhook(drop_pending_updates=True)

        try:
            await dp.start_polling(bot)
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("Остановка support-бота...")

    finally:
        with suppress(Exception):
            await close_db()
        if bot is not None:
            with suppress(Exception):
                await bot.session.close()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
