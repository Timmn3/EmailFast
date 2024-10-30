import operator

from aiogram import F
from aiogram_dialog.widgets.kbd import Group, Button, Select
from aiogram_dialog.widgets.text import Const, Format
from app.services import bot_texts as bt


def prices_kb(on_click):
    return Group(
        Select(
            Format('{item[price]}₽'),
            id='prices_kb',
            item_id_getter=operator.itemgetter('id'),
            items="prices",
            on_click=on_click,
            when=~F['bonus']
        ),
        Select(
            Format('{item[price]}₽ (+10%)'),
            id='prices_kb',
            item_id_getter=operator.itemgetter('id'),
            items="prices",
            on_click=on_click,
            when=F['bonus']
        ),
        width=2
    )


def prices_sale_kb(on_click):
    return Group(
        Select(
            Format('{item[price]}₽ (+10%)'),
            id='prices_kb',
            item_id_getter=operator.itemgetter('id'),
            items="prices",
            on_click=on_click,
        ),
        width=2
    )
