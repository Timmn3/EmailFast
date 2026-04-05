from operator import truediv
from typing import Union

from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram_dialog import DialogManager, StartMode

from app.services.bot_texts import SERVICE_CANCEL
from app.services.onlinesim.sms_client import OnlineSMS
from aiogram.exceptions import TelegramBadRequest
from app.db import models
from app.dependencies import API_KEY_ONLINESIM, bot
from app.dialogs.receive_sms.selected import send_service_info_with_keyboard
from app.dialogs.receive_sms.states import ServiceMenu
from app.services import bot_texts as bt
from app.services.mail.receive_messages import fetch_full_message
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from app.services.periodic_tasks import notice_of_arraignment
from app.services.sms_fast.smsfast_client import get_smsfast_client
from app.services.sms_receive import SmsReceive
from loguru import logger
import html
from bs4 import BeautifulSoup
import re
import asyncio


router = Router()

# Сопоставление сервисов для провайдера SMSFast: название -> код
SMSFAST_SERVICE_MAP = {
    "telegram": "tg",
    "vkcom":    "vk",
    "google":   "go",
    "tiktok":   "tt",
    "amazon":   "am",
    "claude":   "cl",
    "ot":       "ot",   # "Любой другой"
}

async def is_smsfast_enabled() -> bool:
    """Проверяет, включен ли провайдер SMSFast по флагу в настройках."""
    value = await models.AdminSettings.get_setting_value("smsfast_enabled")
    return (str(value).lower() in ("1", "true", "yes"))

def log_exceptions(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.opt(exception=e).error(f"Ошибка в обработчике {func.__name__}: {e}")
            raise
    return wrapper


@router.callback_query(F.data == "receive_sms")
@router.message(Command("get_sms"))
@router.message(F.text == bt.RECEIVE_SMS_BTN)
async def receive_sms(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager):
    """
    Открывает раздел получения SMS.

    Поддерживает:
    - inline-кнопку главного меню;
    - старую текстовую reply-кнопку;
    - команду /get_sms.
    """
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Пользователь запросил получение SMS")

        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="receive_sms").log(
            "USER_ACTION",
            f"Результат из БД: пользователь найден={'True' if user else 'False'}, баланс = {user.balance if user else 'N/A'} ₽"
        )

        if not user:
            if isinstance(message, types.CallbackQuery):
                await message.answer()
            return

        logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Проверка активации")
        activation = await models.Activation.get_active_activation(user.id)

        if activation is None:
            sub = await check_subscribe(user)
            if not sub:
                logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Подписка неактивна, отправляем сообщение")
                await send_subscribe_msg(user)
                if isinstance(message, types.CallbackQuery):
                    await message.answer()
                return

            logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Запуск диалога выбора сервиса")
            await dialog_manager.start(ServiceMenu.select_service, mode=StartMode.RESET_STACK)

            if isinstance(message, types.CallbackQuery):
                await message.answer()
        else:
            logger.bind(user_id=user_id, action="receive_sms").log("USER_ACTION", "Получение информации о текущей активации")
            await activation.fetch_related('country')
            country = activation.country.name if activation.country else 'Неизвестно'

            service = None
            if activation.service_id:
                service = await models.ServicesSmsActivate.get_service_name_by_id(service_id=activation.service_id)
            if not service and activation.service_2_id:
                service = await models.ServicesOnlinesim.get_service_name_by_id(service_id=activation.service_2_id)
            service = service or 'Неизвестно'

            logger.bind(user_id=user_id, action="receive_sms").log(
                "USER_ACTION",
                f"Текущая активация: сервис={service}, страна={country}"
            )

            target_message = message.message if isinstance(message, types.CallbackQuery) else message
            await send_service_info_with_keyboard(
                message=target_message,
                activation=activation,
                service=service,
                country=country
            )

            if isinstance(message, types.CallbackQuery):
                await message.answer()

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_sms: {e}")
        if isinstance(message, types.CallbackQuery):
            try:
                await message.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
            except Exception:
                pass


