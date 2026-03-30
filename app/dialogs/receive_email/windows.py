import operator
from app.dependencies import FREE_EMAIL_PROVIDER
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

def _get_receive_email_header_widget():
    """
    Возвращает верхний текстовый виджет для email-диалогов.

    Логика:
    - при FREE_EMAIL_PROVIDER="mail_tm" оставляем legacy-текст текущего ящика;
    - при FREE_EMAIL_PROVIDER="firstmail" не показываем пользователю старый mail.tm-текст,
      но сохраняем доступ к аренде и списку арендованных ящиков через legacy dialog.

    :return: Виджет текста для верхней части окна.
    """
    if FREE_EMAIL_PROVIDER == "firstmail":
        return Const(
            "Бесплатная почта работает через FirstMail.\n\n"
            "Здесь доступны только действия с арендованными почтовыми ящиками."
        )

    return Format(bt.MY_EMAIL)


def receive_email_window():
    """
    Создает окно для отображения email-раздела.

    Логика:
    - в legacy-режиме mail.tm показываем текущий бесплатный ящик и кнопку его смены;
    - в режиме free FirstMail скрываем legacy mail.tm-текст и кнопку смены,
      но оставляем доступ к аренде и к списку арендованных ящиков.

    :return: Объект Window для отображения email-раздела.
    """
    widgets = [
        _get_receive_email_header_widget(),
    ]

    if FREE_EMAIL_PROVIDER != "firstmail":
        widgets.append(
            Button(Const(bt.CHANGE_EMAIL_BTN), id='change_email', on_click=on_change_email)
        )

    widgets.extend(
        [
            Button(Const(bt.RENT_EMAIL_BTN), id='rent_email', on_click=on_rent_email),
            Button(
                Const(bt.MY_RENT_EMAILS_BTN),
                id='my_rent_emails_btn',
                on_click=on_my_rent_emails,
                when=F['paid_mails_count'] > 0
            ),
        ]
    )

    return Window(
        *widgets,
        state=states.ReceiveEmailMenu.receive_email,
        getter=get_email_info
    )


def rent_email_window():
    """
    Создает окно выбора тарифа аренды почты.

    Важно:
    - при FREE_EMAIL_PROVIDER="firstmail" не показываем legacy mail.tm-текст в шапке;
    - аренда продолжает работать как раньше.
    """
    buttons = rent_email_kb(on_rent_email_item, is_free_week=False)  # неделя ДОСТУПНА
    return Window(
        _get_receive_email_header_widget(),
        buttons,
        Button(Const(bt.BACK_BTN), id='back_rent', on_click=on_back_mail),
        state=states.ReceiveEmailMenu.rent_email,
        getter=get_email_info,
    )

def rent_email_no_free_week():
    """
    Создает окно выбора тарифа аренды почты, если бесплатная неделя уже использована.

    Важно:
    - при FREE_EMAIL_PROVIDER="firstmail" не показываем legacy mail.tm-текст в шапке;
    - логика аренды не меняется.
    """
    buttons = rent_email_kb(on_rent_email_item, is_free_week=True)  # неделя ИСПОЛЬЗОВАНА
    return Window(
        _get_receive_email_header_widget(),
        buttons,
        Button(Const(bt.BACK_BTN), id='back_rent', on_click=on_back_mail),
        state=states.ReceiveEmailMenu.rent_email_no_free_week,
        getter=get_email_info,
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
    return Window(Const(RENT_EMAIL_NO_DISCOUNT),
                  rent_email_kb(on_rent_email_item_discount, is_free_week=True),
                  state=states.ReceiveEmailMenu.rent_email_no_discount,
                  getter=get_email_info
                  )

def rent_email_no_discount_window():
    """
    Создает окно для выбора периода аренды почтового ящика без скидки.

    :return: Объект Window для выбора периода аренды без скидки.
    """
    return Window(Const(RENT_EMAIL_NO_DISCOUNT),
        rent_email_kb(on_rent_email_item_discount, is_free_week=True),
        state=states.ReceiveEmailMenu.rent_email_no_discount,
        getter=get_email_info
    )
