from pyonlinesim import OnlineSMS
import asyncio
API_KEY_ONLINESIM = "REDACTED"

client = OnlineSMS(api_key=API_KEY_ONLINESIM)

async def get_number(service_code, country_id):
    order_number_response = await client.order_number(service=service_code, country=country_id)
    return order_number_response

# Пример вызова функции
if __name__ == '__main__':
    result = asyncio.run(get_number("magnit", 7))
    print(result)


