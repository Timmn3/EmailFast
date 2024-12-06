from typing import Union
from aiogram import types, F, Router
from aiogram.filters import Command, CommandObject
from aiogram_dialog import DialogManager, StartMode
from app.db import models
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.dialogs.receive_email.states import ReceiveEmailMenu
from app.dialogs.receive_sms.selected import send_country_info
from app.services import bot_texts as bt
from app.services.keyboards import start_kb
from app.services.need_subscribe import check_subscribe, send_subscribe_msg

router = Router()


@router.message(F.text == '/id')
async def get_id(message: types.Message):
    await message.answer(text=str(message.chat.id))


@router.callback_query(F.data == 'start')
@router.message(Command('start'))
async def start(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager,
                command: CommandObject):
    user = await models.User.get_user(message.from_user.id)
    if not user:
        refer_id = command.args
        if refer_id and refer_id.isdigit():
            refer_id = int(refer_id)
            refer = await models.User.get_or_none(telegram_id=refer_id)
            user = await models.User.add_user(message.from_user, refer)
        else:
            user = await models.User.add_user(message.from_user)

    if isinstance(message, types.CallbackQuery):
        await message.message.delete()

    # else:
    #     start_arg = command.args
    #     if start_arg and start_arg.startswith('free_'):
    #         payment_link = await models.PaymentLink.get_payment_link(start_arg)
    #         if payment_link:
    #             if len(payment_link.user_id_list) >= payment_link.limit:
    #                 await message.answer(text='Лимит активаций исчерпан')
    #                 return
    #
    #             elif message.from_user.id in payment_link.user_id_list:
    #                 await message.answer(text='Вы уже активировали эту ссылку')
    #                 return
    #
    #             payment_link.user_id_list.append(user.telegram_id)
    #             await payment_link.save(update_fields=['user_id_list'])
    #             user.balance += payment_link.amount
    #             await user.save(update_fields=['balance'])
    #             await message.answer(text=f'Вам начислено {payment_link.amount}₽')
    #             return

    sub = await check_subscribe(user)
    if not sub:
        await send_subscribe_msg(user)
        return

    await message.answer(text=bt.MAIN_MENU, reply_markup=start_kb())


@router.callback_query(F.data == 'check_subscribe')
async def check_subscribe_handler(call: types.CallbackQuery):
    user = await models.User.get_user(call.from_user.id)
    sub = await check_subscribe(user)
    if sub:
        await call.message.delete()
        await call.message.answer(text=bt.MAIN_MENU, reply_markup=start_kb())
    else:
        await call.answer(text='Вы не подписаны на канал', show_alert=True)


@router.message(Command("account"))
@router.message(F.text == bt.PERSONAL_CABINET_BTN)
async def personal_cabinet(message: types.Message, dialog_manager: DialogManager):
    user = await models.User.get_user(message.from_user.id)
    sub = await check_subscribe(user)
    if not sub:
        await send_subscribe_msg(user)
        return

    await dialog_manager.start(PersonalMenu.user_info, mode=StartMode.RESET_STACK)


@router.callback_query(F.data.startswith('mail:'))
async def mail_info(call: types.CallbackQuery):
    mail_id = int(call.data.split(':')[1])
    mail = await models.Mail.get_or_none(id=mail_id)
    if not mail:
        await call.answer()
        return

    msg_text = bt.PAID_EMAIL_INFO.format(email=mail.email, expire_at=mail.expire_at.strftime('%d.%m.%Y'))
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.RECEIVE_MY_EMAIL_BTN, callback_data=f'receive_my_mail:{mail.id}'),
            ],
            [
                types.InlineKeyboardButton(text=bt.EXTEND_EMAIL_BTN, callback_data=f'extend_email:{mail.id}')
            ],
            [
                types.InlineKeyboardButton(text=bt.BACK_BTN, callback_data='my_rent_emails')
            ]
        ]
    )
    await call.message.edit_text(text=msg_text, reply_markup=mk)


@router.callback_query(F.data.startswith('continue_payment:'))
async def continue_payment(call: types.CallbackQuery, dialog_manager: DialogManager):
    payment_id = int(call.data.split(':')[1])
    payment = await models.Payment.get_or_none(id=payment_id)
    if not payment:
        await call.answer()
        return

    if 'email' in payment.continue_data:
        await dialog_manager.start(ReceiveEmailMenu.rent_email_confirm, data=payment.continue_data,
                                   mode=StartMode.RESET_STACK)

    else:  # было (payment.continue_data['country_id'], payment.continue_data['service_code'], call, dialog_manager)
        await send_country_info(payment.continue_data['service_code'], call,
                                dialog_manager)


@router.callback_query(F.data.startswith('bonus_price:'))
async def bonus_price(call: types.CallbackQuery, dialog_manager: DialogManager):
    from app.dialogs.personal_cabinet.selected import send_payment_keyboard
    price = call.data.split(':')[1]
    user = await models.User.get_user(call.from_user.id)
    if not user:
        return

    if price == 'other':
        await call.message.edit_reply_markup()
        await dialog_manager.start(PersonalMenu.enter_amount, mode=StartMode.RESET_STACK)

    else:
        await send_payment_keyboard(call, price=float(price))


@router.message(Command('rent'))
async def rent(message: types.Message, dialog_manager: DialogManager):
    from app.dialogs.receive_email.selected import on_rent_email_check_discount
    await on_rent_email_check_discount(message, dialog_manager)


# Обработчик для inline-кнопки "Продлить аренду"
@router.callback_query(lambda c: c.data and c.data.startswith('rental'))
async def process_rent_callback(message: types.Message, dialog_manager: DialogManager):
    # Получаем пользователя и вызываем функцию on_rent_email_check_discount
    from app.dialogs.receive_email.selected import on_rent_email_check_discount
    await on_rent_email_check_discount(message, dialog_manager)
