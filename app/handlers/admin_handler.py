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
from tortoise.functions import Sum, Count
import calendar

from app.test_upload import load_services_from_file

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
        last_month_name=last_month_name
    )

    await message.answer(msg_text)



@router.message(Command('test_balance'))
async def test_balance(message: types.Message):
    sms = SmsReceive()
    balance = await sms.get_balance()
    print(balance)


@router.message(Command('test_delete'))
async def test_delete(message: types.Message):
    count = await Activation.delete_user_activations(message.from_user.id)
    await message.answer(f'Активации удалены {count}')


@router.message(Command('test_db'))
async def test(message: types.Message):
    await message.answer(f'тест ')
    text = await load_services_from_file("data.txt")
    await message.answer(text)
    return
    # await models.Service.normalize_search_names()


@router.message(Command('test_state'))
async def test_state(message: types.Message, dialog_manager: DialogManager):
    try:
        logger.success(dialog_manager.current_context())
    except Exception as e:
        logger.success(e)


@router.message(Command('freemoney'))
async def free_money(message: types.Message):
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

    await state.clear()
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
    await state.clear()


@router.message(BroadcastState.send_message)
async def on_send_message(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    await state.clear()
    await state.update_data(
        chat_id=message.chat.id,
        message_id=message.message_id
    )
    await bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id=message.chat.id,
        message_id=message.message_id
    )
    users_count = await models.User.all().count()
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.CONFIRM_BTN, callback_data='send_message'),
                types.InlineKeyboardButton(text='Отмена', callback_data='cancel_send_message')
            ]
        ]
    )

    await message.answer(text=f'Сообщение выше будет отправлено {users_count} пользователям. Продолжить?',
                         reply_markup=mk)


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
    await state.clear()


@router.message(Command('affiliate_stat'))
async def affiliate_stat(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

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
    logger.success(f'message.from_user.id: {message.from_user.id} '
                   f'message.successful_payment.telegram_payment_charge_id: {message.successful_payment.telegram_payment_charge_id}')
    refund_star = await message.bot.refund_star_payment(message.from_user.id,
                                                        message.successful_payment.telegram_payment_charge_id)

    await message.answer(str(refund_star))


@router.message(Command('add_balance'))
async def add_balance(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    # Проверка, что команда содержит два аргумента
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Использование: /add_balance <telegram_id> <сумма>")
        return

    try:
        telegram_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("Некорректный Telegram ID или сумма. Пожалуйста, введите числовые значения.")
        return

    # Получаем пользователя по Telegram ID
    user = await models.User.get_user(telegram_id)
    if user is None:
        await message.answer("Пользователь с таким Telegram ID не найден.")
        return

    # Добавляем баланс
    user.balance += amount
    await user.save()  # Сохраняем изменения в базе данных

    # Отправляем уведомление админу
    await message.answer(f"Баланс пользователя {user} пополнен на {amount}.")

    await bot.send_message(telegram_id, f"Администратор пополнил ваш баланс на {amount}.")

    msg_text = f" Администратор пополнил баланс пользователю {user.mention} на {amount}."
    await send_coder(msg_text)

@router.message(Command('services_update'))
async def services(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return
    await message.answer("Обновляю список сервисов...")
    await add_services()
    await message.answer("Сервисы обновлены!")


@router.message(Command('smsactivate'))
async def set_smsactivate(message: types.Message):
    if message.from_user.id not in ADMINS:
        return
    await AdminSettings.update_setting("sms_rental_service", "SMS_Activate")
    await message.answer(text="Установлен сервис SMS_Activate")


@router.message(Command('onlinesim'))
async def set_smsactivate(message: types.Message):
    if message.from_user.id not in ADMINS:
        return
    await AdminSettings.update_setting("sms_rental_service", "Onlinesim")
    await message.answer(text="Установлен сервис Onlinesim")


@router.message(Command('help_admin'))
async def help_admin(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    commands = """
    /stat - Статистика
    /freemoney &lt;сумма&gt; &lt;лимит&gt; - Создать ссылку на бесплатные деньги
    /send - Рассылка сообщений
    /add_balance &lt;telegram_id&gt; &lt;сумма&gt; - Пополнение баланса пользователя
    /smsactivate - Установить SMS_Activate
    /onlinesim - Установить Onlinesim
    """

    await message.answer(f"<b>Доступные команды для админов:</b>\n{commands}", parse_mode="HTML")
