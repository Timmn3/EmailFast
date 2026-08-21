import json
import time
import functools
from math import floor
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import types
from aiohttp import ClientSession
from app.services.onlinesim.sms_client import OnlineSMS
from tortoise.functions import Sum
import html
from tortoise import timezone
from tortoise import Tortoise
from loguru import logger
from app import dependencies
from app.db import models
from app.dependencies import bot, FK_SHOP_ID, FK_FK_API_KEY, CODER, API_KEY_ONLINESIM, PROJECT_MANAGER
from app.dialogs.receive_sms.getters import service_is_smsactivate
from app.dialogs.rent_sms.getters import get_day_string
from app.handlers.get_email_handler import get_extend_email_kb
from app.services.bot_texts import country_flags
from app.services.mail.firstmail_notify import send_firstmail_message
from app.services.mail.receive_messages import get_unread_messages
from app.services.onlinesim.rent_number import OnlineSimRentAPI
from app.services.payments.anypay import AnypayAPI
from app.services.payments.ckassa import CkassaUnavailable, get_ckassa_payments
from app.services.rental_email_pool import (
    pull_free_firstmail_messages,
    pull_rental_email_messages,
)
from app.services.payments.cryptomus import get_paid_order_ids
from app.services.payments.freekassa import Freekassa
from app.services.payments.lava import LavaApi
from app.services.payments.streampay import get_payment_status_streampay
from app.services.sms_fast.smsfast_client import get_smsfast_client
from app.services.sms_receive import SmsReceive
from app.services.temp_mail import TempMail
from app.services import bot_texts as bt
import pytz
import datetime
from app.services.rental_email_pool import release_rental_email_lease
from app.services.payments.yoomoney import check_payment_status

from tortoise import timezone
from tortoise.transactions import in_transaction

from app.db import models
from app.db.models import Activation, StatusResponse
from app.dependencies import bot, FREE_FIRSTMAIL_ACTIVE_DAYS, FREE_FIRSTMAIL_IDLE_DAYS


# === Потолок времени для фоновых задач ===
#
# Задачи APScheduler по умолчанию идут с max_instances=1, и при зависшем проходе
# планировщик молча перестаёт запускать новые: события для такого пропуска он не
# шлёт, а единственную запись в лог гасит фильтр в main.py. Задача умирает
# беззвучно до перезапуска бота — так 16.08.2026 встали зачисления CKassa, а
# 17.08.2026 на трое суток встали возвраты за неполученные SMS (140 активаций,
# 10 233 руб, 102 человека; вскрылось только через жалобы).
#
# Таймауты подобраны заведомо больше нормального времени прохода: задача —
# не оборвать медленный, но живой проход, а не дать зависшему висеть вечно.
# Прерывание безопасно: проходы идемпотентны и продолжат со следующего запуска.

JOB_TIMEOUT_FAST = 120      # пара запросов в БД + одиночные сообщения
JOB_TIMEOUT_NORMAL = 600    # проход по списку с сетевым вызовом на каждом шаге
JOB_TIMEOUT_SLOW = 900      # IMAP-обходы ящиков
JOB_TIMEOUT_BULK = 3600     # массовые рассылки по всей базе

# Отдельно для скана «спящих» FirstMail-ящиков: он легально идёт очень долго
# (зафиксированный максимум — 3700с), а _scan_free_firstmail всегда обходит
# список с начала. Слишком тугой потолок срезал бы хвост списка, и ящики в
# конце перестали бы проверяться совсем. Берём запас вдвое.
JOB_TIMEOUT_FIRSTMAIL_IDLE = 7200

# Отдельно для обновления цен SMSFast: задача обходит 202 страны, делая на каждую
# отдельный запрос getPrices, и упирается в рейт-лимитер клиента — 1 запрос в
# секунду. После перевода записи на пачечный upsert полный проход занимает около
# 220с (замер на бою 21.08.2026), потолок держим с запасом примерно восьмикратным.
#
# Историческая справка: до пачечной записи проход шёл около двух часов, и
# потолок в 600с обрывал его на 15-й стране — цены остальных 187 переставали
# обновляться совсем.
JOB_TIMEOUT_SMSFAST_PRICES = 1800

# Отдельно для обновления справочников OnlineSim: замер на бою 21.08.2026 дал
# 2969с — 82% от прежнего потолка BULK. Задача пишет цены поштучно (UPDATE, а
# при промахе CREATE на каждый сервис), и при RTT до БД около 40мс это тянется
# почти час. Пока потолок с запасом; по-хорошему запись надо перевести на
# пачечный upsert, как уже сделано для цен SMSFast.
JOB_TIMEOUT_SERVICES_SYNC = 7200


# Фактические длительности проходов. Нужны, чтобы потолки калибровались по
# замерам, а не по догадкам: 20.08.2026 потолок для update_smsfast_prices был
# занижен втрое именно потому, что реальную длительность никто не мерил, и
# обновление цен молча обрывалось на 15-й стране из 202.
_job_durations: dict = {}


def _record_job_duration(name: str, elapsed: float, timed_out: bool = False) -> None:
    """Копит статистику по одной задаче; отчёт печатает report_job_durations."""
    stat = _job_durations.setdefault(
        name, {"runs": 0, "max": 0.0, "total": 0.0, "timeouts": 0}
    )
    if timed_out:
        stat["timeouts"] += 1
        return
    stat["runs"] += 1
    stat["total"] += elapsed
    stat["max"] = max(stat["max"], elapsed)


async def report_job_durations() -> None:
    """
    Раз в час пишет в лог, сколько на самом деле идут проходы фоновых задач.

    Одна строка на задачу: максимум, среднее, число проходов и обрывов. По ней
    видно, какой потолок стоит слишком туго, а какой можно опустить.
    """
    if not _job_durations:
        return

    snapshot = _job_durations.copy()
    _job_durations.clear()

    for name, stat in sorted(snapshot.items(), key=lambda kv: -kv[1]["max"]):
        runs = stat["runs"]
        average = stat["total"] / runs if runs else 0.0
        oborvano = f", обрывов {stat['timeouts']}" if stat["timeouts"] else ""
        logger.info(
            f"Длительность проходов | {name}: макс {stat['max']:.1f}с, "
            f"средн {average:.1f}с, проходов {runs}{oborvano}"
        )


