from datetime import timedelta
import asyncio
from aiogram import types
from aiogram.client.session.middlewares.request_logging import logger
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram_dialog import DialogManager, StartMode
from aiogram_dialog.widgets.kbd import Button
from tortoise import timezone
from loguru import logger
from app.db import models
from app.dialogs.receive_email.states import ReceiveEmailMenu
from app.services.bot_texts import RENT_DATA, RENT_DATA_DISCOUNT
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.mail.temp_mail_tm import create_mail
from app.services.temp_mail import TempMail
from app.services import bot_texts as bt


async def on_back_mail(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Назад" в меню почтовых ящиков.
    Переключает состояние диалога на основное меню почтовых ящиков.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """

    user = await models.User.get_user(c.from_user.id)
    if not user:
        return

    await manager.switch_to(ReceiveEmailMenu.receive_email)


async def on_change_email(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Изменить почтовый ящик".
    Генерирует новый почтовый ящик и сохраняет его в базе данных.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """

    ctx = manager.current_context()
    mail_id = ctx.dialog_data.get('mail_id')
    if not mail_id:
        return

    mail = await models.Mail.get_mail(mail_id)
    if mail:
        mail.is_active = False
        await mail.save(update_fields=['is_active'])

    user = await models.User.get_user(c.from_user.id)

    email, token = await create_mail()

    mail = await models.Mail.add_mail(user, email, token)
    ctx.start_data['mail_id'] = mail.id
    ctx.dialog_data['mail_id'] = mail.id
    await manager.switch_to(ReceiveEmailMenu.receive_email)


async def on_rent_email(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Арендовать почтовый ящик".
    Переключает состояние диалога на меню аренды почтового ящика.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """

    await manager.switch_to(ReceiveEmailMenu.rent_email)


async def on_rent_email_item(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для выбора периода аренды почтового ящика.
    Проверяет баланс пользователя и переключает состояние диалога на подтверждение аренды или уведомление о недостаточном балансе.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    from app.dialogs.personal_cabinet.selected import on_deposit_state
    widget_id = widget.widget_id

    user = await models.User.get_user(c.from_user.id)

    ctx = manager.current_context()
    ctx.dialog_data['cost'] = RENT_DATA[widget_id][0]
    ctx.dialog_data['rent_days'] = RENT_DATA[widget_id][1]
    ctx.dialog_data['rent_text'] = RENT_DATA[widget_id][2]

    mail = await models.Mail.get_mail(mail_id=ctx.dialog_data['mail_id'])
    if not mail:
        return

    ctx.dialog_data['email'] = mail.email

    if user.balance < ctx.dialog_data['cost']:
        await on_deposit_state(c=c, widget=widget, manager=manager)
        return

    await manager.switch_to(ReceiveEmailMenu.rent_email_confirm)


async def on_rent_email_item_discount(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для выбора периода аренды почтового ящика со скидкой.
    Проверяет баланс пользователя и переключает состояние диалога на подтверждение аренды или уведомление о недостаточном балансе.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    from app.dialogs.personal_cabinet.selected import on_deposit_state
    widget_id = widget.widget_id

    # Проверяем, предлагалась ли пользователю скидка ранее
    user = await models.User.get_user(c.from_user.id)
    if user.discount_used is True:
        rent_data = RENT_DATA  # Используем обычные данные аренды
    else:
        rent_data = RENT_DATA_DISCOUNT  # Используем данные аренды со скидкой

    ctx = manager.current_context()

    ctx.dialog_data['cost'] = rent_data[widget_id][0]
    ctx.dialog_data['rent_days'] = rent_data[widget_id][1]
    ctx.dialog_data['rent_text'] = rent_data[widget_id][2]

    mail = await models.Mail.get_mail(mail_id=ctx.start_data['mail_id'])

    ctx.dialog_data['email'] = mail.email

    if user.balance < ctx.dialog_data['cost']:
        await on_deposit_state(c=c, widget=widget, manager=manager)
        return

    # Если пользователь использовал скидку, обновляем поле discount_used
    if user.discount_used is not True:
        user.discount_used = True
        await user.save()

    await manager.switch_to(ReceiveEmailMenu.rent_email_confirm)


async def on_rent_email_check_discount(message: types.Message, manager: DialogManager):
    """
    Проверяет, предлагалась ли пользователю скидка, и переключает на соответствующее окно.

    :param message: Объект Message.
    :param manager: Объект DialogManager.
    """
    user = await models.User.get(telegram_id=message.from_user.id)
    # получаем последнюю почту
    last_mail = await models.Mail.filter(user=user, notification_sent=True).order_by('-id').first()
    # Проверяем, предлагалась ли пользователю скидка ранее
    if user.discount_used is True:
        await manager.start(ReceiveEmailMenu.rent_email_no_discount, data={"mail_id": last_mail.id})
    else:
        await manager.start(ReceiveEmailMenu.rent_email_discount, data={"mail_id": last_mail.id})


async def on_confirm_rent_email(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для подтверждения аренды почтового ящика.
    Обновляет информацию о почтовом ящике и балансе пользователя в базе данных.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """

    ctx = manager.current_context()
    if 'email' in ctx.start_data:
        ctx.dialog_data = ctx.start_data

    mail_id = ctx.dialog_data.get('mail_id')
    cost = ctx.dialog_data.get('cost')
    user = await models.User.get_user(c.from_user.id)
    if not user:
        return

    if user.balance < cost:
        await manager.switch_to(ReceiveEmailMenu.not_enough_balance)
        return

    mail = await models.Mail.get_mail(mail_id)
    if not mail:
        return

    mail.is_paid_mail = True
    mail.expire_at = timezone.now() + timedelta(days=ctx.dialog_data['rent_days'])
    mail.is_active = True
    await mail.save(update_fields=['is_paid_mail', 'expire_at'])
    low_balance = await check_low_balance(user, cost)
    user.balance -= cost
    await user.save(update_fields=['balance'])
    ctx.dialog_data['email'] = mail.email
    await manager.switch_to(ReceiveEmailMenu.rent_email_success)
    await asyncio.sleep(2)
    if low_balance:
        await send_low_balance_alert(user)


async def on_my_rent_emails(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Мои арендованные почтовые ящики".
    Отображает список арендованных почтовых ящиков пользователя.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    user = await models.User.get_user(c.from_user.id)
    if not user:
        return

    mails = await models.Mail.filter(user=user, is_paid_mail=True, is_active=True).all()
    if len(mails) == 0:
        await c.answer(text='У вас нет арендованных почтовых ящиков', show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for mail in mails:
        builder.add(types.InlineKeyboardButton(text=mail.email, callback_data=f'mail:{mail.id}'))

    builder.button(text=bt.BACK_BTN, callback_data='receive_email')
    builder.adjust(1)

    await c.message.edit_text(text='Выберите почтовый ящик', reply_markup=builder.as_markup())
    await manager.reset_stack(remove_keyboard=False)

