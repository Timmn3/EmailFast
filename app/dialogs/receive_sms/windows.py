import operator

from aiogram_dialog import Window, DialogManager, Data
from aiogram_dialog.widgets.input import TextInput, MessageInput
from aiogram_dialog.widgets.kbd import Cancel, Back, Button, ScrollingGroup, Select
from aiogram_dialog.widgets.text import Const, Format
from aiogram import F

from app.dialogs.personal_cabinet.selected import send_payment_keyboard_anypay, on_payment_method
from app.dialogs.receive_sms import states
from app.dialogs.receive_sms.getters import get_countries_service, get_services, get_need_balance, get_other_service, \
    get_services_2, get_show_smsfast_other_button
from app.dialogs.receive_sms.selected import on_select_country_new, on_select_service, on_search_country, \
    on_result_country, \
    on_search_service, on_result_service, back_country, on_smsfast_other_service
from app.services import bot_texts as bt
from app.dialogs.personal_cabinet import keyboards
from app.services.stars_pay import send_invoice_handler_stars
from aiogram_dialog.widgets.style import Style
from app.dialogs.personal_cabinet.selected import send_payment_keyboard_anypay, on_payment_method, on_back_to_main

# Окно выбора сервиса
def select_service_window():
    """
    Создает окно выбора сервиса с прокручиваемым списком сервисов и кнопкой поиска.

    :return: Объект Window от aiogram_dialog.
    """
    return Window(
        Const(bt.SELECT_SERVICE),
        ScrollingGroup(
            Select(
                Format("{item[name]}"),
                id="services_select",
                item_id_getter=operator.itemgetter("code"),
                items="services",
                on_click=on_select_service,
            ),
            id="services_scroll",
            width=2,
            height=5
        ),
        Button(
            Const(bt.SEARCH_SERVICE_BTN),
            id="search_service",
            on_click=on_search_service,
            style=Style(
                emoji_id="5188217332748527444",
            ),
        ),
        Button(
            Const(bt.BACK_BTN),
            id='back_to_main_sms',
            on_click=on_back_to_main,
            style=Style(
                emoji_id="5258236805890710909",  # ⬅️
            ),
        ),
        state=states.ServiceMenu.select_service,
        getter=get_services_2
    )

# Окно ввода сервиса
def enter_service_window():
    """
    Создает окно ввода названия сервиса с текстовым вводом и кнопкой назад.

    :return: Объект Window от aiogram_dialog.
    """
    return Window(
        Const(bt.ENTER_SERVICE),
        TextInput(id="service_name", on_success=on_result_service),
        Back(Const(bt.BACK_BTN)),
        state=states.ServiceMenu.enter_service
    )


def enter_service_error_window():
    """
    Создает окно ошибки ввода сервиса с возможностью:
    - снова ввести сервис
    - выбрать категорию "Любой другой" (только для SMSFast)
    - вернуться назад
    """
    return Window(
        Const(bt.ENTER_SERVICE_ERROR),
        TextInput(id="service_name", on_success=on_result_service),

        # ✅ "Любой другой" (SMSFast) — показываем только если SMSFast реально доступен
        Button(
            Const("Любой другой"),
            id="smsfast_other_service",
            on_click=on_smsfast_other_service,
            when=F["show_smsfast_other"],
        ),

        Back(Const(bt.BACK_BTN)),
        state=states.ServiceMenu.enter_service_error,
        getter=get_show_smsfast_other_button,
    )


# Окно выбора страны
def select_country_window():
    """
    Создает окно выбора страны с прокручиваемым списком стран и кнопкой поиска.

    Для сервиса Telegram текст заголовка подменяется на предупреждение с кликабельной ссылкой.
    Для остальных сервисов остается стандартный текст.

    :return: Объект Window от aiogram_dialog.
    """
    return Window(
        Format("{select_country_text}"),
        ScrollingGroup(
            Select(
                Format("{item[country]} {item[price]} ₽"),
                id="countries_select",
                item_id_getter=operator.itemgetter("id"),
                items="countries",
                on_click=on_select_country_new,
            ),
            id="countries_scroll",
            width=2,
            height=5
        ),
        Button(
            Const(bt.SEARCH_COUNTRY_BTN),
            id="search_country",
            on_click=on_search_country,
            style=Style(
                emoji_id="5224450179368767019",
            ),
        ),
        Cancel(
            Const(bt.BACK_BTN),
            style=Style(
                emoji_id="5258236805890710909",  # ⬅️
            ),
        ),
        state=states.CountryMenu.select_country,
        getter=get_countries_service
    )

