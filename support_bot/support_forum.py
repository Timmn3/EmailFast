from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest

from loguru import logger

from app.dependencies import ADMINS
from app.db import models


router = Router()

SETTING_FORUM_CHAT_ID = "forum_chat_id"


def _topic_title(user) -> str:
    full_name = (user.full_name or "User").strip()
    username = (user.username or "").strip()
    title = f"{full_name}" + (f" (@{username})" if username else "") + f" | {user.id}"
    return title[:120]


def _is_thread_not_found(err: TelegramBadRequest) -> bool:
    # TelegramBadRequest: "Bad Request: message thread not found"
    return "message thread not found" in str(err).lower()


async def _get_forum_chat_id() -> int | None:
    row = await models.SupportForumSetting.get_or_none(key=SETTING_FORUM_CHAT_ID)
    if not row:
        return None
    try:
        return int(row.value)
    except Exception:
        return None


async def _set_forum_chat_id(chat_id: int) -> None:
    await models.SupportForumSetting.update_or_create(
        defaults={"value": str(chat_id)},
        key=SETTING_FORUM_CHAT_ID,
    )


async def _drop_user_topic(user_id: int) -> None:
    # Если в БД осталась связь с несуществующим топиком — удаляем, чтобы создать новый.
    await models.SupportForumTopic.filter(telegram_id=user_id).delete()


async def _ensure_topic(message: Message, forum_chat_id: int) -> int:
    """
    Возвращает message_thread_id для пользователя.
    Если топика нет — создаёт новый и сохраняет в БД.
    """
    user_id = message.from_user.id

    existing = await models.SupportForumTopic.get_or_none(telegram_id=user_id)
    if existing:
        return existing.thread_id

    # создаём топик
    topic = await message.bot.create_forum_topic(
        chat_id=forum_chat_id,
        name=_topic_title(message.from_user),
    )
    thread_id = topic.message_thread_id

    await models.SupportForumTopic.create(
        telegram_id=user_id,
        thread_id=thread_id,
        title=_topic_title(message.from_user),
    )

    # шапка тикета
    header = (
        "🆕 <b>Новый тикет</b>\n"
        f"👤 <a href='tg://user?id={user_id}'>{message.from_user.full_name}</a>"
        + (f" (@{message.from_user.username})" if message.from_user.username else "")
        + f"\n🆔 <code>{user_id}</code>\n\n"
        "ℹ️ Отвечайте <b>reply</b> на сообщение пользователя — бот отправит ответ ему.\n"
        "✅ Закрыть тикет в этом топике: <code>/close</code>"
    )
    await message.bot.send_message(
        chat_id=forum_chat_id,
        message_thread_id=thread_id,
        text=header,
        disable_web_page_preview=True,
    )

    return thread_id


@router.message(Command("bind_support"))
async def bind_support(message: Message):
    """
    Привязать текущую супергруппу как форум поддержки.
    Писать в General (или просто в этой группе).
    """
    if message.chat.type != ChatType.SUPERGROUP:
        await message.answer("Команду /bind_support нужно писать в супергруппе (форум-группе).")
        return

    if message.from_user.id not in ADMINS:
        await message.answer("Недостаточно прав.")
        return

    # Доп. проверка: темы должны быть включены
    if not getattr(message.chat, "is_forum", False):
        await message.answer("⚠️ В этой группе не включены «Темы». Включи их и повтори /bind_support.")
        return

    await _set_forum_chat_id(message.chat.id)
    await message.answer(f"✅ Форум-группа поддержки привязана: <code>{message.chat.id}</code>")


@router.message(Command("support_chatid"))
async def support_chatid(message: Message):
    thread_id = getattr(message, "message_thread_id", None)
    await message.answer(
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"thread_id: <code>{thread_id}</code>"
    )