def guard_job(func, timeout: float):
    """
    Оборачивает фоновую задачу потолком времени выполнения.

    Обёртка, а не правка тела каждой задачи: логика задач не трогается, потолок
    ставится единообразно в одном месте при регистрации в планировщике.

    :param func: корутинная функция-задача (вызывается без аргументов)
    :param timeout: потолок на один проход в секундах
    :return: обёрнутая корутинная функция с тем же __name__
    """

    # wraps обязателен: APScheduler берёт имя задачи из __name__ функции и
    # подставляет его в свои сообщения ("Execution of job ... skipped"). Без
    # wraps все задачи стали бы одинаковыми "guarded", и сводки о пропусках
    # в main.py перестали бы показывать, какая именно задача залипла.
    @functools.wraps(func)
    async def guarded() -> None:
        started = time.monotonic()
        try:
            await asyncio.wait_for(func(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            logger.error(
                f"Фоновая задача '{func.__name__}' прервана по таймауту {timeout:.0f}с — "
                f"проход завис, следующий запуск продолжит"
            )
            _record_job_duration(func.__name__, timeout, timed_out=True)
            return
        except asyncio.CancelledError:
            return
        # Прочие исключения намеренно не ловим: их логирует job_listener в main.py

        elapsed = time.monotonic() - started
        _record_job_duration(func.__name__, elapsed)

        # Раннее предупреждение о том, что потолок задан слишком туго: пока проход
        # укладывается, но подобрался к лимиту. Лучше увидеть это в логах заранее,
        # чем обнаружить по оборванным проходам.
        if elapsed > timeout / 2:
            logger.warning(
                f"Фоновая задача '{func.__name__}' шла {elapsed:.0f}с при потолке "
                f"{timeout:.0f}с — потолок стоит поднять"
            )

    return guarded


# Потолок на весь проход. Джоба зарегистрирована с max_instances=1: если проход
# зависнет (подвисший HTTP к Telegram при удалении сообщения или отправке
# уведомления), новые запуски молча перестают стартовать и возвраты встают
# насмерть — ровно так 17.08.2026 они прекратились и не работали трое суток,
# оставив 140 активаций на 10 241 ₽ без возврата.
REFUND_RUN_TIMEOUT = 240

# Потолок на один вызов Telegram: один недоступный чат не должен съесть проход.
REFUND_TELEGRAM_CALL_TIMEOUT = 20

# Максимум активаций за проход. Ограничивает время прохода на большом
# накопившемся хвосте; остаток разберут следующие запуски.
REFUND_BATCH_LIMIT = 200


async def refund_and_cleanup_expired_sms() -> None:
    """
    Находит истёкшие активации, по которым не пришло СМС (WAIT_CODE),
    удаляет сервисное сообщение, возвращает деньги и уведомляет пользователя.

    Идемпотентность:
    - Лочим строку Activation через SELECT FOR UPDATE
    - Внутри транзакции повторно проверяем status/expiry/sms_text
    - Переводим в CANCEL и возвращаем деньги строго один раз
    """
    try:
        await asyncio.wait_for(
            _refund_and_cleanup_expired_sms_impl(),
            timeout=REFUND_RUN_TIMEOUT,
        )
    except (TimeoutError, asyncio.TimeoutError):
        # Сюда попадают и таймаут подключения к БД, и прерывание всего прохода.
        # Возвраты идемпотентны (лок + повторная проверка статуса под ним),
        # поэтому обрыв безопасен: следующий проход продолжит с того же места.
        logger.warning(
            f"refund_and_cleanup_expired_sms: проход прерван по таймауту "
            f"({REFUND_RUN_TIMEOUT}с) либо БД недоступна, следующий проход продолжит"
        )
    except asyncio.CancelledError:
        pass


async def _refund_and_cleanup_expired_sms_impl() -> None:
    now = timezone.now()

    # Берём только ID (чтобы не тащить user relation и не работать со "старыми" объектами)
    # Самые старые первыми: по ним деньги пользователя висят дольше всего.
    expired_ids = await (
        Activation.filter(
            activation_expire_at__lte=now,
            status=StatusResponse.STATUS_WAIT_CODE,
        )
        .order_by("activation_expire_at")
        .limit(REFUND_BATCH_LIMIT)
        .values_list("id", flat=True)
    )

    if not expired_ids:
        return

    for activation_pk in expired_ids:
        try:
            did_refund = False
            user_tg_id: int | None = None
            service_msg_id: int | None = None
            phone_number: str | None = None
            cost: float = 0.0
            ext_activation_id: int | None = None

            async with in_transaction() as conn:
                # 🔒 Лочим активацию
                act = await Activation.filter(id=activation_pk).using_db(conn).select_for_update().first()
                if not act:
                    continue

                # ♻️ Идемпотентность: если уже CANCEL — значит отменили вручную или ранее авто-рефандом
                if act.status == StatusResponse.STATUS_CANCEL:
                    continue

                # Повторный чек условий уже "под замком"
                if act.status != StatusResponse.STATUS_WAIT_CODE:
                    continue

                # если expire_at по какой-то причине NULL — не трогаем
                if not act.activation_expire_at or act.activation_expire_at > now:
                    continue

                # если СМС уже есть — не рефандим
                sms_text = (getattr(act, "sms_text", None) or "").strip()
                if sms_text:
                    continue

                if not getattr(act, "user_id", None):
                    # на всякий — просто закрываем активацию, но без рефанда
                    act.status = StatusResponse.STATUS_CANCEL
                    await act.save(using_db=conn, update_fields=["status"])
                    continue

                # 🔒 Лочим пользователя
                user = await models.User.filter(id=act.user_id).using_db(conn).select_for_update().first()
                if not user:
                    act.status = StatusResponse.STATUS_CANCEL
                    await act.save(using_db=conn, update_fields=["status"])
                    continue

                cost = float(getattr(act, "cost", 0.0) or 0.0)

                user.balance = float(getattr(user, "balance", 0.0) or 0.0) + cost
                await user.save(using_db=conn, update_fields=["balance"])

                act.status = StatusResponse.STATUS_CANCEL
                await act.save(using_db=conn, update_fields=["status"])

                # сохраняем данные для действий после коммита
                did_refund = True
                user_tg_id = int(getattr(user, "telegram_id", 0) or 0) or None
                service_msg_id = getattr(act, "service_msg_id", None)
                phone_number = getattr(act, "phone_number", None)
                ext_activation_id = getattr(act, "activation_id", None)

            if not did_refund or not user_tg_id:
                continue

            # 1) Пытаемся удалить выданное ранее сообщение с номером
            if service_msg_id:
                try:
                    await asyncio.wait_for(
                        bot.delete_message(chat_id=user_tg_id, message_id=service_msg_id),
                        timeout=REFUND_TELEGRAM_CALL_TIMEOUT,
                    )
                    logger.info(
                        f"🗑 Удалено сервисное сообщение: activation_id={ext_activation_id}, msg_id={service_msg_id}"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение activation_id={ext_activation_id}: {e}")

            # 2) Уведомление пользователю
            try:
                await asyncio.wait_for(
                    bot.send_message(
                        chat_id=user_tg_id,
                        text=bt.SMS_NOT_RECEIVED,
                        parse_mode="HTML",
                    ),
                    timeout=REFUND_TELEGRAM_CALL_TIMEOUT,
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление пользователю {user_tg_id}: {e}")

            logger.bind(user_id=user_tg_id, action="refund_activation").log(
                "USER_ACTION",
                f"Возврат средств за истёкшую активацию: id={ext_activation_id}, номер={phone_number}, сумма={cost}₽"
            )

        except Exception as e:
            logger.opt(exception=e).error(f"Ошибка при обработке истёкшей активации pk={activation_pk}")


# Сколько активация может висеть просроченной, прежде чем это считается аварией.
# Активация живёт 14 минут, джоба ходит раз в 20 секунд, поэтому в норме возврат
# случается в пределах минуты после истечения. 30 минут — заведомо ненормально.
REFUND_WATCHDOG_STALE_MINUTES = 30

# Не чаще одного алерта в час, чтобы авария не превратилась в спам.
REFUND_WATCHDOG_ALERT_COOLDOWN = 3600

_refund_watchdog_last_alert = 0.0


async def watch_refund_backlog() -> None:
    """
    Сторож автовозвратов: следит, что деньги за неполученные SMS реально уходят
    обратно пользователям.

    Зачем: 17.08.2026 джоба возвратов молча зависла и не работала трое суток —
    140 активаций на 10 241 ₽ остались без возврата, и вскрылось это только через
    жалобы пользователей. Потолок на проход закрывает ту конкретную причину, а
    сторож ловит любую следующую: он смотрит не на джобу, а на результат её работы.
    """
    global _refund_watchdog_last_alert

    try:
        threshold = timezone.now() - datetime.timedelta(minutes=REFUND_WATCHDOG_STALE_MINUTES)
        stale_costs = await Activation.filter(
            activation_expire_at__lte=threshold,
            status=StatusResponse.STATUS_WAIT_CODE,
        ).values_list("cost", flat=True)

        if not stale_costs:
            return

        count = len(stale_costs)
        money = round(sum(float(c or 0.0) for c in stale_costs), 2)

        logger.error(
            f"Сторож возвратов: {count} активаций на {money}₽ висят просроченными "
            f"дольше {REFUND_WATCHDOG_STALE_MINUTES} мин — возвраты не отрабатывают"
        )

        # Алерт с холодным стартом: первым срабатыванием после запуска бота
        # сообщаем сразу, дальше — не чаще раза в час.
        now_ts = time.monotonic()
        if _refund_watchdog_last_alert and now_ts - _refund_watchdog_last_alert < REFUND_WATCHDOG_ALERT_COOLDOWN:
            return
        _refund_watchdog_last_alert = now_ts

        await send_coder(
            f"""🚨 Автовозвраты за SMS встали
──────────────
⏳ Зависло: {count} активаций
💰 Не возвращено: {money} ₽
🕒 Просрочены дольше: {REFUND_WATCHDOG_STALE_MINUTES} мин

Джоба refund_and_cleanup_expired_sms не разгребает очередь. Проверь логи и перезапусти бота."""
        )

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.opt(exception=e).error("Сторож возвратов: не удалось проверить очередь")


async def check_payment_lava():
    """
    Проверяет статус платежей, выполненных через LavaApi, и обновляет соответствующие записи в базе данных.

    Эта функция:
    1. Получает список платежей, ожидающих проверки, из базы данных.
    2. Для каждого платежа запрашивает статус инвойса через LavaApi.
    3. Если платеж успешен:
       - Обновляет статус платежа в базе данных.
       - Увеличивает баланс пользователя на сумму платежа (с бонусом, если применимо).
       - Начисляет реферальный бонус, если есть привязанный реферал.
       - Отправляет пользователю сообщение об успешной оплате с возможностью продолжения операции.
    4. Логирует и обрабатывает исключения, возникающие в процессе выполнения.

    Исключения обрабатываются и записываются в лог, чтобы не прерывать выполнение функции при возникновении ошибки.

    """

    # Получаем список платежей, которые нужно проверить, из базы данных.
    payments = await models.Payment.get_lava_payments()

    # Проходим по каждому платежу в списке.
    for payment in payments:
        # Инициализируем объект LavaApi для взаимодействия с платежной системой.
        lava = LavaApi()
        try:
            # Запрашиваем статус инвойса по order_id и invoice_id.
            response = await lava.get_invoice_status(payment.order_id, payment.invoice_id)

            # Проверяем успешность запроса к API.
            if response['status'] == 200:
                invoice = response['data']

                # Если статус инвойса "success", обрабатываем успешный платеж.
                if invoice['status'] == 'success':
                    # Отмечаем платеж как успешный в базе данных.
                    payment.is_success = True
                    await payment.save()

                    # Проверяем, есть ли у пользователя активный бонус и его срок не истек.
                    if payment.user.bonus_end_at is not None and payment.user.bonus_end_at > timezone.now():
                        # Если бонус активен, увеличиваем сумму платежа на 10%.
                        amount = floor(payment.amount * 1.1)
                        # Сбрасываем срок действия бонуса.
                        payment.user.bonus_end_at = None
                    else:
                        # Если бонус не активен, сумма остается без изменений.
                        amount = payment.amount

                    # ✅ Если применился бонус +10% — записываем его отдельной строкой в payments
                    bonus_amount = int(amount - payment.amount)
                    if bonus_amount > 0:
                        bonus_payment = await models.Payment.create_payment(
                            user=payment.user,
                            method=models.PaymentMethod.BONUS10,
                            amount=bonus_amount,
                            continue_data={
                                "source_payment_id": payment.id,
                                "source_method": payment.method.value,
                                "source_invoice_id": payment.invoice_id,
                            }
                        )
                        bonus_payment.is_success = True
                        await bonus_payment.save()

                    # Увеличиваем баланс пользователя на сумму платежа (с учетом бонуса, если он был).
                    payment.user.balance += amount
                    await balance_replenishment_notification(payment, "Lava")
                    await bot.send_message(chat_id=payment.user.telegram_id,
                                           text=f'<b>💰Баланс успешно пополнен на {amount}₽</b>')
                    await payment.user.save()

                    # Если у пользователя есть реферал, начисляем реферальный бонус.
                    await process_referral_bonus(payment)

        except Exception as e:
            # Логируем любые исключения, возникшие в процессе обработки платежа.
            pass

async def check_payment_freekassa():
    # Получаем список платежей, которые нужно проверить, из базы данных.
    payments = await models.Payment.get_freekassa_payments()

    # Текущее время и время 5 часов назад
    tz = pytz.timezone('Europe/Moscow')  # Пример для временной зоны Москвы
    now = datetime.datetime.now(tz)
    five_hours_ago = now - datetime.timedelta(hours=1)

    fk = Freekassa(shop_id=FK_SHOP_ID, api_key=FK_FK_API_KEY)
    # Получаем список оплаченных заказов (со статусом 1) за последние hours часов
    try:
        # ⚠️ ВАЖНО: fk.get_orders() внутри использует requests -> нельзя вызывать напрямую в async,
        # иначе блокируется event loop и бот "зависает".
        orders = await asyncio.to_thread(fk.get_orders, order_status=1, date_from=five_hours_ago)
        merchant_order_ids = [order['merchant_order_id'] for order in orders['orders']]
    except Exception as e:
        merchant_order_ids = []
        pass

    if merchant_order_ids:
        for payment in payments:
            try:
                if str(payment.id) in merchant_order_ids:
                    # Отмечаем платеж как успешный в базе данных.
                    payment.is_success = True
                    await payment.save()

                    # Проверяем, есть ли у пользователя активный бонус и его срок не истек.
                    if payment.user.bonus_end_at is not None and payment.user.bonus_end_at > timezone.now():
                        # Если бонус активен, увеличиваем сумму платежа на 10%.
                        amount = floor(payment.amount * 1.1)
                        # Сбрасываем срок действия бонуса.
                        payment.user.bonus_end_at = None
                    else:
                        # Если бонус не активен, сумма остается без изменений.
                        amount = payment.amount

                    # Увеличиваем баланс пользователя на сумму платежа (с учетом бонуса, если он был).
                    payment.user.balance += amount
                    await balance_replenishment_notification(payment, "freekassa")
                    await bot.send_message(chat_id=payment.user.telegram_id,
                                           text=f'<b>💰Баланс успешно пополнен на {amount}₽</b>')
                    await payment.user.save()

                    # Если у пользователя есть реферал, начисляем реферальный бонус.
                    await process_referral_bonus(payment)

            except TelegramBadRequest:
                pass
            except Exception as e:
                # Логируем любые исключения, возникшие в процессе обработки платежа.
                logger.warning(e)
                await replenishment_error_message(payment, "freekassa")



async def check_payment_yoomoney():
    # Получаем список платежей, которые нужно проверить, из базы данных.
    payments = await models.Payment.get_yoomoney_payments()

    for payment in payments:
        try:
            # Получаем список оплаченных заказов
            if await check_payment_status(payment.id):
                # Отмечаем платеж как успешный в базе данных.
                payment.is_success = True
                await payment.save()

                # Проверяем, есть ли у пользователя активный бонус и его срок не истек.
                if payment.user.bonus_end_at is not None and payment.user.bonus_end_at > timezone.now():
                    # Если бонус активен, увеличиваем сумму платежа на 10%.
                    amount = floor(payment.amount * 1.1)
                    # Сбрасываем срок действия бонуса.
                    payment.user.bonus_end_at = None
                else:
                    # Если бонус не активен, сумма остается без изменений.
                    amount = payment.amount

                # Увеличиваем баланс пользователя на сумму платежа (с учетом бонуса, если он был).
                payment.user.balance += amount
                await balance_replenishment_notification(payment, "yoomoney")
                await payment.user.save()
                await bot.send_message(chat_id=payment.user.telegram_id,
                                       text=f'<b>💰Баланс успешно пополнен на {amount}₽</b>')

                # Если у пользователя есть реферал, начисляем реферальный бонус.
                await process_referral_bonus(payment)

        except TelegramBadRequest:
            pass
        except Exception as e:
            # Логируем любые исключения, возникшие в процессе обработки платежа.
            logger.warning(e)
            await replenishment_error_message(payment, "yoomoney")


async def check_payment_anypay():
    # Получаем список платежей, которые нужно проверить, из базы данных.
    payments = await models.Payment.get_anypay_payments()
    api = AnypayAPI()
    for payment in payments:
        try:
            # Получаем список оплаченных заказов
            if await api.check_payment(payment.id):
                # Отмечаем платеж как успешный в базе данных.
                payment.is_success = True
                await payment.save()

                # Проверяем, есть ли у пользователя активный бонус и его срок не истек.
                if payment.user.bonus_end_at is not None and payment.user.bonus_end_at > timezone.now():
                    # Если бонус активен, увеличиваем сумму платежа на 10%.
                    amount = floor(payment.amount * 1.1)
                    # Сбрасываем срок действия бонуса.
                    payment.user.bonus_end_at = None
                else:
                    # Если бонус не активен, сумма остается без изменений.
                    amount = payment.amount

                # Увеличиваем баланс пользователя на сумму платежа (с учетом бонуса, если он был).
                payment.user.balance += amount
                await balance_replenishment_notification(payment, "AnyPay")
                await bot.send_message(chat_id=payment.user.telegram_id,
                                       text=f'<b>💰Баланс успешно пополнен на {amount}₽</b>')
                await payment.user.save()

                # Если у пользователя есть реферал, начисляем реферальный бонус.
                await process_referral_bonus(payment)


        except TelegramBadRequest:
            pass
        except Exception as e:
            # Логируем любые исключения, возникшие в процессе обработки платежа.
            logger.warning(e)
            await replenishment_error_message(payment, "AnyPay")


async def check_payment_streampay():
    # Получаем список платежей, которые нужно проверить, из базы данных.
    payments = await models.Payment.get_streampay_payments()
    for payment in payments:
        try:
            # Получаем оплаченные заказы
            if await get_payment_status_streampay(payment.invoice_id) == 'success':
                # Отмечаем платеж как успешный в базе данных.
                payment.is_success = True
                await payment.save()

                # Проверяем, есть ли у пользователя активный бонус и его срок не истек.
                if payment.user.bonus_end_at is not None and payment.user.bonus_end_at > timezone.now():
                    # Если бонус активен, увеличиваем сумму платежа на 10%.
                    amount = floor(payment.amount * 1.1)
                    # Сбрасываем срок действия бонуса.
                    payment.user.bonus_end_at = None
                else:
                    # Если бонус не активен, сумма остается без изменений.
                    amount = payment.amount

                # Увеличиваем баланс пользователя на сумму платежа (с учетом бонуса, если он был).
                payment.user.balance += amount
                await balance_replenishment_notification(payment, "streampay")
                await bot.send_message(chat_id=payment.user.telegram_id,
                                       text=f'<b>💰Баланс успешно пополнен на {amount}₽</b>')
                await payment.user.save()

                # Если у пользователя есть реферал, начисляем реферальный бонус.
                await process_referral_bonus(payment)

        except TelegramBadRequest:
            pass
        except Exception as e:
            # Логируем любые исключения, возникшие в процессе обработки платежа.
            logger.warning(e)
            await replenishment_error_message(payment, "streampay")


# Окно и размер батча для добора оплат, пропущенных из-за простоя сервиса статусов.
CKASSA_BACKLOG_HOURS = 48
CKASSA_BACKLOG_LIMIT = 300

# Сколько ошибок подряд считать падением сервиса. Единичная ошибка на одном
# invoice_id не должна обрывать проход — иначе один битый платёж блокирует все
# остальные. Подряд идущие ошибки означают, что лёг сам сервис.
CKASSA_ERRORS_TO_STOP = 3

# После какого возраста платёж с ответом «не найден» считается протухшим.
# Ссылка на оплату живёт 1 час (bestBefore в create_invoice_ckassa), поэтому
# в норме 404 через несколько часов означает, что платёж не оплачивали.
# Без этого очередь добора растёт бесконечно: сотни никогда не оплаченных
# инвойсов крутятся каждые 10 минут и вытесняют реально потерянные оплаты.
#
# ВАЖНО: 404 значит «сервис не знает про платёж», а не «платёж не оплачен».
# После простоя 15.08.2026 приёмник поднялся без колбэков за время аварии и
# отвечал 404 по реально оплаченным счетам — автозакрытие пометило их
# протухшими. Поэтому значение вынесено в конфиг: на время разбора аварии
# ставится 0 (закрытие выключено), в норме — CKASSA_STALE_HOURS_DEFAULT.
CKASSA_STALE_HOURS_DEFAULT = 6
CKASSA_STALE_HOURS = int(
    dependencies.config.get('CKASSA_STALE_HOURS', CKASSA_STALE_HOURS_DEFAULT) or 0
)

# Троттлинг алертов о недоступности сервиса статусов: при многочасовом простое
# проход идёт каждые 25 секунд, без троттлинга разработчик получит тысячи сообщений.
CKASSA_ALERT_INTERVAL_SEC = 900
_ckassa_last_alert_at: float | None = None


async def _report_ckassa_unavailable(label: str, reason: str, pending: int) -> None:
    """
    Сообщает о недоступности сервиса статусов CKassa: в лог — всегда, разработчику —
    не чаще раза в CKASSA_ALERT_INTERVAL_SEC.

    Молчать здесь нельзя: пока сервис лежит, пользователи платят, а баланс не
    пополняется — именно этот случай выглядит как «оплата прошла, денег нет».
    """
    global _ckassa_last_alert_at

    logger.error(
        f"CKassa ({label}): сервис статусов недоступен, "
        f"проверка прервана, платежей в очереди={pending}. Причина: {reason}"
    )

    now = time.monotonic()
    if _ckassa_last_alert_at is not None and now - _ckassa_last_alert_at < CKASSA_ALERT_INTERVAL_SEC:
        return
    _ckassa_last_alert_at = now

    try:
        await send_coder(
            f'🚨 CKassa: проверка оплат не работает\n'
            f'этап: {label}\n'
            f'платежей ждёт зачисления: {pending}\n'
            f'причина: {reason[:300]}'
        )
    except Exception as e:
        logger.opt(exception=e).warning("CKassa: не удалось отправить алерт разработчику")


async def _credit_ckassa_payment(payment_pk: int) -> dict | None:
    """
    Атомарно проводит подтверждённую оплату CKassa: помечает платёж успешным
    и пополняет баланс пользователя.

    Почему транзакция: раньше is_success сохранялся отдельным запросом ДО начисления,
    и падение на любом следующем шаге навсегда уводило платёж из выборки
    (она берёт только is_success=False) — оплата есть, зачисления нет.

    :return: данные для уведомлений после коммита либо None, если зачислять нечего
        (платёж уже проведён параллельным проходом или пользователь не найден).
    """
    async with in_transaction() as conn:
        # 🔒 Лочим платёж — защита от двойного зачисления параллельными джобами
        payment = await models.Payment.filter(id=payment_pk).using_db(conn).select_for_update().first()
        if not payment or payment.is_success:
            return None

        # 🔒 Лочим пользователя, иначе параллельные списания затрут пополнение
        user = await models.User.filter(id=payment.user_id).using_db(conn).select_for_update().first()
        if not user:
            logger.error(f"CKassa: платёж id={payment_pk} без пользователя, зачисление невозможно")
            return None

        if user.bonus_end_at is not None and user.bonus_end_at > timezone.now():
            amount = floor(payment.amount * 1.1)
            user.bonus_end_at = None
        else:
            amount = payment.amount

        # ✅ Если применился бонус +10% — записываем его отдельной строкой в payments
        bonus_amount = int(amount - payment.amount)
        if bonus_amount > 0:
            bonus_payment = models.Payment(
                user_id=user.id,
                method=models.PaymentMethod.BONUS10,
                amount=bonus_amount,
                continue_data={
                    "source_payment_id": payment.id,
                    "source_method": payment.method.value,
                    "source_invoice_id": payment.invoice_id,
                },
                is_success=True,
                processed=True,
            )
            await bonus_payment.save(using_db=conn)

        payment.is_success = True
        payment.processed = True
        await payment.save(using_db=conn, update_fields=["is_success", "processed"])

        user.balance = float(user.balance or 0.0) + amount
        await user.save(using_db=conn, update_fields=["balance", "bonus_end_at"])

        return {"payment": payment, "user": user, "amount": amount}


async def _after_ckassa_credit(credited: dict) -> None:
    """
    Уведомления и реферальный бонус после успешного зачисления.

    Каждый шаг обёрнут отдельно: деньги уже на балансе, и падение уведомления
    не должно выглядеть как сбой зачисления.
    """
    payment = credited["payment"]
    user = credited["user"]
    amount = credited["amount"]

    # Восстанавливаем связь для функций, работающих через payment.user
    payment.user = user

    try:
        await balance_replenishment_notification(payment, "ckassa")
    except Exception as e:
        logger.opt(exception=e).warning(f"CKassa: не записано уведомление о пополнении, payment_id={payment.id}")

    try:
        # Таймаут обязателен: подвисшая отправка держит весь проход, а джоб
        # зарегистрирован с max_instances=1 — следующие запуски не стартуют.
        await asyncio.wait_for(
            bot.send_message(
                chat_id=user.telegram_id,
                text=f'<b>💰Баланс успешно пополнен на {amount}₽</b>'
            ),
            timeout=15,
        )
    except TelegramBadRequest:
        pass
    except asyncio.TimeoutError:
        logger.warning(f"CKassa: уведомление пользователю {user.telegram_id} не ушло за 15с (деньги зачислены)")
    except Exception as e:
        logger.opt(exception=e).warning(
            f"CKassa: не удалось уведомить пользователя {user.telegram_id} о пополнении на {amount}₽"
        )

    try:
        await process_referral_bonus(payment)
    except Exception as e:
        logger.opt(exception=e).error(f"CKassa: реферальный бонус не начислен, payment_id={payment.id}")


async def _close_stale_ckassa_payment(payment) -> None:
    """
    Помечает processed=True платёж, который сервис считает несуществующим
    и который уже не могут оплатить. Баланс не трогает — денег по нему не было.
    """
    try:
        await models.Payment.filter(id=payment.id, is_success=False).update(processed=True)
    except Exception as e:
        logger.opt(exception=e).warning(f"CKassa: не удалось закрыть протухший платёж id={payment.id}")


async def _run_ckassa_check(
    lookback_hours: int,
    limit: int,
    label: str,
    min_age_hours: int = 0,
    oldest_first: bool = False,
) -> None:
    """
    Общий проход по неоплаченным инвойсам CKassa.

    :param lookback_hours: верхняя граница возраста платежей.
    :param limit: максимум платежей за проход (0 — без ограничения).
    :param label: метка прохода для логов ('основной' / 'добор').
    :param min_age_hours: нижняя граница возраста (для добора).
    :param oldest_first: начинать с самых старых (для добора).
    """
    try:
        payments = await models.Payment.get_ckassa_payments(
            lookback_hours=lookback_hours,
            min_age_hours=min_age_hours,
            limit=limit,
            oldest_first=oldest_first,
        )
    except Exception as e:
        logger.opt(exception=e).error(f"CKassa ({label}): не удалось получить платежи из БД")
        return

    if not payments:
        return

    now = timezone.now()
    errors_in_row = 0

    for index, payment in enumerate(payments):
        try:
            payment_data = await get_ckassa_payments(payment.invoice_id)
            errors_in_row = 0
        except CkassaUnavailable as e:
            errors_in_row += 1
            # Единичная ошибка — проблема конкретного invoice_id, идём дальше.
            # Ошибки подряд — лёг сам сервис: добивать остаток батча бессмысленно,
            # каждый запрос упрётся в таймаут и проход зависнет. Платежи остаются
            # is_success=False и будут проверены следующим проходом либо добором.
            if errors_in_row >= CKASSA_ERRORS_TO_STOP:
                await _report_ckassa_unavailable(label, str(e), pending=len(payments) - index)
                return
            logger.warning(
                f"CKassa ({label}): статус не получен для invoice_id={payment.invoice_id} "
                f"({errors_in_row}/{CKASSA_ERRORS_TO_STOP}): {e}"
            )
            continue
        except Exception as e:
            logger.opt(exception=e).error(
                f"CKassa ({label}): непредвиденная ошибка проверки invoice_id={payment.invoice_id}"
            )
            continue

        # Сервис ответил «нет такого платежа», а ссылка на оплату уже мертва —
        # закрываем, чтобы очередь добора не забивалась навсегда.
        # CKASSA_STALE_HOURS = 0 полностью выключает закрытие: нужно, когда
        # приёмник мог потерять данные и 404 нельзя считать доказательством.
        if payment_data is None:
            if CKASSA_STALE_HOURS and payment.created_at < now - datetime.timedelta(hours=CKASSA_STALE_HOURS):
                await _close_stale_ckassa_payment(payment)
            continue

        if payment_data.get('state') != 'PAYED':
            continue

        try:
            credited = await _credit_ckassa_payment(payment.id)
        except Exception as e:
            # Оплата подтверждена провайдером, но зачисление не прошло —
            # самый опасный случай, поэтому и лог, и алерт разработчику.
            logger.opt(exception=e).error(
                f"CKassa ({label}): оплата подтверждена, зачисление не выполнено, "
                f"payment_id={payment.id}, invoice_id={payment.invoice_id}, сумма={payment.amount}"
            )
            try:
                await send_coder(
                    f'🚨 CKassa: оплата есть, баланс не пополнен\n'
                    f'payment_id: {payment.id}\n'
                    f'invoice_id: {payment.invoice_id}\n'
                    f'сумма: {payment.amount}₽\n'
                    f'ошибка: {e!r}'
                )
            except Exception as notify_error:
                logger.opt(exception=notify_error).warning("CKassa: не удалось отправить алерт о сбое зачисления")
            await replenishment_error_message(payment, "CKassa")
            continue

        if not credited:
            continue

        # Деньги уже на балансе. Любая ошибка в уведомлениях не должна ронять
        # проход — иначе остальные оплаченные платежи останутся необработанными.
        try:
            await _after_ckassa_credit(credited)
        except Exception as e:
            logger.opt(exception=e).error(
                f"CKassa ({label}): баланс пополнен, но постобработка упала, payment_id={payment.id}"
            )


# Потолок на весь проход. Джобы зарегистрированы с max_instances=1: если проход
# зависнет (подвисший HTTP к сервису статусов или к Telegram при отправке
# уведомления), новые запуски не стартуют и зачисления встают молча — ровно так
# 16.08.2026 оплаты перестали зачисляться на несколько часов.
CKASSA_MAIN_RUN_TIMEOUT = 300
CKASSA_BACKLOG_RUN_TIMEOUT = 600


async def check_payment_ckassa():
    """
    Асинхронная функция для проверки статуса платежей CKassa.
    """
    try:
        await asyncio.wait_for(
            _run_ckassa_check(lookback_hours=2, limit=0, label="основной"),
            timeout=CKASSA_MAIN_RUN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"CKassa (основной): проход не уложился в {CKASSA_MAIN_RUN_TIMEOUT}с и был прерван. "
            f"Платежи остались в очереди, следующий проход продолжит."
        )


async def check_payment_ckassa_backlog():
    """
    Добор оплат за широкое окно.

    Нужен потому, что основной проход смотрит только 2 часа назад: если сервис
    статусов лежал дольше, оплаченные платежи молча выпадали из выборки навсегда.

    Берёт самые старые: они ближе всего к краю окна и вот-вот пропадут насовсем.
    Свежие (моложе 2 часов) не трогает — их и так проверяет основной проход.
    """
    try:
        await asyncio.wait_for(
            _run_ckassa_check(
                lookback_hours=CKASSA_BACKLOG_HOURS,
                min_age_hours=2,
                limit=CKASSA_BACKLOG_LIMIT,
                label="добор",
                oldest_first=True,
            ),
            timeout=CKASSA_BACKLOG_RUN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"CKassa (добор): проход не уложился в {CKASSA_BACKLOG_RUN_TIMEOUT}с и был прерван. "
            f"Платежи остались в очереди, следующий проход продолжит."
        )


async def check_payment_cryptomus():
    # Получаем список платежей, которые нужно проверить, из базы данных.
    payments = await models.Payment.get_cryptomus_payments()
    # Получаем список оплаченных инвойсов
    payments_cryptomus = get_paid_order_ids()

    for payment in payments:
        try:
            # Получаем список оплаченных заказов
            if str(payment.id) in payments_cryptomus:
                # Отмечаем платеж как успешный в базе данных.
                payment.is_success = True
                await payment.save()

                # Проверяем, есть ли у пользователя активный бонус и его срок не истек.
                if payment.user.bonus_end_at is not None and payment.user.bonus_end_at > timezone.now():
                    # Если бонус активен, увеличиваем сумму платежа на 10%.
                    amount = floor(payment.amount * 1.1)
                    # Сбрасываем срок действия бонуса.
                    payment.user.bonus_end_at = None
                else:
                    # Если бонус не активен, сумма остается без изменений.
                    amount = payment.amount

                # ✅ Если применился бонус +10% — записываем его отдельной строкой в payments
                bonus_amount = int(amount - payment.amount)
                if bonus_amount > 0:
                    bonus_payment = await models.Payment.create_payment(
                        user=payment.user,
                        method=models.PaymentMethod.BONUS10,
                        amount=bonus_amount,
                        continue_data={
                            "source_payment_id": payment.id,
                            "source_method": payment.method.value,
                            "source_invoice_id": getattr(payment, "invoice_id", None),
                        }
                    )
                    bonus_payment.is_success = True
                    await bonus_payment.save()

                # Увеличиваем баланс пользователя на сумму платежа (с учетом бонуса, если он был).
                payment.user.balance += amount
                await balance_replenishment_notification(payment, "Cryptomus")
                await bot.send_message(
                    chat_id=payment.user.telegram_id,
                    text=f'<b>💰Баланс успешно пополнен на {amount}₽</b>'
                )
                await payment.user.save()

                # Если у пользователя есть реферал, начисляем реферальный бонус.
                await process_referral_bonus(payment)

        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.warning(e)
            await replenishment_error_message(payment, "Cryptomus")


import html  # убедитесь, что импорт html уже есть

async def check_sms():
    try:
        activations = await models.Activation.get_active_activations()
        for activation in activations:
            # определяем провайдера
            provider = (getattr(activation, "provider", "") or "").strip().lower()
            if not provider:
                # для старых записей fallback по длине activation_id
                provider = "smsactivate" if len(str(activation.activation_id)) > 9 else "onlinesim"

            name = None
            status: str | None = None

            if provider == "onlinesim":
                client = OnlineSMS(api_key=API_KEY_ONLINESIM)
                try:
                    order_info = await client.get_order_info(
                        operation_id=activation.activation_id,
                        get_full_message=True
                    )
                except Exception as e:
                    # ✅ pyonlinesim может отвечать "error_no_operations" — операция недоступна/не существует.
                    # Для планировщика это не критично: просто пропускаем и идём дальше.
                    if "error_no_operations" in str(e):
                        continue

                    # Временная ошибка/лимиты — тоже пропускаем
                    if e.__class__.__name__ == "TryAgainLater":
                        continue

                    # 5xx / HTML вместо JSON — onlinesim временно недоступен
                    err_str = str(e)
                    if any(code in err_str for code in ("503", "502", "500", "unexpected mimetype")):
                        continue

                    raise
                try:
                    name = await activation.get_service_2_name()
                except Exception:
                    name = None
                if order_info and isinstance(order_info, list) and "msg" in order_info[0]:
                    sms_code = order_info[0]["msg"]
                    status = f"STATUS_OK:{sms_code}"
                else:
                    continue

            elif provider == "smsfast":
                smsfast = get_smsfast_client()
                try:
                    status = str(await smsfast.get_status(activation.activation_id))
                except Exception as e:
                    logger.opt(exception=e).warning(f"Ошибка при получении статуса smsfast: {e}")
                    continue
                try:
                    name = activation.service.name
                except Exception:
                    name = None

            else:
                # smsactivate и другие провайдеры
                sms_client = SmsReceive()
                try:
                    status = str(await sms_client.get_activation_status(activation.activation_id))
                except Exception as e:
                    logger.opt(exception=e).warning(f"Ошибка при получении статуса smsactivate: {e}")
                    continue
                try:
                    name = activation.service.name
                except Exception:
                    name = None

            # print(f'activation {activation.activation_id} | status {status}')
            # если получен статус STATUS_OK
            if status and status.startswith(models.StatusResponse.STATUS_OK.name):
                sms_from_status_raw = status.split(":", 1)[1].strip()

                # ⚠️ Критично: финальная проверка под локом, чтобы не отправить SMS после cancel/refund
                should_send = False

                async with in_transaction() as conn:
                    act = await models.Activation.filter(id=activation.id).using_db(conn).select_for_update().first()
                    if not act:
                        continue

                    # если пользователь уже отменил (или авто-рефанд успел сработать) — ничего не пишем и не отправляем
                    if act.status == models.StatusResponse.STATUS_CANCEL:
                        continue

                    prev_sms = (getattr(act, "sms_text", None) or "").strip()
                    should_send = (prev_sms != sms_from_status_raw)

                    act.status = models.StatusResponse.STATUS_OK
                    act.sms_text = sms_from_status_raw
                    await act.save(using_db=conn, update_fields=["status", "sms_text"])

                # дальше работаем уже с "актуальной" активацией
                activation = act

                # загружаем нужные отношения
                try:
                    if provider == "onlinesim":
                        await activation.fetch_related("user", "service_2", "country")
                    else:
                        await activation.fetch_related("user", "service", "country")
                except Exception:
                    pass

                # отправляем только если SMS реально новое
                if should_send:
                    safe_name = html.escape(str(name)) if name else None
                    safe_code = html.escape(str(activation.sms_text))

                    # ✅ После первого полученного SMS включаем обязательную проверку подписки
                    if not getattr(activation.user, "channel_gate_enabled", True):
                        activation.user.channel_gate_enabled = True
                        await activation.user.save(update_fields=["channel_gate_enabled"])

                    if safe_name:
                        msg_text = (
                            f'<tg-emoji emoji-id="5406809207947142040">📲</tg-emoji><b>Новое SMS</b> на номер: +{activation.phone_number}\n\n'
                            f"Ваш код активации для <b>{safe_name}</b>:\n"
                            f"<code>{safe_code}</code>"
                        )
                    else:
                        msg_text = (
                            f'<tg-emoji emoji-id="5406809207947142040">📲</tg-emoji><b>Новое SMS</b> на номер: +{activation.phone_number}\n\n'
                            f"Ваш код активации:\n"
                            f"<code>{safe_code}</code>"
                        )

                    await bot.send_message(
                        chat_id=activation.user.telegram_id,
                        text=msg_text,
                        parse_mode="HTML",
                    )

                    # Редактируем оригинальное сообщение "Ожидаем SMS..." -> "СМС доставлено"
                    service_msg_id = getattr(activation, "service_msg_id", None)
                    if service_msg_id:
                        try:
                            import pytz as _pytz
                            _msk_tz = _pytz.timezone("Europe/Moscow")
                            expire_at = getattr(activation, "activation_expire_at", None)
                            _active_until = expire_at.astimezone(_msk_tz).strftime("%H:%M") if expire_at else "--:--"

                            _country_obj = getattr(activation, "country", None)
                            _country_name = (_country_obj.name if _country_obj else "") or ""
                            _flag = bt.country_flags.get(_country_name, "")
                            _flag_and_country = f"{_flag} {_country_name}".strip()

                            _service_name = str(name) if name else ""
                            if _service_name == "Telegram":
                                _edit_text = bt.SERVICE_INFO_TELEGRAM_SMS_RECEIVED.format(
                                    phone=activation.phone_number,
                                    country=_flag_and_country,
                                    service=_service_name,
                                    active_until=_active_until,
                                    sms_text=safe_code,
                                )
                            else:
                                _edit_text = bt.SERVICE_INFO_SMS_RECEIVED.format(
                                    phone=activation.phone_number,
                                    country=_flag_and_country,
                                    service=_service_name,
                                    active_until=_active_until,
                                    sms_text=safe_code,
                                )

                            await bot.edit_message_text(
                                chat_id=activation.user.telegram_id,
                                message_id=service_msg_id,
                                text=_edit_text,
                                parse_mode="HTML",
                            )
                        except Exception as _edit_err:
                            logger.opt(exception=_edit_err).warning(
                                f"Не удалось отредактировать service_msg (id={service_msg_id}): {_edit_err}"
                            )

                    await notice_of_arraignment("Получение смс", activation, name)
                    logger.bind(
                        user_id=activation.user.telegram_id,
                        action="new_sms"
                    ).log(
                        "USER_ACTION",
                        f"Получено новое SMS для номера {activation.phone_number}, код: {sms_from_status_raw}"
                    )

    except asyncio.CancelledError:
        pass
    except Exception as e:
        # ⚠️ Не полагаемся на activation.user (может быть не загружен и быть QuerySet)
        user_tg_id = None
        try:
            u = getattr(activation, "user", None)
            user_tg_id = getattr(u, "telegram_id", None)

            # если relation не загружен — достанем через FK
            if not user_tg_id and getattr(activation, "user_id", None):
                user_tg_id = await models.User.filter(id=activation.user_id).values_list("telegram_id", flat=True).first()
        except Exception:
            pass

        phone = getattr(activation, "phone_number", None)
        act_id = getattr(activation, "activation_id", None)
        status_obj = getattr(activation, "status", None)
        status_name = getattr(status_obj, "name", None)
        provider = getattr(activation, "provider", None)

        # service/service_2 тоже могут быть не подгружены — берём безопасно
        svc_name = None
        try:
            if provider == "onlinesim":
                svc_name = getattr(getattr(activation, "service_2", None), "name", None)
            else:
                svc_name = getattr(getattr(activation, "service", None), "name", None)
        except Exception:
            svc_name = None

        error_info = f"""
        ❌ Ошибка в check_sms
        ───────────────────
        🔹 Пользователь: {user_tg_id or 'Неизвестно'}
        🔹 Номер: {phone or 'Неизвестно'}
        🔹 Сервис: {svc_name or 'Неизвестно'}
        🔹 Провайдер: {provider or 'Неизвестно'}
        🔹 ID активации: {act_id or 'Неизвестно'}
        🔹 Статус: {status_name or 'Неизвестно'}

        ⚠️ Ошибка: {e}
        """
        await send_coder(error_info)



import asyncio
from aiogram.exceptions import TelegramBadRequest

async def check_email():
    """
    Функция проверяет электронные почты на наличие новых сообщений и уведомляет пользователей через Telegram.

    1. Деактивирует просроченные почтовые ящики.
    2. Проверяет активные почтовые ящики на наличие новых сообщений.
    3. Сохраняет новые сообщения в базе данных и отправляет уведомления пользователям через Telegram.

    Исключения:
        TelegramBadRequest: В случае ошибки при отправке сообщения через Telegram API.
    """
    try:
        # 1) Деактивация просроченных ящиков
        expired_emails = await models.Mail.get_expired_mails()
        for expired_mail in expired_emails:
            # гарантируем, что relation user точно загружен
            try:
                await expired_mail.fetch_related("user")
            except Exception:
                pass

            expired_mail.is_active = False
            await expired_mail.save()

            tg_id = getattr(getattr(expired_mail, "user", None), "telegram_id", None)
            logger.bind(
                user_id=tg_id,
                action="deactivate_mail",
            ).log("USER_ACTION", f"Почтовый ящик {expired_mail.email} деактивирован (истёк срок аренды)")

            await asyncio.sleep(0)

        # 2) Проверка активных ящиков (исключаем token=NULL, чтобы не спамить 401)
        mails = await models.Mail.filter(is_active=True, token__isnull=False).prefetch_related("user")

        for mail in mails:
            # Получаем непрочитанные письма
            try:
                unread_messages = await get_unread_messages(mail.token)
            except Exception as e:
                status = getattr(e, "status", None)

                # 401 = токен недействителен/истёк
                if status == 401:
                    mail.token = None

                    if not mail.is_paid_mail:
                        mail.is_active = False
                        await mail.save(update_fields=["token", "is_active"])
                    else:
                        await mail.save(update_fields=["token"])

                    logger.bind(
                        user_id=getattr(mail.user, "telegram_id", None),
                        action="mail_token_expired",
                    ).log(
                        "USER_ACTION",
                        f"mail.tm вернул 401 Unauthorized — токен сброшен. "
                        f"mail_id={mail.id} email={mail.email} is_paid_mail={mail.is_paid_mail}"
                    )
                else:
                    logger.error(
                        f"Ошибка при получении писем: mail_id={mail.id} email={mail.email} "
                        f"user_id={getattr(mail.user, 'telegram_id', None)} err={type(e).__name__}: {e}"
                    )

                await asyncio.sleep(3)
                continue

            # 3) Обрабатываем каждое непрочитанное письмо для ТЕКУЩЕГО mail
            for unread_message in unread_messages:
                # Лимит для бесплатных — прекращаем обработку этого ящика
                if not mail.is_paid_mail and len(mail.old_messages_id) > 10:
                    logger.warning(f"Бесплатный ящик {mail.email} превысил лимит сообщений")
                    break

                msg_id = unread_message.get("id")
                if not msg_id:
                    continue

                if msg_id in mail.old_messages_id:
                    continue

                # Обновляем список старых сообщений
                try:
                    mail.old_messages_id.append(msg_id)
                    await mail.save()
                except Exception as e:
                    logger.error(
                        f"Ошибка при сохранении old_messages_id: mail_id={mail.id} email={mail.email} "
                        f"msg_id={msg_id} err={type(e).__name__}: {e}"
                    )
                    continue

                # Сохраняем письмо в БД
                try:
                    await models.Letter.add_letter(
                        mail=mail,
                        user=mail.user,
                        text=unread_message.get("content", ""),
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка при сохранении письма в БД: mail_id={mail.id} email={mail.email} "
                        f"msg_id={msg_id} err={type(e).__name__}: {e}"
                    )
                    continue

                mk = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text=bt.GET_FULL_MESSAGE,
                                callback_data=f"full_unread_message|{msg_id}|{mail.id}",
                            )
                        ]
                    ]
                )

                msg_text = (
                    f'<tg-emoji emoji-id="5472239203590888751">📩</tg-emoji><b>Новое сообщение</b> на почту: <b>{mail.email}</b>\n\n'
                    f'<b>От кого:</b> {unread_message.get("from")}\n'
                    f'<b>Тема:</b> {unread_message.get("subject")}\n\n'
                    f'{unread_message.get("content", "")}'
                )

                # Отправка в Telegram
                try:
                    await bot.send_message(
                        chat_id=mail.user.telegram_id,
                        text=msg_text,
                        reply_markup=mk,
                    )

                    logger.bind(
                        user_id=mail.user.telegram_id,
                        action="new_email",
                    ).log("USER_ACTION", f"Новое сообщение на ящике {mail.email}: {unread_message.get('subject')}")
                except TelegramBadRequest as e:
                    logger.warning(f"Ошибка при отправке сообщения пользователю {mail.user.telegram_id}: {e}")
                except Exception as e:
                    logger.error(
                        f"Неожиданная ошибка при отправке сообщения: user_id={mail.user.telegram_id} "
                        f"mail_id={mail.id} email={mail.email} err={type(e).__name__}: {e}"
                    )

            await asyncio.sleep(0)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Необработанная ошибка в check_email(): {type(e).__name__}: {e}")


