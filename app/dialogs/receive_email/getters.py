from datetime import datetime
import pytz
from aiogram_dialog import DialogManager
from app.db import models
from app.services.sms_receive import SmsReceive
from loguru import logger


async def get_email_info(dialog_manager: DialogManager, **middleware_data):
    """
    Получает информацию о почтовом ящике для отображения в интерфейсе.
    :param dialog_manager: Объект DialogManager.
    :param middleware_data: Дополнительные данные из middleware.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        user = await models.User.get_user(user_id)
        mail = await models.Mail.filter(user=user).order_by('-id').first()
        logger.bind(user_id=user_id, action='get_email_info').log(
            "USER_ACTION",
            "Запрос информации о почтовом ящике"
        )

        ctx = dialog_manager.current_context()
        mail_id = ctx.start_data.get('mail_id')
        if not mail_id:
            logger.bind(user_id=user_id, action='get_email_info').log(
                "USER_ACTION",
                "ID почты не передан"
            )
            return {}

        ctx.dialog_data['mail_id'] = mail_id
        mail = await models.Mail.get_mail(mail_id)
        if not mail:
            logger.bind(user_id=user_id, action='get_email_info').log(
                "USER_ACTION",
                f"Почта с ID={mail_id} не найдена"
            )
            return {}

        paid_mails_count = await models.Mail.filter(is_active=True, is_paid_mail=True).count()

        is_free_week = False
        # Определяем временную зону +3
        tz = pytz.timezone('Europe/Moscow')

        # Получаем текущее время в UTC и переводим в нужную временную зону
        now = datetime.now(pytz.utc).astimezone(tz)
        if mail and mail.is_free_week and mail.expire_at <= now:
            # Неделя истекла, снимаем флаг
            mail.is_free_week = False
            await mail.save(update_fields=["is_free_week"])
        elif mail and mail.is_free_week:
            is_free_week = True

        logger.bind(user_id=user_id, action='get_email_info').log(
            "USER_ACTION",
            f"Информация о почте '{mail.email}' успешно получена"
        )

        return {
            'email': mail.email,
            'paid_mails_count': paid_mails_count,
            'is_free_week': not is_free_week  # показываем кнопку, если неделя ещё не была использована
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

        return {
            'balance': user.balance,
            'cost': ctx.dialog_data.get('cost')
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
            'cost': cost
        }
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_rent_info: {e}")
        return {}