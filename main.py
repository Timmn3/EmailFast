# === Импорты ===
import socket
from aiogram.filters import ExceptionTypeFilter
from aiogram_dialog.api.exceptions import UnknownIntent, UnknownState
import asyncio
from aiogram import Dispatcher, F
from app.db.database import init_db
from app.dependencies import bot, ON_SCHEDULE, DB_NAME, FREE_EMAIL_PROVIDER
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from app.handlers import (
    start_handler, affiliate_program, admin_handler, bot_handler,
    get_email_handler, receive_sms_handler, rent_number_handler, report, create_links,
    terms_handler
)
from app.handlers import notifications_handler
from app.handlers.health_check_router import health_check_router
from app.handlers.terms_middleware import TermsMiddleware
from app.services.keyboards import start_kb, send_main_menu
from app.services.notify_admins import notify_wakeup_bot
from app.services.onlinesim.service_updater import add_services
from app.services.periodic_tasks import (
    check_sms, check_email, check_rental_email, close_expired_rental_email_leases,
    notify_rental_email_expiration, notify_rental_free_week_expiration, check_mail_expiration_and_notify,
    check_payment_freekassa, check_payment_anypay, check_payment_streampay,
    check_payment_ckassa, check_payment_ckassa_backlog, check_rent_sms, rents_ending_soon, close_rent,
    checking_inactive_rent, auto_renewal_of_rent, send_coder, check_payment_cryptomus, notify_week_expiration,
    refund_and_cleanup_expired_sms, watch_refund_backlog, check_fraud_balance_discrepancy, auto_fix_users_balance_discrepancy,
    check_free_firstmail, check_free_firstmail_idle, notify_inactive_users, notify_unpaid_sms_payments,
    guard_job, JOB_TIMEOUT_FAST, JOB_TIMEOUT_NORMAL, JOB_TIMEOUT_SLOW, JOB_TIMEOUT_BULK,
    JOB_TIMEOUT_FIRSTMAIL_IDLE, JOB_TIMEOUT_SMSFAST_PRICES, report_job_durations,
)
from app.services.set_bot_commands import set_default_commands
from app.services import stars_pay
from app.services.sms_fast.smsfast_price_loader import update_smsfast_prices
from logger_config import logger
from app.scheduler_instance import scheduler
from app.services import bot_texts as bt
from app.dependencies import dp
import signal
import logging
import time

# Версия для отображения/отладки
msg_text = "Версия 24.06.2026"

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
            await send_main_menu(event.message, bt.MAIN_MENU, parse_mode="HTML")
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
            await send_main_menu(event.message, bt.MAIN_MENU, parse_mode="HTML")
        return

    if isinstance(event, Message):
        await send_main_menu(event, bt.MAIN_MENU, parse_mode="HTML")



