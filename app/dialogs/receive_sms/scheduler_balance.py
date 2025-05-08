from datetime import datetime, timedelta
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from app.db import models
from app.dialogs.receive_sms.selected import send_service_on_country
from app.scheduler_instance import scheduler
from loguru import logger


# Асинхронная функция для проверки баланса и отправки сервиса
async def check_balance_and_send_service(user_id, price, retail_price, free_price_map, country_id, service_code, c, manager):
    try:
        logger.bind(user_id=user_id, action='check_balance_and_send_service').log(
            "USER_ACTION",
            f"Проверка баланса для покупки услуги: цена={price}, страна={country_id}, сервис={service_code}"
        )

        # Получаем информацию о пользователе из базы данных
        user = await models.User.get_user(user_id)

        # Если баланс пользователя достаточен, отправляем услугу
        if user.balance >= price:
            logger.bind(user_id=user_id, action='check_balance_and_send_service').log(
                "USER_ACTION",
                f"Баланс достаточен. Отправка услуги: страна={country_id}, сервис={service_code}"
            )
            await send_service_on_country(
                country_id=country_id,
                service_code=service_code,
                price=price,
                retail_price=retail_price,
                free_price_map=free_price_map,
                c=c,
                manager=manager
            )
            # Удаляем задачу проверки баланса после успешного выполнения
            scheduler.remove_job(f'balance_check_sms_{user_id}')
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в check_balance_and_send_service: {e}")


# Асинхронная функция для запуска проверки баланса
async def start_balance_check(user_id, price, retail_price, free_price_map, country_id, service_code, c, manager):
    try:
        logger.bind(user_id=user_id, action='start_balance_check').log(
            "USER_ACTION",
            f"Запуск проверки баланса для услуги: цена={price}, страна={country_id}, сервис={service_code}"
        )

        # Генерируем уникальный идентификатор задачи с меткой времени
        job_id = f'balance_check_sms_{user_id}'

        # Добавляем задачу для периодической проверки баланса каждые 5 секунд
        scheduler.add_job(
            check_balance_and_send_service,
            IntervalTrigger(seconds=5),
            args=[user_id, price, retail_price, free_price_map, country_id, service_code, c, manager],
            id=job_id,
            replace_existing=True  # Заменяем существующую задачу с тем же ID
        )

        # Определяем время завершения задачи (через 5 минут)
        end_time = datetime.now() + timedelta(minutes=5)

        # Добавляем отдельную задачу для остановки проверки баланса по истечении времени
        scheduler.add_job(
            lambda: scheduler.remove_job(job_id),  # Лямбда-функция для удаления задачи
            DateTrigger(run_date=end_time),  # Запускаем через 5 минут
            id=f'stop_{job_id}'  # Уникальный ID для задачи остановки
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в start_balance_check: {e}")