# Окно ввода страны
def enter_country_window():
    """
    Создает окно ввода названия страны с текстовым вводом и кнопкой назад.

    :return: Объект Window от aiogram_dialog.
    """
    return Window(
        Const(bt.ENTER_COUNTRY),
        TextInput(id="country_name", on_success=on_result_country),
        Button(Const(bt.BACK_BTN), id="back", on_click=back_country),
        state=states.CountryMenu.enter_country
    )


# Окно ошибки ввода страны
def enter_country_error_window():
    """
    Создает окно ошибки ввода страны с кнопкой повторного ввода и кнопкой назад.

    :return: Объект Window от aiogram_dialog.
    """
    return Window(
        Const(bt.ENTER_COUNTRY_ERROR),
        Button(Const(bt.ENTER_AGAIN_BTN), id="enter_again", on_click=on_search_country),
        Cancel(
            Const(bt.BACK_BTN),
            style=Style(
                emoji_id="5258236805890710909",  # ⬅️
            ),
        ),
        state=states.CountryMenu.enter_country_error
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
        state=states.CountryMenu.deposit,
        getter=get_deposit_prices
    )


def enter_amount_window_country():
    from app.dialogs.personal_cabinet.selected import on_enter_other_price
    return Window(
        Const(bt.ENTER_DEPOSIT_AMOUNT),
        TextInput(id='enter_deposit_amount', on_success=on_enter_other_price),
        Back(Const(bt.BACK_BTN)),
        state=states.CountryMenu.enter_amount
    )


# Функция для нового окна выбора метода оплаты
def payment_method_window_country():
    from app.dialogs.personal_cabinet.selected import switch_to_payment, on_deposit
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_CKASSA), id='ckassa', on_click=switch_to_payment),
        Button(Const(bt.METHOD_STREAMPAY), id='bank_card', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_LAVA), id='SBP', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_ANYPAY), id='anypay', on_click=send_payment_keyboard_anypay),
        Button(Const(bt.METHOD_STARS_BTN), id='stars', on_click=send_invoice_handler_stars),
        Button(Const(bt.METHOD_CRYPTO_BTN), id='crypto', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_OTHER_BTN), id='other', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_deposit),
        state=states.CountryMenu.payment_method
    )


# Функция для выбора метода оплаты с платежем менее 300 руб
def payment_method_window_country_minimum_pay():
    from app.dialogs.personal_cabinet.selected import switch_to_payment, on_deposit
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_CKASSA), id='ckassa', on_click=switch_to_payment),
        Button(Const(bt.METHOD_STARS_BTN), id='stars', on_click=send_invoice_handler_stars),
        Button(Const(bt.METHOD_CRYPTO_BTN), id='crypto', on_click=switch_to_payment),
        # Button(Const(bt.METHOD_OTHER_BTN), id='other', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_deposit),
        state=states.CountryMenu.payment_method_minimum_pay
    )

# Функция для нового окна выбора метода оплаты AnyPay
def payment_method_window_anypay():
    from app.dialogs.personal_cabinet.selected import switch_to_payment
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_BANK_CARD), id='card', on_click=switch_to_payment),
        Button(Const(bt.METHOD_BANK_SBP), id='sbp', on_click=switch_to_payment),
        Button(Const(bt.METHOD_BANK_CRYPTOCURRENCY), id='btc', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_payment_method),
        state=states.CountryMenu.payment_method_anypay
    )

# Функция для нового окна выбора метода оплаты AnyPay с непроходящим по сумме патежем
def payment_method_window_anypay_min():
    from app.dialogs.personal_cabinet.selected import switch_to_payment
    return Window(
        Const(bt.SELECT_DEPOSIT_METHOD),
        Button(Const(bt.METHOD_BANK_CRYPTOCURRENCY), id='btc', on_click=switch_to_payment),
        Button(Const(bt.BACK_BTN), id='back', on_click=on_payment_method),
        state=states.CountryMenu.payment_method_anypay_min
    )