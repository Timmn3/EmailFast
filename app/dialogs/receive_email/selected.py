from datetime import timedelta
import asyncio
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram_dialog import DialogManager, StartMode
from aiogram_dialog.widgets.kbd import Button
from tortoise import timezone
from loguru import logger
from app.db import models
from app.db.models import Mail
from app.dialogs.receive_email.states import ReceiveEmailMenu
from app.services.bot_texts import RENT_DATA, RENT_DATA_DISCOUNT
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.mail.temp_mail_tm import create_mail
from app.services.temp_mail import TempMail
from app.services import bot_texts as bt
from app.services.rental_email_pool import issue_rental_email


async def on_back_mail(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Назад" в меню почтовых ящиков.
    Переключает состояние диалога на основное меню почтовых ящиков.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_back_mail').log("USER_ACTION", "Возврат к меню почтовых ящиков")

        user = await models.User.get_user(user_id)
        if not user:
            return

        await manager.switch_to(ReceiveEmailMenu.receive_email)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_back_mail: {e}")


async def on_change_email(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Изменить почтовый ящик".
    Генерирует новый почтовый ящик и сохраняет его в базе данных.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_change_email').log("USER_ACTION", "Запрос на смену почтового ящика")

        ctx = manager.current_context()
        mail_id = ctx.dialog_data.get('mail_id')
        if not mail_id:
            logger.bind(user_id=user_id, action='on_change_email').log("USER_ACTION", "ID почты не найден")
            await c.answer("Не найден идентификатор почты.", show_alert=True)
            return

        mail = await models.Mail.get_mail(mail_id)
        if mail:
            mail.is_active = False
            await mail.save(update_fields=['is_active'])

        user = await models.User.get_user(user_id)
        email, token = await create_mail()

        if not email or not token:
            logger.bind(user_id=user_id, action='on_change_email').log("USER_ACTION", "Ошибка при создании почты")
            await c.answer("Ошибка при создании почтового ящика. Попробуйте позже.", show_alert=True)
            return

        mail = await models.Mail.add_mail(user, email, token)
        ctx.start_data['mail_id'] = mail.id
        ctx.dialog_data['mail_id'] = mail.id

        logger.bind(user_id=user_id, action='on_change_email').log("USER_ACTION", f"Создана новая почта: {email}")

        await manager.start(
            ReceiveEmailMenu.receive_email,
            data={"mail_id": mail.id},
            mode=StartMode.RESET_STACK
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_change_email: {e}")


async def on_rent_email(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Арендовать почтовый ящик".

    Для нового пула FirstMail:
    - больше НЕ требуем существующий временный Mail;
    - просто проверяем, использовал ли пользователь бесплатную неделю ранее;
    - открываем нужное окно выбора срока аренды.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_rent_email').log(
            "USER_ACTION",
            "Переход к аренде почты из пула FirstMail"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await c.answer("Пользователь не найден", show_alert=True)
            return

        # Проверка флага бесплатной недели в новой модели аренд
        if await models.RentalEmailLease.has_used_free_week(user):
            await manager.switch_to(ReceiveEmailMenu.rent_email_no_free_week)
            logger.bind(user_id=user_id, action='on_rent_email').log(
                "USER_ACTION",
                "Открыто окно аренды без бесплатной недели"
            )
        else:
            await manager.switch_to(ReceiveEmailMenu.rent_email)
            logger.bind(user_id=user_id, action='on_rent_email').log(
                "USER_ACTION",
                "Открыто окно аренды с бесплатной неделей"
            )

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_rent_email: {e}")


async def on_rent_email_item(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик выбора периода аренды почтового ящика.

    Для нового пула FirstMail:
    - больше НЕ ищем временный Mail пользователя;
    - email заранее не показываем, он будет выдан автоматически из пула
      только в момент подтверждения аренды;
    - сохраняем в dialog_data только срок и стоимость.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_rent_email_item').log(
            "USER_ACTION",
            "Выбран период аренды"
        )

        from app.dialogs.personal_cabinet.selected import on_deposit_state
        widget_id = widget.widget_id

        user = await models.User.get_user(user_id)
        if not user:
            await c.answer("Пользователь не найден", show_alert=True)
            return

        ctx = manager.current_context()
        ctx.dialog_data['cost'] = RENT_DATA[widget_id][0]
        ctx.dialog_data['rent_days'] = RENT_DATA[widget_id][1]
        ctx.dialog_data['rent_text'] = RENT_DATA[widget_id][2]

        # Проверка: если пользователь пытается повторно арендовать бесплатную неделю
        if widget_id == "rent_email_week" and await models.RentalEmailLease.has_used_free_week(user):
            logger.bind(user_id=user_id, action='on_rent_email_item').log(
                "USER_ACTION",
                "Попытка повторной аренды бесплатной недели"
            )
            await c.answer("Вы уже использовали бесплатную неделю", show_alert=True)
            return

        # Email ещё неизвестен — он будет назначен из пула только после подтверждения
        ctx.dialog_data['email'] = "будет выдан автоматически из пула"

        if user.balance < ctx.dialog_data['cost']:
            logger.bind(user_id=user_id, action='on_rent_email_item').log(
                "USER_ACTION",
                f"Недостаточно средств для аренды: требуется {ctx.dialog_data['cost']}, доступно {user.balance}"
            )
            await on_deposit_state(c=c, widget=widget, manager=manager)
            return

        logger.bind(user_id=user_id, action='on_rent_email_item').log(
            "USER_ACTION",
            f"Выбран срок аренды на {ctx.dialog_data['rent_text']}"
        )

        await manager.switch_to(ReceiveEmailMenu.rent_email_confirm)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_rent_email_item: {e}")


async def on_rent_email_item_discount(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик выбора периода аренды почтового ящика со скидкой.

    Для нового пула FirstMail:
    - больше НЕ ищем временный Mail пользователя;
    - email назначается автоматически из пула только после подтверждения;
    - сохраняем только срок, стоимость и текст срока.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_rent_email_item_discount').log(
            "USER_ACTION",
            "Выбор аренды со скидкой"
        )

        from app.dialogs.personal_cabinet.selected import on_deposit_state
        widget_id = widget.widget_id

        user = await models.User.get_user(user_id)
        if not user:
            await c.answer("Пользователь не найден", show_alert=True)
            return

        rent_data = RENT_DATA_DISCOUNT
        if widget_id not in rent_data:
            await c.answer("Неизвестный вариант аренды", show_alert=True)
            return

        ctx = manager.current_context()

        cost = rent_data[widget_id][0]
        days = rent_data[widget_id][1]
        text = rent_data[widget_id][2]

        ctx.dialog_data.update({
            'cost': cost,
            'rent_days': days,
            'rent_text': text,
            'email': "будет выдан автоматически из пула",
        })

        if user.balance < cost:
            logger.bind(user_id=user_id, action='on_rent_email_item_discount').log(
                "USER_ACTION",
                f"Недостаточно средств для аренды со скидкой: требуется {cost}, доступно {user.balance}"
            )
            await on_deposit_state(c=c, widget=widget, manager=manager)
            return

        if user.discount_used is not True:
            user.discount_used = True
            await user.save()
            logger.bind(user_id=user_id, action='on_rent_email_item_discount').log(
                "USER_ACTION",
                "Пользователь использовал скидку"
            )

        logger.bind(user_id=user_id, action='on_rent_email_item_discount').log(
            "USER_ACTION",
            f"Выбран срок аренды '{text}' по скидке"
        )

        await manager.switch_to(ReceiveEmailMenu.rent_email_confirm)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_rent_email_item_discount: {e}")

async def on_rent_email_check_discount(message: types.Message, manager: DialogManager):
    """
    Проверяет, предлагалась ли пользователю скидка, и переключает
    на соответствующее окно аренды.

    Для нового пула FirstMail:
    - больше НЕ ищем последний Mail пользователя;
    - запускаем окно аренды без привязки к временной почте.
    """
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='on_rent_email_check_discount').log(
            "USER_ACTION",
            "Проверка наличия скидки у пользователя"
        )

        user = await models.User.get_or_none(telegram_id=user_id)
        if not user:
            return

        if user.discount_used is True:
            logger.bind(user_id=user_id, action='on_rent_email_check_discount').log(
                "USER_ACTION",
                "Скидка уже была использована"
            )
            await manager.start(ReceiveEmailMenu.rent_email_no_discount, mode=StartMode.RESET_STACK)
        else:
            logger.bind(user_id=user_id, action='on_rent_email_check_discount').log(
                "USER_ACTION",
                "Скидка ещё не использована"
            )
            await manager.start(ReceiveEmailMenu.rent_email_discount, mode=StartMode.RESET_STACK)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_rent_email_check_discount: {e}")


async def on_confirm_rent_email(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Подтверждает аренду почтового ящика из нового пула FirstMail.

    Новая логика:
    - НЕ переводим старый временный Mail в is_paid_mail=True;
    - создаём отдельную аренду в rental_email_leases через сервис issue_rental_email();
    - списываем деньги только после успешной выдачи ящика из пула.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_confirm_rent_email').log(
            "USER_ACTION",
            "Подтверждение аренды почты из пула FirstMail"
        )

        ctx = manager.current_context()
        if 'email' in ctx.start_data:
            ctx.dialog_data = ctx.start_data

        cost = ctx.dialog_data.get('cost')
        rent_days = ctx.dialog_data.get('rent_days')

        if cost is None or rent_days is None:
            await c.answer("Данные аренды не найдены. Откройте меню заново.", show_alert=True)
            return

        user = await models.User.get_user(user_id)
        if not user:
            await c.answer("Пользователь не найден", show_alert=True)
            return

        if user.balance < cost and cost > 0:
            logger.bind(user_id=user_id, action='on_confirm_rent_email').log(
                "USER_ACTION",
                f"Недостаточно средств для аренды: требуется {cost}, доступно {user.balance}"
            )
            await manager.switch_to(ReceiveEmailMenu.not_enough_balance)
            return

        # Бесплатная неделя — это только сценарий 7 дней за 0 ₽
        is_free_week = rent_days == 7 and cost == 0

        try:
            lease = await issue_rental_email(
                user=user,
                days=rent_days,
                is_free_week=is_free_week,
            )
        except RuntimeError:
            logger.bind(user_id=user_id, action='on_confirm_rent_email').log(
                "USER_ACTION",
                "Свободные почтовые ящики в пуле закончились"
            )
            await c.answer("Свободные почтовые ящики временно закончились", show_alert=True)
            return

        low_balance = await check_low_balance(user, cost)

        # Деньги списываем только если аренда платная
        if cost > 0:
            user.balance -= cost
            await user.save(update_fields=['balance'])

        logger.bind(user_id=user_id, action='on_confirm_rent_email').log(
            "USER_ACTION",
            f"Успешная аренда почты '{lease.email}' на {rent_days} дней"
        )

        ctx.dialog_data['lease_id'] = lease.id
        ctx.dialog_data['email'] = lease.email

        await manager.switch_to(ReceiveEmailMenu.rent_email_success)
        await asyncio.sleep(2)

        if low_balance:
            await send_low_balance_alert(user)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_confirm_rent_email: {e}")

async def on_my_rent_emails(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Мои арендованные почтовые ящики".
    Отображает список арендованных почтовых ящиков пользователя.

    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_my_rent_emails').log("USER_ACTION", "Запрос списка арендованных почт")

        user = await models.User.get_user(user_id)
        if not user:
            return

        mails = await models.Mail.filter(user=user, is_paid_mail=True, is_active=True).all()
        if len(mails) == 0:
            logger.bind(user_id=user_id, action='on_my_rent_emails').log(
                "USER_ACTION",
                "Нет арендованных почт"
            )
            await c.answer(text='У вас нет арендованных почтовых ящиков', show_alert=True)
            return

        builder = InlineKeyboardBuilder()
        for mail in mails:
            builder.add(types.InlineKeyboardButton(text=mail.email, callback_data=f'mail:{mail.id}'))

        builder.button(text=bt.BACK_BTN, callback_data='receive_email')
        builder.adjust(1)

        logger.bind(user_id=user_id, action='on_my_rent_emails').log(
            "USER_ACTION",
            f"Отображено {len(mails)} арендованных почт"
        )

        await c.message.edit_text(text='Выберите почтовый ящик', reply_markup=builder.as_markup())
        await manager.reset_stack(remove_keyboard=False)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_my_rent_emails: {e}")