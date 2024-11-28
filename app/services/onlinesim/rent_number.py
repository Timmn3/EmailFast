import aiohttp
import asyncio

from app.dependencies import API_KEY_ONLINESIM


class OnlineSimRentAPI:
    """
    Класс для работы с API аренды номеров OnlineSim.
    """

    BASE_URL = "https://onlinesim.ru/api/rent/tariffsRent.php"

    def __init__(self, api_key: str):
        """
        Инициализация клиента API.

        :param api_key: str - API-ключ для OnlineSim.
        """
        self.api_key = api_key

    async def get_tariffs(self, country: str = None, lang: str = "ru") -> dict:
        """
        Получение информации о доступных тарифах аренды номеров с фильтрацией по доступным странам.

        :param country: str - Код страны в формате E.164 (без "+"). Если не указан, данные возвращаются по всем странам.
        :param lang: str - Язык ответа. Возможные значения: "fr", "de", "ru", "en", "zh". По умолчанию: "ru".
        :return: dict - Фильтрованный словарь с данными тарифов в формате {'код страны': {'дни': стоимость}}.
        :raises: Exception - В случае ошибки запроса.
        """
        params = {
            "apikey": self.api_key,
            "lang": lang
        }
        if country:
            params["country"] = country

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(self.BASE_URL, params=params) as response:
                    if response.status != 200:
                        raise Exception(f"Ошибка: {response.status}, {await response.text()}")
                    data = await response.json()

                    # Фильтрация данных
                    filtered_data = {
                        country_code: info["days"]
                        for country_code, info in data.items()
                        if info.get("enabled", False)
                    }

                    return filtered_data

            except aiohttp.ClientError as e:
                raise Exception(f"Ошибка при выполнении запроса: {e}")


# Использование класса
async def main():
    api_client = OnlineSimRentAPI(api_key=API_KEY_ONLINESIM)
    try:
        # Получить данные о тарифах аренды
        filtered_tariffs = await api_client.get_tariffs()
        print(filtered_tariffs)
    except Exception as e:
        print("Произошла ошибка:", e)


if __name__ == "__main__":
    asyncio.run(main())
