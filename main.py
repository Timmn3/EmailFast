import logging
from aiogram.filters import ExceptionTypeFilter
from aiogram_dialog import DialogManager, StartMode, ShowMode
from aiogram_dialog.api.exceptions import UnknownIntent, UnknownState
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
from aiogram import Dispatcher
from app.db.database import init_db
from app.dependencies import bot
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, EVENT_JOB_EXECUTED
from app.dialogs.bot_menu.states import BotMenu
from app.handlers import (start_handler, affiliate_program, admin_handler, bot_handler, get_email_handler,
                          receive_sms_handler, rent_number_handler)
from app.handlers.health_check_router import health_check_router
from app.services.notify_admins import notify_wakeup_bot
from app.services.onlinesim.service_updater import add_services
from app.services.periodic_tasks import check_sms, check_email, check_payment_lava, check_mail_expiration_and_notify, \
    check_payment_freekassa, check_payment_yoomoney, check_payment_anypay, check_payment_streampay, \
    check_payment_ckassa, check_rent_sms, rents_ending_soon, close_rent, checking_inactive_rent, auto_renewal_of_rent, \
    send_coder
from app.services.ping_scheduler import userbot_ping
from app.services.set_bot_commands import set_default_commands
from app.services import stars_pay
from loguru import logger

# logger.remove()
# Добавляем обработчик для записи в файл без цветного вывода
logger.add("logs/loguru.log",
           format="{level: <8} {time:YYYY.MM.DD HH:mm:ss} {module}:{function}:{line} - {message}",
           level="DEBUG",
           rotation="5 MB",
           compression="zip")


# Уровни логирования в Loguru:
# TRACE - Самый детализированный уровень. Используется для трассировки и детализированной отладки.
# DEBUG - Для отладки и вывода информации, полезной для разработчиков.
# INFO - Для общих информационных сообщений о нормальном выполнении программы.
# SUCCESS - Для сообщений об успешном завершении операций.
# WARNING - Для сообщений, указывающих на потенциальные проблемы, которые не являются критическими.
# ERROR - Для сообщений об ошибках, которые препятствуют нормальному выполнению.
# CRITICAL - Для критических ошибок, которые могут привести к серьезным последствиям или завершению программы.


scheduler = AsyncIOScheduler()


async def on_unknown_intent(event, dialog_manager: DialogManager):
    await dialog_manager.start(
        BotMenu.start, mode=StartMode.RESET_STACK, show_mode=ShowMode.AUTO,
    )


async def on_unknown_state(event, dialog_manager: DialogManager):
    await dialog_manager.start(
        BotMenu.start, mode=StartMode.RESET_STACK, show_mode=ShowMode.AUTO,
    )


def job_listener(event):
    """
    Listener для обработки ошибок, выполнения и пропусков задач.
    """
    if event.code == EVENT_JOB_ERROR:
        logger.error(f"Job {event.job_id} raised an exception: {event.exception}")
    elif event.code == EVENT_JOB_MISSED:
        logger.warning(f"Job {event.job_id} was missed at {event.scheduled_run_time}")
    elif event.code == EVENT_JOB_EXECUTED:
        logger.info(f"Job {event.job_id} executed successfully at {event.scheduled_run_time}")


async def main(dp: Dispatcher):
    """
    Основная функция запуска бота и планировщика.
    """
    main_routers = [
        admin_handler.router,
        start_handler.router,
        affiliate_program.router,
        health_check_router,
        get_email_handler.router,
        receive_sms_handler.router,
        rent_number_handler.router,
    ]
    dp.errors.register(
        on_unknown_intent,
        ExceptionTypeFilter(UnknownIntent),
    )
    dp.errors.register(
        on_unknown_state,
        ExceptionTypeFilter(UnknownState),
    )

    from app.dialogs import setup_dialogs
    dp.include_routers(bot_handler.router, *main_routers)
    setup_dialogs(dp)

    await set_default_commands(bot)
    await init_db()
    await notify_wakeup_bot(bot)

    dp.pre_checkout_query.register(stars_pay.pre_checkout_handler)

    set_scheduled_jobs(scheduler)
    scheduler.add_listener(job_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    scheduler.start()

    await userbot_ping()

    msg_text = "Версия 21.12.2024"  # git push production master
    await send_coder(msg_text)

    await dp.start_polling(bot)

def set_scheduled_jobs(scheduler):
    try:
        # Проверка SMS
        scheduler.add_job(check_sms, "interval", seconds=15, max_instances=3)
        # Проверка Email
        scheduler.add_job(check_email, "interval", seconds=30, max_instances=3)
        # Проверка платежей через CKassa
        scheduler.add_job(check_payment_ckassa, "interval", seconds=18, max_instances=3)
        # Проверка платежей через Streampay
        scheduler.add_job(check_payment_streampay, "interval", seconds=19, max_instances=3)
        # Проверка платежей через FreeKassa
        scheduler.add_job(check_payment_freekassa, "interval", seconds=22, max_instances=3)
        # Проверка платежей через Anypay
        scheduler.add_job(check_payment_anypay, "interval", seconds=23, max_instances=3)
        # Добавление сервисов
        scheduler.add_job(add_services, "cron", hour=3, minute=0)
        # Пинг userbot
        scheduler.add_job(userbot_ping, "interval", seconds=180, max_instances=3)
        # Проверка истечения срока почты и уведомления
        scheduler.add_job(check_mail_expiration_and_notify, "interval", minutes=20, max_instances=3)
        # Проверка арендованных SMS
        scheduler.add_job(check_rent_sms, "interval", seconds=15, max_instances=3)
        # Уведомление об аренде, которая скоро завершится
        scheduler.add_job(rents_ending_soon, "interval", minutes=1, max_instances=3)
        # Автопродление аренды за 2 часа до окончания
        scheduler.add_job(auto_renewal_of_rent, "interval", minutes=1, max_instances=3)
        # Завершение аренды
        scheduler.add_job(close_rent, "interval", minutes=1, max_instances=3)
        # Проверка незавершенных аренд
        scheduler.add_job(checking_inactive_rent, "interval", minutes=20, max_instances=3)
    except Exception as e:
        # Логирование ошибки
        logger.error(f"Error while adding scheduled jobs: {e}")


class SkipSpecificLogFilter(logging.Filter):
    def filter(self, record):
        return not (
                "Execution of job" in record.getMessage() and
                "skipped: maximum number of running instances reached" in record.getMessage()
        )

import signal

def shutdown_scheduler(scheduler):
    logger.info("Shutting down scheduler...")
    scheduler.shutdown()

signal.signal(signal.SIGTERM, lambda *args: shutdown_scheduler(scheduler))
signal.signal(signal.SIGINT, lambda *args: shutdown_scheduler(scheduler))  # Для Ctrl+C


if __name__ == '__main__':
    try:
        from app.dependencies import dp
        logger.success("Starting")
        logger = logging.getLogger('apscheduler')
        logger.setLevel(logging.WARNING)
        handler = logging.StreamHandler()
        handler.addFilter(SkipSpecificLogFilter())
        logger.addHandler(handler)
        asyncio.run(main(dp))
    except Exception as e:
        logger.exception(f'Stop main\n{e}')
