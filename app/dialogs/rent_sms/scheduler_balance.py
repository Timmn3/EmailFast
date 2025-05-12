from datetime import datetime, timedelta
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from app.db import models
from app.dialogs.rent_sms.selected import rent_number_in_days
from app.scheduler_instance import scheduler
from loguru import logger


# Асинхронная функция для проверки баланса и аренды номера
async def check_balance_and_send_service_rent(user_id, price, day_index, selected_country, c, manager):
    """
    Проверяет баланс пользователя и, при достаточном количестве средств,
    вызывает функцию аренды номера. После успешного выполнения удаляет задачу.

    :param user_id: ID пользователя.
    :param price: Стоимость услуги.
    :param day_index: Индекс количества дней аренды.
    :param selected_country: Выбранная страна.
    :param c: CallbackQuery от aiogram.
    :param manager: Менеджер диалогов aiogram_dialog.
    """
    try:
        logger.bind(user_id=user_id, action='check_balance_and_send_service_rent').log(
            "USER_ACTION",
            f"Проверка баланса для аренды номера: цена={price}, страна={selected_country.get('country', 'неизвестная')}, дни={day_index}"
        )

        # Получаем информацию о пользователе из базы данных
        user = await models.User.get_user(user_id)

        # Если баланс пользователя достаточен, арендуем номер
        if user.balance >= price:
            logger.bind(user_id=user_id, action='check_balance_and_send_service_rent').log(
                "USER_ACTION",
                f"Баланс достаточно. Запуск аренды номера на {day_index} дней"
            )
            await rent_number_in_days(
                c=c,
                widget=None,
                manager=manager,
                day_index=day_index,
                selected_country=selected_country
            )
            # Удаляем задачу проверки баланса после успешного выполнения
            if scheduler.get_job(f'balance_check_rent_{user_id}'):
                scheduler.remove_job(f'balance_check_rent_{user_id}')
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в check_balance_and_send_service_rent: {e}")


# Асинхронная функция для запуска проверки баланса
async def start_balance_check_rent(user_id, price, day_index, selected_country, c, manager):
    """
    Запускает периодическую проверку баланса пользователя для аренды номера.
    Если баланс пополняется, запускается аренда номера.

    :param user_id: ID пользователя.
    :param price: Стоимость аренды.
    :param day_index: Индекс количества дней аренды.
    :param selected_country: Выбранная страна.
    :param c: CallbackQuery от aiogram.
    :param manager: Менеджер диалогов aiogram_dialog.
    """
    try:
        logger.bind(user_id=user_id, action='start_balance_check_rent').log(
            "USER_ACTION",
            f"Запуск проверки баланса для аренды номера: цена={price}, страна={selected_country.get('country', 'неизвестная')}, дни={day_index}"
        )

        job_id = f'balance_check_rent_{user_id}'
        stop_job_id = f'stop_{job_id}'

        # Проверяем, есть ли уже такая задача, и удаляем её перед добавлением новой
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        if scheduler.get_job(stop_job_id):
            scheduler.remove_job(stop_job_id)

        # Добавляем задачу для периодической проверки баланса каждые 5 секунд
        scheduler.add_job(
            check_balance_and_send_service_rent,
            IntervalTrigger(seconds=5),
            args=[user_id, price, day_index, selected_country, c, manager],
            id=job_id
        )

        # Определяем время завершения задачи (через 5 минут)
        end_time = datetime.now() + timedelta(minutes=5)

        # Добавляем отдельную задачу для остановки проверки баланса по истечении времени
        scheduler.add_job(
            lambda: scheduler.remove_job(job_id) if scheduler.get_job(job_id) else None,
            DateTrigger(run_date=end_time),
            id=stop_job_id
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в start_balance_check_rent: {e}")