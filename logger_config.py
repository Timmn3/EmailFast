from loguru import logger
import sys
import os

# Создаем директорию для логов
os.makedirs("logs", exist_ok=True)

# Удаляем стандартные обработчики
logger.remove()

# === Кастомные уровни логгирования ===
logger.level("USER_ACTION", no=38, color="<yellow>")

# === Универсальный безопасный форматтер ===
SAFE_USER_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | "
    "USER {extra[user_id]|-} | "
    "ACTION '{extra[action]|-}' | "
    "{message}"
)

# === Функция для добавления логгера с общими настройками ===
def add_logger(
    file_path: str,
    level: str = "INFO",
    rotation: str = "50 MB",
    retention: str = "7 days",
    compression: str = "zip",
    enqueue: bool = True,
    **kwargs
):
    """Добавляет логгер с заданными параметрами."""
    logger.add(
        file_path,
        level=level,
        rotation=rotation,
        retention=retention,
        compression=compression,
        enqueue=enqueue,
        **kwargs
    )


# === Логирование в консоль (для разработки) ===
logger.add(
    sys.stdout,
    level="DEBUG",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {message}"
)

# === Логирование действий пользователей в отдельный файл ===
add_logger(
    "logs/user_actions_{time}.log",
    level="USER_ACTION",
    rotation="10 MB",
    format=lambda record: (
        f"{record['time']:YYYY-MM-DD HH:mm:ss} | "
        f"USER {record['extra'].get('user_id', '-')}" +
        f" | ACTION '{record['extra'].get('action', '-')}' | " +
        f"{record['message']}"
    )
)


# === Логирование ошибок с traceback ===
add_logger(
    "logs/error_{time}.log",
    level="ERROR",
    rotation="20 MB",
    backtrace=True,
    diagnose=True,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message} | {exception}"
)

# === Все остальные логи ===
add_logger(
    "logs/all_logs_{time}.log",
    level="INFO",
    rotation="50 MB",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)


# === Middleware для логгирования действий пользователей ===
from aiogram import BaseMiddleware
from aiogram.types import Update
from typing import Callable, Dict, Any, Awaitable


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        user_id = None
        action = ""

        if event.message:
            user_id = event.message.from_user.id
            action = f"message: {event.message.text or event.message.content_type}"
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
            action = f"callback: {event.callback_query.data}"

        # Логируем входящее событие
        logger.bind(user_id=user_id, action=action).log("USER_ACTION", "Событие от пользователя")

        try:
            return await handler(event, data)
        except Exception as e:
            # Логируем исключение с трассировкой
            logger.opt(exception=e).error(f"Ошибка при обработке события у пользователя {user_id}: {e}")
            raise
