from aiogram import Router, F, types
from app.services import bot_texts as bt

router = Router()


@router.callback_query(F.data == 'withdraw')
async def withdraw(call: types.CallbackQuery):
    await call.message.delete()
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=bt.ON_BANK_CARD_BTN,
                    callback_data='on_bank_card'
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=bt.ON_BALANCE_BTN,
                    callback_data='on_balance'
                )
            ],
            [
                types.InlineKeyboardButton(text=bt.BACK_BTN,
                                           callback_data='back')
            ]
        ]
    )
    await call.message.answer(text=bt.WITHDRAW_TEXT, reply_markup=mk)
