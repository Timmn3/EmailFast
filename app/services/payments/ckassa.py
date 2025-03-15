import json
import httpx
from datetime import datetime, timedelta
from app.dependencies import API_LOGIN_CKASSA, API_KEY_CKASSA, SERV_CODE_CKASSA, CODER
import pytz




async def create_invoice_ckassa(amount_rub: float, payer_id: str):
    """
    Асинхронная функция для создания инвойса через CKassa API.

    Параметры:
    amount_rub (float): Сумма в рублях.
    payer_id (str): Идентификатор плательщика (например, Telegram ID).

    Возвращает:
    tuple: Возвращает кортеж из двух элементов:
        - invoice_url (str): Ссылка на оплату.
        - invoice_id (str): Уникальный идентификатор инвойса.
    """
    # Конвертируем сумму из рублей в копейки
    amount_kopecks = int(amount_rub * 100)

    # Получаем текущую дату и прибавляем +0300
    tz_moscow = pytz.timezone('Europe/Moscow')
    moscow_time = datetime.now(tz_moscow) + timedelta(hours=2)
    best_before = moscow_time.strftime("%d-%m-%Y %H:%M:%S +0300")


    # Генерируем уникальный invoice_id на основе payer_id и текущего времени
    invoice_id = f"{payer_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    from app.services.periodic_tasks import send_coder
    if payer_id == str(CODER):
        await send_coder(invoice_id)

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

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, data=json.dumps(data))

        if response.status_code == 200:
            invoice_url = response.text  # Ответ содержит URL для оплаты
            return invoice_id, invoice_url
        else:
            return f"Error: {response.status_code}, {response.text}", None


async def get_ckassa_payments(invoice: str):
    """
    Асинхронная функция для получения данных о платеже по инвойсу.

    Параметры:
    invoice (str): Уникальный идентификатор инвойса.

    Возвращает:
    dict: Данные о платеже или сообщение об ошибке.
    """
    # URL вашего API для получения данных о платеже
    url = f'https://emailfast.info/ckassa/payment/{invoice}/'

    try:
        # Выполнение GET-запроса
        async with httpx.AsyncClient() as client:
            response = await client.get(url)

            # Проверка статуса ответа
            if response.status_code == 200:
                # Успешный ответ
                payment_data = response.json()
                return payment_data
            elif response.status_code == 404:
                return {'error': 'Invoice not found'}
            else:
                return {'error': 'Failed to retrieve data', 'status_code': response.status_code}

    except httpx.RequestError as e:
        # Ошибка при выполнении запроса
        return {'error': str(e)}
