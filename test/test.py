import aiohttp
import asyncio

API_URL = "http://127.0.0.1:8000/payments/api/get_payment/{invoice}/"

async def get_payment_data(invoice):
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL.format(invoice=invoice)) as response:
            if response.status == 200:
                return await response.json()
            else:
                return None


async def send_payment_info(invoice):
    data = await get_payment_data(invoice)
    print(data)


if __name__ == "__main__":
    asyncio.run(send_payment_info('cc4aff9e-6af0-4207-962f-209f68e3bc5c'))  # Запускаем event loop для асинхронного кода
