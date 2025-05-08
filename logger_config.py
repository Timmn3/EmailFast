
from loguru import logger
import sys
import os

# === Создание подкаталогов ===
os.makedirs("logs/errors", exist_ok=True)
os.makedirs("logs/users", exist_ok=True)
os.makedirs("logs/general", exist_ok=True)

# === Удаление стандартного обработчика ===
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

# === Универсальная функция для добавления логгера ===
def add_logger(
    file_path: str,
    level: str = "INFO",
    rotation: str = "50 MB",
    retention: str = "7 days",
    compression: str = "zip",
    enqueue: bool = True,
    **kwargs
):
    logger.add(
        file_path,
        level=level,
        rotation=rotation,
        retention=retention,
        compression=compression,
        enqueue=enqueue,
        **kwargs
    )

# === Логирование в консоль с цветами и мета-информацией ===
logger.add(
    sys.stdout,
    level="DEBUG",
    format=lambda record: (
        f"<green>{record['time']:YYYY-MM-DD HH:mm:ss}</green> | "
        f"<cyan>USER {record['extra'].get('user_id', '-')}</cyan> | "
        f"<magenta>ACTION '{record['extra'].get('action', '-')}'</magenta> | "
        f"<blue>{record['file'].path.replace(os.getcwd() + os.sep, '')}:{record['line']}</blue> | "
        f"<level>{record['message']}</level>\n"
    ),
    backtrace=True,
    diagnose=True
)

# === Логирование действий пользователей ===
add_logger(
    "logs/users/user_actions_{time}.log",
    level="USER_ACTION",
    rotation="10 MB",
    format=lambda record: (
        f"{record['time']:YYYY-MM-DD HH:mm:ss} | "
        f"USER {record['extra'].get('user_id', '-')} | "
        f"ACTION '{record['extra'].get('action', '-')}' | "
        f"{record['file'].path.replace(os.getcwd() + os.sep, '')}:{record['line']} | "
        f"{record['message']}\n"
    )
)

# === Логирование ошибок с трассировкой ===
add_logger(
    "logs/errors/error_{time}.log",
    level="ERROR",
    rotation="20 MB",
    backtrace=True,
    diagnose=True,
    format=lambda record: (
        f"{record['time']:YYYY-MM-DD HH:mm:ss} | "
        f"{record['level'].name} | "
        f"{record['file'].path.replace(os.getcwd() + os.sep, '')}:{record['line']} | "
        f"{record['message']} | {record['exception']}\n"
    )
)

# === Общие логи приложения ===
add_logger(
    "logs/general/all_logs_{time}.log",
    level="INFO",
    rotation="50 MB",
    format=lambda record: (
        f"{record['time']:YYYY-MM-DD HH:mm:ss} | "
        f"{record['level'].name} | "
        f"{record['file'].path.replace(os.getcwd() + os.sep, '')}:{record['line']} | "
        f"{record['message']}\n"
    )
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

        logger.bind(user_id=user_id, action=action).log("USER_ACTION", "Событие от пользователя")

        try:
            return await handler(event, data)
        except Exception as e:
            logger.opt(exception=e).error(f"Ошибка при обработке события у пользователя {user_id}: {e}")
            raise
