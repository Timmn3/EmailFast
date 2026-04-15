from aiogram_dialog import Dialog

from app.dialogs.personal_cabinet import windows


def personal_cabinet_dialogs():
    return [
        Dialog(
            windows.personal_cabinet_window(),
            windows.deposit_window(),
            windows.enter_amount_window(),
            windows.payment_method_window(),
            windows.payment_method_window_minimum_pay(),
            windows.payment_method_window_anypay(),
            windows.payment_method_window_anypay_min(),
            windows.order_history_window(),
        )
    ]

