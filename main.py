import logging

from aiogram.filters import ExceptionTypeFilter
from aiogram_dialog import DialogManager, StartMode, ShowMode
from aiogram_dialog.api.exceptions import UnknownIntent, UnknownState
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
from aiogram import Dispatcher

from app.db.database import init_db
from app.dependencies import bot

from app.dialogs.bot_menu.states import BotMenu
from app.handlers import start_handler, affiliate_program, admin_handler, bot_handler
from app.services.notify_admins import notify_wakeup_bot
from app.services.onlinesim.service_updater import add_services
from app.services.periodic_tasks import check_sms, check_email, check_payment_lava, check_mail_expiration_and_notify, \
    check_payment_freekassa, check_payment_yoomoney, check_payment_anypay, check_payment_streampay, check_payment_ckassa
from app.services.set_bot_commands import set_default_commands
from app.services import stars_pay
from loguru import logger
import sys

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


async def main(dp: Dispatcher):
    main_routers = [
        admin_handler.router,
        start_handler.router,
        affiliate_program.router,
    ]
    dp.errors.register(
        on_unknown_intent,
        ExceptionTypeFilter(UnknownIntent),
    )
    dp.errors.register(
        on_unknown_state,
        ExceptionTypeFilter(UnknownState),
    )
    #
    from app.dialogs import setup_dialogs
    dp.include_routers(bot_handler.router, *main_routers)
    setup_dialogs(dp)

    await set_default_commands(bot)
    await init_db()
    await notify_wakeup_bot(bot)

    dp.pre_checkout_query.register(stars_pay.pre_checkout_handler)

    set_scheduled_jobs(scheduler)
    scheduler.start()

    await dp.start_polling(bot)


def set_scheduled_jobs(scheduler, *args, **kwargs):
    try:
        scheduler.add_job(check_sms, "interval", seconds=15, max_instances=3)
        scheduler.add_job(check_email, "interval", seconds=30, max_instances=3)
        scheduler.add_job(check_payment_ckassa, "interval", seconds=18, max_instances=3)
        scheduler.add_job(check_payment_streampay, "interval", seconds=19, max_instances=3)
        scheduler.add_job(check_payment_lava, "interval", seconds=21, max_instances=3)
        scheduler.add_job(check_payment_freekassa, "interval", seconds=22, max_instances=3)
        scheduler.add_job(check_payment_anypay, "interval", seconds=23, max_instances=3)
        scheduler.add_job(check_mail_expiration_and_notify, "interval",minutes=20, max_instances=3)
        scheduler.add_job(add_services, "cron", hour=3, minute=0)
    except Exception:
        pass


    # scheduler.add_job(update_countries_and_services, "interval", minutes=30,
    #                   next_run_time=datetime.now() + timedelta(seconds=10), max_instances=3)


class SkipSpecificLogFilter(logging.Filter):
    def filter(self, record):
        return not (
                "Execution of job" in record.getMessage() and
                "skipped: maximum number of running instances reached" in record.getMessage()
        )


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
