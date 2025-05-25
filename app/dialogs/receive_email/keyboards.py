from aiogram_dialog.widgets.kbd import Group, Button
from aiogram_dialog.widgets.text import Const

from app.dialogs.receive_email.selected import on_rent_email_item
from app.services import bot_texts as bt

def rent_email_kb(data):
    on_click = on_rent_email_item
    buttons = []

    if data.get("is_free_week", False):
        buttons.append(Button(Const(bt.RENT_EMAIL_WEEK_BTN), id='rent_email_week', on_click=on_click))

    buttons.extend([
        Button(Const(bt.RENT_EMAIL_MONTH_BTN), id='rent_email_month', on_click=on_click),
        Button(Const(bt.RENT_EMAIL_SIX_MONTHS_BTN), id='rent_email_six_months', on_click=on_click),
        Button(Const(bt.RENT_EMAIL_YEAR_BTN), id='rent_email_year', on_click=on_click),
    ])

    return Group(*buttons, id='rent_email_kb', width=1)




def rent_email_discount_kb(on_click):
    return Group(
        Button(Const(bt.RENT_EMAIL_WEEK_BTN_DISCOUNT), id='rent_email_week_discount', on_click=on_click),
        Button(Const(bt.RENT_EMAIL_MONTH_BTN_DISCOUNT), id='rent_email_month_discount', on_click=on_click),
        Button(Const(bt.RENT_EMAIL_SIX_MONTHS_BTN_DISCOUNT), id='rent_email_six_months_discount', on_click=on_click),
        Button(Const(bt.RENT_EMAIL_YEAR_BTN_DISCOUNT), id='rent_email_year_discount', on_click=on_click),
        id='rent_email_discount_kb',
        width=1
    )

def rent_email_kb_without_week(on_click):
    return Group(
        Button(Const(bt.RENT_EMAIL_MONTH_BTN), id='rent_email_month', on_click=on_click),
        Button(Const(bt.RENT_EMAIL_SIX_MONTHS_BTN), id='rent_email_six_months', on_click=on_click),
        Button(Const(bt.RENT_EMAIL_YEAR_BTN), id='rent_email_year', on_click=on_click),
    )