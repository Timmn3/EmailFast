import aiohttp
import asyncio
import random

async def fetch_tariffs(country, services_dict):
    """
    Асинхронная функция для получения списка уникальных сервисов из API OnlineSim.

    Args:
        country (int): Код страны.
        services_dict (dict): Словарь для хранения slug -> service.
    """
    url = "https://onlinesim.io/api/getTariffs.php"
    params = {
        "locale_price": "RUB",
        "country": country
    }

    # Добавляем случайную задержку перед каждым запросом (от 1 до 5 секунд)
    await asyncio.sleep(random.uniform(1, 5))

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                services = data.get("services", {})
                for slug, service_info in services.items():
                    service = service_info.get("service")
                    if service:
                        services_dict[slug] = service

async def main():
    country_codes = [
        57, 56, 49, 7, 1, 44, 55, 60, 62, 63, 380, 33, 34, 46, 48, 31, 359, 212, 91, 45,
        351, 357, 420, 40, 84, 61, 371, 381, 370, 372, 373, 995, 77, 996, 254, 52, 54, 90,
        998, 20, 972, 967, 852, 234, 353, 225, 233, 232, 509, 886, 216, 964, 504, 235, 226,
        92, 220, 992, 98, 994, 93, 591, 231, 976, 977, 223, 358, 224, 66, 30, 65, 375, 32,
        237, 39, 593, 966, 41, 421, 36, 389, 386, 51, 505, 249, 503, 256, 260, 265, 245,
        222, 221, 43, 385, 86, 856, 95, 855, 64, 507, 241, 251, 243, 257, 229, 27, 244,
        961, 258, 963, 81, 350, 356, 352
    ]

    services_dict = {}  # Словарь для хранения slug -> service

    tasks = []
    for country in country_codes:
        tasks.append(fetch_tariffs(country, services_dict))
        await asyncio.sleep(random.uniform(0.5, 2))  # Задержка между запусками задач

    await asyncio.gather(*tasks)

    print(services_dict)  # Вывод словаря

if __name__ == '__main__':
    asyncio.run(main())
