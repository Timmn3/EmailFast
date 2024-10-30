from aiogram import types
from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Select, Button

from app.db import models
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.services import bot_texts as bt


async def on_deposit(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.switch_to(PersonalMenu.deposit)


async def on_deposit_price(c: types.CallbackQuery, widget: Select, manager: DialogManager, price_id: str):
    price = bt.prices_data[int(price_id)]
    await c.answer(text='В разработке', show_alert=True)


async def on_other_price(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await c.answer(text='В разработке', show_alert=True)