# === Слушатель задач планировщика ===
def job_listener(event):
    """
    Listener для фоновых задач APScheduler.

    Логируем только проблемы: исключения (EVENT_JOB_ERROR) и пропуски
    (EVENT_JOB_MISSED). Успешные запуски не логируем — десятки задач
    раз в минуту засорили бы логи и скрыли реальные ошибки.
    """
    if event.code == EVENT_JOB_ERROR:
        logger.error(
            f"Фоновая задача '{event.job_id}' завершилась с ошибкой: "
            f"{event.exception!r}\n{event.traceback}"
        )
    elif event.code == EVENT_JOB_MISSED:
        logger.warning(
            f"Фоновая задача '{event.job_id}' пропущена "
            f"(scheduled_run_time={event.scheduled_run_time})"
        )


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
        notifications_handler.router,
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
    scheduler.add_listener(job_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    if not scheduler.running:
        scheduler.start()

    logger.info(DB_NAME)
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        _server_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        _server_ip = "unknown"
    startup_msg = (
        f"🚀 <b>{msg_text}</b>\n"
        f"──────────────\n"
        f"📋 <b>ON_SCHEDULE:</b> {'✅ включён' if ON_SCHEDULE else '❌ выключен'}\n"
        f"🗄 <b>БД:</b> <code>{DB_NAME}</code>\n"
        f"🖥 <b>Сервер:</b> <code>{_server_ip}</code>"
    )
    await send_coder(startup_msg)

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
        if ON_SCHEDULE:
            # Проверка SMS
            scheduler.add_job(guard_job(check_sms, JOB_TIMEOUT_NORMAL), "interval", seconds=30, max_instances=10)

            # Находит истёкшие активации, по которым не пришло СМС
            scheduler.add_job(
                refund_and_cleanup_expired_sms,
                "interval",
                seconds=20,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=10,
            )

            # Отчёт о фактических длительностях проходов: по нему видно,
            # какой потолок задан слишком туго, а какой можно опустить
            scheduler.add_job(
                guard_job(report_job_durations, JOB_TIMEOUT_FAST),
                "interval",
                hours=1,
                max_instances=1,
                coalesce=True,
            )

            # Сторож: деньги за неполученные SMS реально возвращаются пользователям
            scheduler.add_job(
                guard_job(watch_refund_backlog, JOB_TIMEOUT_FAST),
                "interval",
                minutes=15,
                max_instances=1,
                coalesce=True,
            )

            # Обновление цен SMSFast (кэш price_smsfast)
            scheduler.add_job(guard_job(update_smsfast_prices, JOB_TIMEOUT_SMSFAST_PRICES), "interval", minutes=30, max_instances=1)
                
            # Проверка пользователей на пополнение и расходы (бан)
            scheduler.add_job(guard_job(check_fraud_balance_discrepancy, JOB_TIMEOUT_NORMAL), "interval", minutes=30, max_instances=1)

            # Бесплатная почта: выбираем только один провайдер
            if FREE_EMAIL_PROVIDER == "firstmail":
                logger.info("Scheduler: включён бесплатный FirstMail, legacy mail.tm-задачи отключены")
                scheduler.add_job(
                    guard_job(check_free_firstmail, JOB_TIMEOUT_SLOW),
                    "interval",
                    seconds=60,
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=30,
                )

                # Ящики «спящих» пользователей — редко и отдельной задачей,
                # чтобы не тормозить проверку тех, кто сейчас в боте
                scheduler.add_job(
                    guard_job(check_free_firstmail_idle, JOB_TIMEOUT_FIRSTMAIL_IDLE),
                    "interval",
                    minutes=15,
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=60,
                )
            else:
                logger.info("Scheduler: включён legacy mail.tm")

                # Проверка Email mail.tm
                scheduler.add_job(guard_job(check_email, JOB_TIMEOUT_NORMAL), "interval", seconds=60, max_instances=3)

                # Проверка истечения срока почты и уведомления
                scheduler.add_job(
                    guard_job(check_mail_expiration_and_notify, JOB_TIMEOUT_NORMAL),
                    "interval",
                    minutes=20,
                    max_instances=3,
                )

                # Проверка истечения срока почты арендованной на неделю
                scheduler.add_job(guard_job(notify_week_expiration, JOB_TIMEOUT_NORMAL), "interval", minutes=10)

            # Проверка арендованных FirstMail-ящиков
            scheduler.add_job(
                guard_job(check_rental_email, JOB_TIMEOUT_SLOW),
                "interval",
                seconds=60,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

            # Автоосвобождение просроченных FirstMail-аренд
            scheduler.add_job(
                guard_job(close_expired_rental_email_leases, JOB_TIMEOUT_NORMAL),
                "interval",
                minutes=10,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

            # Уведомление о завершении бесплатной недели FirstMail
            scheduler.add_job(
                guard_job(notify_rental_free_week_expiration, JOB_TIMEOUT_NORMAL),
                "interval",
                minutes=10,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

            # Уведомление об истечении аренды FirstMail
            scheduler.add_job(
                guard_job(notify_rental_email_expiration, JOB_TIMEOUT_NORMAL),
                "interval",
                minutes=10,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )

            # Проверка платежей через CKassa
            scheduler.add_job(check_payment_ckassa, "interval", seconds=25, max_instances=1)

            # Добор оплат CKassa за 48 часов: подхватывает платежи, пропущенные
            # основным проходом, пока сервис статусов был недоступен
            scheduler.add_job(
                check_payment_ckassa_backlog,
                "interval",
                minutes=10,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Проверка платежей через Streampay
            scheduler.add_job(guard_job(check_payment_streampay, JOB_TIMEOUT_NORMAL), "interval", seconds=48, max_instances=10)

            # Проверка платежей через FreeKassa
            scheduler.add_job(guard_job(check_payment_freekassa, JOB_TIMEOUT_NORMAL), "interval", seconds=43, max_instances=10)

            # Проверка платежей через Anypay
            scheduler.add_job(guard_job(check_payment_anypay, JOB_TIMEOUT_NORMAL), "interval", seconds=60, max_instances=10)

            # Проверка платежей через cryptomus
            scheduler.add_job(guard_job(check_payment_cryptomus, JOB_TIMEOUT_NORMAL), "interval", seconds=90, max_instances=10)

            # Добавление\обновление сервисов
            scheduler.add_job(guard_job(add_services, JOB_TIMEOUT_BULK), "cron", hour=3, minute=0)

            # Проверка арендованных SMS
            scheduler.add_job(guard_job(check_rent_sms, JOB_TIMEOUT_NORMAL), "interval", seconds=55, max_instances=10)

            # Уведомление об аренде, которая скоро завершится
            scheduler.add_job(guard_job(rents_ending_soon, JOB_TIMEOUT_NORMAL), "interval", minutes=10, max_instances=3)

            # Автопродление аренды за 2 часа до окончания
            scheduler.add_job(guard_job(auto_renewal_of_rent, JOB_TIMEOUT_NORMAL), "interval", minutes=10, max_instances=3)

            # Завершение аренды
            scheduler.add_job(guard_job(close_rent, JOB_TIMEOUT_NORMAL), "interval", minutes=10, max_instances=3)

            # Проверка незавершенных аренд
            scheduler.add_job(guard_job(checking_inactive_rent, JOB_TIMEOUT_NORMAL), "interval", minutes=20, max_instances=3)

            # Уведомление неактивным пользователям (30+ дней без активности → скидка 15%)
            scheduler.add_job(
                guard_job(notify_inactive_users, JOB_TIMEOUT_BULK),
                "interval",
                hours=6,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )

            # Напоминание о незавершённой оплате SMS-номера (через 3 часа после создания счёта)
            scheduler.add_job(
                guard_job(notify_unpaid_sms_payments, JOB_TIMEOUT_NORMAL),
                "interval",
                minutes=5,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
        else:
            logger.info(f"ON_SCHEDULE выключен ({ON_SCHEDULE})")

    except Exception as e:
        logger.opt(exception=e).error("Ошибка при добавлении задач в планировщик")
# === Фильтры для подавления лишних логов apscheduler ===
class SkipSpecificLogFilter(logging.Filter):
    """
    Гасит спам APScheduler о пропуске запуска (max_instances), но раз в
    SUMMARY_INTERVAL выпускает по каждой задаче сводку.

    Полное молчание опасно: для такого пропуска APScheduler не шлёт событий,
    и эта запись в логе — единственный признак того, что предыдущий проход
    ещё выполняется. 17.08.2026 задача возвратов подвисла, все её пропуски
    гасились здесь, и авария оставалась невидимой трое суток.

    Регулярные пропуски бывают и штатно: скан спящих FirstMail-ящиков идёт
    дольше своего интервала. Поэтому не выключаем подавление совсем, а
    сжимаем поток в редкую сводку.
    """

    SUMMARY_INTERVAL = 600

    def __init__(self):
        super().__init__()
        self._skips = {}          # задача -> сколько пропусков накопилось
        self._last_summary = {}   # задача -> когда последний раз отчитывались

    @staticmethod
    def _job_name(message: str) -> str:
        """Достаёт имя задачи из 'Execution of job "<name> (trigger: ...)" skipped: ...'."""
        try:
            return message.split('job "', 1)[1].split(" (trigger", 1)[0]
        except Exception:
            return "неизвестная"

    def filter(self, record):
        message = record.getMessage()
        if not (
            "Execution of job" in message and
            "skipped: maximum number of running instances reached" in message
        ):
            return True

        job = self._job_name(message)
        self._skips[job] = self._skips.get(job, 0) + 1

        now = time.monotonic()
        last = self._last_summary.get(job)
        if last is None or now - last >= self.SUMMARY_INTERVAL:
            skipped = self._skips[job]
            za = f" за {int(now - last)}с" if last is not None else ""
            logger.warning(
                f"Фоновая задача '{job}' — пропущено запусков: {skipped}{za}, "
                f"предыдущий проход ещё выполняется"
            )
            self._skips[job] = 0
            self._last_summary[job] = now

        return False


class MissedJobLogFilter(logging.Filter):
    def filter(self, record):
        return "Job" not in record.getMessage() or "was missed" not in record.getMessage()


class CancelledErrorFilter(logging.Filter):
    """Подавляет трейсбеки CancelledError при штатном завершении."""
    def filter(self, record):
        if record.exc_info and record.exc_info[0] is not None:
            if issubclass(record.exc_info[0], asyncio.CancelledError):
                return False
        msg = record.getMessage()
        if "Executor shutdown has been called" in msg:
            return False
        return True


# === Обработка SIGTERM ===
def shutdown_scheduler(scheduler):
    logger.info("Остановка планировщика...")
    scheduler.shutdown(wait=False)


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
        executors_logger = logging.getLogger('apscheduler.executors.default')
        executors_logger.setLevel(logging.WARNING)
        executors_logger.addFilter(CancelledErrorFilter())
        handler = logging.StreamHandler()
        handler.addFilter(SkipSpecificLogFilter())
        handler.addFilter(MissedJobLogFilter())
        handler.addFilter(CancelledErrorFilter())
        aps_logger.addHandler(handler)

        asyncio.run(main(dp))

    except KeyboardInterrupt:
        logger.info("Бот остановлен")
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception as e:
        logger.opt(exception=e).critical(f'Критическая ошибка в main: {e}')