async def check_rental_email():
    """
    Периодически проверяет активные арендованные FirstMail-ящики на новые письма.

    Логика:
    1. Берём только активные и ещё не истёкшие RentalEmailLease.
    2. Через общий helper читаем новые письма по IMAP.
    3. Используем old_messages_id / is_initialized, чтобы не слать дубли.
    4. Новые письма отправляем пользователю напрямую в Telegram.

    Важно:
    - старая логика Mail/mail.tm не затрагивается;
    - защита от дублей внутри одного процесса достигается общим lock
      в pull_rental_email_messages().
    """
    try:
        now = timezone.now()

        lease_ids = await models.RentalEmailLease.filter(
            is_active=True,
            expire_at__gt=now,
        ).values_list("id", flat=True)

        for lease_id in lease_ids:
            try:
                lease, messages = await pull_rental_email_messages(
                    lease_id=lease_id,
                    limit=5,
                )

                if not messages:
                    await asyncio.sleep(0)
                    continue

                for message_obj in messages:
                    try:
                        await send_firstmail_message(
                            chat_id=lease.user.telegram_id,
                            email_addr=lease.email,
                            message_obj=message_obj,
                        )

                        logger.bind(
                            user_id=lease.user.telegram_id,
                            action="check_rental_email",
                        ).log(
                            "USER_ACTION",
                            f"Новое FirstMail сообщение отправлено пользователю | lease_id={lease.id} email={lease.email} subject={message_obj.subject or '(без темы)'}"
                        )

                    except TelegramBadRequest as e:
                        logger.warning(
                            f"Ошибка при отправке FirstMail сообщения пользователю {lease.user.telegram_id}: {e}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Неожиданная ошибка отправки FirstMail сообщения: "
                            f"user_id={lease.user.telegram_id} lease_id={lease.id} email={lease.email} "
                            f"err={type(e).__name__}: {e}"
                        )

            except ValueError:
                # аренда уже могла завершиться между выборкой lease_ids и обработкой
                continue
            except Exception as e:
                logger.opt(exception=e).error(
                    f"Ошибка при обработке FirstMail lease_id={lease_id}: {type(e).__name__}: {e}"
                )
                continue

            await asyncio.sleep(0)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Необработанная ошибка в check_rental_email(): {type(e).__name__}: {e}")


