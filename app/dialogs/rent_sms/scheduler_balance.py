from datetime import datetime, timedelta
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from app.db import models
from app.dialogs.receive_sms.selected import send_service_on_country
from app.dialogs.rent_sms.selected import rent_number_in_days
from app.scheduler_instance import scheduler


# Функция для проверки баланса
async def check_balance_and_send_service_rent (user_id, price, day_index, selected_country, c, manager):
    user = await models.User.get_user(user_id)
    if user.balance >= price:
        await rent_number_in_days(
            c = c,
            widget = None,
            manager = manager,
            day_index = day_index,
            selected_country = selected_country
        )
        # Останавливаем задачу после успешного выполнения
        scheduler.remove_all_jobs()


# Используем scheduler для добавления задач
async def start_balance_check_rent(user_id, price, day_index, selected_country, c, manager):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    # Добавляем задачу для проверки баланса каждую секунду
    scheduler.add_job(
        check_balance_and_send_service_rent,
        IntervalTrigger(seconds=10),
        args=[user_id, price, day_index, selected_country, c, manager],
        id=f'balance_check_{timestamp}',
        replace_existing=True,
        max_instances=5
    )

    # Добавляем задачу для остановки всех проверок через 5 минут
    end_time = datetime.now() + timedelta(minutes=5)
    scheduler.add_job(
        scheduler.remove_all_jobs,
        DateTrigger(run_date=end_time),
        id=f'stop_balance_check_{timestamp}'
    )
