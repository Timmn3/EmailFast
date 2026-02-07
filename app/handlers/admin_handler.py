import random
import string
from datetime import datetime, timedelta
import pytz
from app.services.onlinesim.service_updater import add_services
from app.services.periodic_tasks import send_coder
from app.services.sms_receive import SmsReceive
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from app.db import models
from app.db.models import Activation, AdminSettings
from app.dependencies import ADMINS, bot, USER_ACCESS_TO_THE_COMMAND_USER_REPORT_AND_ADD_BALANCE
from app.services import bot_texts as bt
from tabulate import tabulate
from aiogram_dialog import DialogManager
from loguru import logger
from tortoise.functions import Sum, Count
import calendar
from tortoise import timezone

from app.services.sms_fast.smsfast_price_loader import update_smsfast_prices
from celery_worker import tasks as broadcast_tasks
from celery_worker.tasks import send_message_batch

from aiogram.types import FSInputFile


router = Router()


class BroadcastState(StatesGroup):
    send_message = State()

MONTHS_RU = {
    "January": "Январь", "February": "Февраль", "March": "Март",
    "April": "Апрель", "May": "Май", "June": "Июнь",
    "July": "Июль", "August": "Август", "September": "Сентябрь",
    "October": "Октябрь", "November": "Ноябрь", "December": "Декабрь"
}

# --- Кэш для /stat (чтобы админ не ждал тяжёлые COUNT'ы при частых запросах) ---
_STAT_CACHE_TTL_SECONDS = 30
_stat_cache_expires_at: datetime | None = None
_stat_cache_text: str | None = None
_stat_cache_kb: types.InlineKeyboardMarkup | None = None

@router.message(Command("update_price_smsfast"))
async def update_price_smsfast_cmd(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="update_price_smsfast").log(
        "USER_ACTION",
        "Команда /update_price_smsfast вызвана"
    )

    if message.from_user.id not in ADMINS:
        return

    await message.answer("⏳ Запускаю обновление цен SMSFast…")

    try:
        await update_smsfast_prices()
    except Exception as e:
        logger.opt(exception=e).error("Ошибка при update_smsfast_prices (ручной запуск)")
        await message.answer("❌ Ошибка при обновлении цен SMSFast. См. логи.")
        return

    await message.answer("✅ Цены SMSFast обновлены.")


@router.message(Command('stat'))
async def stat(message: types.Message):
    logger.bind(user_id=message.from_user.id, action='stat').log("USER_ACTION", "Команда /stat вызвана")

    if message.from_user.id not in ADMINS:
        return

    utc_now = datetime.now(pytz.timezone("Europe/Moscow"))

    # 1) Быстрый ответ из кэша
    global _stat_cache_expires_at, _stat_cache_text, _stat_cache_kb
    if (
        _stat_cache_expires_at
        and _stat_cache_text
        and _stat_cache_kb
        and utc_now < _stat_cache_expires_at
    ):
        await message.answer(_stat_cache_text, reply_markup=_stat_cache_kb)
        return

    today_start = utc_now.replace(hour=0, minute=0, second=0)

    # --- helpers ---
    async def _sum_amount(qs) -> float:
        rows = await qs.annotate(total=Sum("amount")).values("total")
        if not rows:
            return 0.0
        return float(rows[0].get("total") or 0.0)

    # --- Users ---
    users_count = await models.User.all().count()
    users_count_today = await models.User.filter(created_at__gte=today_start).count()

    # --- Email ---
    letters_count = await models.Letter.all().count()
    letters_count_today = await models.Letter.filter(created_at__gte=today_start).count()

    # --- SMS delivered ---
    sms_count = await models.Activation.filter(sms_text__isnull=False, sms_text__not="").count()
    sms_count_today = await models.Activation.filter(
        sms_text__isnull=False,
        sms_text__not="",
        created_at__gte=today_start
    ).count()

    sms_count_month = await models.Activation.filter(
        sms_text__isnull=False,
        sms_text__not="",
        created_at__gte=utc_now - timedelta(days=30)
    ).count()

    # --- SMS rented ---
    rented_sms_total = await models.Activation.all().count()
    rented_sms_month = await models.Activation.filter(created_at__gte=utc_now - timedelta(days=30)).count()
    rented_sms_today = await models.Activation.filter(created_at__gte=today_start).count()

    # --- Payments (КЛЮЧЕВОЕ УСКОРЕНИЕ) ---
    # Вместо: вытянуть все Payment в память и бегать циклом
    payments_count = 0
    payments_repeat_count = 0
    try:
        from tortoise import connections

        pay_table = models.Payment._meta.db_table
        conn = connections.get("default")
        rows = await conn.execute_query_dict(
            f'SELECT COUNT(*)::bigint AS total, '
            f'COUNT(DISTINCT user_id)::bigint AS uniq '
            f'FROM "{pay_table}" '
            f'WHERE is_success = TRUE'
        )
        total = int(rows[0].get("total") or 0) if rows else 0
        uniq = int(rows[0].get("uniq") or 0) if rows else 0
        payments_count = total
        payments_repeat_count = max(total - uniq, 0)
    except Exception:
        # Fallback (всё равно без .all()): считаем уникальных плательщиков
        payments_count = await models.Payment.filter(is_success=True).count()
        payer_ids = await models.Payment.filter(is_success=True).distinct().values_list("user_id", flat=True)
        payments_repeat_count = max(payments_count - len(payer_ids), 0)

    payments_count_today = await models.Payment.filter(is_success=True, created_at__gte=today_start).count()
    payments_amount_today = await _sum_amount(
        models.Payment.filter(is_success=True, created_at__gte=today_start)
    )

    # --- Rent email ---
    rent_email_count = await models.Mail.filter(is_paid_mail=True).all().count()
    rent_email_count_today = await models.Mail.filter(is_paid_mail=True, created_at__gte=today_start).count()
    rent_email_active_count = await models.Mail.filter(
        is_paid_mail=True,
        is_active=True,
        expire_at__gt=utc_now
    ).count()

    # --- Rented numbers ---
    rented_number_total = await models.Rent.all().annotate(total=Sum("purchase_count")).values("total")
    rented_number_total = rented_number_total[0]["total"] or 0

    rented_number_month = await models.Rent.filter(
        created_at__gte=utc_now - timedelta(days=30)
    ).annotate(total=Sum("purchase_count")).values("total")
    rented_number_month = rented_number_month[0]["total"] or 0

    rented_number_today = await models.Rent.filter(
        created_at__gte=today_start
    ).annotate(total=Sum("purchase_count")).values("total")
    rented_number_today = rented_number_today[0]["total"] or 0

    user_purchases = await models.Rent.all().group_by("user_id").annotate(
        total_purchases=Sum("purchase_count")
    ).values("user_id", "total_purchases")
    repeat_purchases_total = sum(
        max(int(user.get("total_purchases") or 0) - 1, 0)
        for user in user_purchases
    )

    # --- Month boundaries ---
    first_day_of_month = utc_now.replace(day=1, hour=0, minute=0, second=0)
    last_month = utc_now.month - 1 if utc_now.month > 1 else 12
    last_year = utc_now.year if utc_now.month > 1 else utc_now.year - 1
    first_day_of_last_month = datetime(last_year, last_month, 1, tzinfo=pytz.timezone("Europe/Moscow"))
    last_month_name = MONTHS_RU[calendar.month_name[last_month]]
    current_month_name = MONTHS_RU[calendar.month_name[utc_now.month]]

    payments_count_month = await models.Payment.filter(is_success=True, created_at__gte=first_day_of_month).count()
    payments_count_last_month = await models.Payment.filter(
        is_success=True,
        created_at__gte=first_day_of_last_month,
        created_at__lt=first_day_of_month
    ).count()

    payments_amount_month = await _sum_amount(
        models.Payment.filter(is_success=True, created_at__gte=first_day_of_month)
    )
    payments_amount_last_month = await _sum_amount(
        models.Payment.filter(
            is_success=True,
            created_at__gte=first_day_of_last_month,
            created_at__lt=first_day_of_month
        )
    )

    # --- STARS ---
    stars_today_count = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=today_start
    ).count()
    stars_today_amount = await _sum_amount(
        models.Payment.filter(
            method=models.PaymentMethod.STARS,
            is_success=True,
            created_at__gte=today_start
        )
    )

    stars_month_count = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=first_day_of_month
    ).count()
    stars_month_amount = await _sum_amount(
        models.Payment.filter(
            method=models.PaymentMethod.STARS,
            is_success=True,
            created_at__gte=first_day_of_month
        )
    )

    stars_last_month_count = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=first_day_of_last_month,
        created_at__lt=first_day_of_month
    ).count()
    stars_last_month_amount = await _sum_amount(
        models.Payment.filter(
            method=models.PaymentMethod.STARS,
            is_success=True,
            created_at__gte=first_day_of_last_month,
            created_at__lt=first_day_of_month
        )
    )

    msg_text = bt.ADMIN_STAT.format(
        users_count=users_count,
        users_count_today=users_count_today,
        received_count=letters_count + sms_count,
        received_sms_count=sms_count,
        received_email_count=letters_count,
        received_count_today=letters_count_today + sms_count_today,
        received_sms_count_today=sms_count_today,
        received_email_count_today=letters_count_today,
        payments_count=payments_count,
        payments_repeat_count=payments_repeat_count,
        payments_count_today=payments_count_today,
        payments_amount_today=payments_amount_today,
        payments_count_month=payments_count_month,
        payments_count_last_month=payments_count_last_month,
        payments_amount_month=payments_amount_month,
        payments_amount_last_month=payments_amount_last_month,
        rent_email_count=rent_email_count,
        rent_email_count_today=rent_email_count_today,
        rent_email_active_count=rent_email_active_count,
        rented_sms_total=rented_sms_total,
        rented_sms_month=rented_sms_month,
        rented_sms_today=rented_sms_today,
        delivered_sms_total=sms_count,
        delivered_sms_month=sms_count_month,
        delivered_sms_today=sms_count_today,
        rented_number_total=rented_number_total,
        rented_number_month=rented_number_month,
        rented_number_today=rented_number_today,
        repeat_purchases_total=repeat_purchases_total,
        month_name=current_month_name,
        last_month_name=last_month_name,
        stars_count_today=stars_today_count,
        stars_amount_today=stars_today_amount,
        stars_month_count=stars_month_count,
        stars_month_amount=stars_month_amount,
        stars_last_month_count=stars_last_month_count,
        stars_last_month_amount=stars_last_month_amount,
    )

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Рефералы", callback_data="admin_referrals_top")]
    ])

    # 2) Сохраняем в кэш
    _stat_cache_text = msg_text
    _stat_cache_kb = kb
    _stat_cache_expires_at = utc_now + timedelta(seconds=_STAT_CACHE_TTL_SECONDS)

    await message.answer(msg_text, reply_markup=kb)

