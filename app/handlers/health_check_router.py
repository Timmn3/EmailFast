from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters.command import Command

from app.dependencies import CODER

health_check_router = Router()


@health_check_router.message(Command("ping"))
async def ping_handler(message: Message) -> None:
    """
    Технический хэндлер для проверки доступности бота.
    """
    await message.answer("OK")


@health_check_router.message(
    F.text,
    F.from_user.id == CODER,
    F.entities.func(lambda entities: any(e.type == "custom_emoji" for e in (entities or []))),
)
async def catch_custom_emoji_id_handler(message: Message) -> None:
    """
    Ловит custom emoji в текстовом сообщении и отправляет их custom_emoji_id.
    Работает только для пользователя CODER.

    Как использовать:
    1. Отправь боту сообщение с нужным premium/custom emoji.
    2. Бот вернёт список найденных custom_emoji_id.
    3. Скопируй нужный ID и подставь его в тег:
       <tg-emoji emoji-id="...">🙂</tg-emoji>

    Важно:
    - хэндлер обрабатывает только текстовые сообщения от CODER;
    - срабатывает только если в сообщении есть хотя бы одно custom emoji;
    - дубликаты ID в одном сообщении убираются.
    """
    entities = message.entities or []
    found_items: list[tuple[str, str]] = []

    for entity in entities:
        if entity.type == "custom_emoji" and entity.custom_emoji_id:
            emoji_text = entity.extract_from(message.text or "")
            found_items.append((emoji_text, entity.custom_emoji_id))

    if not found_items:
        return

    unique_items: list[tuple[str, str]] = []
    seen_ids: set[str] = set()

    for emoji_text, emoji_id in found_items:
        if emoji_id not in seen_ids:
            seen_ids.add(emoji_id)
            unique_items.append((emoji_text, emoji_id))

    response_lines = ["Найдены custom emoji ID:"]

    for index, (emoji_text, emoji_id) in enumerate(unique_items, start=1):
        response_lines.append(f"{index}. {emoji_text} → <code>{emoji_id}</code>")

    await message.answer("\n".join(response_lines), parse_mode="HTML")