@router.callback_query(F.data == 'receive_sms_for_another_service')
async def receive_sms_for_another_service(call: types.CallbackQuery, dialog_manager: DialogManager):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="receive_sms_for_another_service").log("USER_ACTION", "Принять SMS для другого сервиса")
        await dialog_manager.reset_stack()
        await dialog_manager.start(ServiceMenu.select_service, mode=StartMode.NORMAL)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_sms_for_another_service: {e}")


# --- Антидубль: не запускаем несколько запросов "новое SMS" на одну и ту же активацию ---
_request_code_tasks: dict[int, asyncio.Task] = {}


async def _request_code_worker(user_id: int, activation_pk: int) -> None:
    """
    Фоновая обработка кнопки "📩 Принять новое SMS на этот же номер".

    Критично (анти-фрод / анти-гонки):
    - Нельзя отправлять SMS, если активация уже отменена/рефанднута (STATUS_CANCEL).
    - Любое решение "отправлять или нет" + запись sms_text делаем под SELECT FOR UPDATE,
      чтобы закрыть гонку между cancel_service / авто-рефандом / check_sms.
    - Если sms_text уже записан — повторно не шлём.
    """
    try:
        import html as _html
        from tortoise.transactions import in_transaction

        logger.bind(user_id=user_id, action="request_code_worker").log(
            "USER_ACTION",
            f"Фоновая обработка request_code: activation_pk={activation_pk}"
        )

        activation = await models.Activation.get_or_none(id=activation_pk).prefetch_related('service', 'service_2')
        if not activation:
            return

        # Чтобы ниже не падало на relation (если у тебя user relation ленивый)
        try:
            await activation.fetch_related("user")
        except Exception:
            pass

        # ✅ Определяем провайдера строго по activation.provider (а не по наличию relation)
        provider = (getattr(activation, "provider", "") or "").strip().lower()
        if not provider:
            # fallback для старых записей
            provider = "smsactivate" if getattr(activation, "service_id", None) else "onlinesim"

        if provider == "onlinesim":
            client = OnlineSMS(api_key=API_KEY_ONLINESIM)

            try:
                revise_response = await client.revise_order(operation_id=activation.activation_id)
            except Exception as e:
                logger.opt(exception=e).warning(f"Повторный запрос SMS (onlinesim) упал: {e}")
                await bot.send_message(chat_id=user_id, text="⚠️ Не удалось запросить повторное SMS. Попробуйте позже.")
                return

            if revise_response.get("response") != "1":
                await bot.send_message(chat_id=user_id, text="⚠️ Повторная отправка недоступна для этого номера.")
                return

            # Быстрый чек: вдруг SMS уже прилетело. Если нет — молча выходим, дальше отработает твой общий механизм получения SMS.
            try:
                order_info = await client.get_order_info(
                    operation_id=activation.activation_id,
                    get_full_message=True,
                    form=1,
                    clean=0
                )
            except Exception:
                return

            sms_text = None
            if order_info and isinstance(order_info, list):
                sms_text = (order_info[0] or {}).get("msg")

            if not sms_text:
                return

            sms_clean = str(sms_text).strip()

            # ⚠️ Финальная проверка под локом: если CANCEL — ничего не отправляем.
            should_send = False
            act = None

            async with in_transaction() as conn:
                act = await models.Activation.filter(id=activation_pk).using_db(conn).select_for_update().first()
                if not act:
                    return

                # если пользователь уже отменил (или авто-рефанд успел сработать) — не отдаём SMS
                if act.status == models.StatusResponse.STATUS_CANCEL:
                    logger.bind(user_id=user_id, action="request_code_worker").log(
                        "USER_ACTION",
                        f"SMS не отправлено: активация отменена (activation_pk={activation_pk})"
                    )
                    return

                prev_sms = (getattr(act, "sms_text", None) or "").strip()
                should_send = (prev_sms != sms_clean)

                # фиксируем sms_text в БД, чтобы дальше всё было консистентно
                if should_send or act.status != models.StatusResponse.STATUS_OK:
                    act.status = models.StatusResponse.STATUS_OK
                    act.sms_text = sms_clean
                    await act.save(using_db=conn, update_fields=["status", "sms_text"])

            # работаем дальше с "актуальной" активацией (после лока)
            activation = act or activation

            # подгрузим отношения для notice_of_arraignment
            try:
                await activation.fetch_related("user", "service_2")
            except Exception:
                pass

            if should_send:
                safe_sms = _html.escape(sms_clean)
                msg_text = (
                    f"💬<b>Повторное SMS</b> на номер: +{activation.phone_number}\n\n"
                    f"Ваш код активации:\n"
                    f"<code>{safe_sms}</code>"
                )

                # ✅ После первого полученного SMS включаем обязательную проверку подписки
                if getattr(activation, "user", None) is not None and not getattr(activation.user,
                                                                                 "channel_gate_enabled", True):
                    activation.user.channel_gate_enabled = True
                    await activation.user.save(update_fields=["channel_gate_enabled"])

                await bot.send_message(chat_id=user_id, text=msg_text, parse_mode="HTML")
                await notice_of_arraignment("Получение смс", activation, sms_clean)

                try:
                    logger.bind(
                        user_id=getattr(getattr(activation, "user", None), "telegram_id", user_id),
                        action="new_sms"
                    ).log(
                        "USER_ACTION",
                        f"Получено новое SMS для номера {activation.phone_number}, код: {sms_clean}"
                    )
                except Exception:
                    pass

            return

        if provider == "smsfast":
            smsfast = get_smsfast_client()
            try:
                resp = await smsfast.request_additional_sms(activation.activation_id)
            except Exception as e:
                logger.opt(exception=e).warning(f"Повторный запрос SMS (smsfast) упал: {e}")
                await bot.send_message(chat_id=user_id, text="⚠️ Не удалось запросить повторное SMS. Попробуйте позже.")
                return

            # По текущей реализации клиента SMSFast часто отвечает BAD_STATUS, если повторное SMS недоступно
            resp_str = str(resp)
            if "BAD_STATUS" in resp_str or "BAD_ACTION" in resp_str:
                await bot.send_message(chat_id=user_id, text="⚠️ Повторная отправка недоступна. Попробуйте позже")
            return

        # SMSActivate: просто ставим статус, без долгих ожиданий в callback
        sms = SmsReceive()
        try:
            await sms.get_activation_status(activation.activation_id)
            await sms.set_activation_status(
                activation_id=activation.activation_id,
                status=models.ActivationCode.RETRY_GET
            )
        except Exception as e:
            logger.opt(exception=e).warning(f"Повторный запрос SMS (smsactivate) упал: {e}")
            await bot.send_message(chat_id=user_id, text="⚠️ Не удалось запросить повторное SMS. Попробуйте позже.")
            return

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в _request_code_worker: {e}")
        try:
            await bot.send_message(chat_id=user_id, text="⚠️ Ошибка при обработке запроса. Попробуйте позже.")
        except Exception:
            pass
    finally:
        current = asyncio.current_task()
        if current is not None and _request_code_tasks.get(activation_pk) is current:
            _request_code_tasks.pop(activation_pk, None)


