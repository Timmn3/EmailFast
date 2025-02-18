import binascii
import json
from datetime import datetime
import aiohttp
import httpx
from nacl.bindings import crypto_sign, crypto_sign_BYTES

from app.dependencies import PRIVATE_KEY, STORE_ID, API_URL

API_BASE_URL = 'https://api.streampay.org'
key = binascii.unhexlify(PRIVATE_KEY)


async def create_payment_streampay(amount: float, external_id: str, user_id: str):
    """
    Асинхронная функция для создания платежа в StreamPay API.

    Параметры:
    external_id (str): Уникальный внешний идентификатор платежа.
    amount (float): Сумма платежа.
    user_id (str): УНИКАЛЬНЫЙ id который присвоен на нашей стороне вашему клиенту

    Возвращает:
    tuple: Возвращает кортеж из двух элементов:
        - invoice (str): Уникальный идентификатор счета.
        - pay_url (str): URL для оплаты.
    """
    req_content = json.dumps(dict(
        store_id=STORE_ID,  # integer
        customer=user_id,  # string
        external_id=external_id,  # string
        description="Оплата",  # string
        system_currency="USDT",  # string
        payment_type=1,  # integer
        currency="RUB",  # string
        amount=amount,  # float
    ))

    to_sign = req_content.encode('utf-8') + bytes(datetime.utcnow().strftime('%Y%m%d:%H%M'), 'ascii')
    signature = binascii.hexlify(crypto_sign(to_sign, key)[:crypto_sign_BYTES])

    headers = {
        'Content-Type': 'application/json',
        'Signature': signature
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(f'{API_BASE_URL}/api/payment/create', content=req_content, headers=headers)

        if resp.status_code == 200:
            resp_data = resp.json()
            invoice = resp_data['data']['invoice']
            pay_url = resp_data['data']['pay_url']
            return invoice, pay_url
        elif resp.status_code == 403:
            raise Exception('Invalid signature')
        elif resp.status_code == 406:
            raise Exception('Invalid request data')
        elif resp.status_code == 500:
            raise Exception('Internal server error')

async def get_payment_data_streampay(invoice):
    """
        Асинхронная функция для получения данных о платеже по API по заданному invoice.

        Функция выполняет HTTP GET запрос к API указанного API_URL, используя указанный invoice.
        Если запрос успешен (статус 200), возвращает JSON-ответ с данными платежа.
        В случае ошибки возвращает None.

        Параметры:
        invoice (str): Уникальный идентификатор платежа (invoice), который используется для поиска информации о платеже.

        Возвращает:
        dict | None: Возвращает словарь с данными платежа при успешном запросе или None, если запрос не удался.
        """
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL.format(invoice=invoice)) as response:
            if response.status == 200:
                return await response.json()
            else:
                return None


async def get_payment_status_streampay(invoice):
    """
    Асинхронная функция для получения статуса платежа из API StreamPay по заданному invoice.

    Функция выполняет HTTP GET запрос к API, используя указанный invoice.
    Возвращает статус платежа, если запрос успешен и данные найдены.
    В случае ошибки возвращает None.

    Параметры:
    invoice (str): Уникальный идентификатор платежа (invoice), который используется для поиска информации о платеже.

    Возвращает:
    str | None: Возвращает статус платежа при успешном запросе или None, если запрос не удался.
    """
    payment_data = await get_payment_data_streampay(invoice)
    if payment_data is not None:
        return payment_data.get('status')

    return None