async def _scan_free_firstmail(assignment_ids: list, scan_label: str) -> None:
    """
    Обходит переданные бесплатные FirstMail-ящики и отправляет новые письма.

    Общее тело для всех уровней сканирования (живые / спящие), чтобы
    логика обработки писем не разъезжалась между задачами.

    :param assignment_ids: id привязок, которые нужно проверить
    :param scan_label: метка уровня для логов ("active" / "idle")
    """
    started_at = timezone.now()

    try:
        for assignment_id in assignment_ids:
            try:
                assignment, messages = await pull_free_firstmail_messages(
                    assignment_id=assignment_id,
                    limit=5,
                )

                if not messages:
                    await asyncio.sleep(0)
                    continue

                for message_obj in messages:
                    try:
                        await send_firstmail_message(
                            chat_id=assignment.user.telegram_id,
                            email_addr=assignment.email,
                            message_obj=message_obj,
                        )
                        logger.bind(
                            user_id=assignment.user.telegram_id,
                            action="check_free_firstmail",
                        ).log(
                            "USER_ACTION",
                            f"Новое бесплатное FirstMail сообщение отправлено пользователю | "
                            f"assignment_id={assignment.id} email={assignment.email} "
                            f"subject={message_obj.subject or '(без темы)'}"
                        )
                    except TelegramBadRequest as e:
                        logger.warning(
                            f"Ошибка при отправке бесплатного FirstMail сообщения пользователю "
                            f"{assignment.user.telegram_id}: {e}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Неожиданная ошибка отправки бесплатного FirstMail сообщения: "
                            f"user_id={assignment.user.telegram_id} "
                            f"assignment_id={assignment.id} email={assignment.email} "
                            f"err={type(e).__name__}: {e}"
                        )

                    await asyncio.sleep(0)

            except ValueError as e:
                logger.warning(
                    f"Пропуск бесплатного FirstMail assignment_id={assignment_id}: {e}"
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"Ошибка при проверке бесплатного FirstMail assignment_id={assignment_id}: "
                    f"{type(e).__name__}: {e}"
                )

            await asyncio.sleep(0)

        duration = (timezone.now() - started_at).total_seconds()
        logger.info(
            f"FirstMail scan завершён | уровень={scan_label} "
            f"ящиков={len(assignment_ids)} длительность={duration:.1f} сек"
        )

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(
            f"Необработанная ошибка в _scan_free_firstmail({scan_label}): "
            f"{type(e).__name__}: {e}"
        )