@router.callback_query(F.data.startswith('request_code:'))
@log_exceptions
async def request_code(call: types.CallbackQuery, **kwargs):
    user_id = call.from_user.id
    activation_pk = int(call.data.split(':')[1])

    # ✅ СРАЗУ отпускаем Telegram-клиент (убираем "часики" и разблокируем кнопки)
    try:
        await call.answer("⏳ Запросил новое SMS…", show_alert=True)
    except Exception:
        pass

    # Антидубль на время выполнения
    existing = _request_code_tasks.get(activation_pk)
    if existing and not existing.done():
        return

    task = asyncio.create_task(_request_code_worker(user_id, activation_pk))
    _request_code_tasks[activation_pk] = task

    # ✅ Забираем исключение задачи, чтобы не было "Task exception was never retrieved"
    def _consume_task_result(t: asyncio.Task) -> None:
        try:
            _ = t.exception()
        except asyncio.CancelledError:
            pass
        except Exception:
            # exception() уже вернул исключение — логирование делается внутри worker через logger.opt(exception=e)
            pass

    task.add_done_callback(_consume_task_result)


@router.callback_query(F.data.startswith('cancel_service:'))
async def cancel_service(call: types.CallbackQuery, **kwargs):
    """
    Отмена активации пользователем:
    - Проверяем ограничения (первые 2 минуты, SMS уже пришло, идемпотентность).
    - Ставим STATUS_CANCEL + возвращаем деньги атомарно в транзакции.
    - После коммита: отвечаем на callback, чистим клавиатуру, шлём сообщение пользователю,
      и отправляем простой запрос отмены провайдеру (OnlineSim/SMSActivate/SMSFast) + логируем ответ.

    Важно: никаких запросов в Telegram и внешние API внутри транзакции.
    """
    from datetime import timedelta
    from tortoise import timezone
    from tortoise.transactions import in_transaction

    user_id = call.from_user.id

    try:
        activation_pk = int(call.data.split(':', 1)[1])

        # Ответ пользователю (toast через call.answer)
        answer_text: str | None = None

        # UX-флаги
        need_clear_kb: bool = False
        need_send_msg: bool = False
        send_msg_text: str | None = None

        # Данные возврата
        refund_amount: float = 0.0
        new_balance: float | None = None

        # Данные для действия у провайдера (после коммита)
        provider_to_cancel: str | None = None
        provider_activation_id: int | None = None
        need_provider_cancel: bool = False

        # Что делаем у провайдера: "cancel" или "finish"
        provider_action: str | None = None

        async with in_transaction() as conn:
            user = await models.User.select_for_update().using_db(conn).get_or_none(telegram_id=user_id)
            if not user:
                answer_text = "Пользователь не найден."
            else:
                activation = await models.Activation.select_for_update().using_db(conn).get_or_none(id=activation_pk)
                if not activation:
                    logger.bind(user_id=user_id, action="cancel_service").log("USER_ACTION", "Активация не найдена")
                    await bot.send_message(chat_id=user_id, text="Номер автоматически отменится через 15 минут")
                    return
                else:
                    # 📩 Если SMS уже пришло — разрешаем "отменить" даже в первые 2 минуты,
                    # но возврат денег не делаем. Для SMSFast корректнее завершить активацию (status=6).
                    sms_already_received = bool((activation.sms_text or "").strip())

                    # ⏱️ Блокируем отмену в первые 2 минуты ТОЛЬКО если SMS ещё НЕ пришло
                    now = timezone.now()
                    created_at = activation.created_at

                    # на случай, если created_at наивный, а now aware (или наоборот)
                    if created_at and created_at.tzinfo is None and now.tzinfo is not None:
                        now = now.replace(tzinfo=None)

                    if created_at and (not sms_already_received):
                        delta = now - created_at
                        if delta < timedelta(minutes=2):
                            seconds_left = int((timedelta(minutes=2) - delta).total_seconds())
                            await call.answer(
                                f"Нельзя отменить в первые 2 минуты. Осталось ~{seconds_left} сек.",
                                show_alert=True
                            )
                            return

                    if answer_text is None:
                        if sms_already_received:
                            # SMS уже пришло — просто закрываем локально (без возврата)
                            activation.status = models.StatusResponse.STATUS_CANCEL
                            await activation.save(using_db=conn, update_fields=["status"])
                            answer_text = SERVICE_CANCEL

                            # UX: убираем клавиатуру, чтобы не жали дальше
                            need_clear_kb = True

                            # Для SMSFast: вместо cancel -> finish (status=6), чтобы корректно завершить активацию
                            provider_to_cancel = (getattr(activation, "provider", "") or "").strip().lower()
                            if provider_to_cancel == "smsfast":
                                provider_activation_id = int(getattr(activation, "activation_id", 0) or 0)
                                if provider_activation_id:
                                    need_provider_cancel = True
                                    provider_action = "finish"

                        # ♻️ Идемпотентность: если уже CANCEL — повторно не возвращаем
                        elif activation.status == models.StatusResponse.STATUS_CANCEL:
                            answer_text = "Отмена больше не доступна"
                            activation.status = models.StatusResponse.STATUS_CANCEL
                            await activation.save(using_db=conn, update_fields=["status"])

                        else:
                            # ✅ Делаем отмену + возврат атомарно под локом
                            activation.status = models.StatusResponse.STATUS_CANCEL
                            await activation.save(using_db=conn, update_fields=["status"])

                            refund_amount = float(activation.cost or 0.0)
                            user.balance = float(user.balance or 0.0) + refund_amount
                            await user.save(using_db=conn, update_fields=["balance"])

                            new_balance = float(user.balance or 0.0)
                            need_clear_kb = True
                            need_send_msg = True
                            send_msg_text = bt.SERVICE_CANCEL_MONEY_RETURNED

                            # --- провайдер/ID для отмены (после коммита) ---
                            provider_to_cancel = (getattr(activation, "provider", "") or "").strip().lower()
                            if not provider_to_cancel:
                                # fallback для старых записей (по наличию relation)
                                provider_to_cancel = "smsactivate" if getattr(activation, "service_id", None) else "onlinesim"

                            provider_activation_id = int(getattr(activation, "activation_id", 0) or 0)
                            need_provider_cancel = bool(provider_activation_id)
                            provider_action = "cancel"

        # ✅ Сразу отвечаем на callback (чтобы не висел "часик")
        await call.answer()
        if answer_text:
            await bot.send_message(chat_id=user_id, text=answer_text, parse_mode="HTML")

        # 🧹 UX: убираем клавиатуру у конкретного сообщения, по которому нажали
        if need_clear_kb:
            try:
                if call.message:
                    await call.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        # ✉️ Обычным сообщением (не alert)
        if need_send_msg and send_msg_text:
            try:
                await bot.send_message(chat_id=user_id, text=send_msg_text, parse_mode="HTML")
            except Exception as e:
                logger.opt(exception=e).warning("Не удалось отправить сообщение пользователю после отмены")

        # 🧾 Лог возврата (после транзакции)
        if refund_amount > 0:
            logger.bind(user_id=user_id, action="cancel_service").log(
                "USER_ACTION",
                f"Возврат выполнен: activation_pk={activation_pk}, refund={refund_amount}, new_balance={new_balance} ₽"
            )

        # 🔌 Действие у провайдера (простое) + лог ответа
        if need_provider_cancel and provider_to_cancel and provider_activation_id:
            try:
                provider_resp = None

                if provider_to_cancel == "smsactivate":
                    from app.services.sms_receive import SmsReceive
                    sms = SmsReceive()
                    provider_resp = await sms.set_activation_status(
                        activation_id=provider_activation_id,
                        status=models.ActivationCode.CANCEL
                    )

                elif provider_to_cancel == "smsfast":
                    from app.services.sms_fast.smsfast_client import get_smsfast_client
                    smsfast = get_smsfast_client()

                    # Если SMS уже пришло — корректнее завершить (status=6), иначе — отменить (status=8)
                    if provider_action == "finish":
                        provider_resp = await smsfast.finish_activation(activation_id=provider_activation_id)
                    else:
                        provider_resp = await smsfast.cancel_activation(activation_id=provider_activation_id)

                elif provider_to_cancel == "onlinesim":
                    from app.services.onlinesim.sms_client import OnlineSMS
                    client = OnlineSMS(api_key=API_KEY_ONLINESIM)
                    # В SDK явного cancel нет — закрываем операцию (release номера)
                    provider_resp = await client.finish_order(operation_id=provider_activation_id, ban=False)

                else:
                    provider_resp = f"skip: unknown provider '{provider_to_cancel}'"

                logger.bind(user_id=user_id, action="cancel_service").opt(raw=True).log(
                    "USER_ACTION",
                    f"Provider action: action={provider_action}, provider={provider_to_cancel}, "
                    f"provider_activation_id={provider_activation_id}, response={provider_resp!r}"
                )



            except Exception as e:
                logger.opt(exception=e).error(
                    f"Ошибка при действии у провайдера: provider={provider_to_cancel}, provider_activation_id={provider_activation_id}"
                )

    except TelegramBadRequest as e:
        logger.opt(exception=e).warning(f"Telegram server error: {e}")
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в cancel_service: {e}")
        try:
            activation.status = models.StatusResponse.STATUS_CANCEL
            await activation.save(using_db=conn, update_fields=["status"])
            await bot.send_message(chat_id=user_id, text="Отмена больше не доступна")
        except Exception:
            pass


