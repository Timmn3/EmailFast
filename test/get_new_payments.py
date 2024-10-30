import requests


def get_new_payments():
    url = "https://api2.ckassa.ru/api-shop/rs/open/payments/new"

    headers = {
        "ApiLoginAuthorization": "051b1b08-57dd-472f-b3a9-7d66c38b1928",
        "ApiAuthorization": "ce5c98af-2017-40ca-8e42-dedfc791c20e",
        "accept": "application/json",
        "apiVer": "1"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        return f"Error: {response.status_code}, {response.text}"


new_payments = get_new_payments()
print(new_payments)
