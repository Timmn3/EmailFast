import operator

from aiogram import F
from aiogram_dialog import Window
from aiogram_dialog.widgets.kbd import Cancel, Back, Button
from aiogram_dialog.widgets.text import Const, Format


from app.dialogs.receive_email import states
from app.dialogs.receive_email.getters import get_email_info, get_balance
from app.dialogs.receive_email.keyboards import rent_email_kb, rent_email_discount_kb
from app.dialogs.receive_email.selected import on_change_email, on_rent_email, on_rent_email_item, \
    on_confirm_rent_email, on_back_mail, on_my_rent_emails, on_rent_email_item_discount
from app.services import bot_texts as bt
from app.services.bot_texts import RENT_EMAIL_DISCOUNT_PROMO, RENT_EMAIL_NO_DISCOUNT


def receive_email_window():
    """
    Создает окно для отображения информации о текущем почтовом ящике пользователя и предоставляет кнопки для изменения
    почтового ящика, аренды нового и просмотра арендованных ящиков.

    :return: Объект Window для отображения информации о почтовом ящике.
    """

    return Window(
        Format(bt.MY_EMAIL),  # Форматированный текст с информацией о почтовом ящике
        Button(Const(bt.CHANGE_EMAIL_BTN), id='change_email', on_click=on_change_email),
        # Кнопка для изменения почтового ящика
        Button(Const(bt.RENT_EMAIL_BTN), id='rent_email', on_click=on_rent_email),
        # Кнопка для аренды нового почтового ящика
        Button(
            Const(bt.MY_RENT_EMAILS_BTN),
            id='my_rent_emails_btn',
            on_click=on_my_rent_emails,
            when=F['paid_mails_count'] > 0
            # Кнопка для просмотра арендованных ящиков, отображается только если есть арендованные ящики
        ),
        state=states.ReceiveEmailMenu.receive_email,  # Состояние окна
        getter=get_email_info  # Функция для получения информации о почтовом ящике
    )


def rent_email_window():
    """
    Создает окно для выбора периода аренды почтового ящика.

    :return: Объект Window для выбора периода аренды.
    """

    return Window(
        Format(bt.MY_EMAIL),
        *rent_email_kb,  # теперь это callable, принимает data от getter
        Button(Const(bt.BACK_BTN), id='back_rent', on_click=on_back_mail),
        state=states.ReceiveEmailMenu.rent_email,
        getter=get_email_info
    )


def confirm_rent_email_window():
    """
    Создает окно для подтверждения аренды почтового ящика.

    :return: Объект Window для подтверждения аренды.
    """

    return Window(
        Format(bt.CONFIRM_RENT_EMAIL),  # Форматированный текст с подтверждением аренды
        Button(Const(bt.CONFIRM_BTN), id='confirm_btn', on_click=on_confirm_rent_email),
        # Кнопка для подтверждения аренды
        Back(Const(bt.BACK_BTN)),  # Кнопка для возврата назад
        state=states.ReceiveEmailMenu.rent_email_confirm  # Состояние окна
    )


def not_enough_balance_window():
    """
    Создает окно для уведомления пользователя о недостаточном балансе и предоставляет кнопку для пополнения баланса.

    :return: Объект Window для уведомления о недостаточном балансе.
    """
    from app.dialogs.personal_cabinet.selected import on_deposit_state
    return Window(
        Format(bt.NOT_ENOUGH_BALANCE),  # Форматированный текст с уведомлением о недостаточном балансе
        Button(Const(bt.DEPOSIT_BTN), id='deposit_btn', on_click=on_deposit_state),  # Кнопка для пополнения баланса
        Back(Const(bt.BACK_BTN)),  # Кнопка для возврата назад
        state=states.ReceiveEmailMenu.not_enough_balance,  # Состояние окна
        getter=get_balance  # Функция для получения текущего баланса пользователя
    )


def rent_email_success_window():
    """
    Создает окно для уведомления пользователя об успешной аренде почтового ящика и предоставляет кнопку для просмотра арендованных ящиков.

    :return: Объект Window для уведомления об успешной аренде.
    """
    return Window(
        Format(bt.RENT_EMAIL_SUCCESS),  # Форматированный текст с уведомлением об успешной аренде
        Button(Const(bt.MY_RENT_EMAILS_BTN), id='my_rent_emails_btn', on_click=on_my_rent_emails),
        # Кнопка для просмотра арендованных ящиков
        state=states.ReceiveEmailMenu.rent_email_success  # Состояние окна
    )


def rent_email_discount_window():
    """
    Создает окно для выбора периода аренды почтового ящика со скидкой.

    :return: Объект Window для выбора периода аренды со скидкой.
    """
    return Window(Const(RENT_EMAIL_DISCOUNT_PROMO),
        rent_email_discount_kb(on_rent_email_item_discount),
        state=states.ReceiveEmailMenu.rent_email_discount,
        getter=get_email_info
    )

def rent_email_no_discount_window():
    """
    Создает окно для выбора периода аренды почтового ящика без скидки.

    :return: Объект Window для выбора периода аренды без скидки.
    """
    return Window(Const(RENT_EMAIL_NO_DISCOUNT),
        rent_email_kb(on_rent_email_item_discount),
        state=states.ReceiveEmailMenu.rent_email_no_discount,
        getter=get_email_info
    )