@router.callback_query(F.data.startswith('full_unread_message|'))
async def unread_message(call: types.CallbackQuery, **kwargs):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", "Пользователь запросил полный текст сообщения")
        _, message_id, mail_id = call.data.split("|")
        mail_id = int(mail_id)
        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", f"Запрос к БД: получение сообщения ID={mail_id}")
        mail = await models.Mail.get_or_none(id=mail_id).prefetch_related("user")
        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", f"Результат из БД: сообщение найдено={mail is not None}")

        if not mail:
            logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", "Ошибка: сообщение не найдено")
            return

        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", "Получение полного текста сообщения")
        text = await fetch_full_message(mail.token, message_id)

        # Удаляем HTML-теги <a> и <img>
        soup = BeautifulSoup(text, "html.parser")
        for a in soup.find_all("a"):
            a.decompose()
        for img in soup.find_all("img"):
            img.decompose()

        # Получаем очищенный текст
        cleaned_text = soup.get_text()
        # Удаляем ссылки вида "https://example.com "
        cleaned_text = re.sub(r"https?://\S+", "", cleaned_text)
        # Удаляем ссылки в формате [text](https://example.com )
        cleaned_text = re.sub(r"\[.*?\]\(https?://\S+\)", "", cleaned_text)
        # Экранируем HTML
        cleaned_text = html.escape(cleaned_text)

        msg_text = (
            f'📩<b>Полный текст сообщения</b> на почту: <b>{mail.email}</b>\n'
            f'{cleaned_text}'
        )

        logger.bind(user_id=user_id, action="unread_message").log("USER_ACTION", f"Отправка сообщения пользователю {mail.user.telegram_id}")
        if len(msg_text) <= 4096:
            await bot.send_message(chat_id=mail.user.telegram_id, text=msg_text, parse_mode="HTML")
        else:
            parts = await split_message(msg_text, 4096)
            for part in parts:
                await bot.send_message(chat_id=mail.user.telegram_id, text=part, parse_mode="HTML")
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /unread_message: {e}")


async def split_message(text: str, max_length: int) -> list:
    """Разбивает длинное сообщение на части, не превышающие max_length."""
    logger.bind(action="split_message").log("USER_ACTION", f"Разделение сообщения длиной {len(text)} символов")
    lines = text.split('\n')
    parts = []
    current_part = ""
    for line in lines:
        if len(current_part) + len(line) + 1 > max_length:
            parts.append(current_part)
            current_part = ""
        current_part += line + '\n'
    if current_part:
        parts.append(current_part)
    logger.bind(action="split_message").log("USER_ACTION", f"Сообщение разделено на {len(parts)} частей")
    return parts
