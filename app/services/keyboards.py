from aiogram import types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

from app.services import bot_texts as bt


def start_kb():
    builder = ReplyKeyboardBuilder()

    builder.row(
        KeyboardButton(text=bt.RECEIVE_SMS_BTN)
    )
    # builder.add(
    #     KeyboardButton(text=bt.RENT_NUMBER)
    # )
    builder.row(
        KeyboardButton(text=bt.RECEIVE_EMAIL_BTN)
    )
    builder.add(
        KeyboardButton(text=bt.PERSONAL_CABINET_BTN),
    )
    # builder.row(
    #     KeyboardButton(text=bt.AFFILIATE_PROGRAM_BTN)
    # )

    return builder.as_markup(resize_keyboard=True, is_persistent=False)


def payment_kb(url: str):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=bt.PAY_BTN,
        web_app=types.WebAppInfo(url=url)
    )
    return builder.as_markup()
