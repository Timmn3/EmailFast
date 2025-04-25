from aiogram import types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from tortoise.functions import Sum
import tempfile
from datetime import datetime
from app.db import models
from app.dependencies import ADMINS, bot
import pytz
from aiogram import Router, types, F

router = Router()

@router.message(Command('user_report'))
async def user_report(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /user_report [telegram_id]")
        return

    try:
        telegram_id = int(args[1])
    except ValueError:
        await message.answer("Некорректный Telegram ID")
        return

    user = await models.User.get_or_none(telegram_id=telegram_id)
    if not user:
        await message.answer("Пользователь с таким Telegram ID не найден.")
        return

    html_path = await generate_user_report_html(user)

    await message.answer_document(types.FSInputFile(html_path), caption=f"Отчёт по пользователю {user.mention}")

async def generate_user_report_html(user):
    # Получаем данные
    payments = await models.Payment.filter(user=user, is_success=True).order_by("-created_at")
    withdraws = await models.Withdraw.filter(user=user).order_by("-created_at")
    activations = await models.Activation.filter(user=user, sms_text__isnull=False).order_by("-created_at").prefetch_related("country", "service", "service_2")
    rents = await models.Rent.filter(user=user).order_by("-created_at").prefetch_related("country")
    mails = await models.Mail.filter(user=user).order_by("-created_at")
    letters = await models.Letter.filter(user=user).order_by("-created_at")

    # Подсчёты
    total_payments = sum([p.amount for p in payments])
    total_spent = sum([a.cost for a in activations]) + sum([r.cost for r in rents])
    total_rents = len(rents)
    total_sms = len(activations)
    total_mails = len(mails)
    total_letters = len(letters)

    # Формат времени
    def fmt(dt):
        return dt.strftime("%d.%m.%Y %H:%M") if dt else "-"

    # HTML-таблицы
    def make_table(headers, rows):
        head_html = "".join([f"<th>{h}</th>" for h in headers])
        rows_html = "".join([
            f"<tr>{''.join(f'<td>{c}</td>' for c in row)}</tr>"
            for row in rows
        ])
        return f"<table><thead><tr>{head_html}</tr></thead><tbody>{rows_html}</tbody></table><br>"

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
        <h2>Отчёт по пользователю {user.mention} ({user.telegram_id})</h2>

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
            [a.phone_number, (a.service.name if a.service else a.service_2.name) or '-', a.country.name, f"{a.cost:.2f} ₽", a.sms_text or '-', fmt(a.created_at)]
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

    return file_path
