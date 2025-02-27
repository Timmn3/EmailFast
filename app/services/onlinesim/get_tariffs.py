import aiohttp
import asyncio

async def fetch_tariffs(country, filter_service):
    """
    Асинхронная функция для получения цены и идентификатора услуги (slug) по указанной стране
    и фильтру услуги через API OnlineSim.

    Args:
        country (int): Код страны, для которой нужно получить тарифы (например, 7 для России).
        filter_service (str): Название услуги, для которой нужно получить цену (например, "Telegram").

    Returns:
        dict: Словарь с ключами "price" и "slug", если запрос успешен.
              Например, {"price": "58.50", "slug": "telegram"}
        dict: Словарь с ключами "error" и "message", если запрос завершился с ошибкой.

    Пример вызова:
        result = asyncio.run(fetch_tariffs(7, "Telegram"))
        print(result)
    """
    url = "https://onlinesim.io/api/getTariffs.php"
    params = {
        "locale_price": "RUB",
        "country": country,             # Переданная страна
        # "filter_service": filter_service  # Переданный фильтр по сервису
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()

                # Извлечение первого значения "price" и "slug" из "services"
                services = data.get("services", {})
                for service_info in services.values():
                    price = service_info.get("price")
                    slug = service_info.get("slug")
                    return {"price": price, "slug": slug} if price and slug else None
            else:
                return {"error": response.status, "message": await response.text()}

# Пример вызова функции
if __name__ == '__main__':
    result = asyncio.run(fetch_tariffs(7, "Telegram"))
    print(result)
