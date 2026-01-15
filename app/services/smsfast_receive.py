import asyncio
import json
from typing import Any, Dict, Optional

from loguru import logger

from app import dependencies
from app.services.sms_activate_async import SMSActivateAPIAsync


class RateLimiter:
    """
    Простой rate-limit: гарантирует не более N запросов в секунду (на один процесс).
    """

    def __init__(self, requests_per_second: float = 1.0) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second должен быть > 0")
        self._interval = 1.0 / requests_per_second
        self._lock = asyncio.Lock()
        self._next_ts = 0.0

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            if now < self._next_ts:
                await asyncio.sleep(self._next_ts - now)
                now = loop.time()
            self._next_ts = now + self._interval


class SMSFastAPIAsync(SMSActivateAPIAsync):
    """
    SMSFast очень похож на SMS-Activate по схеме handler_api.php?action=...
    Поэтому переиспользуем существующий клиент и просто:
    - меняем base url
    - добавляем rate-limit на каждый запрос
    """

    def __init__(self, api_key: str, requests_per_second: float = 1.0) -> None:
        super().__init__(api_key=api_key)

        # Важно: в родителе url хранится как self.__api_url (name-mangling),
        # поэтому меняем через _SMSActivateAPIAsync__api_url
        self._SMSActivateAPIAsync__api_url = dependencies.SMSFAST_API_URL  # noqa: SLF001

        self._limiter = RateLimiter(requests_per_second=requests_per_second)
        self.debug_mode = False

    async def get_request(self, url, params):  # type: ignore[override]
        await self._limiter.acquire()
        return await super().get_request(url, params)


class SmsFastReceive:
    """
    Обёртка под нужды бота: баланс, страны, статусы, повторный запрос SMS.
    """

    def __init__(self, requests_per_second: float = 1.0) -> None:
        self.sa = SMSFastAPIAsync(
            api_key=dependencies.SMSFAST_API_KEY,
            requests_per_second=requests_per_second,
        )

    async def get_balance(self) -> Dict[str, Any]:
        return await self.sa.getBalance()

    async def get_countries_raw(self) -> Dict[str, Any]:
        """
        Возвращает raw-JSON стран от провайдера.
        """
        return await self.sa.getCountries()

    async def get_numbers_status(self, country: Optional[int] = None) -> Dict[str, Any]:
        """
        Возвращает доступность номеров по сервисам (как отдаёт провайдер).
        """
        return await self.sa.getNumbersStatus(country=country)

    async def get_prices(self, service: Optional[str] = None, country: Optional[int] = None) -> Dict[str, Any]:
        """
        Возвращает цены (как отдаёт провайдер).
        """
        return await self.sa.getPrices(service=service, country=country)

    async def buy_number(self, *, service: str, country: int, max_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Пытается купить номер. Важно: это РЕАЛЬНАЯ покупка, расходует баланс.
        """
        max_price_str = None
        if max_price is not None:
            max_price_str = str(max_price)

        result = await self.sa.getNumberV2(
            service=service,
            country=country,
            maxPrice=max_price_str,
        )
        return result

    async def get_status(self, activation_id: int) -> Any:
        """
        В sms-activate-подобных API getStatus обычно возвращает строку STATUS_...
        """
        return await self.sa.getStatus(id=activation_id)

    async def request_additional_sms(self, activation_id: int) -> Any:
        """
        Повторный запрос SMS.
        В большинстве sms-activate-подобных API это setStatus(status=3).
        """
        return await self.sa.setStatus(id=activation_id, status=3)
