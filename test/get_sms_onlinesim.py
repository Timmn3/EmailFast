from pyonlinesim import OnlineSMS
import asyncio

API_KEY_ONLINESIM = "REDACTED"

async def check_sms(operation_id):
    client = OnlineSMS(api_key=API_KEY_ONLINESIM)

    order_info = await client.get_order_info(operation_id=operation_id, get_full_message=True)

    print(f'order_info: {order_info} ')
    return

    sms_code = order_info[0]['msg']
    print(f'sms_code {sms_code}')

if __name__ == "__main__":
    activation_id = 159402820  # Замени на настоящий ID операции
    asyncio.run(check_sms(activation_id))