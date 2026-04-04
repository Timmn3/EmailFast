# === Импорты ===
from aiogram.filters import ExceptionTypeFilter
from aiogram_dialog.api.exceptions import UnknownIntent, UnknownState
import asyncio
from aiogram import Dispatcher, F
from app.db.database import init_db
from app.dependencies import bot, ON_SCHEDULE, DB_NAME, FREE_EMAIL_PROVIDER
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, EVENT_JOB_EXECUTED
from app.handlers import (
    start_handler, affiliate_program, admin_handler, bot_handler,
    get_email_handler, receive_sms_handler, rent_number_handler, report, create_links,
    terms_handler
)
from app.handlers.health_check_router import health_check_router
from app.handlers.terms_middleware import TermsMiddleware
from app.services.keyboards import start_kb
from app.services.notify_admins import notify_wakeup_bot
from app.services.onlinesim.service_updater import add_services
from app.services.periodic_tasks import (
    check_sms, check_email, check_rental_email, close_expired_rental_email_leases,
    notify_rental_email_expiration, notify_rental_free_week_expiration, check_mail_expiration_and_notify,
    check_payment_freekassa, check_payment_anypay, check_payment_streampay,
    check_payment_ckassa, check_rent_sms, rents_ending_soon, close_rent,
    checking_inactive_rent, auto_renewal_of_rent, send_coder, check_payment_cryptomus, notify_week_expiration,
    refund_and_cleanup_expired_sms, check_fraud_balance_discrepancy, auto_fix_users_balance_discrepancy,
    check_free_firstmail
)
from app.services.ping_scheduler import userbot_ping
from app.services.set_bot_commands import set_default_commands
from app.services import stars_pay
from app.services.sms_fast.smsfast_price_loader import update_smsfast_prices
from logger_config import logger
from app.scheduler_instance import scheduler
from app.services import bot_texts as bt
from app.dependencies import dp
import signal
import logging

# Версия для отображения/отладки
msg_text = "Версия 04.04.2026"

from contextlib import suppress
from aiogram.types import Message, CallbackQuery

async def on_unknown_intent(
    event: Message | CallbackQuery,
    exception: Exception | None = None,
    error: Exception | None = None,
) -> None:
    # В разных версиях aiogram/aiogram_dialog ошибка может приходить как `error` или как `exception`
    exc = error or exception

    user = getattr(event, "from_user", None)
    user_id = getattr(user, "id", "unknown")

    logger.bind(user_id=user_id).log("USER_ACTION", "Неизвестный intent – возврат в главное меню")
    if exc:
        logger.opt(exception=exc).warning("UnknownIntent пойман обработчиком")

    # Если это callback — обязательно ответим, чтобы убрать “часики”
    if isinstance(event, CallbackQuery):
        with suppress(Exception):
            await event.answer("Кнопка устарела. Откройте меню заново.", show_alert=False)

        if event.message:
            await event.message.answer(text=bt.MAIN_MENU, reply_markup=start_kb(), parse_mode="HTML")
        return

    # Если это обычное сообщение
    if isinstance(event, Message):
        await event.answer(text=bt.MAIN_MENU, reply_markup=start_kb(), parse_mode="HTML")


