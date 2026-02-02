import asyncio
import hashlib
import json
from typing import Any

import requests

from app.dependencies import ANY_PAY_ID, ANY_PAY_API_KEY, ANY_PAY_PROJECT_ID


class AnypayAPI:
    """
    Класс для работы с API Anypay.

    ВАЖНО:
    Раньше тут были requests.get(...) внутри async-методов — это блокировало event loop,
    из-за чего бот "зависал", поллинг Telegram ловил таймауты, а APScheduler пропускал джобы.
    Теперь все HTTP-вызовы выполняются в отдельном потоке через asyncio.to_thread.
    """

    def __init__(self):
        self.anypay_id = ANY_PAY_ID
        self.anypay_api_key = ANY_PAY_API_KEY
        self.anypay_project_id = ANY_PAY_PROJECT_ID

    def _generate_sign(self, operation: str, *args: object) -> str:
        """
        Генерирует подпись для API запроса.

        :param operation: Название операции
        :param args: Дополнительные аргументы для формирования подписи
        :return: SHA-256 хеш строки подписи
        """
        sign_str = f"{operation}{self.anypay_id}" + "".join(str(arg) for arg in args) + self.anypay_api_key
        return hashlib.sha256(sign_str.encode()).hexdigest()

    @staticmethod
    def _sync_get(url: str, *, params: dict[str, Any] | None = None, timeout: int = 20) -> requests.Response:
        """
        СИНХРОННЫЙ запрос (для выполнения в отдельном потоке).
        """
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r

    async def _get(self, url: str, *, params: dict[str, Any] | None = None, timeout: int = 20) -> requests.Response:
        """
        НЕ блокирует event loop — выполняет requests.get в отдельном потоке.
        """
        return await asyncio.to_thread(self._sync_get, url, params=params, timeout=timeout)

    async def get_balance(self) -> str:
        """
        Получает текущий баланс пользователя.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign("balance")
        response = await self._get(f"https://anypay.io/api/balance/{self.anypay_id}", params={"sign": sign})
        return response.text

    async def get_rates(self) -> str:
        """
        Получает текущие курсы валют.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign("rates")
        response = await self._get(f"https://anypay.io/api/rates/{self.anypay_id}", params={"sign": sign})
        return response.text

    async def get_commissions(self, project_id: int) -> str:
        """
        Получает комиссии для указанного проекта.

        :param project_id: ID проекта
        :return: Строка с ответом сервера
        """
        sign = self._generate_sign("commissions", project_id)
        response = await self._get(
            f"https://anypay.io/api/commissions/{self.anypay_id}?project_id={project_id}",
            params={"sign": sign},
        )
        return response.text

    async def get_last_pay_id(self) -> int:
        """
        Получает последний ID платежа.

        :return: Последний ID платежа
        """
        try:
            payments = await self.get_payments()
            payments_data = json.loads(payments)

            if payments_data.get("result") and payments_data["result"].get("payments"):
                return int(payments_data["result"]["payments"][0]["pay_id"])

            return 0
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            return 0

    async def create_payment(
        self,
        amount: float,
        currency: str,
        desc: str,
        method: str = "card",
        email: str = "test@mail.ru",
    ) -> str:
        """
        Создает новый платеж.

        :param amount: Сумма платежа
        :param currency: Валюта платежа
        :param desc: Описание платежа
        :param method: Метод оплаты (по умолчанию 'card')
        :param email: Email плательщика (по умолчанию 'test@mail.ru')
        :return: URL для оплаты или сообщение об ошибке
        """
        try:
            pay_id = await self.get_last_pay_id()
            sign = self._generate_sign(
                "create-payment",
                self.anypay_project_id,
                pay_id,
                amount,
                currency,
                desc,
                method,
            )

            url = (
                f"https://anypay.io/api/create-payment/{self.anypay_id}"
                f"?project_id={self.anypay_project_id}&pay_id={pay_id}"
                f"&amount={amount}&currency={currency}&desc={desc}&method={method}&email={email}"
            )

            response = await self._get(url, params={"sign": sign})

            json_data = response.json()
            if "error" in json_data:
                error_code = json_data["error"].get("code")
                error_message = json_data["error"].get("message", "Неизвестная ошибка")
                return f"Ошибка {error_code}: {error_message}"

            return json_data["result"]["payment_url"]

        except requests.exceptions.RequestException as e:
            return f"Ошибка при запросе: {e}"
        except KeyError:
            return "Ошибка в структуре ответа от сервера."
        except Exception as e:
            return f"Непредвиденная ошибка: {e}"

    async def get_payments(self) -> str:
        """
        Получает список платежей.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign("payments", self.anypay_project_id)
        response = await self._get(
            f"https://anypay.io/api/payments/{self.anypay_id}?project_id={self.anypay_project_id}",
            params={"sign": sign},
        )
        return response.text

    async def create_payout(self, payout_id: int, payout_type: str, amount: float, wallet: str) -> str:
        """
        Создает новую выплату.

        :param payout_id: ID выплаты
        :param payout_type: Тип выплаты
        :param amount: Сумма выплаты
        :param wallet: Кошелек для выплаты
        :return: Строка с ответом сервера
        """
        sign = self._generate_sign("create-payout", payout_id, payout_type, amount, wallet)
        url = (
            f"https://anypay.io/api/create-payout/{self.anypay_id}"
            f"?payout_id={payout_id}&payout_type={payout_type}&amount={amount}&wallet={wallet}"
        )
        response = await self._get(url, params={"sign": sign})
        return response.text

    async def get_payouts(self) -> str:
        """
        Получает список выплат.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign("payouts")
        response = await self._get(f"https://anypay.io/api/payouts/{self.anypay_id}", params={"sign": sign})
        return response.text

    async def get_ip_notification(self) -> str:
        """
        Получает IP-адрес для уведомлений.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign("ip-notification")
        response = await self._get(f"https://anypay.io/api/ip-notification/{self.anypay_id}", params={"sign": sign})
        return response.text
