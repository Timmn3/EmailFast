import logging
from typing import Any, Awaitable, Callable, Dict, Optional
from app import dependencies

from aiogram import BaseMiddleware, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, TelegramObject

from app.db import models

TERMS_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-EmailFast-01-21"
TERMS_TEXT_HTML = (
    f'Перед использованием сервиса ознакомьтесь с '
    f'<a href="{TERMS_URL}">пользовательским соглашением</a>.'
)


def terms_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅Я ознакомился", callback_data="terms_accept")]
        ]
    )


class TermsMiddleware(BaseMiddleware):
    """
    Глобальный гейт:
    - если terms_accepted=False → показываем соглашение и НЕ пускаем дальше
    - исключение:
        * callback 'terms_accept' (чтобы можно было принять)
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_tg: Optional[types.User] = None

        if isinstance(event, types.Message):
            user_tg = event.from_user

        elif isinstance(event, types.CallbackQuery):
            user_tg = event.from_user
            if event.data == "terms_accept":
                return await handler(event, data)

        else:
            return await handler(event, data)

        if not user_tg:
            return await handler(event, data)

        # --- получаем/создаём пользователя ---
        user = await models.User.get_user(user_tg.id)
        # --- fraud-ban гейт (аналогично terms gate) ---
        if getattr(user, "fraud_banned", False) and user_tg.id not in dependencies.ADMINS:
            banned_text = (
                "🚫 Доступ ограничен.\n\n"
                "Обнаружено несоответствие баланса и пополнений.\n"
                "Если это ошибка — напишите в поддержку."
            )

            try:
                if isinstance(event, types.CallbackQuery):
                    await event.answer("🚫 Доступ ограничен.", show_alert=True)
                    if event.message:
                        await event.message.answer(banned_text, disable_web_page_preview=True)
                else:
                    await event.answer(banned_text, disable_web_page_preview=True)
            except Exception:
                logging.exception("TermsMiddleware: failed to send fraud_banned message")

            return  # стопаем цепочку

        if not user:
            user = await models.User.add_user(user_tg)

        if getattr(user, "terms_accepted", False):
            return await handler(event, data)

        # --- блокируем и показываем соглашение ---
        try:
            if isinstance(event, types.CallbackQuery):
                # убираем "часики"
                await event.answer("Сначала примите пользовательское соглашение.", show_alert=False)

                # "выкидываем" из текущего меню: заменяем текст/кнопки на соглашение
                if event.message:
                    with suppress_edit_errors():
                        await event.message.edit_text(
                            TERMS_TEXT_HTML,
                            reply_markup=terms_kb(),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    # если edit_text нельзя (старое сообщение, нет прав и т.п.) — просто отправим новое
                    await event.message.answer(
                        TERMS_TEXT_HTML,
                        reply_markup=terms_kb(),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
            else:
                await event.answer(
                    TERMS_TEXT_HTML,
                    reply_markup=terms_kb(),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        except Exception:
            logging.exception("TermsMiddleware: failed to send terms message")

        return  # стопаем цепочку


class suppress_edit_errors:
    """
    Контекст-менеджер: глушим ошибки edit_text, чтобы спокойно сделать fallback на answer().
    """
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True  # подавить исключение
