import aiohttp

from app.dependencies import API_KEY_ONLINESIM


class OnlineSimRentAPI:
    """
    Класс для работы с API аренды номеров OnlineSim.
    """

    BASE_URL = "https://onlinesim.ru/api/rent"

    def __init__(self):
        self.api_key = API_KEY_ONLINESIM

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
                async with session.get(f"{self.BASE_URL}/tariffsRent.php", params=params) as response:
                    if response.status != 200:
                        raise Exception(f"Ошибка: {response.status}, {await response.text()}")
                    data = await response.json()

                    # Фильтрация данных
                    filtered_data = {
                        country_code: dict(sorted(info["days"].items(), key=lambda x: int(x[0])))
                        for country_code, info in data.items()
                        if info.get("enabled", False)
                    }

                    return filtered_data

            except aiohttp.ClientError as e:
                raise Exception(f"Ошибка при выполнении запроса: {e}")

    async def rent_number(
            self,
            country: int,
            days: int,
            extension: bool = True,
            pagination: bool = False,
            lang: str = "ru"
    ) -> dict:
        """
        Аренда номера для приема SMS.

        :param country: int - Код страны (в формате E.164, без "+").
        :param days: int - Начальный период аренды в днях.
        :param extension: bool - Автопродление аренды (по умолчанию: True).
        :param pagination: bool - Пагинация сообщений (по умолчанию: False).
        :param lang: str - Язык ответа (по умолчанию: "ru").
        :return: dict - Данные об арендованном номере.
        :raises: Exception - В случае ошибки запроса.
        """
        params = {
            "apikey": self.api_key,
            "country": country,
            "days": days,
            "extension": str(extension).lower(),
            "pagination": str(pagination).lower(),
            "lang": lang
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{self.BASE_URL}/getRentNum.php", params=params) as response:
                    if response.status != 200:
                        raise Exception(f"Ошибка: {response.status}, {await response.text()}")
                    data = await response.json()

                    if data.get("response") != 1:
                        raise Exception(f"Ошибка API: {data}")

                    return data.get("item", {})

            except aiohttp.ClientError as e:
                raise Exception(f"Ошибка при выполнении запроса: {e}")

    async def extend_rent_state(self, tzid: int, days: int, lang: str = "ru") -> dict:
        """
        Продление аренды номера на указанный период.

        :param tzid: int - ID операции аренды.
        :param days: int - Период продления аренды в днях.
        :param lang: str - Язык ответа (по умолчанию: "ru").
        :return: dict - Данные о продлении аренды.
        :raises: Exception - В случае ошибки запроса.
        """
        params = {
            "apikey": self.api_key,
            "tzid": tzid,
            "days": days,
            "lang": lang
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{self.BASE_URL}/extendRentState.php", params=params) as response:
                    if response.status != 200:
                        raise Exception(f"Ошибка: {response.status}, {await response.text()}")
                    data = await response.json()

                    if data.get("response") != 1:
                        raise Exception(f"Ошибка API: {data}")

                    return data.get("item", {})

            except aiohttp.ClientError as e:
                raise Exception(f"Ошибка при выполнении запроса: {e}")

    async def get_rent_state(self, tzid: int = None, pagination: bool = False, lang: str = "ru") -> dict:
        """
        Получение списка активных арендных номеров или информации о конкретной аренде.

        :param tzid: int - ID операции аренды. Если не указан, возвращается список всех активных аренд.
        :param pagination: bool - Включение пагинации для списка сообщений (по умолчанию: False).
        :param lang: str - Язык ответа (по умолчанию: "ru").
        :return: dict - Список активных арендных номеров или данные конкретной аренды.
        :raises: Exception - В случае ошибки запроса.
        """
        params = {
            "apikey": self.api_key,
            "pagination": str(pagination).lower(),
            "lang": lang
        }
        if tzid is not None:
            params["tzid"] = tzid

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{self.BASE_URL}/getRentState.php", params=params) as response:
                    if response.status != 200:
                        raise Exception(f"Ошибка: {response.status}, {await response.text()}")
                    data = await response.json()

                    if data.get("response") != 1:
                        raise Exception(f"Ошибка API: {data}")

                    return data.get("list", {}) if tzid is None else data

            except aiohttp.ClientError as e:
                raise Exception(f"Ошибка при выполнении запроса: {e}")

    async def close_rent_num(self, tzid: int, lang: str = "ru") -> dict:
        """
        Закрытие аренды номера.

        :param tzid: int - ID операции аренды, которую нужно закрыть.
        :param lang: str - Язык ответа (по умолчанию: "ru").
        :return: dict - Ответ API с подтверждением закрытия аренды.
        :raises: Exception - В случае ошибки запроса.
        """
        params = {
            "apikey": self.api_key,
            "tzid": tzid,
            "lang": lang
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{self.BASE_URL}/closeRentNum.php", params=params) as response:
                    if response.status != 200:
                        raise Exception(f"Ошибка: {response.status}, {await response.text()}")
                    data = await response.json()

                    if data.get("response") is not True:
                        raise Exception(f"Ошибка API: {data}")

                    return data

            except aiohttp.ClientError as e:
                raise Exception(f"Ошибка при выполнении запроса: {e}")