@router.message(Command("close"))
async def close_ticket(message: Message):
    """
    Закрыть тикет (закрывает топик в Telegram + удаляет связь user↔thread из БД).
    Команду писать внутри топика.
    """
    if message.chat.type != ChatType.SUPERGROUP:
        return

    if message.from_user.id not in ADMINS:
        await message.answer("Недостаточно прав.")
        return

    thread_id = getattr(message, "message_thread_id", None)
    if not thread_id:
        await message.answer("Команду /close нужно писать внутри топика.")
        return

    # найдём пользователя по thread_id
    topic = await models.SupportForumTopic.get_or_none(thread_id=thread_id)
    if topic:
        await topic.delete()

    try:
        await message.bot.close_forum_topic(chat_id=message.chat.id, message_thread_id=thread_id)
    except TelegramBadRequest as e:
        logger.warning("Не удалось закрыть топик: {}", e)

    await message.answer("✅ Тикет закрыт.")


@router.message(F.chat.type == ChatType.PRIVATE)
async def user_to_forum(message: Message):
    """
    Пользователь пишет боту → уходит в топик (1 пользователь = 1 топик).
    """
    # команды (например /start) не считаем обращением в поддержку
    if message.text and message.text.startswith("/"):
        return

    forum_chat_id = await _get_forum_chat_id()
    if not forum_chat_id:
        await message.answer("⚠️ Поддержка ещё не настроена. Напишите позже.")
        return

    # 1) получаем thread_id (может быть устаревшим в БД)
    thread_id = await _ensure_topic(message, forum_chat_id)

    # 2) отправка текста
    if message.text:
        try:
            sent = await message.bot.send_message(
                chat_id=forum_chat_id,
                message_thread_id=thread_id,
                text=(
                    f"\n🆔 <code>{message.from_user.id}</code>\n\n"
                    f"{message.text}"
                ),
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as e:
            # Если топик не найден — удаляем связь и создаём новый топик, затем повторяем отправку 1 раз.
            if _is_thread_not_found(e):
                await _drop_user_topic(message.from_user.id)
                thread_id = await _ensure_topic(message, forum_chat_id)
                sent = await message.bot.send_message(
                    chat_id=forum_chat_id,
                    message_thread_id=thread_id,
                    text=(
                        f"\n🆔 <code>{message.from_user.id}</code>\n\n"
                        f"{message.text}"
                    ),
                    disable_web_page_preview=True,
                )
            else:
                raise

        await models.SupportForumMessageMap.create(
            group_chat_id=forum_chat_id,
            message_id=sent.message_id,
            telegram_id=message.from_user.id,
        )
        return

    # 3) отправка медиа/файлов
    try:
        sent = await message.copy_to(chat_id=forum_chat_id, message_thread_id=thread_id)
    except TelegramBadRequest as e:
        if _is_thread_not_found(e):
            await _drop_user_topic(message.from_user.id)
            thread_id = await _ensure_topic(message, forum_chat_id)
            sent = await message.copy_to(chat_id=forum_chat_id, message_thread_id=thread_id)
        else:
            raise
    except Exception as e:
        logger.opt(exception=e).error("Не удалось переслать сообщение в поддержку")
        await message.answer("❌ Не получилось отправить сообщение в поддержку. Попробуйте ещё раз.")
        return

    await models.SupportForumMessageMap.create(
        group_chat_id=forum_chat_id,
        message_id=sent.message_id,
        telegram_id=message.from_user.id,
    )


@router.message(F.chat.type == ChatType.SUPERGROUP)
async def forum_to_user(message: Message):
    """
    Оператор отвечает reply на сообщение пользователя в топике → бот отправляет ответ пользователю.
    """
    forum_chat_id = await _get_forum_chat_id()
    if not forum_chat_id or message.chat.id != forum_chat_id:
        return

    thread_id = getattr(message, "message_thread_id", None)
    if not thread_id:
        return

    # отправляем только если это reply
    if not message.reply_to_message:
        return

    replied_msg_id = message.reply_to_message.message_id
    mapped = await models.SupportForumMessageMap.get_or_none(
        group_chat_id=message.chat.id,
        message_id=replied_msg_id,
    )
    if not mapped:
        return

    user_id = mapped.telegram_id

    try:
        if message.text and not message.text.startswith("/"):
            await message.bot.send_message(chat_id=user_id, text=message.text, disable_web_page_preview=True)
        else:
            await message.copy_to(chat_id=user_id)
    except TelegramBadRequest as e:
        logger.warning("Не удалось отправить пользователю {}: {}", user_id, e)
        await message.answer("❌ Не удалось отправить пользователю (возможно, он заблокировал бота).")
