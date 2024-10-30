from aiogram_dialog import DialogManager
from app.db import models
from app.services.sms_receive import SmsReceive


async def get_email_info(dialog_manager: DialogManager, **middleware_data):
    ctx = dialog_manager.current_context()
    mail_id = ctx.start_data.get('mail_id')
    if not mail_id:
        return

    ctx.dialog_data['mail_id'] = mail_id
    mail = await models.Mail.get_mail(mail_id)
    if not mail:
        return

    paid_mails_count = await models.Mail.filter(is_paid_mail=True).count()

    return {
        'email': mail.email,
        'paid_mails_count': paid_mails_count
    }


async def get_balance(dialog_manager: DialogManager, **middleware_data):
    ctx = dialog_manager.current_context()
    user = await models.User.get_user(dialog_manager.event.from_user.id)
    if not user:
        return

    return {
        'balance': user.balance,
        'cost': ctx.dialog_data.get('cost')
    }


async def get_rent_info(dialog_manager: DialogManager, **middleware_data):
    ctx = dialog_manager.current_context()
