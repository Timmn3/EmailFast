"""
Общие кнопки способов оплаты для окон выбора метода.

Кнопки CKassa (карта и СБП) собраны здесь в одном месте, потому что они
повторяются в шести окнах трёх диалогов. Отключение через один флаг
CKASSA_ENABLED гасит их разом, без правки каждого окна.
"""
from aiogram.enums import ButtonStyle
from aiogram_dialog.widgets.kbd import Button
from aiogram_dialog.widgets.text import Const
from aiogram_dialog.widgets.style import Style

from app.dependencies import CKASSA_ENABLED
from app.services import bot_texts as bt


def ckassa_buttons(on_sbp, on_card):
    """
    Пара кнопок оплаты через CKassa: СБП и банковская карта.

    Возвращает пустой список, когда CKASSA_ENABLED выключен — тогда способ
    просто не появляется в окне. Результат распаковывается в Window через `*`.

    :param on_sbp: обработчик кнопки СБП (switch_to_ckassa_sbp_payment).
    :param on_card: обработчик кнопки карты (switch_to_payment).
    """
    if not CKASSA_ENABLED:
        return []

    return [
        Button(
            Const(bt.METHOD_BANK_SBP),
            id='sbp_ckassa',
            on_click=on_sbp,
            style=Style(style=ButtonStyle.SUCCESS, emoji_id="5265074015868822600"),  # зелёная
        ),
        Button(
            Const(bt.METHOD_CKASSA),
            id='ckassa',
            on_click=on_card,
            style=Style(emoji_id="5472250091332993630"),  # 💳
        ),
    ]
