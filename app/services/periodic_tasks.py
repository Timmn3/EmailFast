import json
from math import floor
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import types
from aiohttp import ClientSession
from app.services.onlinesim.sms_client import OnlineSMS
from tortoise.functions import Sum

from tortoise import timezone
from loguru import logger
from app import dependencies
from app.db import models
from app.dependencies import bot, FK_SHOP_ID, FK_FK_API_KEY, CODER, API_KEY_ONLINESIM, PROJECT_MANAGER
from app.dialogs.receive_sms.getters import service_is_smsactivate
from app.dialogs.rent_sms.getters import get_day_string
from app.handlers.get_email_handler import get_extend_email_kb
from app.services.bot_texts import country_flags
from app.services.mail.receive_messages import get_unread_messages
from app.services.onlinesim.rent_number import OnlineSimRentAPI
from app.services.payments.anypay import AnypayAPI
from app.services.payments.ckassa import get_ckassa_payments
from app.services.payments.cryptomus import get_paid_order_ids
from app.services.payments.freekassa import Freekassa
from app.services.payments.lava import LavaApi
from app.services.payments.streampay import get_payment_status_streampay
from app.services.sms_receive import SmsReceive
from app.services.temp_mail import TempMail
from app.services import bot_texts as bt
import pytz
import datetime

from app.services.payments.yoomoney import check_payment_status

from tortoise import timezone
from tortoise.transactions import in_transaction

from app.db import models
from app.db.models import Activation, StatusResponse
from app.dependencies import bot


