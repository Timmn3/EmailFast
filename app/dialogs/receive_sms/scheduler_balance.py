from datetime import datetime, timedelta
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from app.db import models
from app.dialogs.receive_sms.selected import send_service_on_country
from app.scheduler_instance import scheduler
from loguru import logger


# Асинхронная функция для проверки баланса и отправки сервиса
async def check_balance_and_send_service(user_id, job_id, price, retail_price, free_price_map, country_id, country_name, service_code, c, manager):
    try:
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
                country_name=country_name,
                service_code=service_code,
                price=price,
                retail_price=retail_price,
                free_price_map=free_price_map,
                c=c,
                manager=manager
            )

            # После успеха чистим обе задачи: проверку и авто-стоп
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass

            try:
                scheduler.remove_job(f"stop_{job_id}")
            except Exception:
                pass

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в check_balance_and_send_service: {e}")


# Асинхронная функция для запуска проверки баланса
async def start_balance_check(user_id, price, retail_price, free_price_map, country_id, country_name, service_code, c, manager):
    if price is None:
        return

    try:
        logger.bind(user_id=user_id, action='start_balance_check').log(
            "USER_ACTION",
            f"Запуск проверки баланса для услуги: цена={price}, страна={country_id}, сервис={service_code}"
        )

        # ID задачи (важно: используем его же для удаления)
        job_id = f"balance_check_sms_{user_id}"
        stop_job_id = f"stop_{job_id}"

        # Задача: периодическая проверка баланса
        scheduler.add_job(
            check_balance_and_send_service,
            IntervalTrigger(seconds=5),
            args=[user_id, job_id, price, retail_price, free_price_map, country_id, country_name, service_code, c, manager],
            id=job_id,
            replace_existing=True
        )

        # Задача: авто-остановка через 5 минут (чтобы не висело вечно)
        end_time = datetime.now() + timedelta(minutes=5)
        scheduler.add_job(
            lambda: scheduler.remove_job(job_id),
            DateTrigger(run_date=end_time),
            id=stop_job_id,
            replace_existing=True
        )

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в start_balance_check: {e}")
