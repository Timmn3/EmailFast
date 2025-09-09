# === Импорты ===
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import Message
from aiogram_dialog.api.exceptions import UnknownIntent, UnknownState
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
from aiogram import Dispatcher
from app.db.database import init_db
from app.dependencies import bot, ON_SCHEDULE, DB_NAME
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, EVENT_JOB_EXECUTED
from app.dialogs.bot_menu.states import BotMenu
from app.handlers import (
    start_handler, affiliate_program, admin_handler, bot_handler,
    get_email_handler, receive_sms_handler, rent_number_handler, report
)
from app.handlers.health_check_router import health_check_router
from app.services.keyboards import start_kb
from app.services.notify_admins import notify_wakeup_bot
from app.services.onlinesim.service_updater import add_services
from app.services.periodic_tasks import (
    check_sms, check_email, check_payment_lava, check_mail_expiration_and_notify,
    check_payment_freekassa, check_payment_yoomoney, check_payment_anypay, check_payment_streampay,
    check_payment_ckassa, check_rent_sms, rents_ending_soon, close_rent,
    checking_inactive_rent, auto_renewal_of_rent, send_coder, check_payment_cryptomus, notify_week_expiration
)
from app.services.ping_scheduler import userbot_ping
from app.services.set_bot_commands import set_default_commands
from app.services import stars_pay
from logger_config import logger
from app.scheduler_instance import scheduler
from app.services import bot_texts as bt
from app.dependencies import dp
import signal
import logging

# Версия для отображения/отладки
msg_text = "Версия 09.09.2025"



async def on_unknown_intent(event, exception):
    user_id = getattr(event.from_user, 'id', 'unknown') if isinstance(event, Message) else 'unknown'
    logger.bind(user_id=user_id).log("USER_ACTION", "Неизвестный intent – возврат в главное меню")

    if isinstance(event, Message):
        await event.answer(text=bt.MAIN_MENU, reply_markup=start_kb())


async def on_unknown_state(event):
    user_id = getattr(event.from_user, 'id', 'unknown') if isinstance(event, Message) else 'unknown'
    logger.bind(user_id=user_id).log("USER_ACTION", "Неизвестный state – возврат в главное меню")

    if isinstance(event, Message):
        await event.answer(text=bt.MAIN_MENU, reply_markup=start_kb())



# === Слушатель задач планировщика ===
def job_listener(event):
    """
    Listener для обработки ошибок, выполнения и пропусков задач.
    """
    pass
    # if event.code == EVENT_JOB_ERROR:
    #     logger.error(f"Задача {event.job_id} вызвала исключение: {event.exception}")
    # elif event.code == EVENT_JOB_MISSED:
    #     logger.warning(f"Задача {event.job_id} была пропущена в {event.scheduled_run_time}")
    # elif event.code == EVENT_JOB_EXECUTED:
    #     logger.log("SUCCESS", f"Задача {event.job_id} успешно выполнена в {event.scheduled_run_time}")


# === Основной запуск бота ===
async def main(dp: Dispatcher):
    """
    Основная функция запуска бота и планировщика.
    """
    logger.success("Запуск Telegram-бота")

    main_routers = [
        admin_handler.router,
        report.router,
        start_handler.router,
        affiliate_program.router,
        health_check_router,
        get_email_handler.router,
        receive_sms_handler.router,
        rent_number_handler.router,
    ]

    # Регистрация глобальных обработчиков ошибок
    dp.errors.register(on_unknown_intent, ExceptionTypeFilter(UnknownIntent))
    dp.errors.register(on_unknown_state, ExceptionTypeFilter(UnknownState))

    # Подключение диалогов и роутеров
    from app.dialogs import setup_dialogs
    dp.include_routers(bot_handler.router, *main_routers)
    logger.success("Роутеры и диалоги зарегистрированы")

    setup_dialogs(dp)

    # Команды для Telegram-бота
    await set_default_commands(bot)
    logger.success("Установлены команды по умолчанию")

    # Инициализация базы данных
    await init_db()
    logger.success("Инициализация базы данных завершена")

    # Уведомление администратора о запуске
    await notify_wakeup_bot(bot)
    logger.success("Бот сообщил о пробуждении")

    # Регистрация обработчика оплаты
    dp.pre_checkout_query.register(stars_pay.pre_checkout_handler)

    dp.errors.register(error_handler)

    # Планировщик задач
    set_scheduled_jobs(scheduler)
    scheduler.add_listener(job_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_EXECUTED)
    if not scheduler.running:
        scheduler.start()

    logger.info(DB_NAME)
    # Прочие задачи перед polling
    await userbot_ping()
    await send_coder(msg_text)

    # Старт polling
    await dp.start_polling(bot)