async def check_free_firstmail():
    """
    Проверяет бесплатные FirstMail-ящики активных пользователей.

    Сканируем только тех, кто заходил в бота за последние
    FREE_FIRSTMAIL_ACTIVE_DAYS дней: обход всех выданных ящиков подряд
    занимает десятки минут и письма ждут очереди.

    Ящики остальных не теряют письма: old_messages_id обновляется только
    при сканировании, поэтому непрочитанные UID остаются новыми и придут,
    как только пользователь вернётся в бота и снова попадёт в выборку.
    """
    active_since = timezone.now() - datetime.timedelta(days=FREE_FIRSTMAIL_ACTIVE_DAYS)

    assignment_ids = await models.FreeFirstMailAssignment.filter(
        is_active=True,
        user__last_active_at__gte=active_since,
    ).values_list("id", flat=True)

    await _scan_free_firstmail(assignment_ids, scan_label="active")


async def check_free_firstmail_idle():
    """
    Проверяет ящики «спящих» пользователей — тех, кто заходил в бота
    между FREE_FIRSTMAIL_ACTIVE_DAYS и FREE_FIRSTMAIL_IDLE_DAYS дней назад.

    Запускается редко: письма таким пользователям не срочны, но и совсем
    без автопроверки их оставлять не хочется.
    """
    now = timezone.now()
    active_since = now - datetime.timedelta(days=FREE_FIRSTMAIL_ACTIVE_DAYS)
    idle_since = now - datetime.timedelta(days=FREE_FIRSTMAIL_IDLE_DAYS)

    assignment_ids = await models.FreeFirstMailAssignment.filter(
        is_active=True,
        user__last_active_at__lt=active_since,
        user__last_active_at__gte=idle_since,
    ).values_list("id", flat=True)

    await _scan_free_firstmail(assignment_ids, scan_label="idle")


