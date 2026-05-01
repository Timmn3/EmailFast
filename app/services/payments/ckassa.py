import json
import httpx
from datetime import datetime, timedelta
from uuid import uuid4

from loguru import logger
from app.dependencies import API_LOGIN_CKASSA, API_KEY_CKASSA, SERV_CODE_CKASSA, CODER
import pytz


async def create_invoice_ckassa(amount_rub: float, payer_id: str):
    """
    Асинхронная функция для создания инвойса через CKassa API.

    Параметры:
    amount_rub (float): Сумма в рублях.
    payer_id (str): Идентификатор плательщика (например, Telegram ID).

    Возвращает:
    tuple:
        - invoice_id (str|None): Уникальный идентификатор инвойса (<= 40 символов) или None, если инвойс не создан.
        - invoice_url (str|None): Ссылка на оплату или None, если провайдер вернул ошибку.
    """
    # Конвертируем сумму из рублей в копейки
    amount_kopecks = int(amount_rub * 100)

    # Получаем текущую дату и прибавляем +0300 (оставляю как у тебя, чтобы не менять поведение)
    tz_moscow = pytz.timezone('Europe/Moscow')
    best_before = (datetime.now(tz_moscow) + timedelta(hours=1)).strftime("%d-%m-%Y %H:%M:%S %z")

    payer_id = str(payer_id)

    # ✅ invoice_id идёт в "properties" => у CKassa реквизит "Логин" максимум 40 символов
    # Делаем короткий, но уникальный суффикс (16 hex = 64 бита, коллизии крайне маловероятны)
    invoice_id = f"{payer_id}_{uuid4().hex[:16]}"
    if len(invoice_id) > 40:
        invoice_id = invoice_id[:40]

    from app.services.coder_notify import send_coder
    if payer_id == str(CODER):
        # await send_coder(invoice_id)
        print(invoice_id)

    url = "https://api2.ckassa.ru/api-shop/rs/open/invoice/create2"

    headers = {
        "ApiLoginAuthorization": API_LOGIN_CKASSA,
        "ApiAuthorization": API_KEY_CKASSA,
        "Content-Type": "application/json",
        "accept": "text/plain"
    }

    data = {
        "servCode": SERV_CODE_CKASSA,
        "tgInvPayer": payer_id,
        "amount": amount_kopecks,  # Сумма в копейках
        "bestBefore": best_before,  # Дата окончания действия ссылки с таймзоной +0500
        "nodeName": "ACQ4I",  # Название узла
        "invType": "READ_ONLY",  # Тип инвойса
        "startPaySelect": True,  # Переход сразу к выбору метода оплаты
        "properties": [invoice_id]  # Реквизиты платежа (уникальный идентификатор)
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, data=json.dumps(data))
    except Exception as e:
        # ВАЖНО: если запрос не ушёл/упал — инвойса у провайдера НЕТ, значит invoice_id нельзя считать валидным
        logger.opt(exception=e).error(f"CKassa create2: ошибка запроса (invoice_id={invoice_id})")
        return None, None

    if response.status_code == 200:
        invoice_url = response.text
        return invoice_id, invoice_url

    logger.error(f"CKassa create2: status={response.status_code}, body={response.text[:500]}")
    # ВАЖНО: инвойс не создан — не возвращаем invoice_id, чтобы не плодить 'Invoice not found' в проверке
    return None, None


async def get_ckassa_payments(invoice: str):
    """
    Асинхронная функция для получения данных о платеже по инвойсу.

    Параметры:
    invoice (str): Уникальный идентификатор инвойса.

    Возвращает:
    dict: Данные о платеже или сообщение об ошибке.
    """
    url = f'https://emailfast.info/ckassa/payment/{invoice}/'

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)

            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                return {'error': 'Invoice not found'}

            return {'error': 'Failed to retrieve data', 'status_code': response.status_code}

    except httpx.RequestError as e:
        return {'error': str(e)}
