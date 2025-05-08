import aiohttp
from loguru import logger

from app.dependencies import API_KEY_ONLINESIM


class OnlineSimRentAPI:
    """
    Класс для работы с API аренды номеров OnlineSim.
    """

    BASE_URL = "https://onlinesim.ru/api/rent "

    def __init__(self):
        self.api_key = API_KEY_ONLINESIM

    async def get_tariffs(self, country: str = None, lang: str = "ru", user_id: int = None) -> dict:
        """
        Получение информации о доступных тарифах аренды номеров с фильтрацией по доступным странам.

        :param country: str - Код страны в формате E.164 (без "+"). Если не указан, данные возвращаются по всем странам.
        :param lang: str - Язык ответа. Возможные значения: "fr", "de", "ru", "en", "zh". По умолчанию: "ru".
        :param user_id: ID пользователя (для логгирования).
        :return: dict - Фильтрованный словарь с данными тарифов в формате {'код страны': {'дни': стоимость}}.
        :raises: Exception - В случае ошибки запроса.
        """
        try:
            logger.bind(user_id=user_id, action='get_tariffs').log(
                "USER_ACTION",
                f"Запрос тарифов: country={country}, lang={lang}"
            )

            params = {
                "apikey": self.api_key,
                "lang": lang
            }
            if country:
                params["country"] = country

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.BASE_URL}/tariffsRent.php", params=params) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.bind(user_id=user_id, action='get_tariffs').log(
                            "USER_ACTION",
                            f"Ошибка HTTP {response.status}: {error_text}"
                        )
                        raise Exception(f"Ошибка: {response.status}, {error_text}")

                    data = await response.json()

                    # Фильтрация данных
                    filtered_data = {
                        country_code: dict(sorted(info["days"].items(), key=lambda x: int(x[0])))
                        for country_code, info in data.items()
                        if info.get("enabled", False)
                    }

                    logger.bind(user_id=user_id, action='get_tariffs').log(
                        "USER_ACTION",
                        f"Получено {len(filtered_data)} стран с тарифами"
                    )

                    return filtered_data

        except aiohttp.ClientError as e:
            logger.opt(exception=True).error(f"Ошибка при выполнении запроса get_tariffs: {e}")
            raise Exception(f"Ошибка при выполнении запроса: {e}")

    async def rent_number(
            self,
            country: int,
            days: int,
            extension: bool = True,
            pagination: bool = False,
            lang: str = "ru",
            user_id: int = None
    ) -> dict:
        """
        Аренда номера для приема SMS.

        :param country: int - Код страны (в формате E.164, без "+").
        :param days: int - Начальный период аренды в днях.
        :param extension: bool - Автопродление аренды (по умолчанию: True).
        :param pagination: bool - Пагинация сообщений (по умолчанию: False).
        :param lang: str - Язык ответа (по умолчанию: "ru").
        :param user_id: ID пользователя (для логгирования).
        :return: dict - Данные об арендованном номере.
        :raises: Exception - В случае ошибки запроса.
        """
        try:
            logger.bind(user_id=user_id, action='rent_number').log(
                "USER_ACTION",
                f"Аренда номера: страна={country}, дни={days}, автопродление={extension}"
            )

            params = {
                "apikey": self.api_key,
                "country": country,
                "days": days,
                "extension": str(extension).lower(),
                "pagination": str(pagination).lower(),
                "lang": lang
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.BASE_URL}/getRentNum.php", params=params) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.bind(user_id=user_id, action='rent_number').log(
                            "USER_ACTION",
                            f"Ошибка HTTP {response.status}: {error_text}"
                        )
                        raise Exception(f"Ошибка: {response.status}, {error_text}")

                    data = await response.json()

                    if data.get("response") != 1:
                        logger.bind(user_id=user_id, action='rent_number').log(
                            "USER_ACTION",
                            f"Ошибка API: {data}"
                        )
                        raise Exception(f"Ошибка API: {data}")

                    logger.bind(user_id=user_id, action='rent_number').log(
                        "USER_ACTION",
                        f"Номер арендован: tzid={data['item'].get('tzid')}, номер={data['item'].get('number')}"
                    )

                    return data.get("item", {})

        except aiohttp.ClientError as e:
            logger.opt(exception=True).error(f"Ошибка при выполнении запроса rent_number: {e}")
            raise Exception(f"Ошибка при выполнении запроса: {e}")

    async def extend_rent_state(self, tzid: int, days: int, lang: str = "ru", user_id: int = None) -> dict:
        """
        Продление аренды номера на указанный период.

        :param tzid: int - ID операции аренды.
        :param days: int - Период продления аренды в днях.
        :param lang: str - Язык ответа (по умолчанию: "ru").
        :param user_id: ID пользователя (для логгирования).
        :return: dict - Данные о продлении аренды.
        :raises: Exception - В случае ошибки запроса.
        """
        try:
            logger.bind(user_id=user_id, action='extend_rent_state').log(
                "USER_ACTION",
                f"Продление аренды: tzid={tzid}, дни={days}"
            )

            params = {
                "apikey": self.api_key,
                "tzid": tzid,
                "days": days,
                "lang": lang
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.BASE_URL}/extendRentState.php", params=params) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.bind(user_id=user_id, action='extend_rent_state').log(
                            "USER_ACTION",
                            f"Ошибка HTTP {response.status}: {error_text}"
                        )
                        raise Exception(f"Ошибка: {response.status}, {error_text}")

                    data = await response.json()

                    if data.get("response") != 1:
                        logger.bind(user_id=user_id, action='extend_rent_state').log(
                            "USER_ACTION",
                            f"Ошибка API: {data}"
                        )
                        raise Exception(f"Ошибка API: {data}")

                    logger.bind(user_id=user_id, action='extend_rent_state').log(
                        "USER_ACTION",
                        f"Аренда продлена: tzid={tzid}, дни={days}"
                    )

                    return data.get("item", {})

        except aiohttp.ClientError as e:
            logger.opt(exception=True).error(f"Ошибка при выполнении запроса extend_rent_state: {e}")
            raise Exception(f"Ошибка при выполнении запроса: {e}")

    async def get_rent_state(self, tzid: int = None, pagination: bool = False, lang: str = "ru", user_id: int = None) -> dict:
        """
        Получение списка активных арендных номеров или информации о конкретной аренде.

        :param tzid: int - ID операции аренды. Если не указан, возвращается список всех активных аренд.
        :param pagination: bool - Включение пагинации для списка сообщений (по умолчанию: False).
        :param lang: str - Язык ответа (по умолчанию: "ru").
        :param user_id: ID пользователя (для логгирования).
        :return: dict - Список активных арендных номеров или данные конкретной аренды.
        :raises: Exception - В случае ошибки запроса.
        """
        try:
            logger.bind(user_id=user_id, action='get_rent_state').log(
                "USER_ACTION",
                f"Запрос состояния аренды: tzid={tzid}, пагинация={pagination}"
            )

            params = {
                "apikey": self.api_key,
                "pagination": str(pagination).lower(),
                "lang": lang
            }
            if tzid is not None:
                params["tzid"] = tzid

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.BASE_URL}/getRentState.php", params=params) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.bind(user_id=user_id, action='get_rent_state').log(
                            "USER_ACTION",
                            f"Ошибка HTTP {response.status}: {error_text}"
                        )
                        raise Exception(f"Ошибка: {response.status}, {error_text}")

                    data = await response.json()

                    if data.get("response") != 1:
                        logger.bind(user_id=user_id, action='get_rent_state').log(
                            "USER_ACTION",
                            f"Ошибка API: {data}"
                        )
                        raise Exception(f"Ошибка API: {data}")

                    result = data.get("list", {}) if tzid is None else data

                    logger.bind(user_id=user_id, action='get_rent_state').log(
                        "USER_ACTION",
                        f"Состояние аренды получено: {len(result)} записей"
                    )

                    return result

        except aiohttp.ClientError as e:
            logger.opt(exception=True).error(f"Ошибка при выполнении запроса get_rent_state: {e}")
            raise Exception(f"Ошибка при выполнении запроса: {e}")

    async def close_rent_num(self, tzid: int, lang: str = "ru", user_id: int = None) -> dict:
        """
        Закрытие аренды номера.

        :param tzid: int - ID операции аренды, которую нужно закрыть.
        :param lang: str - Язык ответа (по умолчанию: "ru").
        :param user_id: ID пользователя (для логгирования).
        :return: dict - Ответ API с подтверждением закрытия аренды.
        :raises: Exception - В случае ошибки запроса.
        """
        try:
            logger.bind(user_id=user_id, action='close_rent_num').log(
                "USER_ACTION",
                f"Запрос закрытия аренды: tzid={tzid}"
            )

            params = {
                "apikey": self.api_key,
                "tzid": tzid,
                "lang": lang
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.BASE_URL}/closeRentNum.php", params=params) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.bind(user_id=user_id, action='close_rent_num').log(
                            "USER_ACTION",
                            f"Ошибка HTTP {response.status}: {error_text}"
                        )
                        raise Exception(f"Ошибка: {response.status}, {error_text}")

                    data = await response.json()

                    if data.get("response") is not True:
                        logger.bind(user_id=user_id, action='close_rent_num').log(
                            "USER_ACTION",
                            f"Ошибка API: {data}"
                        )
                        raise Exception(f"Ошибка API: {data}")

                    logger.bind(user_id=user_id, action='close_rent_num').log(
                        "USER_ACTION",
                        f"Аренда успешно закрыта: tzid={tzid}"
                    )

                    return data

        except aiohttp.ClientError as e:
            logger.opt(exception=True).error(f"Ошибка при выполнении запроса close_rent_num: {e}")
            raise Exception(f"Ошибка при выполнении запроса: {e}")