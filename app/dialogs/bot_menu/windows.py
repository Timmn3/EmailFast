from aiogram_dialog import Window, DialogManager, Data
from aiogram_dialog.widgets.kbd import Cancel, Back, Button, Row
from aiogram_dialog.widgets.text import Const, Format

from app.dialogs.bot_menu import states
from app.dialogs.bot_menu.selected import on_receive_sms, on_personal_cabinet, on_affiliate_program
from app.services import bot_texts as bt


def main_menu_window():
    return Window(
        Const(bt.MAIN_MENU),
        Row(Button(Const(bt.RECEIVE_SMS_BTN), id='receive_sms', on_click=on_receive_sms)),
            # Button(Const(bt.RECEIVE_EMAIL_BTN), id='receive_email', on_click=on_receive_email)),

        Button(Const(bt.PERSONAL_CABINET_BTN), id='personal_cabinet', on_click=on_personal_cabinet),
        Button(Const(bt.AFFILIATE_PROGRAM_BTN), id='affiliate_program', on_click=on_affiliate_program),
        state=states.BotMenu.start
    )

# async def on_process_result(data: Data, result: dict, manager: DialogManager):
#     if result:
#         switch_to_window = result.get('switch_to_window')
#         if switch_to_window == SwitchToWindow.SelectProducts:
#             await manager.switch_to(states.BotMenu.select_products)
