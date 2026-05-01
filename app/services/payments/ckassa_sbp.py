import base64
import httpx
from loguru import logger
from app.dependencies import API_LOGIN_CKASSA, API_KEY_CKASSA, SERV_CODE_CKASSA

_BASE_URL = "https://api2.ckassa.ru/api-shop/rs/shop"


def _auth_header() -> str:
    """Формирует строку BasicAuth для заголовка Authorization."""
    raw = f"{API_LOGIN_CKASSA}:{API_KEY_CKASSA}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


async def create_sbp_payment(amount_rub: float, order_id: str) -> dict | None:
    """
    Создаёт анонимный СБП-платёж через Shop API CKassa.

    Для СБП регистрация пользователя (userToken) не требуется —
    используется метод анонимного платежа /do/payment/anonymous.

    Аргументы:
        amount_rub: сумма платежа в рублях (например, 10.0).
        order_id:   уникальный идентификатор заказа на стороне магазина.

    Возвращает:
        dict с ключами:
            - payUrl     (str)  — deeplink / ссылка на оплату через СБП;
            - payUrlImg  (str)  — QR-код, зашифрованный в Base64, или None;
            - regPayNum  (str)  — номер платежа в системе CKassa.
        None — при любой ошибке запроса или неуспешном ответе.
    """
    url = f"{_BASE_URL}/do/payment/anonymous"
    headers = {"Authorization": _auth_header(), "Content-Type": "application/json"}

    # Сумма передаётся в копейках (целое число)
    amount_kopecks = str(int(amount_rub * 100))

    data = {
        "serviceCode": SERV_CODE_CKASSA,
        "amount": amount_kopecks,
        "comission": "0",
        "payType": "sbp",
        "properties": [
            {"name": "Логин", "value": order_id},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=data)

            # Готовая строка без placeholders — избегаем конфликта с {} в JSON
            # Экранируем { и } в теле ответа, чтобы loguru не интерпретировал их как placeholders
            safe_body = response.text[:500].replace("{", "{{").replace("}", "}}")
            logger.info(
                f"CKassa do/payment/anonymous (sbp): status={response.status_code} body={safe_body}"
            )

        if response.status_code == 200:
            return response.json()

    except Exception as e:
        logger.opt(exception=e).error("CKassa do/payment/anonymous (sbp): ошибка запроса")

    return None