import aiohttp
import asyncio
import json
import os

JSON_FILE = "services.json"

def load_existing_services():
    """Загружает существующие сервисы из JSON-файла."""
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)  # Загружаем словарь из JSON
            except json.JSONDecodeError:
                return {}  # Если файл поврежден, возвращаем пустой словарь
    return {}

def save_services(services_dict):
    """Сохраняет обновленный словарь сервисов в JSON-файл."""
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(services_dict, f, ensure_ascii=False, indent=4)
    print("Данные сохранены в", JSON_FILE)

async def fetch_tariffs(country, services_dict):
    """
    Асинхронная функция для получения списка уникальных сервисов из API OnlineSim.

    Args:
        country (int): Код страны.
        services_dict (dict): Словарь для хранения уникальных сервисов (slug -> service).
    """
    url = "https://onlinesim.io/api/getTariffs.php"
    params = {
        "locale_price": "RUB",
        "country": country
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                print(f"Парсим данные для страны {country}...")
                data = await response.json()
                services = data.get("services", {})
                for service_info in services.values():
                    slug = service_info.get("slug")
                    service = service_info.get("service")
                    if slug and service:
                        services_dict[slug] = service  # Обновляем словарь без дубликатов

async def main():
    country_codes = [31, 359, 212, 91, 45,
        351, 357, 420, 40, 84, 61, 371, 381, 370, 372, 373, 995, 77, 996, 254, 52, 54, 90,
        998, 20, 972, 967, 852, 234, 353, 225, 233, 232, 509, 886, 216, 964, 504, 235, 226,
        92, 220, 992, 98, 994, 93, 591, 231, 976, 977, 223, 358, 224, 66, 30, 65, 375, 32,
        237, 39, 593, 966, 41, 421, 36, 389, 386, 51, 505, 249, 503, 256, 260, 265, 245,
        222, 221, 43, 385, 86, 856, 95, 855, 64, 507, 241, 251, 243, 257, 229, 27, 244,
        961, 258, 963, 81, 350, 356, 352
    ]
    existing_services = load_existing_services()  # Загружаем данные из JSON
    new_services = existing_services.copy()  # Создаем копию для обновления

    for country in country_codes:
        await fetch_tariffs(country, new_services)
        await asyncio.sleep(5)  # Задержка в 5 секунд перед следующей страной

    # Определяем новые сервисы
    added_services = {slug: service for slug, service in new_services.items() if slug not in existing_services}

    if added_services:
        print("Добавлены новые сервисы:")
        for slug, service in added_services.items():
            print(f"{slug}: {service}")

        save_services(new_services)  # Сохраняем обновленный JSON

if __name__ == '__main__':
    asyncio.run(main())
