import requests
import json
from hashlib import md5
import base64

API_URL = "https://api.cryptomus.com/v1/"

class CryptomusPayoutAPIException(Exception):
    """Кастомное исключение для работы с API выплат"""
    def __init__(self, code, message, full_error=""):
        self.code = code
        self.message = message
        self.full_error = full_error
        super().__init__(self.message)


class CryptomusPayoutAPI:
    def __init__(self, merchant_uuid, payout_api_key, print_errors=False, timeout=None):
        """
        Инициализация клиента для работы с API выплат.

        :param merchant_uuid: Идентификатор мерчанта
        :param payout_api_key: API-ключ для работы с выплатами
        :param print_errors: (Опционально) Логировать ошибки
        :param timeout: (Опционально) Таймаут для запросов
        """
        self.merchant_uuid = merchant_uuid
        self.payout_api_key = payout_api_key
        self.print_errors = print_errors
        self.timeout = timeout

    def __generate_signature(self, data):
        """
        Генерация подписи для запроса.

        :param data: Данные для формирования подписи
        :return: Подпись в виде строки
        """
        json_data = json.dumps(data)
        sign = md5((base64.b64encode(json_data.encode('utf-8')) + self.payout_api_key.encode('utf-8'))).hexdigest()
        return sign

    def __request(self, method_url, data):
        """
        Выполнение POST-запроса к API.

        :param method_url: URL метода
        :param data: Данные для отправки в запросе
        :return: Ответ API в виде словаря
        """
        headers = {
            "merchant": self.merchant_uuid,
            "sign": self.__generate_signature(data),
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(API_URL + method_url, headers=headers, json=data, timeout=self.timeout)
            response_data = response.json()

            if response_data.get("state") != 0:
                raise CryptomusPayoutAPIException(
                    response_data.get("state"),
                    response_data.get("message", "Ошибка запроса к API"),
                    full_error=response_data
                )
            return response_data.get("result", {})
        except requests.RequestException as e:
            if self.print_errors:
                print(f"Ошибка запроса: {e}")
            raise CryptomusPayoutAPIException(-1, f"Ошибка запроса: {e}")

    def create_payout(self, amount, order_id, address, network, to_currency, currency="RUB", is_subtract=False, **kwargs):
        """
        Создание выплаты.

        :param amount: Сумма выплаты
        :param currency: Валюта, в нашем случае рубли
        :param order_id: Уникальный идентификатор заказа
        :param address: Адрес кошелька получателя
        :param is_subtract: Оплата комиссии
        :param network: Сеть блокчейна
        :param to_currency: Код криптовалюты валюты для выплаты
        :param kwargs: Дополнительные параметры (url_callback, to_currency и др.)
        :return: Ответ API о созданной выплате
        """
        data = {
            "amount": str(amount),
            "currency": currency,
            "order_id": order_id,
            "address": address,
            "is_subtract": is_subtract,
            "network": network,
            "to_currency": to_currency
        }
        data.update(kwargs)
        return self.__request("payout", data)
