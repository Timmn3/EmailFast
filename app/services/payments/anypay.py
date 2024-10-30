import requests
import hashlib
import json

from app.dependencies import ANY_PAY_ID, ANY_PAY_API_KEY, ANY_PAY_PROJECT_ID


class AnypayAPI:
    """
    Класс для работы с API Anypay.

    Предоставляет методы для выполнения различных операций через API Anypay,
    включая проверку баланса, получение курсов валют, создание платежей и выплат.
    """

    def __init__(self):
        self.anypay_id = ANY_PAY_ID
        self.anypay_api_key = ANY_PAY_API_KEY
        self.anypay_project_id = ANY_PAY_PROJECT_ID

    def _generate_sign(self, operation, *args):
        """
        Генерирует подпись для API запроса.

        :param operation: Название операции
        :param args: Дополнительные аргументы для формирования подписи
        :return: SHA-256 хеш строки подписи
        """
        sign_str = f'{operation}{self.anypay_id}' + ''.join(str(arg) for arg in args) + self.anypay_api_key
        return hashlib.sha256(sign_str.encode()).hexdigest()

    async def get_balance(self):
        """
        Получает текущий баланс пользователя.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign('balance')
        response = requests.get(f"https://anypay.io/api/balance/{self.anypay_id}", params={"sign": sign})
        return response.text

    async def get_rates(self):
        """
        Получает текущие курсы валют.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign('rates')
        response = requests.get(f"https://anypay.io/api/rates/{self.anypay_id}", params={"sign": sign})
        return response.text

    async def get_commissions(self, project_id):
        """
        Получает комиссии для указанного проекта.

        :param project_id: ID проекта
        :return: Строка с ответом сервера
        """
        sign = self._generate_sign('commissions', project_id)
        response = requests.get(f"https://anypay.io/api/commissions/{self.anypay_id}?project_id={project_id}",
                                params={"sign": sign})
        return response.text

    async def create_payment(self, amount, desc, method, currency='RUB', email='test@mail.ru'):
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
            sign = self._generate_sign('create-payment', self.anypay_project_id, pay_id, amount, currency, desc, method)
            response = requests.get(
                f"https://anypay.io/api/create-payment/{self.anypay_id}?project_id={self.anypay_project_id}&pay_id={pay_id}&amount={amount}&currency={currency}&desc={desc}&method={method}&email={email}",
                params={"sign": sign}
            )
            response.raise_for_status()

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

    async def get_payments(self):
        """
        Получает список платежей.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign('payments', self.anypay_project_id)
        response = requests.get(f"https://anypay.io/api/payments/{self.anypay_id}?project_id={self.anypay_project_id}",
                                params={"sign": sign})
        return response.text

    async def create_payout(self, payout_id, payout_type, amount, wallet):
        """
        Создает новую выплату.

        :param payout_id: ID выплаты
        :param payout_type: Тип выплаты
        :param amount: Сумма выплаты
        :param wallet: Кошелек для выплаты
        :return: Строка с ответом сервера
        """
        sign = self._generate_sign('create-payout', payout_id, payout_type, amount, wallet)
        response = requests.get(
            f"https://anypay.io/api/create-payout/{self.anypay_id}?payout_id={payout_id}&payout_type={payout_type}&amount={amount}&wallet={wallet}",
            params={"sign": sign})
        return response.text

    async def get_payouts(self):
        """
        Получает список выплат.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign('payouts')
        response = requests.get(f"https://anypay.io/api/payouts/{self.anypay_id}", params={"sign": sign})
        return response.text

    async def get_ip_notification(self):
        """
        Получает IP-адрес для уведомлений.

        :return: Строка с ответом сервера
        """
        sign = self._generate_sign('ip-notification')
        response = requests.get(f"https://anypay.io/api/ip-notification/{self.anypay_id}", params={"sign": sign})
        return response.text

    async def check_payment(self, comment):
        """
        Проверяет статус платежа по комментарию.

        :param comment: Комментарий к платежу
        :return: Кортеж (статус оплаты)
        """
        payments = json.loads(await self.get_payments())["result"]["payments"]
        for payment in payments.values():
            if payment["desc"] == comment and payment["status"] == "paid":
                return True

        return False

    async def get_last_pay_id(self):
        """
        Получает последний использованный ID платежа.

        :return: Следующий доступный ID платежа
        """
        payments = json.loads(await self.get_payments())["result"]["payments"]
        if payments:
            return max(int(pay_id) for pay_id in payments.keys()) + 1
        return 0

