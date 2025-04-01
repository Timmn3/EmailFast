from datetime import datetime, timedelta
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from app.db import models
from app.dialogs.rent_sms.selected import rent_number_in_days
from app.scheduler_instance import scheduler


# Асинхронная функция для проверки баланса и аренды номера
async def check_balance_and_send_service_rent(user_id, price, day_index, selected_country, c, manager):
    # Получаем информацию о пользователе из базы данных
    user = await models.User.get_user(user_id)

    # Если баланс пользователя достаточен, арендуем номер
    if user.balance >= price:
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


# Асинхронная функция для запуска проверки баланса
async def start_balance_check_rent(user_id, price, day_index, selected_country, c, manager):
    job_id = f'balance_check_rent_{user_id}'
    stop_job_id = f'stop_{job_id}'

    # Проверяем, есть ли уже такая задача, и удаляем перед добавлением
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

    # Добавляем отдельную задачу для остановки проверки баланса
    scheduler.add_job(
        lambda: scheduler.remove_job(job_id) if scheduler.get_job(job_id) else None,
        DateTrigger(run_date=end_time),
        id=stop_job_id
    )