@router.callback_query(F.data == "admin_referrals_top")
async def admin_referrals_top(call: types.CallbackQuery):
    if call.from_user.id not in ADMINS:
        await call.answer()
        return

    await call.answer()

    # 1) ТОП по количеству приглашённых (users.refer_id = id инвайтера)
    ref_rows = (
        await models.User
        .filter(refer_id__isnull=False)
        .group_by("refer_id")
        .annotate(invited=Count("id"))
        .values("refer_id", "invited")
    )

    if not ref_rows:
        await call.message.answer("Рефералы не найдены.")
        return

    ref_rows.sort(key=lambda x: x["invited"], reverse=True)
    top_rows = ref_rows[:20]

    referrer_ids = [r["refer_id"] for r in top_rows]              # это internal users.id
    invited_map = {r["refer_id"]: r["invited"] for r in top_rows}

    # 2) Количество успешных оплат у приглашённых, сгруппированное по инвайтеру
    pay_rows = (
        await models.Payment
        .filter(is_success=True, user__refer_id__in=referrer_ids)
        .group_by("user__refer_id")
        .annotate(payments_count=Count("id"))
        .values("user__refer_id", "payments_count")
    )
    payments_map = {r["user__refer_id"]: r["payments_count"] for r in pay_rows}

    # 3) Баланс реферала (инвайтера)
    referrers = await models.User.filter(id__in=referrer_ids).values("id", "telegram_id", "ref_balance")
    ref_map = {u["id"]: u for u in referrers}

    lines = ["🏆 <b>ТОП-20 рефоводов</b>\n"]
    for i, rid in enumerate(referrer_ids, start=1):
        u = ref_map.get(rid)
        if not u:
            continue

        telegram_id = u["telegram_id"]
        ref_balance = float(u["ref_balance"] or 0)
        invited = int(invited_map.get(rid, 0))
        pays = int(payments_map.get(rid, 0))

        lines.append(
            f"{i}) <code>{telegram_id}</code>\n"
            f"Приведено пользователей: <b>{invited}</b>\n"
            f"Количество оплат: <b>{pays}</b>\n"
            f"Партнерский баланс: <b>{ref_balance:.2f} ₽</b>\n"
        )

    await call.message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command('test_balance'))
async def test_balance(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="test_balance").log("USER_ACTION", "Команда /test_balance вызвана")
    if message.from_user.id not in ADMINS:
        return
    sms = SmsReceive()
    balance = await sms.get_balance()
    logger.bind(user_id=message.from_user.id, action="info").log("USER_ACTION", f"Баланс: {balance}")


@router.message(Command('test_delete'))
async def test_delete(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="test_delete").log("USER_ACTION", "Команда /test_delete вызвана")
    if message.from_user.id not in ADMINS:
        return
    count = await Activation.delete_user_activations(message.from_user.id)
    await message.answer(f'Активации удалены {count}')


@router.message(Command('test_db'))
async def test(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="test_db").log("USER_ACTION", "Команда /test_db вызвана")
    if message.from_user.id not in ADMINS:
        return
    await message.answer(f'тест ')
    # text = await load_services_from_file("data.txt")
    # await message.answer(text)
    return
    # await models.Service.normalize_search_names()


@router.message(Command('test_state'))
async def test_state(message: types.Message, dialog_manager: DialogManager):
    logger.bind(user_id=message.from_user.id, action="test_state").log("USER_ACTION", "Команда /test_state вызвана")
    if message.from_user.id not in ADMINS:
        return
    try:
        logger.bind(user_id=message.from_user.id, action="success").log("USER_ACTION", dialog_manager.current_context())
    except Exception as e:
        logger.bind(user_id=message.from_user.id, action="success").log("USER_ACTION", e)


@router.message(Command('freemoney'))
async def free_money(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="freemoney").log("USER_ACTION", "Команда /freemoney вызвана")
    if message.from_user.id not in ADMINS:
        return

    command_args = message.text.split(' ')
    if len(command_args) != 3:
        await message.answer('Неверный формат команды')
        return

    _, amount, limit = command_args
    try:
        amount = float(amount)
        limit = int(limit)
    except ValueError:
        await message.answer('Неверный формат команды')
        return

    # generate unique 32 symbols string
    word = ''.join(random.choice(string.ascii_letters) for _ in range(16))
    word = 'free_' + word

    await models.PaymentLink.add_payment_link(
        payment_link_id=word,
        amount=amount,
        limit=limit
    )

    me = await bot.me()
    msg_text = f"""
<b>Ссылка готова</b>

<b>Сумма:</b> {amount} руб.
<b>Количество активаций:</b> {limit}

https://t.me/{me.username}?start={word}
"""

    await message.answer(text=msg_text, disable_web_page_preview=True)

@router.message(Command('send'))
async def send_message(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    logger.bind(user_id=message.from_user.id, action="clear_state").log("USER_ACTION", "Сброс состояния FSM")
    await state.clear()
    logger.bind(user_id=message.from_user.id, action='init_broadcast').log('USER_ACTION', 'Инициализация рассылки')
    logger.bind(user_id=message.from_user.id, action="set_broadcast_state").log("USER_ACTION", "Установка состояния: send_message")
    await state.set_state(BroadcastState.send_message)
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text='Отменить', callback_data='cancel_send_message')
            ]
        ]
    )
    await message.answer('Отправьте сообщение для рассылки', reply_markup=mk)


@router.callback_query(F.data == 'cancel_send_message')
async def cancel_send_message(c: types.CallbackQuery, state: FSMContext):
    await c.message.edit_text(text='Ввод отменен')
    logger.bind(user_id=c.from_user.id, action="clear_state").log("USER_ACTION", "Сброс состояния FSM")
    await state.clear()