# === Планировщик задач ===
def set_scheduled_jobs(scheduler):
    try:
        if ON_SCHEDULE:
            # Проверка SMS
            scheduler.add_job(check_sms, "interval", seconds=10, max_instances=10)
            # Проверка Email
            scheduler.add_job(check_email, "interval", seconds=30, max_instances=3)
            # Проверка платежей через CKassa
            scheduler.add_job(check_payment_ckassa, "interval", seconds=25, max_instances=10)
            # Проверка платежей через Streampay
            scheduler.add_job(check_payment_streampay, "interval", seconds=28, max_instances=10)
            # Проверка платежей через FreeKassa
            scheduler.add_job(check_payment_freekassa, "interval", seconds=33, max_instances=10)
            # Проверка платежей через Anypay
            scheduler.add_job(check_payment_anypay, "interval", seconds=45, max_instances=10)
            # Проверка платежей через cryptomus
            scheduler.add_job(check_payment_cryptomus, "interval", seconds=50, max_instances=10)
            # Добавление\обновление сервисов
            scheduler.add_job(add_services, "cron", hour=3, minute=0)
            # Пинг userbot
            scheduler.add_job(userbot_ping, "interval", seconds=300, max_instances=3)
            # Проверка истечения срока почты и уведомления
            scheduler.add_job(check_mail_expiration_and_notify, "interval", minutes=20, max_instances=3)
            # Проверка истечения срока почты арендованной на неделю
            scheduler.add_job(notify_week_expiration, "interval", minutes=10)
            # Проверка арендованных SMS
            scheduler.add_job(check_rent_sms, "interval", seconds=35, max_instances=10)
            # Уведомление об аренде, которая скоро завершится
            scheduler.add_job(rents_ending_soon, "interval", minutes=1, max_instances=3)
            # Автопродление аренды за 2 часа до окончания
            scheduler.add_job(auto_renewal_of_rent, "interval", minutes=1, max_instances=3)
            # Завершение аренды
            scheduler.add_job(close_rent, "interval", minutes=1, max_instances=3)
            # Проверка незавершенных аренд
            scheduler.add_job(checking_inactive_rent, "interval", minutes=20, max_instances=3)
        else:
            logger.info(f'ON_SCHEDULE выключен ({ON_SCHEDULE})')
    except Exception as e:
        logger.opt(exception=e).error("Ошибка при добавлении задач в планировщик")


# === Фильтры для подавления лишних логов apscheduler ===
class SkipSpecificLogFilter(logging.Filter):
    def filter(self, record):
        return not (
            "Execution of job" in record.getMessage() and
            "skipped: maximum number of running instances reached" in record.getMessage()
        )


class MissedJobLogFilter(logging.Filter):
    def filter(self, record):
        return "Job" not in record.getMessage() or "was missed" not in record.getMessage()


# === Обработка SIGTERM ===
def shutdown_scheduler(scheduler):
    logger.info("Остановка планировщика...")
    scheduler.shutdown()


signal.signal(signal.SIGTERM, lambda *args: shutdown_scheduler(scheduler))


async def error_handler(event, exception):
    if isinstance(exception, OutdatedIntent):
        logger.warning("Пойман OutdatedIntent — пользователь нажал устаревшую кнопку")
        return  # пропускаем без ошибок
    raise exception  # пробрасываем остальные исключения дальше


# === Точка входа ===
if __name__ == '__main__':
    try:

        from aiogram_dialog.api.exceptions import OutdatedIntent  # импорт нужного исключения

        logger.success("=== Старт main.py ===")



        # Подавление лишних логов apscheduler
        aps_logger = logging.getLogger('apscheduler')
        aps_logger.setLevel(logging.WARNING)
        logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)
        handler = logging.StreamHandler()
        handler.addFilter(SkipSpecificLogFilter())
        handler.addFilter(MissedJobLogFilter())
        aps_logger.addHandler(handler)

        asyncio.run(main(dp))

    except Exception as e:
        logger.opt(exception=e).critical(f'Критическая ошибка в main: {e}')

