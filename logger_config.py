from loguru import logger
import sys
import os
import asyncio
import traceback
from aiogram import BaseMiddleware
from aiogram.types import Update
from typing import Callable, Dict, Any, Awaitable


# === Создание подкаталогов ===
os.makedirs("logs/errors", exist_ok=True)
os.makedirs("logs/users", exist_ok=True)
os.makedirs("logs/general", exist_ok=True)

# === Удаление стандартного обработчика ===
logger.remove()

# === Кастомные уровни логгирования ===
logger.level("USER_ACTION", no=38, color="<yellow>")
logger.level("REFERRAL_BONUS", no=39, color="<cyan>")


# === Фильтр для подавления шума при завершении ===
def _shutdown_noise_filter(record):
    msg = record["message"]
    if "Executor shutdown has been called" in msg:
        return False
    exc = record.get("exception")
    if exc and exc[0] is not None and issubclass(exc[0], asyncio.CancelledError):
        return False
    return True


# === Форматтеры ===

def formatter(record):
    rel_path = os.path.relpath(record["file"].path, os.getcwd())
    user_id = record["extra"].get("user_id", "-")
    action = record["extra"].get("action", "-")

    # Если есть exception, формируем трассировку
    exc = record.get("exception")
    tb = ""
    if exc:
        tb = "".join(traceback.format_exception(*exc))

    return (
        f"<green>{record['time']:DD-MM-YYYY HH-mm-SS}</green> | "
        f"<level>{record['level']}</level> | "
        f"<cyan>USER {user_id}</cyan> | "
        f"<magenta>ACTION '{action}'</magenta> | "
        f"<blue>{rel_path}:{record['line']}</blue> | "
        f"<level>{record['message']}</level>\n"
        f"{tb}"
    )


def user_action_formatter(record):
    rel_path = os.path.relpath(record["file"].path, os.getcwd())
    user_id = record["extra"].get("user_id", "-")
    action = record["extra"].get("action", "-")

    return (
        f"{record['time']:YYYY-MM-DD HH:mm:ss} | USER {user_id} | ACTION '{action}' | "
        f"{rel_path}:{record['line']} | {record['message']}\n"
    )


def error_formatter(record):
    exc = record.get("exception")
    tb = ""
    if exc:
        tb = "".join(traceback.format_exception(*exc))

    rel_path = os.path.relpath(record["file"].path, os.getcwd())
    user_id = record["extra"].get("user_id", "-")
    action = record["extra"].get("action", "-")

    return (
        f"{record['time']:DD-MM-YYYY HH-mm-SS} | LEVEL {record['level']} | FILE {rel_path}:{record['line']}\n"
        f"USER: {user_id} | ACTION: {action}\n"
        f"MESSAGE: {record['message']}\n"
        f"EXCEPTION:\n{tb}\n"
    )


# === Логирование в консоль с цветами и мета-информацией ===
logger.add(
    sys.stdout,
    level="DEBUG",
    format=formatter,
    backtrace=True,
    diagnose=True,
    colorize=True,
    filter=_shutdown_noise_filter,
)

# === Логирование действий пользователей ===
logger.add(
    "logs/users/user_actions_{time:DD-MM-YYYY HH:mm}.log",
    level="USER_ACTION",
    rotation="10 MB",
    retention="14 days",
    compression="zip",
    encoding="utf-8",
    format=user_action_formatter,
    enqueue=False
)

# === Логирование ошибок с трассировкой ===
logger.add(
    "logs/errors/error_{time:DD-MM-YYYY HH:mm}.log",
    level="ERROR",
    rotation="20 MB",
    retention="30 days",
    format=error_formatter,
    backtrace=True,
    diagnose=True,
    compression="zip",
    encoding="utf-8",
    enqueue=False
)

# === Общие логи приложения (без user_id/action) ===
logger.add(
    "logs/general/all_logs_{time:DD-MM-YYYY HH:mm}.log",
    level="INFO",
    rotation="50 MB",
    retention="14 days",
    compression="zip",
    encoding="utf-8",
    format=lambda r: (
        f"{r['time']:YYYY-MM-DD HH:mm:ss} | {r['level']} | "
        f"{os.path.relpath(r['file'].path, os.getcwd())}:{r['line']} | "
        f"{r['message']}\n"
    ),
    enqueue=False  # полезно при многопоточности
)


# === Middleware для логгирования действий пользователей ===
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
        except Exception:
            logger.exception(
                f"Ошибка при обработке события у пользователя {user_id}"
            )
            raise


# === Декоратор для логирования действий ===
def log_action(action_name: str):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            update = args[0]  # предполагаем, что первый аргумент — это Update
            user_id = getattr(update, "from_user", None)
            user_id = user_id.id if user_id else None
            logger.bind(user_id=user_id, action=action_name).log("USER_ACTION", f"Запуск действия: {action_name}")
            return await func(*args, **kwargs)
        return wrapper
    return decorator