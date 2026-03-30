from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from loguru import logger

from app.dependencies import CODER, bot


async def send_coder(
    msg_text: str,
    reply_markup: types.InlineKeyboardMarkup | None = None,
) -> None:
    """
    Отправка служебных сообщений в чат CODER.

    Важно:
    - helper вынесен в отдельный модуль, чтобы не создавать циклический импорт
      через periodic_tasks;
    - отправляем через HTML, как и было раньше;
    - ошибки Telegram не должны ронять бизнес-логику.
    """
    if not CODER:
        return

    try:
        await bot.send_message(
            chat_id=CODER,
            text=str(msg_text),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        logger.warning(f"send_coder: TelegramBadRequest: {e}")
    except Exception as e:
        logger.opt(exception=e).error("send_coder: ошибка отправки сообщения в CODER")