@router.message(BroadcastState.send_message)
async def on_send_message(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    logger.bind(user_id=message.from_user.id, action="clear_state").log("USER_ACTION", "Сброс состояния FSM")
    await state.clear()

    # Проверяем, есть ли у сообщения текст
    message_text = message.text if message.content_type == types.ContentType.TEXT else None

    # Сохраняем информацию о рассылке в базе данных
    campaign = await models.BroadcastCampaign.create(
        message_id=message.message_id,
        message_text=message_text,  # Сохраняем текст сообщения, если он есть
        sent_by_admin_id = message.from_user.id  # Сохраняем ID администратора
    )

    # Обновляем состояние с данными рассылки и campaign_id
    await state.update_data(
        campaign_id=campaign.id,  # Сохраняем campaign_id
        chat_id=message.chat.id,
        message_id=message.message_id,
        message_text=message_text
    )

    # Копируем сообщение (для наглядности перед подтверждением)
    await bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id=message.chat.id,
        message_id=message.message_id
    )

    if broadcast_tasks.test:
        users_count = len(set(broadcast_tasks.LIMITED_USERS))
        mode_note = " (тестовый режим)"
    else:
        users_count = await models.User.filter(telegram_id__isnull=False).count()
        mode_note = ""

    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.CONFIRM_BTN, callback_data='send_message_celery'),
                types.InlineKeyboardButton(text='Отмена', callback_data='cancel_send_message')
            ]
        ]
    )

    await message.answer(
        text=f'Сообщение выше будет отправлено {users_count} пользователям{mode_note}. Продолжить?',
        reply_markup=mk
    )


