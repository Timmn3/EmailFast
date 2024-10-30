import requests
import json


def create_invoice():
    url = "https://api2.ckassa.ru/api-shop/rs/open/invoice/create2"

    headers = {
        "ApiLoginAuthorization": "051b1b08-57dd-472f-b3a9-7d66c38b1928",  # Боевой логин
        "ApiAuthorization": "ce5c98af-2017-40ca-8e42-dedfc791c20e",  # Боевой ключ
        "Content-Type": "application/json",
        "accept": "text/plain"
    }

    data = {
        "servCode": "17233-19052-1",  # Укажи нужный код сервиса
        "tgInvPayer": "string",  # Укажи идентификатор плательщика (например, Telegram ID)
        "amount": 5000,  # Сумма в копейках
        "bestBefore": "27-12-2024 04:40:07 +0500",  # Дата окончания действия ссылки
        "nodeName": "ACQ4I",  # Укажи нужное название узла
        "invType": "READ_ONLY",  # Тип инвойса
        "startPaySelect": True,  # Переход сразу к выбору метода оплаты
        "properties": ["1234567890123"]  # Реквизиты платежа
    }

    response = requests.post(url, headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        invoice_url = response.text  # Ответ в виде текста содержит URL для оплаты
        return invoice_url
    else:
        return f"Error: {response.status_code}, {response.text}"


# Пример использования
invoice_link = create_invoice()
print(invoice_link)
