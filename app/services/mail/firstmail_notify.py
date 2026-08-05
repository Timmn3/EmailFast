"""
Формирование и отправка уведомлений о новых письмах FirstMail.

Общий код для обоих потоков (платная аренда и бесплатная выдача)
и для обеих точек входа (периодическая проверка и ручная кнопка),
чтобы формат письма и логика вложений не разъезжались по четырём местам.
"""

import asyncio
import html

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile
from loguru import logger

from app.dependencies import bot


# Запас до лимита Telegram на длину сообщения (4096 символов)
MAX_CONTENT_LENGTH = 3500

# Пауза между документами, чтобы не словить flood limit
ATTACHMENT_SEND_DELAY = 0.3

NO_TEXT_PLACEHOLDER = "Нет текста в сообщении."
ONLY_ATTACHMENT_PLACEHOLDER = "Текста нет, письмо содержит только вложение."


def _format_size(size: int) -> str:
    """
    Человекочитаемый размер файла.
    """
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} МБ"
    if size >= 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size} Б"


def build_firstmail_text(email_addr: str, message_obj) -> str:
    """
    Собирает текст уведомления о новом письме.

    :param email_addr: адрес ящика, на который пришло письмо
    :param message_obj: FirstMailMessage
    :return: готовый HTML-текст для Telegram
    """
    attachments = getattr(message_obj, "attachments", None) or []

    from_text = html.escape(message_obj.from_header or "-")
    subject_text = html.escape(message_obj.subject or "(без темы)")

    body = (message_obj.content or "").strip()
    if not body:
        body = ONLY_ATTACHMENT_PLACEHOLDER if attachments else NO_TEXT_PLACEHOLDER

    content_text = html.escape(body)
    if len(content_text) > MAX_CONTENT_LENGTH:
        content_text = content_text[:MAX_CONTENT_LENGTH] + "\n\n...[обрезано]"

    msg_text = (
        f'<tg-emoji emoji-id="5472239203590888751">📩</tg-emoji><b>Новое сообщение</b> на почту: <b>{html.escape(email_addr)}</b>\n\n'
        f"<b>От кого:</b> {from_text}\n"
        f"<b>Тема:</b> {subject_text}\n\n"
        f"{content_text}"
    )

    if attachments:
        lines = ["\n\n<b>📎 Вложения:</b>"]
        for attachment in attachments:
            line = f"• {html.escape(attachment.filename)} ({_format_size(attachment.size)})"
            if attachment.skip_reason == "too_large":
                line += " - слишком большой файл, не отправлен"
            lines.append(line)
        msg_text += "\n".join(lines)

    return msg_text


async def send_firstmail_message(chat_id: int, email_addr: str, message_obj) -> None:
    """
    Отправляет пользователю уведомление о письме и его вложения.

    Ошибка на одном вложении не мешает отправить остальные.

    :param chat_id: telegram_id получателя
    :param email_addr: адрес ящика, на который пришло письмо
    :param message_obj: FirstMailMessage
    """
    await bot.send_message(
        chat_id=chat_id,
        text=build_firstmail_text(email_addr, message_obj),
    )

    attachments = getattr(message_obj, "attachments", None) or []

    for attachment in attachments:
        if not attachment.content:
            continue

        try:
            await bot.send_document(
                chat_id=chat_id,
                document=BufferedInputFile(
                    attachment.content,
                    filename=attachment.filename,
                ),
            )
        except TelegramBadRequest as e:
            logger.warning(
                f"Не удалось отправить вложение {attachment.filename} "
                f"({attachment.size} байт) пользователю {chat_id}: {e}"
            )
        except Exception as e:
            logger.error(
                f"Неожиданная ошибка при отправке вложения: chat_id={chat_id} "
                f"email={email_addr} file={attachment.filename} "
                f"err={type(e).__name__}: {e}"
            )

        await asyncio.sleep(ATTACHMENT_SEND_DELAY)
