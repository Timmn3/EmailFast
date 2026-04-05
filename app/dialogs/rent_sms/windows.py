import operator
from aiogram import F
from aiogram_dialog import Window
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Cancel, Back, Button, ScrollingGroup, Select
from aiogram_dialog.widgets.text import Const, Format
from app.dialogs.personal_cabinet import keyboards
from aiogram_dialog.widgets.kbd import Cancel, Back, Button, ScrollingGroup, Select
from aiogram_dialog.widgets.style import Style
from app.dialogs.rent_sms import states
from app.dialogs.rent_sms.getters import get_rent_countries, get_country_details, cancel_btn
from app.dialogs.rent_sms.selected import rent_on_result_country, rent_on_select_country_new, \
    rent_back_country, on_search_rent_country, rent_number_in_days, rent_on_deposit
from app.services import bot_texts as bt
from app.services.stars_pay import send_invoice_handler_stars


# Окно выбора страны для аренды
def select_rent_window():
    """
    Создает окно выбора страны для аренды с прокручиваемым списком стран и кнопкой поиска.

    :return: Объект Window от aiogram_dialog.
    """
    from app.dialogs.personal_cabinet.selected import on_back_to_main
    return Window(
        Const(bt.SELECT_COUNTRY_RENT),
        ScrollingGroup(
            Select(
                Format("{item[country]} от {item[price]} ₽"),
                id="rent_countries_select",
                item_id_getter=operator.itemgetter("id"),
                items="rent_countries",
                on_click=rent_on_select_country_new,
            ),
            id="rent_countries_scroll",
            width=2,
            height=5
        ),
        # Button(Const(bt.SEARCH_COUNTRY_BTN), id="rent_search_country", on_click=on_search_rent_country),
        Button(                                  # ← ДОБАВИТЬ
            Const(bt.BACK_BTN),
            id='back_to_main_rent',
            on_click=on_back_to_main,
            style=Style(
                emoji_id="5258236805890710909",  # ⬅️
            ),
        ),
        state=states.RentCountryMenu.select_country,
        getter=get_rent_countries
    )

# Окно ввода страны
def enter_country_window():
    """
    Создает окно ввода названия страны с текстовым вводом и кнопкой назад.

    :return: Объект Window от aiogram_dialog.
    """
    return Window(
        Const(bt.ENTER_COUNTRY),
        TextInput(id="rent_country_name", on_success=rent_on_result_country),
        Button(Const(bt.BACK_BTN), id="rent_back", on_click=rent_back_country),
        state=states.RentCountryMenu.enter_country
    )


# Окно ошибки ввода страны
def enter_country_error_window():
    """
    Создает окно ошибки ввода страны с кнопкой повторного ввода и кнопкой назад.

    :return: Объект Window от aiogram_dialog.
    """
    return Window(
        Const(bt.ENTER_COUNTRY_ERROR),
        Button(Const(bt.ENTER_AGAIN_BTN), id="enter_again", on_click=on_search_rent_country),
        Cancel(Const(bt.BACK_BTN)),
        state=states.RentCountryMenu.enter_country_error
    )


def country_details_window():
    """
    Окно отображения информации о выбранной стране.

    :return: Объект Window от aiogram_dialog.
    """
    return Window(
        Const(bt.SELECT_RENTAL_PERIOD),
        ScrollingGroup(
            Select(
                Format("{item[days]}, {item[price]} ₽"),
                id="tariffs_scroll",
                item_id_getter=operator.itemgetter("days"),
                items="tariffs",
                on_click=rent_number_in_days,
            ),
            id="tariffs_group",
            width=1,
            height=5
        ),
        Button(
            Const(bt.BACK_BTN),
            id="back_to_country_select",
            on_click=rent_back_country,
            style=Style(
                emoji_id="5258236805890710909",  # ⬅️
            ),
        ),
        state=states.RentCountryMenu.country_details,
        getter=get_country_details,
    )



def deposit_window_country():
    from app.dialogs.personal_cabinet.selected import on_deposit_price
    from app.dialogs.personal_cabinet.selected import on_other_price
    from app.dialogs.personal_cabinet.getters import get_deposit_prices
    return Window(
        Const(bt.SELECT_DEPOSIT_PRICE),
        keyboards.prices_kb(on_deposit_price),
        # Button(Const(bt.OTHER_DEPOSIT_PRICE_BTN), id='other_price', on_click=on_other_price, when=~F['bonus']),
        # Button(Const(bt.OTHER_DEPOSIT_PRICE_BTN + ' (+10%)'), id='other_price', on_click=on_other_price,
        #        when=F['bonus']),
        Button(Const(bt.OTHER_DEPOSIT_PRICE_BTN), id='other_price', on_click=on_other_price),
        Back(Const(bt.BACK_BTN)),
        state=states.RentCountryMenu.deposit,
        getter=get_deposit_prices
    )


def enter_amount_window_country():
    from app.dialogs.personal_cabinet.selected import on_enter_other_price
    return Window(
        Const(bt.ENTER_DEPOSIT_AMOUNT),
        TextInput(id='enter_deposit_amount', on_success=on_enter_other_price),
        Back(Const(bt.BACK_BTN)),
        state=states.RentCountryMenu.enter_amount
    )


# Функция для нового окна выбора метода оплаты
def payment_method_window_country():
    from app.dialogs.personal_cabinet.selected import switch_to_payment
    from app.dialogs.personal_cabinet.selected import send_payment_keyboard_anypay
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_CKASSA), id='ckassa', on_click=switch_to_payment),
        Button(Const(bt.METHOD_STREAMPAY), id='bank_card', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_LAVA), id='SBP', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_ANYPAY), id='anypay', on_click=send_payment_keyboard_anypay),
        Button(Const(bt.METHOD_STARS_BTN), id='stars', on_click=send_invoice_handler_stars),
        Button(Const(bt.METHOD_CRYPTO_BTN), id='crypto', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_OTHER_BTN), id='other', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=rent_on_deposit),
        state=states.RentCountryMenu.payment_method
    )


# Функция для выбора метода оплаты с платежем менее 300 руб
def payment_method_window_country_minimum_pay():
    from app.dialogs.personal_cabinet.selected import switch_to_payment
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_CKASSA), id='ckassa', on_click=switch_to_payment),
        Button(Const(bt.METHOD_STARS_BTN), id='stars', on_click=send_invoice_handler_stars),
        Button(Const(bt.METHOD_CRYPTO_BTN), id='crypto', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_OTHER_BTN), id='other', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=rent_on_deposit),
        state=states.RentCountryMenu.payment_method_minimum_pay
    )

# Функция для нового окна выбора метода оплаты AnyPay
def payment_method_window_anypay():
    from app.dialogs.personal_cabinet.selected import switch_to_payment, on_payment_method
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_BANK_CARD), id='card', on_click=switch_to_payment),
        Button(Const(bt.METHOD_BANK_SBP), id='sbp', on_click=switch_to_payment),
        Button(Const(bt.METHOD_BANK_CRYPTOCURRENCY), id='btc', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_payment_method),
        state=states.RentCountryMenu.payment_method_anypay
    )

# Функция для нового окна выбора метода оплаты AnyPay с непроходящим по сумме патежем
def payment_method_window_anypay_min():
    from app.dialogs.personal_cabinet.selected import switch_to_payment, on_payment_method
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_BANK_CRYPTOCURRENCY), id='btc', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_payment_method),
        state=states.RentCountryMenu.payment_method_anypay_min
    )