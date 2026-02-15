from contextlib import suppress
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, types, F

from app.db import models
from app.services.bot_texts import MAIN_MENU
from app.services.keyboards import start_kb

TERMS_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-EmailFast-01-21"
TERMS_TEXT = (
    'Перед использованием сервиса ознакомьтесь с "пользовательским соглашением" '
    f"({TERMS_URL}) ({TERMS_URL})"
)

router = Router()


@router.callback_query(F.data == "terms_accept")
async def terms_accept(call: types.CallbackQuery):
    """
    Принимаем пользовательское соглашение.

    Почему так:
    - Telegram ждёт answerCallbackQuery ограниченное время.
    - При нагрузке/задержках callback может "протухнуть" -> TelegramBadRequest.
    - Поэтому подтверждаем callback сразу, а повторные/поздние answer глушим.
    """
    # ✅ Сразу убираем "часики" (и не падаем, если callback уже протух)
    with suppress(TelegramBadRequest):
        await call.answer(cache_time=0)

    user_tg = call.from_user
    user = await models.User.get_user(user_tg.id)
    if not user:
        user = await models.User.add_user(user_tg)

    user.terms_accepted = True
    await user.save(update_fields=["terms_accepted"])

    # убираем сообщение с соглашением (если возможно)
    try:
        if call.message:
            await call.message.delete()
    except Exception:
        pass

    # ✅ Пытаемся показать тост "Принято", но не падаем на протухшем callback
    with suppress(TelegramBadRequest):
        await call.answer("✅ Принято", cache_time=0)

    # пускаем в меню
    if call.message:
        await call.message.answer(text=MAIN_MENU, reply_markup=start_kb())
