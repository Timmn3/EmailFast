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
    user_tg = call.from_user
    user = await models.User.get_user(user_tg.id)
    if not user:
        user = await models.User.add_user(user_tg)

    user.terms_accepted = True
    await user.save()

    # убираем сообщение с соглашением (если возможно)
    try:
        await call.message.delete()
    except Exception:
        pass

    await call.answer("✅ Принято")

    # пускаем в меню
    await call.message.answer(text=MAIN_MENU, reply_markup=start_kb())
