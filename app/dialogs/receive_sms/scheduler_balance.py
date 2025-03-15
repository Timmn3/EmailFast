from datetime import datetime, timedelta
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from app.db import models
from app.dialogs.receive_sms.selected import send_service_on_country
from app.scheduler_instance import scheduler


# Функция для проверки баланса и вызова send_service_on_country
async def check_balance_and_send_service(user_id, price, retail_price, free_price_map, country_id, service_code, c, manager):
    user = await models.User.get_user(user_id)
    if user.balance >= price:
        await send_service_on_country(
            country_id=country_id,
            service_code=service_code,
            price=price,
            retail_price=retail_price,
            free_price_map=free_price_map,
            c=c,
            manager=manager
        )
        # Останавливаем задачу после успешного выполнения
        scheduler.remove_all_jobs()


# Используем scheduler для добавления задач
async def start_balance_check(user_id, price, retail_price, free_price_map, country_id, service_code, c, manager):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    # Добавляем задачу для проверки баланса каждую секунду
    scheduler.add_job(
        check_balance_and_send_service,
        IntervalTrigger(seconds=10),
        args=[user_id, price, retail_price, free_price_map, country_id, service_code, c, manager],
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
