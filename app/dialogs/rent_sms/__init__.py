from aiogram_dialog import Dialog

from app.dialogs.rent_sms import windows


def select_rent_dialogs():
    return [
        Dialog(
            windows.select_rent_window(),
            windows.country_details_window(),
            windows.deposit_window_country(),
            windows.enter_amount_window_country(),
            windows.payment_method_window_country(),
            windows.payment_method_window_country_minimum_pay(),
            windows.payment_method_window_anypay(),
            windows.enter_country_window(),
            windows.enter_country_error_window(),
        )
    ]

