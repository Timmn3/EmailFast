from aiogram import types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from tortoise.functions import Sum
import tempfile
from datetime import datetime
import html
from app.db import models
from app.dependencies import ADMINS, bot
import pytz
from aiogram import Router, types, F
from loguru import logger

router = Router()


@router.message(Command('user_report'))
async def user_report(message: types.Message):
    user_id = message.from_user.id
    logger.bind(user_id=user_id, action="user_report").log("USER_ACTION", "Запрос на генерацию отчёта по пользователю")

    if user_id not in ADMINS:
        logger.warning(f"Пользователь {user_id} не является администратором")
        return

    args = message.text.split()
    if len(args) != 2:
        logger.bind(user_id=user_id, action="user_report").warning("Неверное количество аргументов")
        await message.answer("Использование: /user_report [telegram_id]")
        return

    try:
        telegram_id = int(args[1])
        logger.bind(user_id=user_id, action="user_report").info(f"Получен Telegram ID: {telegram_id}")
    except ValueError:
        logger.bind(user_id=user_id, action="user_report").warning("Некорректный Telegram ID")
        await message.answer("Некорректный Telegram ID")
        return

    user = await models.User.get_or_none(telegram_id=telegram_id)
    if not user:
        logger.bind(user_id=user_id, action="user_report").info(f"Пользователь с ID={telegram_id} не найден")
        await message.answer("Пользователь с таким Telegram ID не найден.")
        return

    logger.bind(user_id=user_id, action="user_report").info(f"Генерация отчёта для пользователя {telegram_id}")
    try:
        html_path = await generate_user_report_html(user)
        logger.bind(user_id=user_id, action="user_report").success(
            f"Отчёт успешно сгенерирован для пользователя {telegram_id}"
        )

        # ✅ Безопасный caption при parse_mode='HTML' (экранируем имя, ссылку формируем сами)
        safe_mention = html.escape(user.mention or str(user.telegram_id))
        caption = f'Отчёт по пользователю <a href="tg://user?id={user.telegram_id}">{safe_mention}</a>'

        await message.answer_document(types.FSInputFile(html_path), caption=caption)

    except Exception as e:
        logger.bind(user_id=user_id, action="user_report").opt(exception=e).error("Ошибка при генерации отчёта")
        await message.answer("Произошла ошибка при генерации отчёта.")


async def generate_user_report_html(user):
    tz = pytz.timezone('Europe/Moscow')

    # Получаем данные
    logger.bind(user_id=user.telegram_id, action="generate_user_report").debug("Запрос данных из БД для отчёта")
    payments = await models.Payment.filter(user=user, is_success=True).order_by("-created_at")
    withdraws = await models.Withdraw.filter(user=user).order_by("-created_at")
    activations = await models.Activation.filter(user=user, sms_text__isnull=False).order_by(
        "-created_at").prefetch_related("country", "service", "service_2")
    rents = await models.Rent.filter(user=user, sms_text__isnull=False).order_by("-created_at").prefetch_related(
        "country")
    mails = await models.Mail.filter(user=user).order_by("-created_at")
    letters = await models.Letter.filter(user=user).order_by("-created_at")

    # Подсчёты
    total_payments = sum([p.amount for p in payments])
    total_spent = sum([a.cost for a in activations]) + sum([r.cost for r in rents if r.sms_text])  # Только аренды с SMS
    total_rents = len([r for r in rents if r.sms_text])  # Только аренды с SMS
    total_sms = len(activations)
    total_mails = len(mails)
    total_letters = len(letters)

    # Формат времени
    def fmt(dt):
        if dt:
            localized_dt = dt.astimezone(tz)
            return localized_dt.strftime("%d.%m.%Y %H:%M")
        return "-"

    # HTML-таблицы
    def make_table(headers, rows):
        head_html = "".join([f"<th>{h}</th>" for h in headers])
        rows_html = "".join([
            f"<tr>{''.join(f'<td>{c}</td>' for c in row)}</tr>"
            for row in rows
        ])
        return f"<table><thead><tr>{head_html}</tr></thead><tbody>{rows_html}</tbody></table><br>"

    logger.bind(user_id=user.telegram_id, action="generate_user_report").debug("Формирование HTML-отчёта")
    html_content = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
            th, td {{ border: 1px solid #ccc; padding: 6px; text-align: left; }}
            th {{ background-color: #f4f4f4; }}
        </style>
    </head>
    <body>
        <h2>Отчёт по пользователю {html.escape(user.mention or '-')} ({user.telegram_id})</h2>

        <h3>Общие данные:</h3>
        <p>Баланс: {user.balance:.2f} ₽<br>
        Бонусный баланс: {user.ref_balance:.2f} ₽<br>
        Всего заработано на рефералах: {user.total_ref_earnings:.2f} ₽<br>
        В канале: {'Да' if user.in_channel else 'Нет'}<br>
        Дата регистрации: {fmt(user.created_at)}<br>
        Последний запрос: {fmt(user.last_request_time)}</p>

        <h3>Пополнения (всего {len(payments)} на сумму {total_payments:.2f} ₽):</h3>
        {make_table(['Сумма', 'Метод', 'Дата'], [[f"{p.amount:.2f} ₽", p.method.value, fmt(p.created_at)] for p in payments])}

        <h3>Выводы (всего {len(withdraws)}):</h3>
        {make_table(['Сумма', 'Реквизиты', 'Статус', 'Дата'], [[f"{w.amount:.2f} ₽", w.requisites, '✅' if w.is_success else '❌', fmt(w.created_at)] for w in withdraws])}

        <h3>Активации SMS (всего {total_sms}):</h3>
        {make_table(['Номер', 'Сервис', 'Страна', 'Цена', 'Текст SMS', 'Дата'], [
        [
            a.phone_number,
            (lambda a: "-" if not a.service and not a.service_2 else a.service.name if a.service else a.service_2.name)(a),
            a.country.name if a.country else "-",
            f"{a.cost:.2f} ₽",
            a.sms_text or '-',
            fmt(a.created_at)
        ]
        for a in activations
        ])}

        <h3>Аренды номеров (всего {total_rents}):</h3>
        {make_table(['Номер', 'Страна', 'Цена', 'SMS', 'Дата', 'Автопродление'], [
        [r.phone_number, r.country.name, f"{r.cost:.2f} ₽", r.sms_text or '-', fmt(r.created_at), 'Да' if r.autorenew else 'Нет']
        for r in rents
    ])}

        <h3>Почты (всего {total_mails}):</h3>
        {make_table(['Email', 'Платная', 'Создана', 'Истекает'], [
        [m.email, 'Да' if m.is_paid_mail else 'Нет', fmt(m.created_at), fmt(m.expire_at)]
        for m in mails
    ])}

        <h3>Письма (всего {total_letters}):</h3>
        {make_table(['Текст', 'Дата'], [
        [l.text, fmt(l.created_at)]
        for l in letters
    ])}

        <h3>Итоги:</h3>
        <p>Всего пополнено: {total_payments:.2f} ₽<br>
        Всего потрачено: {total_spent:.2f} ₽<br>
        Аренд: {total_rents}<br>
        SMS: {total_sms}<br>
        Почт: {total_mails}<br>
        Писем: {total_letters}</p>
    </body>
    </html>
    """

    # Сохраняем временный файл
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        f.flush()
        file_path = f.name

    logger.bind(user_id=user.telegram_id, action="generate_user_report").success("HTML-файл отчёта успешно сохранён")
    return file_path