@router.callback_query(F.data == 'send_message_celery')
async def on_confirm_send_message(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in ADMINS:
        return
    # Получаем данные из состояния
    data = await state.get_data()
    campaign_id = data.get("campaign_id")  # Получаем campaign_id
    logger.bind(user_id=c.from_user.id, action='confirm_broadcast').log('USER_ACTION', f'Запуск рассылки #{campaign_id}')
    await c.message.edit_text(f"Рассылка #{campaign_id} запущена!")
    # Запуск задачи Celery с campaign_id
    send_message_batch.delay(campaign_id)  # Передаем campaign_id в Celery-задачу
    logger.bind(user_id=c.from_user.id, action="clear_state").log("USER_ACTION", "Сброс состояния FSM")
    await state.clear()



@router.callback_query(F.data == 'send_message')
async def on_confirm_send_message(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in ADMINS:
        return

    data = await state.get_data()
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    await c.message.edit_text(text='Отправка сообщения...')
    users = await models.User.all()
    for user in users:
        await bot.copy_message(
            chat_id=user.telegram_id,
            from_chat_id=chat_id,
            message_id=message_id
        )
    await c.message.answer(text=f'Сообщение отправлено {len(users)} пользователям')
    logger.bind(user_id=c.from_user.id, action="clear_state").log("USER_ACTION", "Сброс состояния FSM")
    await state.clear()


@router.message(Command('affiliate_stat'))
async def affiliate_stat(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="affiliate_stat").log("USER_ACTION", "Команда /affiliate_stat вызвана")
    if message.from_user.id not in ADMINS:
        return

    logger.bind(user_id=message.from_user.id, action='affiliate_stat').log('USER_ACTION', 'Команда /affiliate_stat вызвана')
    users = await models.User.filter(refer_id__isnull=False).all()
    referrers_dict = {}
    for user in users:
        refer = await models.User.get_or_none(id=user.refer_id)
        refer_id = refer.telegram_id
        if refer_id not in referrers_dict:
            referrers_dict[refer_id] = {
                'count': 0,
            }
        referrers_dict[refer_id]['count'] += 1
        payments = await models.Payment.filter(is_success=True, user=user).all()
        for payment in payments:
            if refer_id not in referrers_dict:
                referrers_dict[refer_id] = {
                    'count': 0,
                    'payments_count': 0
                }
            elif 'payments_count' not in referrers_dict[refer_id]:
                referrers_dict[refer_id]['payments_count'] = 0

            referrers_dict[refer_id]['payments_count'] += 1

    referrers_ids = list(referrers_dict.keys())
    referrers = await models.User.filter(telegram_id__in=referrers_ids).all()
    for refer in referrers:
        referrers_dict[refer.telegram_id]['total_earning'] = refer.total_ref_earnings
        referrers_dict[refer.telegram_id]['ref_balance'] = refer.ref_balance

    data_tab = []
    for refer_id, data in referrers_dict.items():
        if 'payments_count' not in data:
            data['payments_count'] = 0
        if 'total_earning' not in data:
            data['total_earning'] = 0
        if 'ref_balance' not in data:
            data['ref_balance'] = 0

        data_tab.append([
            refer_id,
            data['count'],
            data['payments_count'],
            data['total_earning'],
            data['ref_balance']
        ])

    stat = tabulate(data_tab, headers=['ID', 'Приглашено', 'Оплаты', 'Заработано', 'Доступно'])

    await message.answer(bt.AFFILIATE_STAT.format(stat=stat))


@router.message(Command("refund"))
async def handle_refund_command(message: types.Message):
    logger.bind(user_id=message.from_user.id, action='refund').log('USER_ACTION', 'Команда /refund вызвана')
    logger.info(f'message.from_user.id: {message.from_user.id} '
                   f'message.successful_payment.telegram_payment_charge_id: {message.successful_payment.telegram_payment_charge_id}')
    refund_star = await message.bot.refund_star_payment(message.from_user.id,
                                                        message.successful_payment.telegram_payment_charge_id)

    await message.answer(str(refund_star))


@router.message(Command('add_balance'))
async def add_balance(message: types.Message):
    logger.bind(user_id=message.from_user.id, action='add_balance').log("USER_ACTION", "Команда /add_balance вызвана")
    logger.bind(user_id=message.from_user.id, action="add_balance").log("USER_ACTION", "Команда /add_balance вызвана")

    allowed_users = set(ADMINS) | {USER_ACCESS_TO_THE_COMMAND_USER_REPORT_AND_ADD_BALANCE}

    if message.from_user.id not in allowed_users:
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("Использование: /add_balance [telegram_id] [сумма]")
        return

    try:
        telegram_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("Некорректный Telegram ID или сумма. Пожалуйста, введите числовые значения.")
        return

    user = await models.User.get_user(telegram_id)
    if user is None:
        await message.answer("Пользователь с таким Telegram ID не найден.")
        return

    # Важно: делаем изменение баланса и запись в payments атомарно (одной транзакцией)
    from tortoise.transactions import in_transaction

    try:
        async with in_transaction() as conn:
            user.balance += amount
            await user.save(using_db=conn)

            # Запись в payments как успешное пополнение админом (для отчётов)
            await models.Payment.create(
                user=user,
                method=models.PaymentMethod.ADMIN,
                amount=amount,
                is_success=True,
                continue_data={
                    "source": "admin_command",
                    "command": "add_balance",
                    "admin_id": message.from_user.id,
                },
                using_db=conn
            )
    except Exception as e:
        logger.bind(user_id=message.from_user.id, action="add_balance").opt(exception=e).error(
            "Ошибка при ручном пополнении: не удалось записать изменения в БД"
        )
        await message.answer("❌ Не удалось пополнить баланс (ошибка записи в БД). Проверь логи.")
        return

    logger.bind(user_id=message.from_user.id, action='add_balance').log(
        "USER_ACTION", f"Пополнение баланса {telegram_id} на {amount}"
    )
    await message.answer(f"Баланс пользователя {user} пополнен на {amount}.")

    if amount > 0:
        await bot.send_message(telegram_id, f"Администратор пополнил ваш баланс на {amount}.")

    msg_text = f"Администратор пополнил баланс пользователю {user.mention} на {amount}."
    await send_coder(msg_text)

@router.message(Command('reset_ref_balance'))
async def reset_ref_balance(message: types.Message):
    logger.bind(user_id=message.from_user.id, action='reset_ref_balance').log(
        "USER_ACTION", "Команда /reset_ref_balance вызвана"
    )
    if message.from_user.id not in ADMINS:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /reset_ref_balance [telegram_id]")
        return

    try:
        telegram_id = int(args[1])
    except ValueError:
        await message.answer("Некорректный Telegram ID. Пожалуйста, введите числовое значение.")
        return

    exists = await models.User.filter(telegram_id=telegram_id).exists()
    if not exists:
        await message.answer("Пользователь с таким Telegram ID не найден.")
        return

    try:
        from tortoise import connections

        user_table = models.User._meta.db_table
        conn = connections.get("default")
        dialect = getattr(conn.capabilities, "dialect", "")
        p1 = "$1" if dialect == "postgres" else "?"

        await conn.execute_query(
            f'UPDATE "{user_table}" '
            f'SET ref_balance = 0, total_ref_earnings = 0 '
            f'WHERE telegram_id = {p1}',
            [telegram_id],
        )
    except Exception:
        logger.bind(user_id=message.from_user.id, action='reset_ref_balance').exception(
            "Ошибка при обнулении ref_balance/total_ref_earnings"
        )
        await message.answer("Ошибка при обновлении данных. См. логи.")
        return

    await message.answer(f"✅ Реферальный баланс обнулён для telegram_id={telegram_id}.")
    await send_coder(f"Админ обнулил ref_balance/total_ref_earnings для telegram_id={telegram_id}.")


@router.message(Command('reset_ref_stats'))
async def reset_ref_stats(message: types.Message):
    logger.bind(user_id=message.from_user.id, action='reset_ref_stats').log(
        "USER_ACTION", "Команда /reset_ref_stats вызвана"
    )
    if message.from_user.id not in ADMINS:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /reset_ref_stats [telegram_id]")
        return

    try:
        telegram_id = int(args[1])
    except ValueError:
        await message.answer("Некорректный Telegram ID. Пожалуйста, введите числовое значение.")
        return

    # Находим внутренний id (users.id), он нужен для refer_id и referral_links.user_id
    ref_user = await models.User.get_or_none(telegram_id=telegram_id).only("id")
    if not ref_user:
        await message.answer("Пользователь с таким Telegram ID не найден.")
        return

    referrer_internal_id = ref_user.id

    # invited_users / invited_success_payments — это агрегаты (считаются по refer_id и payments),
    # поэтому для "обнуления" нужно отвязать приглашённых пользователей (refer_id -> NULL).
    # /petr_links показывает статистику из referral_links.total_starts/total_pays/total_payment_amount,
    # поэтому их тоже сбрасываем.
    try:
        invited_before = await models.User.filter(refer_id=referrer_internal_id).count()
        pays_before = await models.Payment.filter(is_success=True, user__refer_id=referrer_internal_id).count()

        links = await models.ReferralLink.filter(user_id=referrer_internal_id).all()
        links_count = len(links)
        starts_before = sum((l.total_starts or 0) for l in links)
        pays_links_before = sum((l.total_pays or 0) for l in links)
        amount_before = sum((l.total_payment_amount or 0) for l in links)

        from tortoise.transactions import in_transaction

        user_table = models.User._meta.db_table
        referral_links_table = models.ReferralLink._meta.db_table

        async with in_transaction() as conn:
            dialect = getattr(conn.capabilities, "dialect", "")
            p1 = "$1" if dialect == "postgres" else "?"

            # 1) Обнуляем балансы реферера
            await conn.execute_query(
                f'UPDATE "{user_table}" '
                f'SET ref_balance = 0, total_ref_earnings = 0 '
                f'WHERE telegram_id = {p1}',
                [telegram_id],
            )

            # 2) Отвязываем всех приглашённых пользователей (иначе агрегатная статистика снова посчитается)
            await conn.execute_query(
                f'UPDATE "{user_table}" '
                f'SET refer_id = NULL '
                f'WHERE refer_id = {p1}',
                [referrer_internal_id],
            )

            # 3) Сбрасываем статистику по персональным реф-ссылкам (petr_links / whodi_links / silobus_links)
            await conn.execute_query(
                f'UPDATE "{referral_links_table}" '
                f'SET total_starts = 0, total_pays = 0, total_payment_amount = 0 '
                f'WHERE user_id = {p1}',
                [referrer_internal_id],
            )

    except Exception:
        logger.bind(user_id=message.from_user.id, action='reset_ref_stats').exception(
            "Ошибка при полном обнулении реф статистики"
        )
        await message.answer("Ошибка при обновлении данных. См. логи.")
        return

    await message.answer(
        "✅ Реферальная статистика полностью обнулена.\n\n"
        f"Было (агрегаты по рефералам):\n"
        f"Приведено пользователей: {invited_before}\n"
        f"Количество успешных оплат: {pays_before}\n\n"
        f"Было (referral_links):\n"
        f"Ссылок: {links_count}\n"
        f"Запусков бота: {starts_before}\n"
        f"Оплат: {pays_links_before}\n"
        f"Сумма оплат: {amount_before:.2f}₽\n\n"
        f"Сейчас:\n"
        f"Приведено пользователей: 0\n"
        f"Количество успешных оплат: 0\n"
        f"По ссылкам: запуски=0, оплаты=0, сумма=0"
    )
    await send_coder(
        f"Админ полностью обнулил реф статистику для telegram_id={telegram_id}: "
        f"ref_balance/total_ref_earnings=0, отвязано рефералов={invited_before}, "
        f"оплат по приглашённым было={pays_before}, referral_links: links={links_count}, "
        f"starts={starts_before}, pays={pays_links_before}, amount={amount_before:.2f}."
    )


@router.message(Command('services_update'))
async def services(message: types.Message, state: FSMContext):
    logger.bind(user_id=message.from_user.id, action="services_update").log("USER_ACTION", "Команда /services_update вызвана")
    if message.from_user.id not in ADMINS:
        return
    await message.answer("Обновляю список сервисов...")
    await add_services()
    await message.answer("Сервисы обновлены!")


@router.message(Command('smsactivate'))
async def set_smsactivate(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="smsactivate").log("USER_ACTION", "Команда /smsactivate вызвана")
    if message.from_user.id not in ADMINS:
        return
    await AdminSettings.update_setting("sms_rental_service", "SMS_Activate")
    await message.answer(text="Установлен сервис SMS_Activate")


@router.message(Command('onlinesim'))
async def set_smsactivate(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="onlinesim").log("USER_ACTION", "Команда /onlinesim вызвана")
    if message.from_user.id not in ADMINS:
        return
    await AdminSettings.update_setting("sms_rental_service", "Onlinesim")
    await message.answer(text="Установлен сервис Onlinesim")


@router.message(Command('info_id'))
async def info_id(message: types.Message):
    logger.bind(user_id=message.from_user.id, action='info_id').log("USER_ACTION", "Команда /info_id вызвана")
    logger.bind(user_id=message.from_user.id, action="info_id").log("USER_ACTION", "Команда /info_id вызвана")
    if message.from_user.id not in ADMINS:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /info_id [telegram_id]")
        return

    try:
        telegram_id = int(args[1])
    except ValueError:
        await message.answer("Некорректный Telegram ID")
        return

    logger.bind(user_id=message.from_user.id, action='info_id').log('USER_ACTION', f'Запрос информации по пользователю {telegram_id}')
    user = await models.User.get_user(telegram_id)
    if user is None:
        await message.answer("Пользователь с таким Telegram ID не найден.")
        return

    amount = user.balance
    mail = await models.Mail.get_user_mails(user.id)
    last_element = mail[-1] if mail else "-"

    data = await models.Activation.get_last_rented_info(user.id)

    # Проверка наличия арендованного номера с sms_text
    rent_with_sms = await models.Rent.filter(user=user, sms_text__isnull=False).order_by('-created_at').first()

    rent_number = rent_with_sms.phone_number if rent_with_sms else "Нет"
    rent_date = rent_with_sms.created_at.strftime("%Y-%m-%d %H:%M") if rent_with_sms else "Нет"

    # Получаем сумму всех успешных пополнений
    from tortoise.functions import Sum
    total_payments_result = await models.Payment.filter(user=user, is_success=True).annotate(total=Sum("amount")).values("total")
    total_payments = total_payments_result[0]["total"] or 0


    # Сумма списаний из Rent
    rent_cost_result = await models.Rent.filter(user=user, sms_text__isnull=False).exclude(sms_text="").annotate(total=Sum("cost")).values("total")
    rent_total = rent_cost_result[0]["total"] or 0

    # Сумма списаний из Activation
    activation_cost_result = await models.Activation.filter(user=user, sms_text__isnull=False).exclude(sms_text="").annotate(total=Sum("cost")).values("total")
    activation_total = activation_cost_result[0]["total"] or 0

    total_spent = rent_total + activation_total

    await message.answer(
        f"<b>📋 Информация о пользователе:</b>\n\n"
        f"<b>💳 Баланс:</b> {amount} ₽\n"
        f"<b>💰 Всего пополнений:</b> {total_payments:.2f} ₽\n"
        f"<b>📤 Всего списаний:</b> {total_spent:.2f} ₽\n"
        f"\n<i>📱 Последний арендованный номер:</i> {rent_number}\n"
        f"<i>🗓 Дата аренды:</i> {rent_date}\n"
        f"\n<b>📩 Последний номер, на который было получено SMS:</b>\n"
        f"<i>📞 Номер:</i> {data[0] if data and data[0] else 'Нет'}\n"
        f"<i>🗓 Дата:</i> {data[1] if data and data[1] else 'Нет'}\n"
        f"<i>🛠 Сервис:</i> {data[2] if data and data[2] else 'Нет'}\n"
        f"<i>🌍 Страна:</i> {data[3] if data and data[3] else 'Нет'}\n"

        f"\n<i>📫 Почта:</i> <a href='mailto:{last_element}'></a>\n",
        parse_mode="HTML"
    )


@router.message(Command('sending_status'))
async def campaign_status(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="sending_status").log("USER_ACTION", "Команда /sending_status вызвана")
    if message.from_user.id not in ADMINS:
        return

    # Проверяем, передан ли campaign_id
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /sending_status [номер рассылки]")
        return

    try:
        campaign_id = int(args[1])
    except ValueError:
        await message.answer("Некорректный номер рассылки")
        return

    # Проверяем существование рассылки
    campaign = await models.BroadcastCampaign.get_or_none(id=campaign_id)
    if not campaign:
        await message.answer("Рассылка с таким ID не найдена.")
        return

    # Подсчитываем количество отправленных сообщений
    sent_count = await models.Broadcast.filter(campaign_id=campaign_id).count()

    await message.answer(f"В рамках рассылки #{campaign_id} отправлено сообщений: {sent_count}")

import tempfile

@router.message(Command('users_with_balance'))
async def users_with_balance_html(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="users_with_balance").log("USER_ACTION", "Команда /users_with_balance вызвана")
    if message.from_user.id not in ADMINS:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /users_with_balance [сумма]")
        return

    try:
        balance_threshold = float(args[1])
    except ValueError:
        await message.answer("Некорректное значение суммы.")
        return

    users = await models.User.filter(balance__gt=balance_threshold).all()

    if not users:
        await message.answer(f"Нет пользователей с балансом выше {balance_threshold} ₽.")
        return

    # Формируем HTML-таблицу
    rows = "".join([
        f"<tr><td>{u.telegram_id}</td><td>{u.full_name}</td><td>{u.username or '-'}</td><td>{u.mention}</td><td>{u.balance:.2f} ₽</td></tr>"
        for u in users
    ])
    html_content = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
        </style>
    </head>
    <body>
        <h2>Пользователи с балансом выше {balance_threshold} ₽</h2>
        <table>
            <thead>
                <tr>
                    <th>Telegram ID</th>
                    <th>Имя</th>
                    <th>Username</th>
                    <th>Mention</th>
                    <th>Баланс</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </body>
    </html>
    """

    # Создаём временный файл
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        f.flush()
        file_path = f.name

    # Отправляем файл
    await message.answer_document(types.FSInputFile(file_path), caption=f"Пользователи с балансом > {balance_threshold} ₽")


@router.message(Command('users_without_payments'))
async def users_without_payments(message: types.Message):
    logger.bind(user_id=message.from_user.id, action='users_without_payments').log("USER_ACTION", "Команда /users_without_payments вызвана")
    if message.from_user.id not in ADMINS:
        return

    from tortoise.functions import Sum

    # Получаем всех пользователей с положительным балансом
    users_with_balance = await models.User.filter(balance__gt=0).all()

    # Получаем ID пользователей с успешными платежами
    paid_user_ids = set(await models.Payment.filter(is_success=True).values_list('user_id', flat=True))

    # Оставляем только тех, кто не пополнял
    filtered_users = [u for u in users_with_balance if u.id not in paid_user_ids]

    if not filtered_users:
        await message.answer("Нет пользователей с балансом больше 0 и без пополнений.")
        return

    # Словарь user_id -> total_spent
    user_spent = {}

    for user in filtered_users:
        rent_cost_result = await models.Rent.filter(
            user=user, sms_text__isnull=False
        ).exclude(sms_text="").annotate(total=Sum("cost")).values("total")
        rent_total = rent_cost_result[0]["total"] or 0

        activation_cost_result = await models.Activation.filter(
            user=user, sms_text__isnull=False
        ).exclude(sms_text="").annotate(total=Sum("cost")).values("total")
        activation_total = activation_cost_result[0]["total"] or 0

        user_spent[user.id] = rent_total + activation_total

    # Формируем HTML-таблицу
    rows = "".join([
        f"<tr><td>{u.id}</td><td>{u.telegram_id}</td><td>{u.full_name}</td><td>{u.username or '-'}</td>"
        f"<td>{u.mention}</td><td>{u.balance:.2f} ₽</td><td>{user_spent[u.id]:.2f} ₽</td><td>{u.created_at.strftime('%Y-%m-%d %H:%M')}</td></tr>"
        for u in filtered_users
    ])
    html_content = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
        </style>
    </head>
    <body>
        <h3>Пользователи с балансом > 0 и без успешных пополнений</h3>
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Telegram ID</th>
                    <th>Имя</th>
                    <th>Username</th>
                    <th>Mention</th>
                    <th>Баланс</th>
                    <th>Всего списаний</th>
                    <th>Зарегистрирован</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </body>
    </html>
    """

    import tempfile
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        file_path = f.name

    await message.answer_document(types.FSInputFile(file_path), caption="Пользователи без пополнений с балансом.")



@router.message(Command("users_with_discrepancy"))
async def users_with_discrepancy(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    # Получаем суммы пополнений по пользователям
    payments = await models.Payment.filter(is_success=True).group_by("user_id").annotate(
        total_paid=Sum("amount")
    ).values("user_id", "total_paid")

    # Получаем суммы расходов на аренды
    rents = await models.Rent.filter(sms_text__isnull=False).exclude(sms_text="").group_by("user_id").annotate(
        total_rent=Sum("cost")
    ).values("user_id", "total_rent")

    # Получаем суммы расходов на активации
    activations = await models.Activation.filter(sms_text__isnull=False).exclude(sms_text="").group_by("user_id").annotate(
        total_activation=Sum("cost")
    ).values("user_id", "total_activation")

    # Сопоставляем user_id с их данными
    user_data = {}

    for p in payments:
        user_data[p["user_id"]] = {"total_paid": p["total_paid"], "total_spent": 0}

    for r in rents:
        user_data.setdefault(r["user_id"], {"total_paid": 0, "total_spent": 0})
        user_data[r["user_id"]]["total_spent"] += r["total_rent"]

    for a in activations:
        user_data.setdefault(a["user_id"], {"total_paid": 0, "total_spent": 0})
        user_data[a["user_id"]]["total_spent"] += a["total_activation"]

    # Получаем баланс всех этих пользователей
    user_ids = list(user_data.keys())
    users = await models.User.filter(id__in=user_ids).values(
        "id", "telegram_id", "full_name", "username", "balance", "mention"
    )

    user_info_map = {u["id"]: u for u in users}

    # Выбираем пользователей с расхождениями
    filtered = []
    for user_id, data in user_data.items():
        user = user_info_map.get(user_id)
        if not user:
            continue
        total_paid = data["total_paid"] or 0
        total_spent = data["total_spent"] or 0
        balance = user["balance"] or 0

        if total_spent + balance > total_paid:
            filtered.append({
                "telegram_id": user["telegram_id"],
                "full_name": user["full_name"],
                "username": user["username"] or "-",
                "mention": user["mention"] or "-",
                "balance": balance,
                "total_paid": total_paid,
                "total_spent": total_spent,
                "difference": (total_spent + balance - total_paid)
            })

    # сортировка по убыванию разницы
    filtered.sort(key=lambda x: x["difference"], reverse=True)

    if not filtered:
        await message.answer("Нет пользователей с расхождениями.")
        return

    # Формируем HTML-таблицу
    rows = "".join([
        f"<tr>"
        f"<td>{u['telegram_id']}</td>"
        f"<td>{u['full_name']}</td>"
        f"<td>{u['username']}</td>"
        f"<td>{u['mention']}</td>"
        f"<td>{u['balance']:.2f} ₽</td>"
        f"<td>{u['total_paid']:.2f} ₽</td>"
        f"<td>{u['total_spent']:.2f} ₽</td>"
        f"<td>{u['difference']:.2f} ₽</td>"
        f"</tr>"
        for u in filtered
    ])

    html_content = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
        </style>
    </head>
    <body>
        <h2>Пользователи с (расходы + баланс) > пополнений</h2>
        <table>
            <thead>
                <tr>
                    <th>Telegram ID</th>
                    <th>Имя</th>
                    <th>Username</th>
                    <th>Mention</th>
                    <th>Баланс</th>
                    <th>Пополнения</th>
                    <th>Расходы</th>
                    <th>Разница</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </body>
    </html>
    """

    with tempfile.NamedTemporaryFile(mode='w+', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        file_path = f.name

    await message.answer_document(
        FSInputFile(file_path),
        caption="Пользователи с (расходы + баланс) > пополнений"
    )

@router.message(Command("users_with_overspent"))
async def users_with_overspent(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    # Получаем суммы пополнений
    payments = await models.Payment.filter(is_success=True).group_by("user_id").annotate(
        total_paid=Sum("amount")
    ).values("user_id", "total_paid")

    # Расходы: аренда
    rents = await models.Rent.filter(sms_text__isnull=False).exclude(sms_text="").group_by("user_id").annotate(
        total_rent=Sum("cost")
    ).values("user_id", "total_rent")

    # Расходы: активации
    activations = await models.Activation.filter(sms_text__isnull=False).exclude(sms_text="").group_by("user_id").annotate(
        total_activation=Sum("cost")
    ).values("user_id", "total_activation")

    user_data = {}

    for p in payments:
        user_data[p["user_id"]] = {"total_paid": p["total_paid"], "total_spent": 0}

    for r in rents:
        user_data.setdefault(r["user_id"], {"total_paid": 0, "total_spent": 0})
        user_data[r["user_id"]]["total_spent"] += r["total_rent"]

    for a in activations:
        user_data.setdefault(a["user_id"], {"total_paid": 0, "total_spent": 0})
        user_data[a["user_id"]]["total_spent"] += a["total_activation"]

    user_ids = list(user_data.keys())
    users = await models.User.filter(id__in=user_ids).values(
        "id", "telegram_id", "full_name", "username", "mention"
    )
    user_info_map = {u["id"]: u for u in users}

    filtered = []
    for user_id, data in user_data.items():
        user = user_info_map.get(user_id)
        if not user:
            continue

        total_paid = data["total_paid"] or 0
        total_spent = data["total_spent"] or 0

        if total_spent > total_paid:
            filtered.append({
                "telegram_id": user["telegram_id"],
                "full_name": user["full_name"],
                "username": user["username"] or "-",
                "mention": user["mention"] or "-",
                "total_paid": total_paid,
                "total_spent": total_spent,
                "difference": total_spent - total_paid
            })

    filtered.sort(key=lambda x: x["difference"], reverse=True)

    if not filtered:
        await message.answer("Нет пользователей, у которых расходы превышают пополнения.")
        return

    rows = "".join([
        f"<tr>"
        f"<td>{u['telegram_id']}</td>"
        f"<td>{u['full_name']}</td>"
        f"<td>{u['username']}</td>"
        f"<td>{u['mention']}</td>"
        f"<td>{u['total_paid']:.2f} ₽</td>"
        f"<td>{u['total_spent']:.2f} ₽</td>"
        f"<td>{u['difference']:.2f} ₽</td>"
        f"</tr>"
        for u in filtered
    ])

    html_content = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
        </style>
    </head>
    <body>
        <h2>Пользователи с расходами выше, чем пополнения</h2>
        <table>
            <thead>
                <tr>
                    <th>Telegram ID</th>
                    <th>Имя</th>
                    <th>Username</th>
                    <th>Mention</th>
                    <th>Пополнения</th>
                    <th>Расходы</th>
                    <th>Разница</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </body>
    </html>
    """

    with tempfile.NamedTemporaryFile(mode='w+', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        file_path = f.name

    await message.answer_document(
        FSInputFile(file_path),
        caption="Пользователи с расходами выше пополнений"
    )

from tortoise.transactions import in_transaction
from tortoise.functions import Sum

@router.callback_query(F.data.startswith("fraud_unban:"))
async def fraud_unban(callback_query: types.CallbackQuery):
    admin_id = callback_query.from_user.id

    if admin_id not in ADMINS:
        await callback_query.answer("Недостаточно прав", show_alert=True)
        return

    try:
        telegram_id = int(callback_query.data.split(":", 1)[1])
    except Exception:
        await callback_query.answer("Некорректные данные кнопки", show_alert=True)
        return

    async with in_transaction() as conn:
        user = await models.User.select_for_update().using_db(conn).get_or_none(telegram_id=telegram_id)
        if not user:
            await callback_query.answer("Пользователь не найден", show_alert=True)
            return

        if not getattr(user, "fraud_banned", False):
            await callback_query.answer("Пользователь уже не в бане", show_alert=True)
            return

        # ✅ пересчитываем diff на лету: (spent + balance) - paid
        paid_rows = await (
            models.Payment.filter(
                user_id=user.id,
                is_success=True,
            )
            .using_db(conn)
            .annotate(total_paid=Sum("amount"))
            .values("total_paid")
        )
        total_paid = float((paid_rows[0].get("total_paid") if paid_rows else 0.0) or 0.0)

        rent_rows = await (
            models.Rent.filter(
                user_id=user.id,
                sms_text__isnull=False,
                sms_text__not="",
            )
            .using_db(conn)
            .annotate(total_rent_cost=Sum("cost"))
            .values("total_rent_cost")
        )
        total_rent_cost = float((rent_rows[0].get("total_rent_cost") if rent_rows else 0.0) or 0.0)

        act_rows = await (
            models.Activation.filter(
                user_id=user.id,
                sms_text__isnull=False,
                sms_text__not="",
            )
            .using_db(conn)
            .annotate(total_activation_cost=Sum("cost"))
            .values("total_activation_cost")
        )
        total_activation_cost = float((act_rows[0].get("total_activation_cost") if act_rows else 0.0) or 0.0)

        total_spent = total_rent_cost + total_activation_cost
        balance = float(getattr(user, "balance", 0.0) or 0.0)

        diff = (total_spent + balance) - total_paid
        diff = float(diff)

        # ✅ создаём запись в payments как “пополнил админ”, чтобы закрыть расхождение в отчётах
        if diff > 0:
            await models.Payment.create(
                user=user,
                method=models.PaymentMethod.ADMIN,
                amount=diff,
                is_success=True,
                continue_data={
                    "source": "auto_fraud_unban",
                    "unbanned_by": admin_id,
                    "telegram_id": telegram_id,
                    "total_paid": total_paid,
                    "total_spent": total_spent,
                    "balance": balance,
                    "diff": diff,
                },
                using_db=conn,
            )

        # ✅ снимаем бан (у тебя только одно поле — так и оставляем)
        user.fraud_banned = False
        await user.save(update_fields=["fraud_banned"], using_db=conn)

    # убираем кнопку, чтобы не нажимали повторно
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback_query.answer(
        f"✅ Разбанен. Запись в payments добавлена на {diff:.2f} ₽.",
        show_alert=True
    )

@router.message(Command("fix_discrepancy_balance"))
async def fix_discrepancy_balance(message: types.Message):
    """
    Админ-команда: находит пользователей с расхождением (как /users_with_discrepancy),
    но НЕ учитывает операции за последние 30 минут, и правит баланс так, чтобы diff стал 0.
    """
    if message.from_user.id not in ADMINS:
        return

    logger.bind(user_id=message.from_user.id, action="fix_discrepancy_balance").log(
        "USER_ACTION", "Команда /fix_discrepancy_balance вызвана"
    )

    cutoff_dt = timezone.now() - timedelta(minutes=30)

    # ✅ пополнения (до cutoff)
    payments = (
        await models.Payment.filter(is_success=True, created_at__lte=cutoff_dt)
        .group_by("user_id")
        .annotate(total_paid=Sum("amount"))
        .values("user_id", "total_paid")
    )

    # ✅ расходы на аренды (до cutoff)
    rents = (
        await models.Rent.filter(created_at__lte=cutoff_dt, sms_text__isnull=False)
        .exclude(sms_text="")
        .group_by("user_id")
        .annotate(total_rent=Sum("cost"))
        .values("user_id", "total_rent")
    )

    # ✅ расходы на активации (до cutoff)
    activations = (
        await models.Activation.filter(created_at__lte=cutoff_dt, sms_text__isnull=False)
        .exclude(sms_text="")
        .group_by("user_id")
        .annotate(total_activation=Sum("cost"))
        .values("user_id", "total_activation")
    )

    user_data: dict[int, dict[str, float]] = {}

    def _get(uid: int) -> dict[str, float]:
        if uid not in user_data:
            user_data[uid] = {"total_paid": 0.0, "total_spent": 0.0}
        return user_data[uid]

    for p in payments:
        uid = int(p["user_id"])
        _get(uid)["total_paid"] = float(p["total_paid"] or 0.0)

    for r in rents:
        uid = int(r["user_id"])
        _get(uid)["total_spent"] += float(r["total_rent"] or 0.0)

    for a in activations:
        uid = int(a["user_id"])
        _get(uid)["total_spent"] += float(a["total_activation"] or 0.0)

    user_ids = list(user_data.keys())
    if not user_ids:
        await message.answer("Нет данных для пересчёта (user_ids пуст).")
        return

    # Берём текущие балансы кандидатов
    users = await models.User.filter(id__in=user_ids).values(
        "id", "telegram_id", "full_name", "username", "mention", "balance"
    )
    user_info_map = {u["id"]: u for u in users}

    changed = 0
    total_adjustment = 0.0

    for uid, data in user_data.items():
        u = user_info_map.get(uid)
        if not u:
            continue

        total_paid = float(data["total_paid"] or 0.0)
        total_spent = float(data["total_spent"] or 0.0)
        balance = float(u["balance"] or 0.0)

        diff = (total_spent + balance - total_paid)
        if diff <= 0:
            continue

        # мелочь игнорируем, чтобы не дрожать по копейкам
        if abs(diff) < 0.01:
            continue

        new_balance = balance - diff

        await models.User.filter(id=uid).update(balance=new_balance)

        changed += 1
        total_adjustment += diff

        logger.bind(
            user_id=u["telegram_id"],
            action="fix_discrepancy_balance"
        ).log(
            "USER_ACTION",
            f"Баланс скорректирован: был={balance:.2f} -> стал={new_balance:.2f}, diff={diff:.2f}, cutoff={cutoff_dt}"
        )

    await message.answer(
        "✅ Готово.\n"
        f"cutoff: {cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"пользователей исправлено: {changed}\n"
        f"суммарно снято (diff): {total_adjustment:.2f} ₽"
    )

@router.message(Command('smsfast'))
async def set_smsfast(message: types.Message):
    logger.bind(user_id=message.from_user.id, action="smsfast").log(
        "USER_ACTION", "Команда /smsfast вызвана"
    )
    if message.from_user.id not in ADMINS:
        return

    # /smsfast            -> toggle
    # /smsfast on|off     -> set
    # /smsfast status     -> show
    args = (message.text or "").split(maxsplit=1)
    subcmd = args[1].strip().lower() if len(args) > 1 else ""

    # текущий статус
    setting = await AdminSettings.get_or_none(name_setting="smsfast_enabled")
    current = (setting.value_setting or "").strip().lower() if setting else ""
    current_bool = current in ("true", "1", "yes", "y", "on", "enable", "enabled")

    if subcmd in ("status", "статус"):
        await message.answer(
            text=f"SMSFast: {'✅ включен' if current_bool else '⛔ выключен'}"
        )
        return

    if subcmd in ("on", "enable", "enabled", "1", "true", "вкл", "включить"):
        new_bool = True
    elif subcmd in ("off", "disable", "disabled", "0", "false", "выкл", "выключить"):
        new_bool = False
    elif subcmd == "":
        new_bool = not current_bool
    else:
        await message.answer(
            text=(
                "Использование: /smsfast [on|off|status]\n"
                "Примеры: /smsfast on, /smsfast off, /smsfast status\n"
                "Без аргументов — переключает Включить/выключить."
            )
        )
        return

    # гарантируем наличие записи, иначе update_setting вернёт None
    if not setting:
        await AdminSettings.create(name_setting="smsfast_enabled", value_setting="false")
        await AdminSettings.get_or_none(name_setting="smsfast_enabled")

    await AdminSettings.update_setting("smsfast_enabled", "true" if new_bool else "false")

    text = f"SMSFast: {'✅ включен' if new_bool else '⛔ выключен'}"
    await message.answer(text)
    logger.bind(user_id=message.from_user.id, action="smsfast").log(
        "USER_ACTION", text)

@router.message(Command("sms_service_stat"))
async def sms_service_stat(message: types.Message) -> None:
    """
    Админ-команда: статистика доставляемости SMS по сервису в разрезе провайдеров.

    Использование:
        /sms_service_stat tiktok
        /sms_service_stat TikTok
        /sms_service_stat tk
        /sms_service_stat тикток

    Важно:
    - Для smsactivate/smsfast сервис хранится в activations.service (FK на ServicesSmsActivate)
    - Для onlinesim сервис хранится в activations.service_2 (FK на ServicesOnlinesim)
    - Коды сервисов могут отличаться между провайдерами, поэтому резолвим сервис
      по code / name / search_names в соответствующей таблице сервисов.
    """
    logger.bind(user_id=message.from_user.id, action="sms_service_stat").log(
        "USER_ACTION", "Команда /sms_service_stat вызвана"
    )

    if message.from_user.id not in ADMINS:
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer(
            "Использование: /sms_service_stat [service]\n"
            "Примеры: /sms_service_stat tiktok | /sms_service_stat tk | /sms_service_stat тикток"
        )
        return

    import html as _html
    from tortoise.expressions import Q

    raw_service = args[1].strip()
    service_key = raw_service.lower()

    providers = [
        ("smsactivate", "SMSActivate"),
        ("onlinesim", "OnlineSim"),
        ("smsfast", "SMSFast"),
    ]

    async def _resolve_smsactivate_service_ids() -> tuple[list[int], list[str]]:
        """
        Ищем сервис в ServicesSmsActivate по:
        - code (точно, без регистра)
        - name (точно, без регистра)
        - search_names (подстрока, без регистра)
        """
        qs = models.ServicesSmsActivate.filter(
            Q(code__iexact=service_key)
            | Q(name__iexact=raw_service)
            | Q(search_names__icontains=service_key)
        )
        ids = list(await qs.values_list("id", flat=True))
        codes = list(await qs.values_list("code", flat=True))
        return ids, codes

    async def _resolve_onlinesim_service_ids() -> tuple[list[int], list[str]]:
        """
        Ищем сервис в ServicesOnlinesim по:
        - code (точно, без регистра)
        - name (точно, без регистра)
        - search_names (подстрока, без регистра)
        """
        qs = models.ServicesOnlinesim.filter(
            Q(code__iexact=service_key)
            | Q(name__iexact=raw_service)
            | Q(search_names__icontains=service_key)
        )
        ids = list(await qs.values_list("id", flat=True))
        codes = list(await qs.values_list("code", flat=True))
        return ids, codes

    # Резолвим заранее (дешевле, чем по кругу)
    smsactivate_ids, smsactivate_codes = await _resolve_smsactivate_service_ids()
    onlinesim_ids, onlinesim_codes = await _resolve_onlinesim_service_ids()

    rows: list[list[object]] = []
    total_all = 0
    delivered_all = 0

    # Для наглядности покажем, что именно сматчилось в справочниках
    resolved_lines: list[str] = []
    resolved_lines.append(
        f"SMSActivate/SMSFast: {', '.join(smsactivate_codes) if smsactivate_codes else 'не найдено'}"
    )
    resolved_lines.append(
        f"OnlineSim: {', '.join(onlinesim_codes) if onlinesim_codes else 'не найдено'}"
    )

    for provider, title in providers:
        if provider == "onlinesim":
            ids = onlinesim_ids
            if not ids:
                total = 0
                delivered = 0
            else:
                total = await models.Activation.filter(
                    provider=provider,
                    service_2_id__in=ids,
                ).count()

                delivered = await (
                    models.Activation.filter(
                        provider=provider,
                        service_2_id__in=ids,
                        sms_text__isnull=False,
                    )
                    .exclude(sms_text="")
                    .count()
                )
        else:
            ids = smsactivate_ids
            if not ids:
                total = 0
                delivered = 0
            else:
                total = await models.Activation.filter(
                    provider=provider,
                    service_id__in=ids,
                ).count()

                delivered = await (
                    models.Activation.filter(
                        provider=provider,
                        service_id__in=ids,
                        sms_text__isnull=False,
                    )
                    .exclude(sms_text="")
                    .count()
                )

        pct = (delivered / total * 100.0) if total else 0.0

        total_all += int(total)
        delivered_all += int(delivered)

        rows.append([title, int(total), int(delivered), f"{pct:.1f}%"])

    pct_all = (delivered_all / total_all * 100.0) if total_all else 0.0
    rows.append(["ИТОГО", int(total_all), int(delivered_all), f"{pct_all:.1f}%"])

    table = tabulate(
        rows,
        headers=["Провайдер", "Запрошено", "Доставлено", "Доставляемость"],
        tablefmt="github",
    )

    await message.answer(
        text=(
            f"📊 <b>SMS доставляемость по сервису</b> <code>{_html.escape(raw_service)}</code>\n"
            f"<pre>{_html.escape(table)}</pre>"
        ),
        parse_mode="HTML",
    )

@router.message(Command("sms_service_stat_from_date"))
async def sms_service_stat_from_date(message: types.Message) -> None:
    """
    Админ-команда: статистика доставляемости SMS по сервису в разрезе провайдеров
    за период: с указанной даты по текущий день.

    Использование:
        /sms_service_stat_from_date tiktok 01.01.2026
        /sms_service_stat_from_date tg 01.01.2026

    Формат даты:
        dd.mm.yyyy (например 01.01.2026)

    Важно:
    - Для smsactivate/smsfast сервис хранится в activations.service (FK на ServicesSmsActivate)
    - Для onlinesim сервис хранится в activations.service_2 (FK на ServicesOnlinesim)
    - Коды сервисов могут отличаться между провайдерами, поэтому резолвим сервис
      по code / name / search_names в соответствующей таблице сервисов.
    """
    logger.bind(user_id=message.from_user.id, action="sms_service_stat_from_date").log(
        "USER_ACTION", "Команда /sms_service_stat_from_date вызвана"
    )

    if message.from_user.id not in ADMINS:
        return

    args = (message.text or "").split()
    if len(args) < 3:
        await message.answer(
            "Использование: /sms_service_stat_from_date [service_code] [dd.mm.yyyy]\n"
            "Пример: /sms_service_stat_from_date tiktok 01.01.2026"
        )
        return

    import html as _html
    from datetime import datetime
    import pytz
    from tortoise.expressions import Q

    raw_service = args[1].strip()
    raw_date = args[2].strip()

    # ✅ парсим дату
    try:
        start_naive = datetime.strptime(raw_date, "%d.%m.%Y")
    except ValueError:
        await message.answer(
            "❌ Некорректная дата.\n"
            "Ожидаю формат: dd.mm.yyyy (например 01.01.2026)"
        )
        return

    tz = pytz.timezone("Europe/Moscow")
    start_dt = tz.localize(start_naive).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = datetime.now(tz)

    if start_dt > end_dt:
        await message.answer("❌ Дата 'с' не может быть больше сегодняшней.")
        return

    service_key = raw_service.lower()

    providers = [
        ("smsactivate", "SMSActivate"),
        ("onlinesim", "OnlineSim"),
        ("smsfast", "SMSFast"),
    ]

    async def _resolve_smsactivate_service_ids() -> tuple[list[int], list[str]]:
        """
        Ищем сервис в ServicesSmsActivate по:
        - code (точно, без регистра)
        - name (точно, без регистра)
        - search_names (подстрока, без регистра)
        """
        qs = models.ServicesSmsActivate.filter(
            Q(code__iexact=service_key)
            | Q(name__iexact=raw_service)
            | Q(search_names__icontains=service_key)
        )
        ids = list(await qs.values_list("id", flat=True))
        codes = list(await qs.values_list("code", flat=True))
        return ids, codes

    async def _resolve_onlinesim_service_ids() -> tuple[list[int], list[str]]:
        """
        Ищем сервис в ServicesOnlinesim по:
        - code (точно, без регистра)
        - name (точно, без регистра)
        - search_names (подстрока, без регистра)
        """
        qs = models.ServicesOnlinesim.filter(
            Q(code__iexact=service_key)
            | Q(name__iexact=raw_service)
            | Q(search_names__icontains=service_key)
        )
        ids = list(await qs.values_list("id", flat=True))
        codes = list(await qs.values_list("code", flat=True))
        return ids, codes

    # Резолвим заранее (дешевле, чем по кругу)
    smsactivate_ids, smsactivate_codes = await _resolve_smsactivate_service_ids()
    onlinesim_ids, onlinesim_codes = await _resolve_onlinesim_service_ids()

    rows: list[list[object]] = []
    total_all = 0
    delivered_all = 0

    # Для наглядности покажем, что именно сматчилось в справочниках
    resolved_lines: list[str] = []
    resolved_lines.append(
        f"SMSActivate/SMSFast: {', '.join(smsactivate_codes) if smsactivate_codes else 'не найдено'}"
    )
    resolved_lines.append(
        f"OnlineSim: {', '.join(onlinesim_codes) if onlinesim_codes else 'не найдено'}"
    )

    for provider, title in providers:
        if provider == "onlinesim":
            ids = onlinesim_ids
            if not ids:
                total = 0
                delivered = 0
            else:
                total = await models.Activation.filter(
                    provider=provider,
                    service_2_id__in=ids,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                ).count()

                delivered = await (
                    models.Activation.filter(
                        provider=provider,
                        service_2_id__in=ids,
                        created_at__gte=start_dt,
                        created_at__lte=end_dt,
                        sms_text__isnull=False,
                    )
                    .exclude(sms_text="")
                    .count()
                )
        else:
            ids = smsactivate_ids
            if not ids:
                total = 0
                delivered = 0
            else:
                total = await models.Activation.filter(
                    provider=provider,
                    service_id__in=ids,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                ).count()

                delivered = await (
                    models.Activation.filter(
                        provider=provider,
                        service_id__in=ids,
                        created_at__gte=start_dt,
                        created_at__lte=end_dt,
                        sms_text__isnull=False,
                    )
                    .exclude(sms_text="")
                    .count()
                )

        pct = (delivered / total * 100.0) if total else 0.0
        total_all += int(total)
        delivered_all += int(delivered)
        rows.append([title, int(total), int(delivered), f"{pct:.1f}%"])

    pct_all = (delivered_all / total_all * 100.0) if total_all else 0.0
    rows.append(["ИТОГО", int(total_all), int(delivered_all), f"{pct_all:.1f}%"])

    table = tabulate(
        rows,
        headers=["Провайдер", "Запрошено", "Доставлено", "Доставляемость"],
        tablefmt="github",
    )

    period_line = f"📅 <b>Период:</b> {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}"
    resolved_block = "\n".join(resolved_lines)

    await message.answer(
        text=(
            f"📊 <b>SMS доставляемость по сервису</b> <code>{_html.escape(raw_service)}</code>\n"
            f"{period_line}\n"
            f"<i>{_html.escape(resolved_block)}</i>\n"
            f"<pre>{_html.escape(table)}</pre>"
        ),
        parse_mode="HTML",
    )


@router.message(Command('help_admin'))
async def help_admin(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    commands = """
    /stat - Статистика
    /freemoney [сумма] [лимит] - Создать ссылку на бесплатные деньги
    /send - Рассылка сообщений
    /sending_status [номер рассылки] - Проверка рассылки сообщений
    /add_balance [telegram_id] [сумма] - Пополнение баланса пользователя
    /reset_ref_balance [telegram_id] - Обнулить ref_balance 
    /reset_ref_stats [telegram_id] - Обнулить всю реф статистику
    /info_id [telegram_id] - Информация о пользователе
    /user_report [telegram_id] - HTML-отчёт по пользователю
    /users_with_balance [сумма] - Выгрузка пользователей с балансом выше указанного
    /users_without_payments - Пользователи с балансом > 0 и без пополнений
    /users_with_discrepancy - Выгрузка пользователей с (расходы + баланс) > пополнений
    /users_with_overspent - Пользователи с расходами > пополнений (без учёта баланса)
    /petr_links - Статистика по реферальным ссылкам Петра
    /whodi_links - Статистика по реферальным ссылкам whodi
    /silobus_links - Статистика по реферальным ссылкам Silobus
    /create_petr_links - Создать новую реферальную ссылку для Петра (следующую по порядку)
    /create_whodi_links - Создать новую реферальную ссылку для Whodi (следующую по порядку)
    /create_silobus_links - Создать новую реферальную ссылку для Silobus (следующую по порядку)
    /smsactivate - Установить SMS_Activate
    /onlinesim - Установить Onlinesim
    /update_price_smsfast - Вручную обновить сервисы SMSFast
    /smsfast [on|off|status] - Включить/выключить SMSFast (без аргументов — просто переключение Включить/выключить)
    /sms_service_stat [service_code] - Доставляемость SMS по сервису
    /sms_service_stat_from_date [service_code] [dd.mm.yyyy] - Доставляемость SMS по сервису с даты по сегодня


    """

    await message.answer(f"<b>Доступные команды для админов:</b>\n{commands}", parse_mode="HTML")
