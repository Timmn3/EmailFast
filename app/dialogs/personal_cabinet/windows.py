from aiogram import F
from aiogram_dialog import Window
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Back, Button, Url
from aiogram_dialog.widgets.text import Const, Format
from app import dependencies
from app.dialogs.personal_cabinet import states, keyboards
from app.dialogs.personal_cabinet.getters import get_user_info, get_deposit_prices
from app.dialogs.personal_cabinet.selected import on_deposit_price, on_other_price, on_deposit, on_enter_other_price, \
    switch_to_payment, send_payment_keyboard_anypay, on_payment_method, affiliate
from app.handlers.affiliate_program import affiliate_program
from app.services import bot_texts as bt
from app.services.bot_texts import LINK_TO_BUTTON
from app.services.stars_pay import send_invoice_handler_stars


def personal_cabinet_window():
    return Window(
        Format(bt.PERSONAL_CABINET),
        Button(Const(bt.DEPOSIT_BTN), id='deposit', on_click=on_deposit),
        Button(Const(bt.AFFILIATE_PROGRAM_BTN), id='affiliate', on_click=affiliate),
        Url(Const(bt.INSTRUCTIONS), url=Const(LINK_TO_BUTTON)),
        Url(Const(bt.SUPPORT_BTN), url=Const(dependencies.SUPPORT_URL)),
        state=states.PersonalMenu.user_info,
        getter=get_user_info
    )


def deposit_window():
    return Window(
        Const(bt.SELECT_DEPOSIT_PRICE),
        keyboards.prices_kb(on_deposit_price),
        Button(Const(bt.OTHER_DEPOSIT_PRICE_BTN), id='other_price', on_click=on_other_price, when=~F['bonus']),
        Button(Const(bt.OTHER_DEPOSIT_PRICE_BTN + ' (+10%)'), id='other_price', on_click=on_other_price,
               when=F['bonus']),
        Back(Const(bt.BACK_BTN)),
        state=states.PersonalMenu.deposit,
        getter=get_deposit_prices
    )


def enter_amount_window():
    return Window(
        Const(bt.ENTER_DEPOSIT_AMOUNT),
        TextInput(id='enter_deposit_amount', on_success=on_enter_other_price),
        Back(Const(bt.BACK_BTN)),
        state=states.PersonalMenu.enter_amount
    )


# Функция для нового окна выбора метода оплаты
def payment_method_window():
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_CKASSA), id='ckassa', on_click=switch_to_payment),
        Button(Const(bt.METHOD_STREAMPAY), id='bank_card', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_LAVA), id='SBP', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_ANYPAY), id='anypay', on_click=send_payment_keyboard_anypay),
        # Button(Const(bt.METHOD_STARS_BTN), id='stars', on_click=send_invoice_handler_stars),
        Button(Const(bt.METHOD_CRYPTO_BTN), id='crypto', on_click=switch_to_payment),
        Button(Const(bt.METHOD_OTHER_BTN), id='other', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_deposit),
        state=states.PersonalMenu.payment_method
    )

# Функция для выбора метода оплаты с платежем менее 300 руб
def payment_method_window_minimum_pay():
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_CKASSA), id='ckassa', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_STARS_BTN), id='stars', on_click=send_invoice_handler_stars),
        Button(Const(bt.METHOD_CRYPTO_BTN), id='crypto', on_click=switch_to_payment),
        Button(Const(bt.METHOD_OTHER_BTN), id='other', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_deposit),
        state=states.PersonalMenu.payment_method_minimum_pay
    )


# Функция для нового окна выбора метода оплаты AnyPay
def payment_method_window_anypay():
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_BANK_CARD), id='card', on_click=switch_to_payment),
        Button(Const(bt.METHOD_BANK_SBP), id='sbp', on_click=switch_to_payment),
        Button(Const(bt.METHOD_BANK_CRYPTOCURRENCY), id='btc', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_payment_method),
        state=states.PersonalMenu.payment_method_anypay
    )

# Функция для нового окна выбора метода оплаты AnyPay
def payment_method_window_anypay_min():
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_BANK_CRYPTOCURRENCY), id='btc', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_payment_method),
        state=states.PersonalMenu.payment_method_anypay_min
    )
