from aiogram_dialog import Dialog

from app.dialogs.bot_menu import windows


def bot_menu_dialogs():
    return [
        Dialog(
            windows.main_menu_window()
        )
    ]
