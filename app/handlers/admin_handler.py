import asyncio
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
from app.dependencies import ADMINS, bot
from app.services import bot_texts as bt
from tabulate import tabulate
from aiogram_dialog import DialogManager
from loguru import logger
from tortoise.functions import Sum
import calendar
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

@router.message(Command('stat'))
async def stat(message: types.Message):
    logger.bind(user_id=message.from_user.id, action='stat').log("USER_ACTION", "Команда /stat вызвана")
    utc_now = datetime.now(pytz.timezone("Europe/Moscow"))
    if message.from_user.id not in ADMINS:
        return

    users_count = await models.User.all().count()
    users_count_today = await models.User.filter(
        created_at__gte=utc_now.replace(hour=0, minute=0, second=0)).count()

    letters_count = await models.Letter.all().count()
    letters_count_today = await models.Letter.filter(
        created_at__gte=utc_now.replace(hour=0, minute=0, second=0)).count()

    sms_count = await models.Activation.filter(sms_text__isnull=False, sms_text__not="").count()
    sms_count_today = await models.Activation.filter(
        sms_text__isnull=False, sms_text__not="",
        created_at__gte=utc_now.replace(hour=0, minute=0, second=0)
    ).count()

    sms_count_month = await models.Activation.filter(
        sms_text__isnull=False, sms_text__not="",
        created_at__gte=utc_now - timedelta(days=30)
    ).count()

    rented_sms_total = await models.Activation.all().count()
    rented_sms_month = await models.Activation.filter(
        created_at__gte=utc_now - timedelta(days=30)
    ).count()
    rented_sms_today = await models.Activation.filter(
        created_at__gte=utc_now.replace(hour=0, minute=0, second=0)
    ).count()

    payments = await models.Payment.filter(is_success=True).all().prefetch_related('user')
    payments_count = len(payments)
    payments_repeat_count = 0
    user_ids = []
    for payment in payments:
        if payment.user.id in user_ids:
            payments_repeat_count += 1
        else:
            user_ids.append(payment.user.id)
        await asyncio.sleep(0)

    payments_count_today = await models.Payment.filter(
        is_success=True,
        created_at__gte=utc_now.replace(hour=0, minute=0, second=0)
    ).count()

    payments_amount_today = sum(await models.Payment.filter(
        is_success=True,
        created_at__gte=utc_now.replace(hour=0, minute=0, second=0)
    ).values_list('amount', flat=True))

    rent_email_count = await models.Mail.filter(is_paid_mail=True).all().count()
    rent_email_count_today = await models.Mail.filter(
        is_paid_mail=True,
        created_at__gte=utc_now.replace(hour=0, minute=0, second=0)
    ).count()

    # Общая сумма всех покупок
    rented_number_total = await models.Rent.all().annotate(total=Sum("purchase_count")).values("total")
    rented_number_total = rented_number_total[0]["total"] or 0

    # Сумма покупок за последние 30 дней
    rented_number_month = await models.Rent.filter(
        created_at__gte=utc_now - timedelta(days=30)
    ).annotate(total=Sum("purchase_count")).values("total")
    rented_number_month = rented_number_month[0]["total"] or 0

    # Сумма покупок за сегодняшний день
    rented_number_today = await models.Rent.filter(
        created_at__gte=utc_now.replace(hour=0, minute=0, second=0)
    ).annotate(total=Sum("purchase_count")).values("total")
    rented_number_today = rented_number_today[0]["total"] or 0

    # Группируем по user_id и считаем количество записей для каждого пользователя
    user_purchases = await models.Rent.all().group_by("user_id").annotate(
        total_purchases=Sum("purchase_count")
    ).values("user_id", "total_purchases")

    first_day_of_month = utc_now.replace(day=1, hour=0, minute=0, second=0)
    last_month = utc_now.month - 1 if utc_now.month > 1 else 12
    last_year = utc_now.year if utc_now.month > 1 else utc_now.year - 1
    first_day_of_last_month = datetime(last_year, last_month, 1, tzinfo=pytz.timezone("Europe/Moscow"))
    last_month_name = MONTHS_RU[calendar.month_name[last_month]]
    current_month_name = MONTHS_RU[calendar.month_name[utc_now.month]]

    payments_count_month = await models.Payment.filter(
        is_success=True,
        created_at__gte=first_day_of_month
    ).count()

    payments_count_last_month = await models.Payment.filter(
        is_success=True,
        created_at__gte=first_day_of_last_month,
        created_at__lt=first_day_of_month
    ).count()

    payments_amount_month = sum(await models.Payment.filter(
        is_success=True,
        created_at__gte=first_day_of_month
    ).values_list('amount', flat=True))

    payments_amount_last_month = sum(await models.Payment.filter(
        is_success=True,
        created_at__gte=first_day_of_last_month,
        created_at__lt=first_day_of_month
    ).values_list('amount', flat=True))

    # Суммируем (count - 1) для каждого пользователя
    repeat_purchases_total = sum(user["total_purchases"] - 1 for user in user_purchases)

    # Для STARS
    today_start = utc_now.replace(hour=0, minute=0, second=0)

    # За сегодня
    stars_today_count = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=today_start
    ).count()

    # Используем values_list для суммы
    amounts_today = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=today_start
    ).values_list("amount", flat=True)
    stars_today_amount = sum(amounts_today) if amounts_today else 0.0

    # За текущий месяц
    stars_month_count = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=first_day_of_month
    ).count()

    amounts_month = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=first_day_of_month
    ).values_list("amount", flat=True)
    stars_month_amount = sum(amounts_month) if amounts_month else 0.0

    # За предыдущий месяц
    stars_last_month_count = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=first_day_of_last_month,
        created_at__lt=first_day_of_month
    ).count()

    amounts_last_month = await models.Payment.filter(
        method=models.PaymentMethod.STARS,
        is_success=True,
        created_at__gte=first_day_of_last_month,
        created_at__lt=first_day_of_month
    ).values_list("amount", flat=True)
    stars_last_month_amount = sum(amounts_last_month) if amounts_last_month else 0.0

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

    await message.answer(msg_text)



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

    users_count = await models.User.all().count()
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.CONFIRM_BTN, callback_data='send_message_celery'),
                types.InlineKeyboardButton(text='Отмена', callback_data='cancel_send_message')
            ]
        ]
    )

    await message.answer(
        text=f'Сообщение выше будет отправлено {users_count} пользователям. Продолжить?',
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
    if message.from_user.id not in ADMINS:
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

    user.balance += amount
    await user.save()

    logger.bind(user_id=message.from_user.id, action='add_balance').log('USER_ACTION', f'Пополнение баланса {telegram_id} на {amount}')
    await message.answer(f"Баланс пользователя {user} пополнен на {amount}.")

    if amount > 0:
        await bot.send_message(telegram_id, f"Администратор пополнил ваш баланс на {amount}.")

    msg_text = f"Администратор пополнил баланс пользователю {user.mention} на {amount}."
    await send_coder(msg_text)


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

    from tortoise.expressions import Q
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
    /info_id [telegram_id] - Информация о пользователе
    /user_report [telegram_id] - HTML-отчёт по пользователю
    /users_with_balance [сумма] - Выгрузка пользователей с балансом выше указанного
    /users_without_payments - Пользователи с балансом > 0 и без пополнений
    /users_with_discrepancy - Выгрузка пользователей с (расходы + баланс) > пополнений
    /users_with_overspent - Пользователи с расходами > пополнений (без учёта баланса)
    /smsactivate - Установить SMS_Activate
    /onlinesim - Установить Onlinesim
    """

    await message.answer(f"<b>Доступные команды для админов:</b>\n{commands}", parse_mode="HTML")