async def on_unknown_state(
    event: Message | CallbackQuery,
    exception: Exception | None = None,
    error: Exception | None = None,
) -> None:
    exc = error or exception

    user = getattr(event, "from_user", None)
    user_id = getattr(user, "id", "unknown")

    logger.bind(user_id=user_id).log("USER_ACTION", "Неизвестный state – возврат в главное меню")
    if exc:
        logger.opt(exception=exc).warning("UnknownState пойман обработчиком")

    if isinstance(event, CallbackQuery):
        with suppress(Exception):
            await event.answer("Сессия устарела. Откройте меню заново.", show_alert=False)

        if event.message:
            await event.message.answer(text=bt.MAIN_MENU, reply_markup=start_kb(), parse_mode="HTML")
        return

    if isinstance(event, Message):
        await event.answer(text=bt.MAIN_MENU, reply_markup=start_kb(), parse_mode="HTML")



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
    dp.update.middleware(TermsMiddleware())

    main_routers = [
        admin_handler.router,
        report.router,
        terms_handler.router,
        start_handler.router,
        affiliate_program.router,
        get_email_handler.router,
        receive_sms_handler.router,
        rent_number_handler.router,
        create_links.router,
        health_check_router,
    ]

    # Регистрация глобальных обработчиков ошибок
    dp.errors.register(on_unknown_intent, ExceptionTypeFilter(UnknownIntent))
    dp.errors.register(on_unknown_state, ExceptionTypeFilter(UnknownState))

    # ✅ Глобальный гейт по пользовательскому соглашению
    dp.message.outer_middleware(TermsMiddleware())
    dp.callback_query.outer_middleware(TermsMiddleware())

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
    dp.message.register(stars_pay.successful_payment_handler, F.successful_payment)


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
    """
    Регистрирует задачи планировщика.

    Важно:
    - при FREE_EMAIL_PROVIDER="mail_tm" остаётся legacy scheduler для mail.tm;
    - при FREE_EMAIL_PROVIDER="firstmail" legacy mail.tm-задачи не регистрируем;
    - бесплатный FirstMail получает свой отдельный scheduler check_free_firstmail();
    - арендованный FirstMail scheduler check_rental_email() работает всегда.
    """
    try:
        # Проверка SMS
        scheduler.add_job(check_sms, "interval", seconds=30, max_instances=10)

        # Находит истёкшие активации, по которым не пришло СМС
        scheduler.add_job(
            refund_and_cleanup_expired_sms,
            "interval",
            seconds=20,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=10,
        )

        # Обновление цен SMSFast (кэш price_smsfast)
        scheduler.add_job(update_smsfast_prices, "interval", minutes=30, max_instances=1)

        if ON_SCHEDULE:
            # Проверка пользователей на пополнение и расходы (бан)
            scheduler.add_job(check_fraud_balance_discrepancy, "interval", minutes=30, max_instances=1)

            # Бесплатная почта: выбираем только один провайдер
            if FREE_EMAIL_PROVIDER == "firstmail":
                logger.info("Scheduler: включён бесплатный FirstMail, legacy mail.tm-задачи отключены")
                scheduler.add_job(
                    check_free_firstmail,
                    "interval",
                    seconds=60,
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=30,
                )
            else:
                logger.info("Scheduler: включён legacy mail.tm")

                # Проверка Email mail.tm
                scheduler.add_job(check_email, "interval", seconds=60, max_instances=3)

                # Проверка истечения срока почты и уведомления
                scheduler.add_job(
                    check_mail_expiration_and_notify,
                    "interval",
                    minutes=20,
                    max_instances=3,
                )

                # Проверка истечения срока почты арендованной на неделю
                scheduler.add_job(notify_week_expiration, "interval", minutes=10)

            # Проверка арендованных FirstMail-ящиков
            scheduler.add_job(
                check_rental_email,
                "interval",
                seconds=60,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

            # Автоосвобождение просроченных FirstMail-аренд
            scheduler.add_job(
                close_expired_rental_email_leases,
                "interval",
                minutes=10,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

            # Уведомление о завершении бесплатной недели FirstMail
            scheduler.add_job(
                notify_rental_free_week_expiration,
                "interval",
                minutes=10,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

            # Уведомление об истечении аренды FirstMail
            scheduler.add_job(
                notify_rental_email_expiration,
                "interval",
                minutes=10,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

            # Проверка платежей через CKassa
            scheduler.add_job(check_payment_ckassa, "interval", seconds=25, max_instances=1)

            # Проверка платежей через Streampay
            scheduler.add_job(check_payment_streampay, "interval", seconds=48, max_instances=10)

            # Проверка платежей через FreeKassa
            scheduler.add_job(check_payment_freekassa, "interval", seconds=43, max_instances=10)

            # Проверка платежей через Anypay
            scheduler.add_job(check_payment_anypay, "interval", seconds=60, max_instances=10)

            # Проверка платежей через cryptomus
            scheduler.add_job(check_payment_cryptomus, "interval", seconds=90, max_instances=10)

            # Добавление\обновление сервисов
            scheduler.add_job(add_services, "cron", hour=3, minute=0)

            # Пинг userbot
            scheduler.add_job(userbot_ping, "interval", seconds=300, max_instances=3)

            # Проверка арендованных SMS
            scheduler.add_job(check_rent_sms, "interval", seconds=55, max_instances=10)

            # Уведомление об аренде, которая скоро завершится
            scheduler.add_job(rents_ending_soon, "interval", minutes=10, max_instances=3)

            # Автопродление аренды за 2 часа до окончания
            scheduler.add_job(auto_renewal_of_rent, "interval", minutes=10, max_instances=3)

            # Завершение аренды
            scheduler.add_job(close_rent, "interval", minutes=10, max_instances=3)

            # Проверка незавершенных аренд
            scheduler.add_job(checking_inactive_rent, "interval", minutes=20, max_instances=3)
        else:
            logger.info(f"ON_SCHEDULE выключен ({ON_SCHEDULE})")

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


from aiogram.types import ErrorEvent


from aiogram.types import ErrorEvent


async def error_handler(
    event: ErrorEvent,
    exception: Exception | None = None,
    error: Exception | None = None,
) -> None:
    """
    Глобальный обработчик ошибок.

    В разных версиях aiogram ошибка может приходить:
    - как ErrorEvent (event.exception)
    - или как второй аргумент exception/error

    Важно: в проде НЕ пробрасываем исключение дальше, чтобы не “ронять” поллинг.
    """
    exc = getattr(event, "exception", None) or error or exception

    if exc and isinstance(exc, OutdatedIntent):
        logger.warning("Пойман OutdatedIntent — пользователь нажал устаревшую кнопку")
        return  # пропускаем без ошибок

    if exc:
        logger.opt(exception=exc).error("Необработанное исключение при обработке апдейта")
        return



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

