from aiogram_dialog import Window
from aiogram_dialog.widgets.kbd import Cancel, Back, Button
from aiogram_dialog.widgets.text import Const, Format

from app.dialogs.personal_cabinet import states, keyboards
from app.dialogs.personal_cabinet.getters import get_user_info, get_deposit_prices
from app.dialogs.personal_cabinet.selected import on_deposit_price, on_other_price, on_deposit
from app.services import bot_texts as bt


def personal_cabinet_window():
    return Window(
        Format(bt.PERSONAL_CABINET),
        Button(Const(bt.DEPOSIT_BTN), id='deposit', on_click=on_deposit),
        Cancel(Const(bt.BACK_BTN)),
        state=states.PersonalMenu.user_info,
        getter=get_user_info
    )


def deposit_window():
    return Window(
        Const(bt.SELECT_DEPOSIT_PRICE),
        keyboards.prices_kb(on_deposit_price),
        Button(Const(bt.OTHER_DEPOSIT_PRICE_BTN), id='other_price', on_click=on_other_price),
        Back(Const(bt.BACK_BTN)),
        state=states.PersonalMenu.deposit,
        getter=get_deposit_prices
    )
