from aiogram_dialog import DialogManager
from tortoise import timezone

from app.db import models
from app.services.sms_receive import SmsReceive
from app.services import bot_texts as bt
from loguru import logger

# Fallback: цены по кол-ву дней для старых записей без cost
_DAYS_TO_EMAIL_PRICE = {7: 0, 30: 199, 183: 599, 365: 999}


def _resolve_email_cost(lease) -> int:
    """Реальная стоимость аренды: из поля cost, либо fallback по days для старых записей."""
    stored = lease.cost or 0
    # Если cost > 0 — берём как есть (новая запись с реальной ценой)
    # Если is_free_week или is_change — 0₽ честные, не делаем fallback
    if stored > 0 or lease.is_free_week or lease.is_change:
        return int(stored)
    # Старая запись без cost — восстанавливаем по кол-ву дней
    return _DAYS_TO_EMAIL_PRICE.get(lease.days, 0)


async def get_user_info(dialog_manager: DialogManager, **middleware_data):
    """
    Получает информацию о пользователе для отображения в интерфейсе.
    :param dialog_manager: Объект DialogManager.
    :param middleware_data: Дополнительные данные из middleware.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        logger.bind(user_id=user_id, action='get_user_info').log(
            "USER_ACTION",
            "Запрос информации о пользователе"
        )

        user = await models.User.get_user(user_id)
        if not user:
            logger.bind(user_id=user_id, action='get_user_info').log(
                "USER_ACTION",
                "Пользователь не найден"
            )
            return {}

        logger.bind(user_id=user_id, action='get_user_info').log(
            "USER_ACTION",
            f"Информация о пользователе получена: баланс={user.balance}, ref_balance={user.ref_balance}"
        )
        return {
            'user_id': user.telegram_id,
            'balance': int(user.balance),
            'ref_balance': int(user.ref_balance),
        }
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_user_info: {e}")
        return {}


async def get_deposit_prices(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список доступных сумм пополнения и проверяет наличие бонуса у пользователя.
    :param dialog_manager: Объект DialogManager.
    :param middleware_data: Дополнительные данные из middleware.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        if user_id is None:
            user_id = dialog_manager.start_data.get("user_id")

        logger.bind(user_id=user_id, action='get_deposit_prices').log(
            "USER_ACTION",
            "Запрос списка цен на пополнение"
        )

        user = await models.User.get_user(user_id)

        bonus_active = False
        if user and user.bonus_end_at and user.bonus_end_at > timezone.now():
            bonus_active = True
            logger.bind(user_id=user_id, action='get_deposit_prices').log(
                "USER_ACTION",
                f"Бонус активен до {user.bonus_end_at}"
            )

        logger.bind(user_id=user_id, action='get_deposit_prices').log(
            "USER_ACTION",
            "Список цен успешно подготовлен"
        )
        return {
            'prices': bt.prices_data,
            'bonus': bonus_active
        }
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_deposit_prices: {e}")
        return {}


async def get_order_history(dialog_manager: DialogManager, **middleware_data):
    """
    Собирает последние 10 заказов пользователя (SMS-активации + Email-аренды).
    """
    try:
        user_id = dialog_manager.event.from_user.id
        user = await models.User.get_user(user_id)
        if not user:
            return {'order_history_text': 'Заказы не найдены.'}

        orders = []

        # SMS-активации — только те, по которым пришла смс
        activations = (
            await models.Activation.filter(user=user)
            .exclude(sms_text=None)
            .exclude(sms_text='')
            .order_by('-created_at')
            .limit(10)
            .select_related('service', 'service_2')
            .all()
        )
        for a in activations:
            if a.service and hasattr(a.service, 'name'):
                svc_name = a.service.name
            elif a.service_2 and hasattr(a.service_2, 'name'):
                svc_name = a.service_2.name
            else:
                svc_name = '—'
            orders.append({
                'created_at': a.created_at,
                'type': 'Принять СМС',
                'cost': int(a.cost),
                'detail_label': 'Номер',
                'detail_value': a.phone_number,
                'service_name': svc_name,
            })

        # Email-аренды (RentalEmailLease) — все записи включая смены ящика
        leases = (
            await models.RentalEmailLease.filter(user=user)
            .order_by('-created_at')
            .limit(10)
            .all()
        )
        for lease in leases:
            orders.append({
                'created_at': lease.created_at,
                'type': 'Принять Email',
                'cost': _resolve_email_cost(lease),
                'detail_label': 'Email',
                'detail_value': lease.email,
            })

        # Сортируем все заказы по дате, берём 10 последних
        orders.sort(key=lambda x: x['created_at'], reverse=True)
        orders = orders[:10]

        if not orders:
            return {'order_history_text': 'У вас пока нет заказов.'}

        lines = []
        for o in orders:
            # Переводим дату в МСК (UTC+3)
            from datetime import timezone as dt_timezone, timedelta
            msk = dt_timezone(timedelta(hours=3))
            dt_msk = o['created_at'].astimezone(msk)
            date_str = dt_msk.strftime('%d.%m.%Y')

            service_line = f"\nСервис: {o['service_name']}" if o.get('service_name') else ''
            block = (
                f"Дата заказа: {date_str}\n"
                f"Тип: {o['type']}{service_line}\n"
                f"Сумма: {o['cost']} ₽\n"
                f"{o['detail_label']}: {o['detail_value']}"
            )
            lines.append(block)

        text = '\n\n'.join(lines)
        return {'order_history_text': text}

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_order_history: {e}")
        return {'order_history_text': 'Ошибка при загрузке истории.'}