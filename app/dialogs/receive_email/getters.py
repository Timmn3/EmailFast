from datetime import datetime
import pytz
from aiogram_dialog import DialogManager
from app.db import models
from app.dialogs.receive_email.keyboards import rent_email_kb
from app.dialogs.receive_email.selected import on_rent_email_item
from app.services.sms_receive import SmsReceive
from loguru import logger

async def get_email_info(dialog_manager: DialogManager, **middleware_data):
    """
    Получает данные для email-диалогов.

    Важно:
    - legacy mail.tm остаётся в старой модели Mail;
    - аренда FirstMail теперь может открываться и без legacy mail_id;
    - поэтому getter обязан корректно работать в двух режимах:
      1) с mail_id (старый сценарий mail.tm),
      2) без mail_id (новый сценарий free FirstMail -> аренда).
    """
    try:
        user_id = dialog_manager.event.from_user.id
        user = await models.User.get_user(user_id)

        logger.bind(user_id=user_id, action='get_email_info').log(
            "USER_ACTION",
            "Запрос информации о почтовом ящике"
        )

        if not user:
            logger.bind(user_id=user_id, action='get_email_info').log(
                "USER_ACTION",
                "Пользователь не найден"
            )
            return {}

        ctx = dialog_manager.current_context()

        # Важно:
        # - в legacy-сценарии mail_id обычно лежит в start_data;
        # - в старых переходах внутри диалога он мог лежать в dialog_data;
        # - в новом сценарии free FirstMail -> аренда mail_id может не быть вообще.
        start_data = ctx.start_data or {}
        dialog_data = ctx.dialog_data or {}

        mail_id = dialog_data.get('mail_id') or start_data.get('mail_id')

        # Количество активных арендованных ящиков ТОЛЬКО текущего пользователя.
        paid_mails_count = await models.RentalEmailLease.filter(
            user=user,
            is_active=True
        ).count()

        # История использования бесплатной недели живёт в новой модели аренд.
        free_week_used = await models.RentalEmailLease.has_used_free_week(user)

        email_value = ""

        if mail_id:
            dialog_data['mail_id'] = mail_id

            mail = await models.Mail.get_mail(mail_id)
            if mail:
                email_value = mail.email
                logger.bind(user_id=user_id, action='get_email_info').log(
                    "USER_ACTION",
                    f"Информация о почте '{mail.email}' успешно получена | "
                    f"free_week_used={free_week_used} | paid_mails_count={paid_mails_count}"
                )
            else:
                logger.bind(user_id=user_id, action='get_email_info').log(
                    "USER_ACTION",
                    f"Почта с ID={mail_id} не найдена, продолжаем без legacy mail"
                )
        else:
            logger.bind(user_id=user_id, action='get_email_info').log(
                "USER_ACTION",
                f"mail_id отсутствует, продолжаем без legacy mail | "
                f"free_week_used={free_week_used} | paid_mails_count={paid_mails_count}"
            )

        return {
            "email": email_value,
            "is_free_week": free_week_used,
            "rent_keyboard": rent_email_kb(on_rent_email_item, free_week_used),
            "paid_mails_count": paid_mails_count,
        }

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_email_info: {e}")
        return {}

async def get_balance(dialog_manager: DialogManager, **middleware_data):
    """
    Получает текущий баланс пользователя и стоимость услуги.
    :param dialog_manager: Объект DialogManager.
    :param middleware_data: Дополнительные данные из middleware.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        logger.bind(user_id=user_id, action='get_balance').log(
            "USER_ACTION",
            "Запрос текущего баланса пользователя"
        )

        ctx = dialog_manager.current_context()
        user = await models.User.get_user(user_id)
        if not user:
            logger.bind(user_id=user_id, action='get_balance').log(
                "USER_ACTION",
                "Пользователь не найден"
            )
            return {}

        logger.bind(user_id=user_id, action='get_balance').log(
            "USER_ACTION",
            f"Текущий баланс: {user.balance}, стоимость услуги: {ctx.dialog_data.get('cost')}"
        )

        cost = ctx.dialog_data.get('cost')
        return {
            'balance': int(user.balance),
            'cost': int(cost) if cost is not None else cost
        }
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_balance: {e}")
        return {}


async def get_rent_info(dialog_manager: DialogManager, **middleware_data):
    """
    Получает информацию о сроке аренды почтового ящика.
    :param dialog_manager: Объект DialogManager.
    :param middleware_data: Дополнительные данные из middleware.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        logger.bind(user_id=user_id, action='get_rent_info').log(
            "USER_ACTION",
            "Запрос информации о сроке аренды"
        )

        ctx = dialog_manager.current_context()
        rent_days = ctx.dialog_data.get('rent_days')
        cost = ctx.dialog_data.get('cost')

        logger.bind(user_id=user_id, action='get_rent_info').log(
            "USER_ACTION",
            f"Срок аренды: {rent_days} дней, стоимость: {cost}₽"
        )

        return {
            'rent_days': rent_days,
            'cost': int(cost) if cost is not None else cost
        }
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_rent_info: {e}")
        return {}