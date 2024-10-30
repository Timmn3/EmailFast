from aiogram import types
from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.kbd import Select, Button
from app.dialogs.receive_sms.states import CountryMenu
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.handlers.affiliate_program import send_affiliate_message


async def on_receive_sms(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.start(CountryMenu.select_country)


# async def on_receive_email(c: types.CallbackQuery, widget: Button, manager: DialogManager):
#     await c.message.edit_text(text=bt.CREATING_EMAIL)
#     user = await models.User.get_user(c.from_user.id)
#     if not user:
#         return
#
#     mails = await models.Mail.get_user_mails(user)
#     if len(mails) > 0:
#         mail = mails[0]
#
#     else:
#         tm = MailTm()
#         try:
#             account = await tm.create_account()
#         except Exception:
#             await c.answer(text='Ошибка, повторите попытку через пару секунд', show_alert=True)
#             return
#
#         mail = await models.Mail.add_mail(user, account.id_, account.address, account.password)
#
#     await manager.start(ReceiveEmailMenu.receive_email,
#                         data={"mail_id": mail.id})


async def on_personal_cabinet(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.start(PersonalMenu.user_info)


async def on_affiliate_program(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    await manager.reset_stack(remove_keyboard=False)
    await send_affiliate_message(c)
