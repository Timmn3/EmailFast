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


class CkassaUnavailable(Exception):
    """
    Статус платежа выяснить НЕ удалось (сеть, 5xx, битый ответ).

    Отдельный тип нужен, чтобы вызывающий код не спутал «сервис лежит»
    с «платёж не оплачен»: в первом случае платёж обязан остаться в очереди
    на повторную проверку, иначе деньги списаны, а баланс не пополнен.
    """


# Таймаут намеренно короткий: статусы проверяются пачкой в одном проходе,
# и при недоступности сервиса длинный таймаут умножается на размер батча.
CKASSA_STATUS_TIMEOUT = 10.0


async def get_ckassa_payments(invoice: str):
    """
    Асинхронная функция для получения данных о платеже по инвойсу.

    Параметры:
    invoice (str): Уникальный идентификатор инвойса.

    Возвращает:
        dict — сервис ответил, статус платежа известен (ключ 'state');
        None — платёж не найден (404), штатный ответ для ещё не оплаченного инвойса.

    Исключения:
        CkassaUnavailable — статус выяснить не удалось, платёж НЕЛЬЗЯ считать неоплаченным.
    """
    url = f'https://emailfast.info/ckassa/payment/{invoice}/'

    try:
        async with httpx.AsyncClient(timeout=CKASSA_STATUS_TIMEOUT) as client:
            response = await client.get(url)
    except Exception as e:
        raise CkassaUnavailable(f"нет связи с {url}: {e!r}") from e

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        raise CkassaUnavailable(
            f"HTTP {response.status_code} от {url}: {response.text[:200]}"
        )

    try:
        return response.json()
    except Exception as e:
        raise CkassaUnavailable(
            f"невалидный JSON от {url}: {response.text[:200]}"
        ) from e
