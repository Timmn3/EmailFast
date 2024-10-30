from aiogram_dialog import Dialog

from app.dialogs.personal_cabinet import windows


def personal_cabinet_dialogs():
    return [
        Dialog(
            windows.personal_cabinet_window(),
            windows.deposit_window()
        )
    ]

