import requests
import json

# URL вашего обработчика
url = "https://emailfast.info/ckassa/"

# Данные, которые нужно отправить
data = {
    "regPayNum": "185663662",
    "property": {
        "ЛОГИН": "5635586329_20250529123932"
    },
    "rrn": "006390142417",
    "irn": None,
    "approvalCode": None,
    "cardPan": None,
    "amount": 300,
    "state": "PAYED",
    "result": {
        "code": 0,
        "message": None,
        "details": None
    },
    "created": "2025-03-15 12:10:43"
}

# Отправка POST-запроса с правильной кодировкой
response = requests.post(
    url,
    data=json.dumps(data, ensure_ascii=False).encode('utf-8'),  # Отключаем ASCII-кодировку
    headers={"Content-Type": "application/json; charset=utf-8"}
)

# Вывод ответа от сервера
print(f"Response status code: {response.status_code}")