def get_rental_email_notice_kb(lease_id: int) -> types.InlineKeyboardMarkup:
    """
    Клавиатура для уведомлений по арендованному FirstMail-ящику.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=bt.EXTEND_EMAIL_BTN, callback_data=f"extend_rental_email:{lease_id}")
    builder.button(text="Открыть ящик", callback_data=f"mail:{lease_id}")
    builder.adjust(1)
    return builder.as_markup()


async def notify_rental_free_week_expiration():
    """
    Отправляет уведомление о завершении бесплатной недели FirstMail примерно за 24 часа.

    Важно:
    - используем отдельную дату free_week_expires_at;
    - после успешной отправки помечаем и free_week_notified, и expiration_notified,
      чтобы не было второго общего уведомления об истечении аренды на тот же момент;
    - старую ветку Mail/mail.tm не затрагиваем.
    """
    try:
        now = timezone.now()
        notify_start = now + datetime.timedelta(hours=23, minutes=50)
        notify_end = now + datetime.timedelta(hours=24, minutes=10)

        leases = await models.RentalEmailLease.filter(
            is_active=True,
            is_free_week=True,
            free_week_notified=False,
            free_week_expires_at__gte=notify_start,
            free_week_expires_at__lte=notify_end,
        ).prefetch_related("user", "account")

        for lease in leases:
            try:
                await bot.send_message(
                    chat_id=lease.user.telegram_id,
                    text=(
                        f"⏰ Бесплатная неделя аренды почтового ящика "
                        f"<b>{html.escape(lease.email)}</b> заканчивается примерно через 24 часа.\n"
                        f"Чтобы сохранить ящик и продолжить приём писем, продлите аренду:"
                    ),
                    reply_markup=get_rental_email_notice_kb(lease.id),
                )

                lease.free_week_notified = True
                lease.expiration_notified = True
                await lease.save(update_fields=["free_week_notified", "expiration_notified"])

                logger.bind(
                    user_id=lease.user.telegram_id,
                    action="notify_rental_free_week_expiration",
                ).log(
                    "USER_ACTION",
                    f"Отправлено уведомление о завершении бесплатной недели FirstMail: lease_id={lease.id} email={lease.email}"
                )

            except Exception as e:
                logger.opt(exception=e).error(
                    f"Ошибка уведомления о завершении бесплатной недели FirstMail "
                    f"lease_id={lease.id} email={lease.email}: {e}"
                )
                continue

            await asyncio.sleep(0)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.opt(exception=e).error(
            f"Необработанная ошибка в notify_rental_free_week_expiration(): {e}"
        )


async def notify_rental_email_expiration():
    """
    Отправляет общее уведомление об истечении аренды FirstMail примерно за 24 часа.

    Важно:
    - это уведомление идёт только для текущего срока expire_at;
    - если в этот же момент у аренды заканчивается именно бесплатная неделя,
      общий алерт пропускаем, чтобы не было дубля;
    - старую ветку Mail/mail.tm не затрагиваем.
    """
    try:
        now = timezone.now()
        notify_start = now + datetime.timedelta(hours=23, minutes=50)
        notify_end = now + datetime.timedelta(hours=24, minutes=10)

        leases = await models.RentalEmailLease.filter(
            is_active=True,
            expiration_notified=False,
            expire_at__gte=notify_start,
            expire_at__lte=notify_end,
        ).prefetch_related("user", "account")

        for lease in leases:
            try:
                free_week_due_now = (
                    lease.free_week_expires_at is not None
                    and not lease.free_week_notified
                    and notify_start <= lease.free_week_expires_at <= notify_end
                )

                if free_week_due_now:
                    await asyncio.sleep(0)
                    continue

                await bot.send_message(
                    chat_id=lease.user.telegram_id,
                    text=(
                        f"⏰ У вас истекает срок аренды почтового ящика "
                        f"<b>{html.escape(lease.email)}</b>.\n"
                        f"До окончания осталось примерно 24 часа.\n\n"
                        f"Чтобы сохранить ящик и продолжить приём писем, продлите аренду:"
                    ),
                    reply_markup=get_rental_email_notice_kb(lease.id),
                )

                lease.expiration_notified = True
                await lease.save(update_fields=["expiration_notified"])

                logger.bind(
                    user_id=lease.user.telegram_id,
                    action="notify_rental_email_expiration",
                ).log(
                    "USER_ACTION",
                    f"Отправлено уведомление об истечении FirstMail-аренды: lease_id={lease.id} email={lease.email}"
                )

            except Exception as e:
                logger.opt(exception=e).error(
                    f"Ошибка уведомления об истечении FirstMail-аренды "
                    f"lease_id={lease.id} email={lease.email}: {e}"
                )
                continue

            await asyncio.sleep(0)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.opt(exception=e).error(
            f"Необработанная ошибка в notify_rental_email_expiration(): {e}"
        )

async def close_expired_rental_email_leases():
    """
    Автоматически завершает просроченные аренды FirstMail-ящиков.

    Логика:
    - берём только активные аренды RentalEmailLease, у которых expire_at уже наступил;
    - для каждой аренды вызываем release_rental_email_lease();
    - почтовый ящик освобождается и возвращается в конец очереди;
    - старая mail.tm-ветка здесь не затрагивается.
    """
    try:
        now = timezone.now()

        expired_leases = await models.RentalEmailLease.filter(
            is_active=True,
            expire_at__lte=now,
        ).prefetch_related("user", "account")

        for lease in expired_leases:
            try:
                released = await release_rental_email_lease(lease.id)

                if not released:
                    logger.bind(
                        user_id=getattr(getattr(lease, "user", None), "telegram_id", None),
                        action="close_expired_rental_email_leases",
                    ).warning(
                        f"Просроченная FirstMail-аренда не была освобождена: "
                        f"lease_id={lease.id} email={lease.email}"
                    )
                    await asyncio.sleep(0)
                    continue

                logger.bind(
                    user_id=getattr(getattr(lease, "user", None), "telegram_id", None),
                    action="close_expired_rental_email_leases",
                ).log(
                    "USER_ACTION",
                    f"Просроченная FirstMail-аренда автоматически завершена: "
                    f"lease_id={lease.id} email={lease.email}"
                )

            except Exception as e:
                logger.opt(exception=e).error(
                    f"Ошибка при автоосвобождении просроченной FirstMail-аренды "
                    f"lease_id={lease.id} email={lease.email}: {e}"
                )
                continue

            await asyncio.sleep(0)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.opt(exception=e).error(
            f"Необработанная ошибка в close_expired_rental_email_leases(): {e}"
        )


async def get_services_names():
    try:
        data = {
            'act': 'getServicesList',
            'csrf': ''
        }
        url = 'https://sms-activate.org/api/api.php'
        async with ClientSession() as session:
            async with session.post(url=url, data=data) as response:
                data = json.loads(await response.text())

        services_dict = {}
        for service in data['data']:
            services_dict[service['code']] = service['name'].replace('<small>+переадресация</small>', '')
            await asyncio.sleep(0)

        return services_dict
    except Exception as e:
        logger.opt(exception=e).error("Критическая ошибка при получении списка сервисов из SMS-Activate")
        await send_coder(f"❌ Ошибка получения сервисов из SMS-Activate:\n{e}")


async def update_countries_and_services():
    sms = SmsReceive()
    countries = await sms.get_countries()
    countries_db_ids = await models.CountriesSmsActivate.get_country_id_list()
    for country in countries:
        await models.CountriesSmsActivate.get_or_create(country_id=country["id"], name=country["name"])
        try:
            countries_db_ids.remove(int(country["id"]))
        except ValueError:
            pass

        await asyncio.sleep(0)

    for country_id in countries_db_ids:
        country = await models.CountriesSmsActivate.get_country_by_id(country_id)
        await country.delete()

    services = await sms.get_services()
    services_dict = await get_services_names()
    services_db_codes = await models.ServicesSmsActivate.get_codes_list()
    for service in services:
        service_obj = await models.ServicesSmsActivate.get_service(code=service["code"])
        if service_obj is None:
            await models.ServicesSmsActivate.add_service(
                code=service["code"],
                name=services_dict[service["code"]],
                search_names=service["search_names"]
            )
        else:
            service_obj.name = services_dict[service["code"]]
            service_obj.search_names = service["search_names"]
            await service_obj.save()

        try:
            services_db_codes.remove(service["code"])
        except ValueError:
            pass

        await asyncio.sleep(0)

    for service_code in services_db_codes:
        service = await models.ServicesSmsActivate.get_service(code=service_code)
        await service.delete()


async def check_mail_expiration_and_notify():
    """
    Проверяет почтовые ящики, срок аренды которых истекает через 24 часа, и уведомляет пользователей.
    """
    # Определяем временную зону +3
    tz = pytz.timezone('Europe/Moscow')

    # Получаем текущее время в UTC и переводим в нужную временную зону
    now = datetime.datetime.now(pytz.utc).astimezone(tz)
    next_day = now + datetime.timedelta(days=1)

    # Округляем до начала часа
    now_rounded = now.replace(minute=0, second=0, microsecond=0)
    next_day_rounded = next_day.replace(minute=0, second=0, microsecond=0)

    # Получаем все ID почтовых ящиков, которые истекают ровно через 24 часа и еще не уведомлены
    expiring_mail_ids = await models.Mail.filter(
        expire_at__gte=now_rounded,
        expire_at__lte=next_day_rounded,
        is_active=True,
        is_paid_mail=True,
        notification_sent=False
    ).values_list('id', flat=True)

    # Проходим по каждому ID и получаем почту вместе с пользователем
    for mail_id in expiring_mail_ids:
        mail = await models.Mail.get(id=mail_id).prefetch_related('user')
        user = mail.user
        inline_kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(text='Продлить аренду', callback_data='rental')
                ]
            ]
        )

        try:

            # Пытаемся отправить уведомление пользователю через Telegram с inline-кнопкой
            await bot.send_message(
                chat_id=mail.user.telegram_id,
                text=f'У вас истекает срок аренды почтового ящика <b>{mail.email}</b>️\n'
                     f'Чтобы продлить аренду нажмите кнопку⤵️',
                reply_markup=inline_kb
            )

            # Обновляем флаг уведомления
            mail.notification_sent = True
            await mail.save()
            logger.bind(user_id=user.telegram_id, action="mail_expiration_notification").log(
                "USER_ACTION",
                f"Пользователь {user.telegram_id} получил уведомление об истечении срока аренды ящика {mail.email}"
            )
        except TelegramBadRequest as e:
            # Обрабатываем исключение, если возникает ошибка при отправке сообщения
            await send_coder(f"❌ Ошибка при отправке уведомления о сроке аренды ящика:\n"
                             f"Пользователь: {user.telegram_id}\nОшибка: {e}")


async def notify_week_expiration():
    """
    Отправляет уведомление пользователям, у которых бесплатная неделя
    аренды почты заканчивается примерно через 24 часа.

    Важно:
    - НЕ сбрасываем is_free_week после истечения срока.
    - Этот флаг теперь используется как исторический признак того,
      что бесплатная неделя уже была использована.
    """
    tz = pytz.timezone('Europe/Moscow')
    now = datetime.datetime.now(pytz.utc).astimezone(tz)
    notify_start = now + datetime.timedelta(hours=23, minutes=50)
    notify_end = now + datetime.timedelta(hours=24, minutes=10)

    mails = await models.Mail.filter(
        is_active=True,
        is_free_week=True,
        notification_sent=False,
        expire_at__gte=notify_start,
        expire_at__lte=notify_end
    ).prefetch_related("user")

    for mail in mails:
        try:
            mail.notification_sent = True
            await mail.save(update_fields=["notification_sent"])

            await bot.send_message(
                chat_id=mail.user.telegram_id,
                text="⏰ Бесплатная неделя аренды почты заканчивается через 24 часа!\n"
                     "Для продления аренды выберите тариф:",
                reply_markup=get_extend_email_kb(mail.id, False)
            )
        except Exception as e:
            logger.opt(exception=e).error("Ошибка уведомления о завершении недели")
            continue

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from loguru import logger

async def send_coder(msg_text: str, reply_markup: types.InlineKeyboardMarkup | None = None) -> None:
    """
    Отправка служебных сообщений в чат CODER.

    Важно: отправляем БЕЗ parse_mode, чтобы любые символы пользователя
    (например '<', '>', '&') не ломали Telegram entities и не роняли
    платежи/аренды/уведомления.
    """
    if not CODER:
        return

    try:
        await bot.send_message(
            chat_id=CODER,
            text=str(msg_text),
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        # Не даём уведомлениям ронять бизнес-логику
        logger.warning(f"send_coder: TelegramBadRequest: {e}")
    except Exception as e:
        logger.opt(exception=e).error("send_coder: ошибка отправки сообщения в CODER")


async def check_rent_sms():
    try:
        # Получаем все активные аренды
        activations = await models.Rent.get_all_active_rents()
        # Обрабатываем каждую активную аренду
        for activation in activations:
            # Получаем состояние аренды через OnlineSimRentAPI
            api_client = OnlineSimRentAPI()
            rent_state = await api_client.get_rent_state(tzid=activation.rent_id)
            if rent_state:
                # Проверяем, есть ли данные в 'list'
                if rent_state.get("response") == 1 and "list" in rent_state:
                    if rent_state["list"]:  # Проверяем, что список не пуст
                        rent_info = rent_state["list"][0]  # Обрабатываем первую запись в списке
                        # Извлекаем сообщения
                        messages = rent_info.get("messages", [])
                        minutes = rent_info.get("time", 0)
                        # Формируем строку для сравнения
                        new_sms_text = "\n".join([f"{msg.get('service', 'Unknown')}: {msg.get('code', '')}" for msg in messages])

                        # Проверяем, отличается ли новое сообщение от текущего
                        if activation.sms_text == new_sms_text:
                            continue

                        # Сохраняем новый текст в sms_text
                        activation.sms_text = new_sms_text

                        # Обновляем время аренды
                        activation.rent_expire_at = (
                                datetime.datetime.now(pytz.timezone("Europe/Moscow")).replace(microsecond=0)
                                + datetime.timedelta(minutes=minutes)
                        )
                        await activation.save()

                        if messages:
                            activation.status = models.StatusResponse.STATUS_OK
                            await activation.save()
                            # Получаем последнее сообщение
                            last_message = messages[0]
                            service = last_message.get("service", "Unknown")
                            text = last_message.get("code", "")

                            # Формируем текст для отправки пользователю
                            msg_text = f"""
                            💬 <b>Новое SMS</b> на номер: +{activation.phone_number}\nВаш код активации для сервиса <b>{service}</b>: <code>{text}</code>
                            """

                            # ✅ После первого полученного SMS включаем обязательную проверку подписки
                            if not getattr(activation.user, "channel_gate_enabled", True):
                                activation.user.channel_gate_enabled = True
                                await activation.user.save(update_fields=["channel_gate_enabled"])

                            # Отправляем сообщение пользователю в Telegram
                            await bot.send_message(
                                chat_id=activation.user.telegram_id,
                                text=msg_text,
                                parse_mode="HTML"
                            )

                            # Логгируем событие аренды (или получения SMS)
                            name = service
                            await notice_of_arraignment("Аренда номера",activation, name)

        # Если в течение 20 минут не воспользовался номером
        expired_activations = await models.Rent.get_expired_activations()

        # Обрабатываем каждую истекшую аренду
        for expired_activation in expired_activations:
            # Обновляем статус аренды на 'STATUS_CANCEL'
            expired_activation.status = models.StatusResponse.STATUS_CANCEL
            expired_activation.is_canceled = True
            expired_activation.purchase_count -= 1
            await expired_activation.save()

            # Здесь важно, чтобы user был загружен
            user = await expired_activation.user
            if expired_activation.sms_text == '' and not expired_activation.refund_processed:
                user.balance += expired_activation.cost
                expired_activation.refund_processed = True
                await expired_activation.save()
                await user.save()
                # Логируем возврат средств за истёкшую аренду
                logger.bind(
                    user_id=user.telegram_id,
                    action="refund_rent"
                ).log(
                    "USER_ACTION",
                    f"Возврат средств за истёкшую аренду номера {expired_activation.phone_number}: {expired_activation.cost}₽"
                )


    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.opt(exception=True).error(f"Критическая ошибка в check_rent_sms(): {e}")


async def rents_ending_soon():
    try:
        # Получает список аренд, для которых срок истекает ровно через 5 часов
        rents_ending = await models.Rent.get_rents_ending_soon()
        for ending in rents_ending:
            user_id = ending.user.telegram_id
            if not ending.autorenew:
                msg_text = f"""
                    💬 <b>Скоро закончится срок аренды номера +{ending.phone_number}.\nУспейте продлить срок аренды или арендовать новый номер⤵️</b>
                """

                inline_kb = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(text='🔄Продлить аренду', callback_data=f"extend_rent_{ending.id}")
                        ],
                        [
                            types.InlineKeyboardButton(text=bt.RENT_NEW_ROOM, callback_data="new_number")
                        ]
                    ]
                )

                # Отправляем сообщение пользователю
                await bot.send_message(chat_id=user_id, text=msg_text, reply_markup=inline_kb)
                # Логгируем действие пользователя
                logger.bind(
                    user_id=user_id,
                    action="rent_expiration_notification"
                ).log(
                    "USER_ACTION",
                    f"Пользователь {user_id} получил уведомление об истечении срока аренды номера +{ending.phone_number}"
                )
                # Обновляем статус уведомления
                ending.is_notified = True
                await ending.save(update_fields=["is_notified"])  # Сохраняем только это поле

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.opt(exception=True).error(f"Критическая ошибка в rents_ending_soon(): {e}")


async def auto_renewal_of_rent():
    try:
        # Получает список аренд, для которых срок истекает ровно через 2 часа
        rents_ending = await models.Rent.get_rents_ending_soon()
        for ending in rents_ending:
            user_id = ending.user.telegram_id
            if ending.autorenew:
                if ending.user.balance < ending.cost:
                    inline_replenish = types.InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                types.InlineKeyboardButton(text=bt.DEPOSIT_BTN, callback_data=f"top_up_balance")
                            ],
                        ]
                    )
                    # Отправляем сообщение пользователю
                    await bot.send_message(chat_id=user_id, text=bt.NOT_ENOUGH_FUNDS_FOR_RENT, reply_markup=inline_replenish)

                    # Логируем недостаток средств
                    logger.bind(
                        user_id=user_id,
                        action="autorenew_not_enough_funds"
                    ).log("USER_ACTION", f"Недостаточно средств для автопродления аренды номера +{ending.phone_number}")

                    # Обновляем статус уведомления
                    ending.is_notified = True
                    await ending.save(update_fields=["is_notified"])

                else:
                    # Создаем экземпляр API клиента и делаем запрос аренды
                    api_client = OnlineSimRentAPI()
                    try:
                        rent_result = await api_client.extend_rent_state(tzid=ending.rent_id, days=ending.days)
                    except Exception as e:
                        error_msg = f"Ошибка при продлении аренды через API: {str(e)}"
                        logger.opt(exception=e).error(f"{error_msg} (user_id={user_id})")

                        await bot.send_message(chat_id=user_id, text=f"Ошибка при аренде: {str(e)}", show_alert=True)
                        return

                    if rent_result is None:
                        no_numbers_msg = "Не удалось продлить аренду: нет доступных номеров"
                        logger.warning(f"{no_numbers_msg} (user_id={user_id}, phone={ending.phone_number})")

                        await bot.send_message(chat_id=user_id, text=bt.NOT_NUMBERS_ALERT, show_alert=True)
                        return

                    # Извлекаем данные активации
                    rent_id = int(rent_result.get("tzid", 0))
                    phone_number = rent_result.get("number", None)
                    minutes = int(rent_result.get("time", 0))

                    if phone_number is None:
                        no_numbers_msg = "Не удалось продлить аренду: отсутствует номер"
                        logger.warning(f"{no_numbers_msg} (user_id={user_id}, phone={ending.phone_number})")

                        await bot.send_message(chat_id=user_id, text=bt.NOT_NUMBERS_ALERT, show_alert=True)
                        return

                    # Добавляем запись об активации в базу данных
                    activation = await models.Rent.add_rent(
                        user=ending.user,
                        rent_id=ending.rent_id,
                        country=ending.country,
                        cost=ending.cost,
                        phone_number=ending.phone_number,
                        rent_expire_at=datetime.datetime.now(pytz.timezone("Europe/Moscow")).replace(microsecond=0)
                                       + datetime.timedelta(minutes=minutes),
                        is_notified=False,
                        days=ending.days,
                        autorenew=ending.autorenew
                    )

                    # Логируем успешное продление аренды
                    logger.bind(
                        user_id=user_id,
                        action="autorenew_rent_success"
                    ).log("USER_ACTION", f"Аренда номера +{activation.phone_number} продлена на {activation.days} дней. Списано: {activation.cost}₽")

                    # Отправляем пользователю сообщение о номере телефона
                    country = activation.country.name.strip()
                    flag = country_flags.get(country, "")
                    flag_and_country = f"{flag} {country}"

                    # Сообщение о количестве дней аренды
                    days_text = get_day_string(ending.days)

                    await bot.send_message(chat_id=user_id, text=bt.RENT_SUCCESS_MESSAGE.format(days=days_text))
                    await bot.send_message(chat_id=user_id,
                                           text=bt.NUMBER_INFO.format(country=flag_and_country, phone=activation.phone_number))


                    # Списываем средства с баланса пользователя
                    ending.user.balance -= ending.cost
                    await ending.user.save(update_fields=['balance'])

                    # Логируем событие списания средств за автопродление аренды
                    logger.bind(
                        user_id=ending.user.telegram_id,
                        action="autorenew_rent_payment"
                    ).log(
                        "USER_ACTION",
                        f"Списано {activation.cost}₽ за автопродление аренды номера +{activation.phone_number}. "
                        f"Новый баланс: {ending.user.balance}₽"
                    )

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.opt(exception=e).error(f"Критическая ошибка в auto_renewal_of_rent(): {e}")


async def close_rent():
    """
    Обработка закрытия аренды номера срок действия которых заканчивается через 5 минут.
    """
    rents_closes = await models.Rent.close_rent_before_end()

    for rent in rents_closes:
        user_id = rent.user.telegram_id if rent.user else None

        # Обновляем статус аренды в базе данных
        rent.is_canceled = True
        await rent.save()
        # Используем API для отмены аренды
        api = OnlineSimRentAPI()  # Создаем экземпляр API
        try:
            response = await api.close_rent_num(tzid=rent.rent_id)  # Передаем ID операции аренды
            if response.get("response"):
                logger.bind(
                    user_id=user_id,
                    action="close_rent_success"
                ).log("USER_ACTION", f"Аренда номера {rent.phone_number} успешно закрыта")
                await bot.send_message(chat_id=rent.user.telegram_id, text=bt.NUMBER_RENTAL_CLOSED.format(number=rent.phone_number))
            else:
                # Если API вернул неизвестный ответ
                msg_text = f'Неизвестный ответ закрытия аренды {response}\nпользователь {rent.user.id} номер {rent.phone_number}'
                await send_coder(msg_text)
        except Exception as e:
            # Проверяем на конкретную ошибку API
            if str(e) == "Ошибка API: {'response': '1'}":
                await bot.send_message(chat_id=rent.user.telegram_id,
                                       text=bt.NUMBER_RENTAL_CLOSED.format(number=rent.phone_number))
                logger.bind(
                    user_id=user_id,
                    action="close_rent_already_closed"
                ).log("USER_ACTION", f"Срок аренды номера {rent.phone_number} истек")
            else:
                # Обработка остальных ошибок
                logger.bind(
                    user_id=rent.user.id,
                    action="close_rent_error"
                ).opt(exception=e).error(
                    f"Ошибка при закрытии аренды номера {rent.phone_number}: {str(e)}"
                )
                await bot.send_message(chat_id=rent.user.telegram_id,
                                       text=bt.NUMBER_RENTAL_CLOSED.format(number=rent.phone_number))


async def checking_inactive_rent():
    """
    Проверяем все активные аренды на сервисе и если по БД какая-то из них закрыта, тогда закрываем ее принудительно
    :return:
    """
    api_client = OnlineSimRentAPI()
    rent_state = await api_client.get_rent_state()
    # Проверяем, есть ли данные в 'list'
    if rent_state:
        for rent_info in rent_state:
            # Извлекаем tzid
            tzid = rent_info.get("tzid", 0)
            rent = await models.Rent.inactive_rent(tzid)
            if rent:
                rent.status = models.StatusResponse.STATUS_CANCEL
                await rent.save()
                try:
                    await api_client.close_rent_num(tzid)
                except Exception as e:
                    if str(e) == "Ошибка API: {'response': '1'}":
                        continue


async def balance_replenishment_notification(payment, service):
    user = payment.user
    msg_text = (
        f'💰💰💰\n'
        f'Пополнение {service}\n'
        f'пользователь {user.mention}\n'
        f'id {user.telegram_id}\n'
        f'сумма {payment.amount}\n'
        f'баланс: {user.balance}'
    )
    await send_coder(msg_text)

    logger.bind(
        user_id=user.telegram_id,
        action=f"top_up_balance_{service.lower()}"
    ).log("USER_ACTION", f"Пополнение баланса на {payment.amount}₽ через {service}, Баланс={user.balance}₽")


async def replenishment_error_message(payment, service):
    user = payment.user
    # msg_text = (
    #     f'❌ ошибка\n'
    #     f'{service} \n'
    #     f'пользователь {user.mention}\n'
    #     f'id {user.telegram_id}\n'
    #     f'сумма {payment.amount}\n'
    #     f'баланс: {user.balance}'
    # )
    # await send_coder(msg_text)
    #
    # logger.bind(
    #     user_id=user.telegram_id,
    #     action=f"top_up_balance_{service.lower()}_error"
    # ).error(f"Ошибка пополнения баланса через {service}, сумма: {payment.amount}₽, Баланс={user.balance}₽")


async def notice_of_arraignment(name_rent, activation, name):
    user = activation.user
    msg_text = (
        f'✅ {name_rent}\n'
        f'{name} \n'
        f'пользователь {user.mention}\n'
        f'id {user.telegram_id}\n'
        f'сумма аренды {activation.cost}\n'
        f'баланс: {user.balance}'
    )
    await send_coder(msg_text)

    logger.bind(
        user_id=user.telegram_id,
        action="rent_number"
    ).log("USER_ACTION", f"Аренда номера сервиса '{name}', Стоимость: {activation.cost}₽, Баланс={user.balance}₽")


async def referral_bonus_notification(payment, referrer, ref_sum):
    """
    Логгирует и уведомляет о начислении реферального бонуса.

    :param payment: объект платежа
    :param referrer: пользователь-реферал
    :param ref_sum: сумма бонуса
    """
    user = payment.user
    msg_text = (
        f'🧾 <b>Начислен реферальный бонус</b>\n'
        f'Реферер: {referrer.mention}\n'
        f'id: {referrer.telegram_id}\n'
        f'Пользователь: {user.mention} (id: {user.telegram_id})\n'
        f'Сумма платежа: {payment.amount}₽\n'
        f'Бонус: {ref_sum}₽\n'
        f'Текущий реф. баланс: {round(referrer.ref_balance)}₽'
    )
    await send_coder(msg_text)

    # Логгируем в системные логи
    logger.bind(
        user_id=referrer.telegram_id,
        action="referral_bonus"
    ).log(
        "REFERRAL_BONUS",
        f"Реферальный бонус {ref_sum}₽ за пользователя {user.telegram_id}, "
        f"платёж на сумму {payment.amount}₽"
    )

async def process_referral_bonus(payment):
    """
    Обрабатывает начисление реферального бонуса с учетом обычных и персональных ссылок.
    """
    if not payment.user.refer_id:
        return

    refer = await models.User.get_or_none(id=payment.user.refer_id)
    if not refer:
        return

    # Расчёт бонуса
    ref_bonus = int(dependencies.REF_BONUS) / 100
    ref_sum = round(payment.amount * ref_bonus, 1)

    refer.ref_balance += ref_sum
    refer.total_ref_earnings += ref_sum
    await refer.save()

    # Учет персональной ссылки
    link_text = ""
    if payment.user.referral_link_code:
        ref_link = await models.ReferralLink.get_or_none(link_code=payment.user.referral_link_code)
        if ref_link:
            # Проверяем — первая ли оплата
            is_first_payment = not await models.Payment.filter(
                user=payment.user,
                is_success=True
            ).exclude(id=payment.id).exists()

            if is_first_payment:
                ref_link.total_pays += 1
                ref_link.total_payment_amount += payment.amount  # 💰 Добавляем сумму только первой оплаты
                await ref_link.save()

            link_text = f" ({f'https://t.me/emailfastbot?start={ref_link.link_code}'})"

    # Уведомление рефереру
    if not refer.disable_ref_notifications:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text="🔕 Отключить уведомления",
                callback_data=f"disable_notify:{refer.telegram_id}"
            )]
        ])

        msg = (
            f"✅Партнерское вознаграждение{link_text}\n"
            f"├ Аккаунт: {payment.user.telegram_id}\n"
            f"├ Сумма зачисления: {payment.amount}₽\n"
            f"└ Ваш доход: {ref_sum}₽ (10%)"
        )

        await bot.send_message(chat_id=refer.telegram_id, text=msg, reply_markup=keyboard)

    await referral_bonus_notification(payment, refer, ref_sum)

FRAUD_DIFF_THRESHOLD = 1000.0

# Автобан по расхождению баланса. Выключен по умолчанию: джоб долго не работал
# (падал на лимите параметров asyncpg), а после аварии 15-16.08.2026 у части
# пользователей баланс закономерно не сходится с пополнениями — ручные
# компенсации, зачисления задним числом. Пока флаг выключен, джоб ничего
# не меняет в БД и только присылает разработчику список кандидатов.
# Включить: строка `FRAUD_AUTOBAN_ENABLED: true` в config.yaml и перезапуск.
FRAUD_AUTOBAN_ENABLED = str(
    dependencies.config.get('FRAUD_AUTOBAN_ENABLED', False)
).strip().lower() in ('true', '1', 'yes')

# Джоб крутится каждые 30 минут, но сводку в сухом режиме шлём редко.
FRAUD_REPORT_INTERVAL_SEC = 6 * 3600
FRAUD_REPORT_LIMIT = 15
_fraud_last_report_at: float | None = None

# Разница считается одним запросом на стороне БД.
# Раньше три полные агрегации (payments/rents/activations) выгружались в память,
# а список id подставлялся в IN (...). При десятках тысяч пользователей asyncpg
# падал с "the number of query arguments cannot exceed 32767", и джоб не
# отрабатывал вовсе — молча, потому что исключение гасилось общим except.
# Список админов передаётся одним параметром-массивом, а не тысячей плейсхолдеров.
_FRAUD_SQL = """
WITH paid AS (
    SELECT user_id, sum(amount) AS total
    FROM payments WHERE is_success GROUP BY user_id
), rent AS (
    SELECT user_id, sum(cost) AS total
    FROM rents WHERE sms_text IS NOT NULL AND sms_text <> '' GROUP BY user_id
), act AS (
    SELECT user_id, sum(cost) AS total
    FROM activations WHERE sms_text IS NOT NULL AND sms_text <> '' GROUP BY user_id
)
SELECT u.id, u.telegram_id, u.username, u.balance, u.fraud_suspect_since,
       coalesce(p.total, 0) AS total_paid,
       coalesce(r.total, 0) AS total_rent,
       coalesce(a.total, 0) AS total_act,
       (coalesce(r.total, 0) + coalesce(a.total, 0) + u.balance - coalesce(p.total, 0)) AS diff
