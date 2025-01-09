from loguru import logger
from app.dependencies import CRYPTOMUS_API_KEY, CRYPTOMUS_MERCHANT_ID
from datetime import datetime, timedelta
from pyCryptomusAPI import pyCryptomusAPI
import pytz

client = pyCryptomusAPI(
    merchant_uuid=CRYPTOMUS_MERCHANT_ID,
    payment_api_key=CRYPTOMUS_API_KEY
)

url_bot = "https://t.me/emailfastbot"

def link_to_cryptomus(amount: float, order_id: str, currency: str = "RUB"):
    """Создание ссылки на оплату через Cryptomus"""
    try:
        response = client.create_invoice(
            amount=amount,  # Сумма платежа
            currency=currency,  # Код валюты
            order_id=order_id,  # Уникальный ID заказа
            url_return=url_bot,  # URL возврата на ваш сайт
            url_success=url_bot,  # URL успешного платежа
        )
        return response.url
    except Exception as e:
        logger.error(f"Ошибка при создании инвойса: {e}")
        return None


def checking_invoice_cryptomus(order):
    """Проверка статуса инвойса"""
    try:
        invoice_status = client.payment_information(order_id=order)
        status = handle_payment_status(invoice_status.payment_status)
        return status
    except Exception as e:
        # Проверяем, если ошибка связана с отсутствием данных
        if str(e) == r"No query results for model [App\Models\MerchantPayment].":
            return "unknown"
        else:
            logger.warning(f"Ошибка при проверке инвойса: {e}")
            return "error"  # Возвращаем "error" на случай других ошибок

def handle_payment_status(status):
    """Обработка статуса платежа"""
    if status in ['paid', 'paid_over']:
        return "paid"  # Платеж успешен или оплачено больше
    elif status in ['process', 'confirm_check', 'check', 'wrong_amount_waiting']:
        return "wait"  # Платеж в процессе, ожидает подтверждений или дополнительные платежи
    elif status in ['wrong_amount', 'fail', 'cancel', 'system_fail', 'locked']:
        return "cancel"  # Платеж отменен или произошла ошибка
    elif status in ['refund_process', 'refund_fail']:
        return "cancel"  # Процесс возврата средств или ошибка возврата
    else:
        return "unknown"  # Если статус неизвестен



# Получаем Московский часовой пояс
moscow_tz = pytz.timezone('Europe/Moscow')


def get_invoices_last_hour():
    try:
        # Получаем текущее время в Москве
        now_moscow = datetime.now(moscow_tz)

        # Получаем время за последний час в Москве
        date_from = now_moscow - timedelta(hours=1)
        date_to = now_moscow

        # Получаем историю платежей за последний час
        invoice_history = client.payment_history(date_from=date_from, date_to=date_to)

        # Формируем словарь, где ключ - order_id, а значение - статус
        invoices_dict = {
            invoice.order_id: handle_payment_status(invoice.payment_status) for invoice in invoice_history.items
        }

        return invoices_dict

    except Exception as e:
        logger.error(f"Ошибка при получении инвойсов за последний час: {e}")
        return {}


def get_paid_order_ids():
    # Фильтруем ключи, где значение равно "paid"
    invoices_dict = get_invoices_last_hour()
    paid_order_ids = [order_id for order_id, status in invoices_dict.items() if status == "paid"]
    return paid_order_ids