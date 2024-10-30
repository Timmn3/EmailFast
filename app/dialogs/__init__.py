from aiogram import Dispatcher
from aiogram_dialog import setup_dialogs as sd

from . import bot_menu
from . import receive_sms
from . import receive_email
from . import personal_cabinet


def setup_dialogs(dp: Dispatcher):
    for dialog in [
        *bot_menu.bot_menu_dialogs(),
        *receive_sms.select_countries_dialogs(),
        *receive_sms.select_services_dialogs(),
        *receive_email.receive_email_dialogs(),
        *personal_cabinet.personal_cabinet_dialogs()

    ]:
        dp.include_router(dialog)

    sd(dp)