FROM users u
LEFT JOIN paid p ON p.user_id = u.id
LEFT JOIN rent r ON r.user_id = u.id
LEFT JOIN act  a ON a.user_id = u.id
WHERE u.fraud_banned = false
  AND NOT (u.telegram_id = ANY($1::bigint[]))
  AND (
        (coalesce(r.total, 0) + coalesce(a.total, 0) + u.balance - coalesce(p.total, 0)) > $2
        OR u.fraud_suspect_since IS NOT NULL
      )
ORDER BY diff DESC
"""


async def _fraud_send_dry_report(rows: list) -> None:
    """
    Сухой режим: показываем, кого джоб забанил бы, и ничего не трогаем.
    Шлём не чаще раза в FRAUD_REPORT_INTERVAL_SEC, иначе при интервале
    в 30 минут разработчик утонет в одинаковых сводках.
    """
    global _fraud_last_report_at

    now = time.monotonic()
    if _fraud_last_report_at is not None and now - _fraud_last_report_at < FRAUD_REPORT_INTERVAL_SEC:
        return
    _fraud_last_report_at = now

    top = rows[:FRAUD_REPORT_LIMIT]
    lines = [
        "🔍 Проверка расхождения баланса (режим наблюдения, баны выключены)",
        f"кандидатов: {len(rows)}",
        "",
    ]
    for r in top:
        lines.append(
            f"{r['telegram_id']} @{r['username'] or '-'}: "
            f"разница {float(r['diff']):.0f}₽ "
            f"(оплатил {float(r['total_paid']):.0f}, "
            f"потратил {float(r['total_rent']) + float(r['total_act']):.0f}, "
            f"баланс {float(r['balance']):.0f})"
        )
    if len(rows) > FRAUD_REPORT_LIMIT:
        lines.append(f"... и ещё {len(rows) - FRAUD_REPORT_LIMIT}")
    lines.append("")
    lines.append("Включить бан: FRAUD_AUTOBAN_ENABLED: true в config.yaml")

    try:
        await send_coder("\n".join(lines))
    except Exception as e:
        logger.opt(exception=e).warning("Не удалось отправить сводку по расхождению баланса")


async def check_fraud_balance_discrepancy() -> None:
    """
    Ищет пользователей, у которых (расходы + баланс) - пополнения превышает
    FRAUD_DIFF_THRESHOLD, то есть деньги на балансе взялись не из оплат.

    При FRAUD_AUTOBAN_ENABLED=False (по умолчанию) ничего не меняет в БД,
    только шлёт сводку. При True — работает как раньше: первое срабатывание
    помечает подозрительным, второе банит.
    """
    try:
        admins = [int(a) for a in (dependencies.ADMINS or [])]

        conn = Tortoise.get_connection("default")
        rows = (await conn.execute_query(_FRAUD_SQL, [admins, FRAUD_DIFF_THRESHOLD]))[1]

        over = [r for r in rows if float(r["diff"]) > FRAUD_DIFF_THRESHOLD]

        logger.bind(action="check_fraud_balance_discrepancy").info(
            f"Проверка расхождения: строк {len(rows)}, сверх порога {len(over)}, "
            f"автобан {'включён' if FRAUD_AUTOBAN_ENABLED else 'ВЫКЛЮЧЕН (режим наблюдения)'}"
        )

        if not FRAUD_AUTOBAN_ENABLED:
            if over:
                await _fraud_send_dry_report(over)
            return

        now = timezone.now()
        pending_cutoff = now - datetime.timedelta(minutes=10)
        banned_count = 0
        suspect_count = 0

        for r in rows:
            diff = float(r["diff"])
            user_pk = r["id"]

            if diff <= FRAUD_DIFF_THRESHOLD:
                # Дисбаланс пропал — снимаем подозрение, если оно было
                if r["fraud_suspect_since"] is not None:
                    await models.User.filter(id=user_pk).update(fraud_suspect_since=None)
                    logger.bind(user_id=r["telegram_id"], action="check_fraud_balance_discrepancy").info(
                        f"Подозрение снято (diff={diff:.2f} ≤ порога)"
                    )
                continue

            # Платёж мог быть только что оплачен, а зачисление ещё не прошло
            has_pending = await models.Payment.filter(
                user_id=user_pk,
                is_success=False,
                created_at__gte=pending_cutoff,
            ).exists()
            if has_pending:
                logger.bind(user_id=r["telegram_id"], action="check_fraud_balance_discrepancy").info(
                    f"Fraud skip: есть pending-платёж за последние 10 мин, diff={diff:.2f}"
                )
                continue

            # Первое срабатывание — только подозрение, бан на следующем проходе
            if r["fraud_suspect_since"] is None:
                await models.User.filter(id=user_pk).update(fraud_suspect_since=now)
                suspect_count += 1
                logger.bind(user_id=r["telegram_id"], action="check_fraud_balance_discrepancy").warning(
                    f"Fraud suspect: первое срабатывание, diff={diff:.2f}. Бан — на следующей проверке."
                )
                continue

            user = await models.User.get_or_none(id=user_pk)
            if not user:
                continue

            user.fraud_banned = True
            user.fraud_suspect_since = None
            update_fields = ["fraud_banned", "fraud_suspect_since"]

            if hasattr(user, "fraud_banned_reason"):
                user.fraud_banned_reason = f"auto: (spent+balance)-paid > {FRAUD_DIFF_THRESHOLD}"
                update_fields.append("fraud_banned_reason")
            if hasattr(user, "fraud_banned_diff"):
                user.fraud_banned_diff = float(diff)
                update_fields.append("fraud_banned_diff")
            if hasattr(user, "fraud_banned_at"):
                user.fraud_banned_at = now
                update_fields.append("fraud_banned_at")

            await user.save(update_fields=update_fields)
            banned_count += 1

            total_paid = float(r["total_paid"])
            total_spent = float(r["total_rent"]) + float(r["total_act"])
            msg = (
                "🚨 AUTO-FRAUD BAN\n"
                f"telegram_id: {r['telegram_id']}\n"
                f"username: @{r['username'] or '-'}\n"
                f"оплаты: {total_paid:.2f}\n"
                f"потрачено: {total_spent:.2f} (rent={float(r['total_rent']):.2f} + act={float(r['total_act']):.2f})\n"
                f"баланс: {float(r['balance']):.2f}\n"
                f"разница: {diff:.2f}\n"
                f"порог: {FRAUD_DIFF_THRESHOLD:.2f}\n"
            )
            kb = InlineKeyboardBuilder()
            kb.button(text="✅ Разбанить", callback_data=f"fraud_unban:{r['telegram_id']}")

            await send_coder(msg, reply_markup=kb.as_markup())

            try:
                await bot.send_message(
                    chat_id=PROJECT_MANAGER,
                    text=str(msg),
                    parse_mode='HTML',
                    disable_web_page_preview=True,
                    reply_markup=kb.as_markup(),
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить AUTO-FRAUD BAN PM={PROJECT_MANAGER}: {e}")

            try:
                await bot.send_message(
                    chat_id=r["telegram_id"],
                    text=(
                        "🚫 Доступ ограничен.\n\n"
                        "Обнаружено несоответствие баланса и пополнений.\n"
                        "Если это ошибка — напишите в поддержку."
                    ),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

            logger.bind(user_id=r["telegram_id"], action="check_fraud_balance_discrepancy").warning(
                f"Пользователь fraud_banned=True, diff={diff:.2f}"
            )

        logger.bind(action="check_fraud_balance_discrepancy").info(
            f"Готово. Забанено: {banned_count}, новых подозреваемых: {suspect_count}"
        )

    except Exception as e:
        logger.opt(exception=e).error("Ошибка в check_fraud_balance_discrepancy")


async def auto_fix_users_balance_discrepancy() -> None:
    """
    Авто-исправление расхождения по аналогии с /users_with_discrepancy:
    если (расходы + баланс) > пополнений (diff > 0) — уменьшаем баланс до корректного.

    Корректный баланс: max(0, total_paid - total_spent)

    Важно:
    - учитываем только "доставленные" расходы (sms_text not null/empty) как в /users_with_discrepancy
    - используем grace window, чтобы не трогать совсем свежие операции (по умолчанию 10 минут)
    - уведомляем CODER по каждому исправлению
    """
    from datetime import timedelta
    from tortoise import timezone
    from tortoise.functions import Sum
    from tortoise.transactions import in_transaction

    GRACE_MINUTES = 15  # окно безопасности от "свежих" операций
    EPS = 0.01          # чтобы не дёргать копейки из-за float
    MAX_FIX_PER_RUN = 50  # защита от спама, если внезапно много пользователей

    try:

        cutoff_dt = timezone.now() - timedelta(minutes=GRACE_MINUTES)

        # --- агрегируем пополнения (только успешные и до cutoff) ---
        payments = await models.Payment.filter(
            is_success=True,
            created_at__lte=cutoff_dt,
        ).group_by("user_id").annotate(
            total_paid=Sum("amount")
        ).values("user_id", "total_paid")

        # --- агрегируем расходы (как в /users_with_discrepancy), тоже до cutoff ---
        rents = await models.Rent.filter(
            sms_text__isnull=False,
            created_at__lte=cutoff_dt,
        ).exclude(
            sms_text=""
        ).group_by("user_id").annotate(
            total_rent_cost=Sum("cost")
        ).values("user_id", "total_rent_cost")

        activations = await models.Activation.filter(
            sms_text__isnull=False,
            created_at__lte=cutoff_dt,
        ).exclude(
            sms_text=""
        ).group_by("user_id").annotate(
            total_activation_cost=Sum("cost")
        ).values("user_id", "total_activation_cost")

        user_data: dict[int, dict[str, float]] = {}

        def _get(uid: int) -> dict[str, float]:
            if uid not in user_data:
                user_data[uid] = {
                    "total_paid": 0.0,
                    "total_rent_cost": 0.0,
                    "total_activation_cost": 0.0,
                }
            return user_data[uid]

        for p in payments:
            uid = int(p["user_id"])
            _get(uid)["total_paid"] = float(p["total_paid"] or 0.0)

        for r in rents:
            uid = int(r["user_id"])
            _get(uid)["total_rent_cost"] = float(r["total_rent_cost"] or 0.0)

        for a in activations:
            uid = int(a["user_id"])
            _get(uid)["total_activation_cost"] = float(a["total_activation_cost"] or 0.0)

        user_ids = list(user_data.keys())
        if not user_ids:
            logger.bind(action="auto_fix_users_balance_discrepancy").info("Нет данных для проверки (user_ids пуст)")
            return

        # берём пользователей (кроме админов) — fraud_banned не фильтруем специально:
        # задача не про бан, а про выравнивание баланса
        candidates = await models.User.filter(
            id__in=user_ids,
        ).exclude(
            telegram_id__in=dependencies.ADMINS
        )

        fixed_count = 0
        overspend_count = 0

        for user in candidates:
            if fixed_count >= MAX_FIX_PER_RUN:
                break

            d = user_data.get(int(user.id), {})
            total_paid = float(d.get("total_paid", 0.0) or 0.0)
            total_rent_cost = float(d.get("total_rent_cost", 0.0) or 0.0)
            total_activation_cost = float(d.get("total_activation_cost", 0.0) or 0.0)

            total_spent = total_rent_cost + total_activation_cost
            balance_before = float(getattr(user, "balance", 0.0) or 0.0)

            diff = (total_spent + balance_before) - total_paid
            if diff <= EPS:
                continue

            # целевой баланс по формуле
            target_balance = max(0.0, total_paid - total_spent)

            # если баланс и так уже <= target_balance (или близко) — ничего не делаем
            if abs(balance_before - target_balance) <= EPS or balance_before < target_balance:
                # balance_before < target_balance — это уже другой тип проблемы (не хватает начислений),
                # мы её тут не решаем.
                continue

            # --- фиксируем в транзакции и перепроверяем внутри (защита от гонок) ---
            async with in_transaction() as conn:
                locked_user = await models.User.select_for_update().using_db(conn).get_or_none(id=user.id)
                if not locked_user:
                    continue

                # Пересчитываем внутри транзакции ещё раз (строго до cutoff)
                paid_row = await models.Payment.filter(
                    is_success=True,
                    created_at__lte=cutoff_dt,
                    user_id=locked_user.id,
                ).using_db(conn).annotate(total=Sum("amount")).values("total")
                total_paid_tx = float((paid_row[0]["total"] if paid_row else 0.0) or 0.0)

                rent_row = await models.Rent.filter(
                    sms_text__isnull=False,
                    created_at__lte=cutoff_dt,
                    user_id=locked_user.id,
                ).exclude(sms_text="").using_db(conn).annotate(total=Sum("cost")).values("total")
                total_rent_tx = float((rent_row[0]["total"] if rent_row else 0.0) or 0.0)

                act_row = await models.Activation.filter(
                    sms_text__isnull=False,
                    created_at__lte=cutoff_dt,
                    user_id=locked_user.id,
                ).exclude(sms_text="").using_db(conn).annotate(total=Sum("cost")).values("total")
                total_act_tx = float((act_row[0]["total"] if act_row else 0.0) or 0.0)

                total_spent_tx = total_rent_tx + total_act_tx
                balance_now = float(getattr(locked_user, "balance", 0.0) or 0.0)

                diff_tx = (total_spent_tx + balance_now) - total_paid_tx
                if diff_tx <= EPS:
                    continue

                target_balance_tx = max(0.0, total_paid_tx - total_spent_tx)

                if balance_now <= target_balance_tx + EPS:
                    # либо уже исправлено, либо проблема не “в лишнем балансе”
                    continue

                # Если target_balance_tx == 0, а diff_tx всё равно большой — это перерасход (балансом не лечится)
                if target_balance_tx <= EPS and diff_tx > EPS:
                    overspend_count += 1
                    msg = (
                        "⚠️ OVESPEND (балансом не исправить)\n"
                        f"telegram_id: {locked_user.telegram_id}\n"
                        f"оплаты: {total_paid_tx:.2f}\n"
                        f"потрачено: {total_spent_tx:.2f} (rent={total_rent_tx:.2f} + act={total_act_tx:.2f})\n"
                        f"баланс: {balance_now:.2f}\n"
                        f"разница: {diff_tx:.2f}\n"
                        f"cutoff: now-{GRACE_MINUTES}min\n"
                    )
                    await send_coder(msg)
                    continue

                locked_user.balance = float(target_balance_tx)
                await locked_user.save(update_fields=["balance"])

            fixed_count += 1

            msg = (
                "🛠️ AUTO-FIX BALANCE DISCREPANCY\n"
                f"telegram_id: {user.telegram_id}\n"
                f"оплаты: {total_paid:.2f}\n"
                f"потрачено: {total_spent:.2f} (rent={total_rent_cost:.2f} + act={total_activation_cost:.2f})\n"
                f"баланс: {balance_before:.2f} → {target_balance:.2f}\n"
                f"разница: {diff:.2f}\n"
                f"cutoff: now-{GRACE_MINUTES}min\n"
            )
            await send_coder(msg)

            logger.bind(
                user_id=user.telegram_id,
                action="auto_fix_users_balance_discrepancy",
            ).warning(
                f"Исправлен баланс: {balance_before:.2f} -> {target_balance:.2f} (diff={diff:.2f})"
            )

        logger.bind(action="auto_fix_users_balance_discrepancy").info(
            f"Готово. Исправлено: {fixed_count}, overspend: {overspend_count}"
        )

    except Exception as e:
        logger.opt(exception=e).error("Ошибка в auto_fix_users_balance_discrepancy")


# ──────────────────────────────────────────────────────────────────────────────
# Уведомление 1: пользователь неактивен 30+ дней → скидка 15%
# ──────────────────────────────────────────────────────────────────────────────

_EMOJI_LETTER = "<tg-emoji emoji-id='5472239203590888751'>📩</tg-emoji>"
_EMOJI_DOWN   = "<tg-emoji emoji-id='5197474438970363734'>⤵️</tg-emoji>"
_EMOJI_PHONE  = "<tg-emoji emoji-id='5819099456745770209'>📱</tg-emoji>"

_INACTIVITY_TEXT = (
    f"{_EMOJI_LETTER}Вижу ты давно не использовал EmailFast\n\n"
    "Специально для тебя сегодня скидка на все наши номера и электронные почты\n\n"
    f"Жми на кнопку и получи скидку 15% на сутки{_EMOJI_DOWN}"
)

def _unpaid_sms_text(service_name: str) -> str:
    return (
        f"{_EMOJI_PHONE}Тебе остался всего один шаг, чтобы получить номер для {service_name}\n\n"
        f"Нажми кнопку ниже чтобы продолжить{_EMOJI_DOWN}"
    )


async def notify_inactive_users() -> None:
    """
    Ищет пользователей, неактивных 30+ дней, и отправляет им предложение скидки 15%.
    Отправляет только если скидка сейчас не активна и уведомление в эту волну ещё не слали.
    """
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        now = datetime.datetime.now(pytz.utc)
        threshold = now - datetime.timedelta(days=30)

        candidates = await models.User.filter(
            last_active_at__lt=threshold,
            last_active_at__isnull=False,
        ).all()

        sent = 0
        for user in candidates:
            # Скидка уже активна — не отправляем
            if (
                user.inactivity_discount_end_at is not None
                and user.inactivity_discount_end_at.replace(tzinfo=pytz.utc) > now
            ):
                continue

            # Уже уведомляли после последней активности — не дублируем
            if (
                user.inactivity_notified_at is not None
                and user.last_active_at is not None
                and user.inactivity_notified_at.replace(tzinfo=pytz.utc)
                    > user.last_active_at.replace(tzinfo=pytz.utc)
            ):
                continue

            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Получить скидку", callback_data="get_inactivity_discount")
            ]])
            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=_INACTIVITY_TEXT,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
                user.inactivity_notified_at = now
                await user.save(update_fields=["inactivity_notified_at"])
                sent += 1
            except Exception as e:
                logger.bind(user_id=user.telegram_id).warning(
                    f"notify_inactive_users: не удалось отправить: {e}"
                )

        logger.bind(action="notify_inactive_users").info(f"Отправлено уведомлений о неактивности: {sent}")

    except Exception as e:
        logger.opt(exception=e).error("Ошибка в notify_inactive_users")


# ──────────────────────────────────────────────────────────────────────────────
# Уведомление 2: пользователь создал счёт на номер, но не оплатил — через 3 часа
# ──────────────────────────────────────────────────────────────────────────────

async def notify_unpaid_sms_payments() -> None:
    """
    Ищет неоплаченные Payment-записи с continue_data (покупка SMS-номера),
    созданные 3–24 часа назад, и отправляет напоминание.
    """
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        now = datetime.datetime.now(pytz.utc)
        window_start = now - datetime.timedelta(hours=24)
        window_end   = now - datetime.timedelta(hours=3)

        payments = await models.Payment.filter(
            is_success=False,
            reminder_sent=False,
            created_at__gte=window_start,
            created_at__lte=window_end,
        ).prefetch_related("user").all()

        # Оставляем только те, у которых есть continue_data с service_code
        # (признак покупки SMS-номера, а не просто пополнение баланса)
        seen_users: set[int] = set()
        sent = 0

        for payment in payments:
            cd = payment.continue_data
            if not cd or "service_code" not in cd:
                continue

            uid = payment.user_id
            if uid in seen_users:
                continue
            seen_users.add(uid)

            service_name = cd.get("service_name") or cd.get("service_code") or "сервис"
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Получить номер", callback_data="resume_sms_payment")
            ]])
            try:
                await bot.send_message(
                    chat_id=payment.user.telegram_id,
                    text=_unpaid_sms_text(service_name),
                    reply_markup=kb,
                    parse_mode="HTML",
                )
                # Помечаем все незавершённые платежи этого пользователя как notified
                await models.Payment.filter(
                    user_id=uid,
                    is_success=False,
                    reminder_sent=False,
                ).update(reminder_sent=True)
                sent += 1
            except Exception as e:
                logger.bind(user_id=payment.user.telegram_id).warning(
                    f"notify_unpaid_sms_payments: не удалось отправить: {e}"
                )

        logger.bind(action="notify_unpaid_sms_payments").info(f"Отправлено напоминаний об оплате: {sent}")

    except Exception as e:
        logger.opt(exception=e).error("Ошибка в notify_unpaid_sms_payments")