async def refund_and_cleanup_expired_sms() -> None:
    """
    Находит истёкшие активации, по которым не пришло СМС (WAIT_CODE),
    удаляет сервисное сообщение, возвращает деньги и уведомляет пользователя.

    Идемпотентность:
    - Лочим строку Activation через SELECT FOR UPDATE
    - Внутри транзакции повторно проверяем status/expiry/sms_text
    - Переводим в CANCEL и возвращаем деньги строго один раз
    """
    now = timezone.now()

    # Берём только ID (чтобы не тащить user relation и не работать со "старыми" объектами)
    expired_ids = await Activation.filter(
        activation_expire_at__lte=now,
        status=StatusResponse.STATUS_WAIT_CODE,
    ).values_list("id", flat=True)

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

                # Повторный чек условий уже "под замком"
                if act.status != StatusResponse.STATUS_WAIT_CODE:
                    continue

                # если expire_at по какой-то причине NULL — не трогаем
                if not act.activation_expire_at or act.activation_expire_at > timezone.now():
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
                    await bot.delete_message(chat_id=user_tg_id, message_id=service_msg_id)
                    logger.info(
                        f"🗑 Удалено сервисное сообщение: activation_id={ext_activation_id}, msg_id={service_msg_id}"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение activation_id={ext_activation_id}: {e}")

            # 2) Уведомление пользователю
            try:
                await bot.send_message(
                    chat_id=user_tg_id,
                    text=(
                        "⚡️<b>SMS не поступило, деньги уже вернулись на ваш баланс.</b>\n\n"
                        "🔄Попробуйте новый номер или выберите другую страну.\n"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление пользователю {user_tg_id}: {e}")

            logger.bind(user_id=user_tg_id, action="refund_activation").log(
                "USER_ACTION",
                f"Возврат средств за истёкшую активацию: id={ext_activation_id}, номер={phone_number}, сумма={cost}₽"
            )

        except Exception as e:
            logger.opt(exception=e).error(f"Ошибка при обработке истёкшей активации pk={activation_pk}")



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
        orders = fk.get_orders(order_status=1, date_from=five_hours_ago)
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


async def check_payment_ckassa():
    """
    Асинхронная функция для проверки статуса платежей CKassa.
    """
    payments = await models.Payment.get_ckassa_payments()

    for payment in payments:
        try:
            payment_data = await get_ckassa_payments(payment.invoice_id)

            if payment_data and payment_data.get('state') == 'PAYED':
                payment.is_success = True
                await payment.save()

                if payment.user.bonus_end_at is not None and payment.user.bonus_end_at > timezone.now():
                    amount = floor(payment.amount * 1.1)
                    payment.user.bonus_end_at = None
                else:
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

                payment.user.balance += amount
                await payment.user.save()

                await balance_replenishment_notification(payment, "ckassa")
                await bot.send_message(
                    chat_id=payment.user.telegram_id,
                    text=f'<b>💰Баланс успешно пополнен на {amount}₽</b>'
                )

                await process_referral_bonus(payment)

        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.warning(e)
            await replenishment_error_message(payment, "CKassa")


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


import html  # <- добавь этот импорт рядом с import re
import re


async def check_sms():
    try:
        # Получаем все активные активации
        activations = await models.Activation.get_active_activations()

        # Обрабатываем каждую активную активацию
        for activation in activations:
            # Получаем статус активации по её идентификатору
            if len(str(activation.activation_id)) > 9:
                sms = SmsReceive()
                status = str(await sms.get_activation_status(activation.activation_id))
                try:
                    name = activation.service.name
                except Exception:
                    name = None
                # STATUS_OK:1231
            else:
                client = OnlineSMS(api_key=API_KEY_ONLINESIM)

                # ⚠️ OnlineSim может отвечать временной ошибкой TryAgainLater — не валим весь job, просто ждём следующий тик
                try:
                    order_info = await client.get_order_info(
                        operation_id=activation.activation_id,
                        get_full_message=True
                    )
                except Exception as e:
                    if e.__class__.__name__ == "TryAgainLater":
                        continue
                    raise

                name = await activation.get_service_2_name()

                if order_info and isinstance(order_info, list) and "msg" in order_info[0]:
                    sms_code = order_info[0]["msg"]
                    status = f"STATUS_OK:{sms_code}"
                else:
                    continue

            # Проверяем, начинается ли статус с 'STATUS_OK'
            if status.startswith(models.StatusResponse.STATUS_OK.name):
                # Обновляем статус активации на 'STATUS_OK'
                activation.status = models.StatusResponse.STATUS_OK

                # Смотрим какая смс в БД
                current_sms = activation.sms_text if activation.sms_text is not None else '1'

                # Извлекаем полный текст SMS из статуса
                sms_from_status_raw = status.split(":", 1)[1].strip()

                # Ты хочешь хранить/передавать весь текст — ок
                sms_from_status = sms_from_status_raw

                activation.sms_text = sms_from_status_raw
                await activation.save()

                # Загружаем связанные данные пользователя и сервиса
                if await service_is_smsactivate():
                    await activation.fetch_related("user", "service")
                else:
                    await activation.fetch_related("user", "service_2")

                # Формируем текст сообщения для отправки пользователю если смс новая
                if current_sms != sms_from_status:
                    safe_name = html.escape(str(name)) if name else None
                    safe_code = html.escape(str(activation.sms_text))

                    if safe_name:
                        msg_text = (
                            f"💬<b>Новое SMS</b> на номер: +{activation.phone_number}\n\n"
                            f"Ваш код активации для <b>{safe_name}</b>:\n"
                            f"<code>{safe_code}</code>"
                        )
                    else:
                        msg_text = (
                            f"💬<b>Новое SMS</b> на номер: +{activation.phone_number}\n\n"
                            f"Ваш код активации:\n"
                            f"<code>{safe_code}</code>"
                        )

                    # Отправляем сообщение пользователю в Telegram (явно HTML)
                    await bot.send_message(
                        chat_id=activation.user.telegram_id,
                        text=msg_text,
                        parse_mode="HTML",
                    )

                    await notice_of_arraignment("Получение смс", activation, name)
                    logger.bind(
                        user_id=activation.user.telegram_id,
                        action="new_sms"
                    ).log(
                        "USER_ACTION",
                        f"Получено новое SMS для номера {activation.phone_number}, код: {sms_from_status}"
                    )
        # Идемпотентный авто-рефанд SMS
        await refund_and_cleanup_expired_sms()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        # ✅ Безопасно достаём telegram_id: relation может быть не загружен и выглядеть как QuerySet/manager
        user_tg = None
        user_pk = None
        phone = None
        act_id = None
        status_name = None
        svc_name = None

        if 'activation' in locals():
            user_tg = getattr(getattr(activation, "user", None), "telegram_id", None)
            user_pk = getattr(activation, "user_id", None)
            phone = getattr(activation, "phone_number", None)
            act_id = getattr(activation, "activation_id", None)
            status_obj = getattr(activation, "status", None)
            status_name = getattr(status_obj, "name", None)
        if 'name' in locals():
            svc_name = name

        user_label = user_tg or (f"Неизвестно (user_id={user_pk})" if user_pk else "Неизвестно")

        error_info = f"""
        ❌ Ошибка в check_sms
        ───────────────────
        🔹 Пользователь: {user_label}
        🔹 Номер: {phone or 'Неизвестно'}
        🔹 Сервис: {svc_name or 'Неизвестно'}
        🔹 ID активации: {act_id or 'Неизвестно'}
        🔹 Статус: {status_name or 'Неизвестно'}

        ⚠️ Ошибка: {e}
        """
        await send_coder(error_info)
        logger.opt(exception=e).error("Необработанная ошибка в check_sms")


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
                    f'📩<b>Новое сообщение</b> на почту: <b>{mail.email}</b>\n\n'
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
            await mail.save()
            await bot.send_message(
                chat_id=mail.user.telegram_id,
                text="⏰ Бесплатная неделя аренды почты заканчивается через 24 часа!\n"
                     "Для продления аренды выберите тариф:",
                reply_markup=get_extend_email_kb(mail.id, False)
            )
        except Exception as e:
            logger.opt(exception=e).error("Ошибка уведомления о завершении недели")
            continue

    # отключение флага is_free_week, если неделя уже истекла
    expired = await models.Mail.filter(
        is_active=False,
        is_free_week=True,
        expire_at__lt=now
    )

    for mail in expired:
        mail.is_free_week = False
        await mail.save()


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
            parse_mode=None,
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

FRAUD_DIFF_THRESHOLD = 200.0


async def check_fraud_balance_discrepancy() -> None:
    """
    Ежечасная проверка по аналогии с /users_with_discrepancy:
    если (расходы + баланс) - пополнения > FRAUD_DIFF_THRESHOLD → уведомляем CODER и баним пользователя (fraud_banned=True).
    """
    try:
        logger.bind(action="check_fraud_balance_discrepancy").info("Старт проверки fraud-дисбаланса")

        # --- агрегируем пополнения ---
        payments = await models.Payment.filter(is_success=True).group_by("user_id").annotate(
            total_paid=Sum("amount")
        ).values("user_id", "total_paid")

        # --- агрегируем расходы (как в /users_with_discrepancy) ---
        rents = await models.Rent.filter(
            sms_text__isnull=False,
        ).exclude(
            sms_text=""
        ).group_by("user_id").annotate(
            total_rent_cost=Sum("cost")
        ).values("user_id", "total_rent_cost")

        activations = await models.Activation.filter(
            sms_text__isnull=False,
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
            uid = p["user_id"]
            _get(uid)["total_paid"] = float(p["total_paid"] or 0.0)

        for r in rents:
            uid = r["user_id"]
            _get(uid)["total_rent_cost"] = float(r["total_rent_cost"] or 0.0)

        for a in activations:
            uid = a["user_id"]
            _get(uid)["total_activation_cost"] = float(a["total_activation_cost"] or 0.0)

        base_ids = set(user_data.keys())

        # ✅ КЛЮЧЕВОЕ: добавляем пользователей с большим балансом,
        # даже если у них нет записей в payments/rents/activations (например, баланс поправили руками).
        extra_balance_ids = await models.User.filter(
            fraud_banned=False,
            balance__gt=FRAUD_DIFF_THRESHOLD,
        ).exclude(
            telegram_id__in=dependencies.ADMINS
        ).values_list("id", flat=True)

        for uid in extra_balance_ids:
            _get(int(uid))  # создаём дефолтные нули, чтобы diff считался корректно

        user_ids = list(base_ids.union(set(map(int, extra_balance_ids))))
        if not user_ids:
            logger.bind(action="check_fraud_balance_discrepancy").info("Нет данных для проверки (user_ids пуст)")
            return

        # берём только не забаненных fraud и НЕ админов
        candidates = await models.User.filter(
            id__in=user_ids,
            fraud_banned=False,
        ).exclude(
            telegram_id__in=dependencies.ADMINS
        )

        logger.bind(action="check_fraud_balance_discrepancy").info(
            f"Кандидаты: {len(candidates)} (base={len(base_ids)}, extra_balance={len(extra_balance_ids)})"
        )

        now = timezone.now()
        banned_count = 0

        for user in candidates:
            d = user_data.get(user.id, {})
            total_paid = float(d.get("total_paid", 0.0) or 0.0)
            total_rent_cost = float(d.get("total_rent_cost", 0.0) or 0.0)
            total_activation_cost = float(d.get("total_activation_cost", 0.0) or 0.0)

            total_spent = total_rent_cost + total_activation_cost
            balance = float(getattr(user, "balance", 0.0) or 0.0)

            diff = (total_spent + balance) - total_paid

            if diff <= FRAUD_DIFF_THRESHOLD:
                continue

            # --- баним ---
            user.fraud_banned = True
            update_fields = ["fraud_banned"]

            # эти поля могут отсутствовать — ставим только если реально есть в модели
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

            username = getattr(user, "username", None) or "-"
            first_name = getattr(user, "first_name", None) or "-"
            last_name = getattr(user, "last_name", None) or "-"

            msg = (
                "🚨 AUTO-FRAUD BAN\n"
                f"telegram_id: {user.telegram_id}\n"
                f"username: @{username}\n"
                f"оплаты: {total_paid:.2f}\n"
                f"потрачено: {total_spent:.2f} (rent={total_rent_cost:.2f} + act={total_activation_cost:.2f})\n"
                f"баланс: {balance:.2f}\n"
                f"разница: {diff:.2f}\n"
                f"порог: {FRAUD_DIFF_THRESHOLD:.2f}\n"
            )
            kb = InlineKeyboardBuilder()
            kb.button(text="✅ Разбанить", callback_data=f"fraud_unban:{user.telegram_id}")

            await send_coder(msg, reply_markup=kb.as_markup())

            if PROJECT_MANAGER:
                try:
                    await bot.send_message(
                        chat_id=PROJECT_MANAGER,
                        text=str(msg),
                        parse_mode=None,
                        disable_web_page_preview=True,
                        reply_markup=kb.as_markup(),
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить AUTO-FRAUD BAN PM={PROJECT_MANAGER}: {e}")

            # опционально: уведомим пользователя (мягко)
            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        "🚫 Доступ ограничен.\n\n"
                        "Обнаружено несоответствие баланса и пополнений.\n"
                        "Если это ошибка — напишите в поддержку."
                    ),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

            logger.bind(user_id=user.telegram_id, action="check_fraud_balance_discrepancy").warning(
                f"Пользователь fraud_banned=True, diff={diff:.2f}"
            )

        logger.bind(action="check_fraud_balance_discrepancy").info(f"Готово. Забанено: {banned_count}")

    except Exception as e:
        logger.opt(exception=e).error("Ошибка в check_fraud_balance_discrepancy")

