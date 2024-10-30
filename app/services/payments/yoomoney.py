from app.dependencies import YOOMONEY_TOKEN, YOOMONEY_RECEIVER
from app.services.api_yoomoney import Quickpay, Client
from loguru import logger


async def create_yoomoney_url(sum_amount, label):
    """
    Создает URL для оплаты через ЮMoney с заданной меткой и суммой.

    Аргументы:
        label (str): Метка платежа для отслеживания транзакции.
        sum_amount (float): Сумма платежа в рублях.

    Возвращает:
        str: URL для перенаправления пользователя на страницу оплаты ЮMoney.

    Исключения:
        Exception: Обрабатываются любые ошибки, возникающие при создании платежа.
    """
    try:
        quickpay = Quickpay(
            receiver=YOOMONEY_RECEIVER,  # Номер кошелька получателя
            quickpay_form="shop",  # Форма платежа (настраиваемое поле)
            targets="Email Fast📨",  # Цель платежа (отображается на странице оплаты)
            paymentType="",  # Способ оплаты (в данном случае Сбербанк)
            sum=sum_amount,  # Сумма платежа
            label=label  # Метка платежа для идентификации транзакции
        )
        return quickpay.redirected_url  # Возвращает ссылку для оплаты
    except Exception as e:
        # Логирование ошибки и возврат None, если что-то пошло не так
        logger.error(f"Ошибка при создании платежа: {e}")
        return None


async def check_payment_status(label):
    """
    Проверяет статус платежа в ЮMoney по метке.

    Аргументы:
        label (str): Метка платежа, по которой проверяется статус транзакции.

    Возвращает:
        bool: True, если найден успешный платеж по данной метке, иначе False.

    Исключения:
        Exception: Обрабатываются ошибки при получении истории операций.
    """
    try:
        client = Client(YOOMONEY_TOKEN)  # Создаем клиента ЮMoney с использованием токена
        history = client.operation_history(label=label)  # Получаем историю операций по метке

        # Проходим по всем операциям в истории и проверяем статус
        for operation in history.operations:
            if operation.status == 'success':  # Если операция успешна
                return True  # Возвращаем True, если найден успешный платеж
        return False  # Возвращаем False, если успешных платежей не найдено
    except Exception as e:
        # Логирование ошибки и возврат False, если произошла ошибка
        logger.error(f"Ошибка при проверке статуса платежа: {e}")
        return False
