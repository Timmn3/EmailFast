from aiogram_dialog import Dialog

from app.dialogs.receive_sms import windows


def select_countries_dialogs():
    return [
        Dialog(
            windows.select_country_window(),
            windows.deposit_window_country(),
            windows.enter_amount_window_country(),
            windows.payment_method_window_country(),
            windows.payment_method_window_country_minimum_pay(),
            windows.payment_method_window_anypay(),
            windows.enter_country_window(),
            windows.enter_country_error_window(),
        )
    ]


def select_services_dialogs():
    return [
        Dialog(
            windows.select_service_window(),
            windows.select_favorites_window(),
            windows.enter_service_window(),
            windows.enter_service_error_window(),
        )
    ]
