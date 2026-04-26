from loguru import logger
from app.dependencies import CRYPTOMUS_API_KEY, CRYPTOMUS_MERCHANT_ID, CRYPTOMUS_API_KEY_PAYOUT
from datetime import datetime, timedelta
from pyCryptomusAPI import pyCryptomusAPI
import pytz
import json
import hashlib
import base64
import requests

from app.services.payments.cryptomus_payout_api import CryptomusPayoutAPI, CryptomusPayoutAPIException

client = pyCryptomusAPI(
    merchant_uuid=CRYPTOMUS_MERCHANT_ID,
    payment_api_key=CRYPTOMUS_API_KEY,
    payout_api_key=CRYPTOMUS_API_KEY_PAYOUT
)

url_bot = "https://t.me/emailfastbot"

HELEKET_API_URL = "https://api.heleket.com/v1/payment"


def link_to_heleket(amount: float, order_id: str, currency: str = "RUB"):
    """Создание ссылки на оплату через Heleket (прямой HTTP-запрос)"""
    payload_json = None
    try:
        payload = {
            "amount": str(int(amount)) if amount == int(amount) else str(amount),
            "currency": currency,
            "order_id": str(order_id),
            "url_return": url_bot,
            "url_success": url_bot,
        }
        payload_json = json.dumps(payload)
        sign_raw = base64.b64encode(payload_json.encode("ascii")).decode() + CRYPTOMUS_API_KEY
        sign = hashlib.md5(sign_raw.encode("ascii")).hexdigest()
        headers = {
            "merchant": CRYPTOMUS_MERCHANT_ID,
            "sign": sign,
            "Content-Type": "application/json",
        }
        logger.info("[Heleket] Запрос | URL: {} | payload: {} | headers: {}", HELEKET_API_URL, payload_json, headers)
        resp = requests.post(HELEKET_API_URL, data=payload_json, headers=headers, timeout=15)
        logger.info("[Heleket] Ответ | status: {} | body: {}", resp.status_code, resp.text)
        data = resp.json()
        if data.get("state") == 0:
            result = data["result"]
            if result.get("is_final") and result.get("payment_status") == "cancel":
                logger.error("[Heleket] Инвойс для order_id {} уже финальный/отменён (создан {}), новый не создан", order_id, result.get("created_at"))
                return None
            return result["url"]
        else:
            logger.error("[Heleket] вернул ошибку: {}", data)
            return None
    except Exception as e:
        logger.info("[Heleket] Ошибка при создании инвойса | payload: {} | error: {}", payload_json, e)
        return None


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


from typing import Optional, Dict

# Инициализация клиента
payout_client = CryptomusPayoutAPI(
    merchant_uuid=CRYPTOMUS_MERCHANT_ID,
    payout_api_key=CRYPTOMUS_API_KEY_PAYOUT,
    print_errors=True,
    timeout=30
)

# Создание выплаты
async def create_a_payout(
    amount: str,  # Сумма выплаты (ожидается строка, как в документации Cryptomus)
    to_currency: str, # Криптовалюта выплаты
    order_id: str,  # Уникальный ID заказа
    address: str,  # Адрес кошелька для выплаты
    network: str   # Блокчейн-сеть
) -> Optional[Dict]:
    """
    Создает выплату через Cryptomus API.

    :param amount: Сумма выплаты (в виде строки)
    :param to_currency: Криптовалюта выплаты (в виде строки)
    :param order_id: Уникальный идентификатор заказа
    :param address: Адрес кошелька получателя
    :param network: Код блокчейн-сети (например, TRON, BTC)
    :return: Ответ от API или None в случае ошибки
    """
    try:
        response = client.create_payout(
            amount=amount,
            currency="RUB",
            order_id=order_id,
            address=address,
            is_subtract=False,
            network=network,
            to_currency=to_currency
        )
        logger.info("Выплата успешно создана:", response)
        return response
    except CryptomusPayoutAPIException as e:
        logger.error(f"Ошибка создания выплаты: {e.message}")
        return None

