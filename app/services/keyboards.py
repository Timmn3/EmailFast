from aiogram import types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

from app.services import bot_texts as bt


def start_kb():
    """
    Inline-клавиатура главного меню.

    Используется вместо reply-клавиатуры, чтобы:
    - показывать premium emoji на кнопках;
    - не зависеть от текстового ввода пользователя;
    - вызывать нужные разделы через callback_data.
    """
    builder = InlineKeyboardBuilder()

    builder.button(
        text="Принять SMS",
        callback_data="receive_sms",
        icon_custom_emoji_id="5406809207947142040",
    )
    builder.button(
        text="Длительная аренда",
        callback_data="rent_number",
        icon_custom_emoji_id="5258419835922030550",
    )
    builder.button(
        text="Принять Email",
        callback_data="receive_email",
        icon_custom_emoji_id="5472239203590888751",
    )
    builder.button(
        text="Личный кабинет",
        callback_data="personal_cabinet",
        icon_custom_emoji_id="5257963315258204021",
    )

    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


def payment_kb(url: str):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=bt.PAY_BTN,
        web_app=types.WebAppInfo(url=url)
    )
    return builder.as_markup()
