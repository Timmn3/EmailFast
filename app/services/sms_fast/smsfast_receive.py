import asyncio
import json
from typing import Any, Dict, Optional, Union

import aiohttp

from app import dependencies


class RateLimiter:
    """
    Простой rate-limit: гарантирует не более N запросов в секунду (на один процесс).
    """

    def __init__(self, requests_per_second: float = 1.0) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second должен быть > 0")
        self._interval = 1.0 / float(requests_per_second)
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


class SmsFastReceive:
    """
    Обёртка под нужды бота: баланс, статусы, цены, покупка номера, отмена/завершение.

    Документация (Activation API / handler_api.php):
    https://smsfast.guru/doc#h-activation-0
    """

    def __init__(
        self,
        requests_per_second: float = 1.0,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        timeout_total: float = 25.0,
    ) -> None:
        self._api_key = api_key or dependencies.SMSFAST_API_KEY
        self._api_url = api_url or dependencies.SMSFAST_API_URL
        self._timeout_total = float(timeout_total)

        rps = float(requests_per_second or 1.0)
        self._limiter = RateLimiter(requests_per_second=rps)

        # Один session на инстанс (меньше накладных расходов + keep-alive)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout_total)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "SmsFastReceive":
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    @staticmethod
    def _looks_like_error(text: str) -> bool:
        # Типичные строковые ошибки sms-activate-подобных API
        prefixes = (
            "BAD_",
            "NO_",
            "ERROR_",
            "WRONG_",
            "INVALID_",
        )
        return text.startswith(prefixes)

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        # Не доверяем Content-Type: у провайдера он может быть text/html даже для JSON/ACCESS_*
        # Считаем HTML только если реально похоже на HTML по телу.
        t = text.lstrip().lower()
        return t.startswith("<!doctype html") or t.startswith("<html") or t.startswith("<")

    async def _call(self, *, action: str, **params: Any) -> Union[str, Dict[str, Any]]:
        """
        Универсальный вызов handler_api.php.

        Возвращает:
        - dict, если провайдер вернул JSON (или если это явная ошибка HTTP/HTML)
        - str, если провайдер вернул текст (ACCESS_*, STATUS_*, либо код ошибки)
        """
        await self._limiter.acquire()

        req_params: Dict[str, Any] = {"api_key": self._api_key, "action": action}
        for k, v in params.items():
            if v is None:
                continue
            req_params[k] = v

        session = await self._get_session()
        async with session.get(self._api_url, params=req_params) as resp:
            text = (await resp.text()).strip()
            http_status = resp.status

        # 1) HTTP-level ошибка
        if http_status != 200:
            return {
                "error": "HTTP_ERROR",
                "http_status": http_status,
                "action": action,
                "raw": text,
            }

        # 2) СНАЧАЛА пробуем распарсить JSON (даже если Content-Type кривой)
        parsed = self._try_parse_json(text)
        if parsed is not None:
            if isinstance(parsed, list):
                return {"data": parsed, "raw": text}
            if isinstance(parsed, dict):
                parsed.setdefault("raw", text)
                return parsed

        # 3) Затем — "текстовые" ответы протокола (ACCESS_*, STATUS_*, BAD_*/NO_* и т.д.)
        # Их нельзя ошибочно помечать как HTML только из-за заголовка Content-Type.
        t = text.strip()
        if t.startswith(("ACCESS_", "STATUS_")) or self._looks_like_error(t):
            return t

        # 4) Только теперь считаем это HTML (реально по телу)
        if self._looks_like_html(t):
            return {
                "error": "HTML_RESPONSE",
                "http_status": http_status,
                "action": action,
                "raw": text,
            }

        # 5) Неизвестный текст — отдаём как есть
        return t

    @staticmethod
    def _try_parse_json(text: str) -> Optional[Union[Dict[str, Any], list]]:
        t = text.strip()
        if not (t.startswith("{") or t.startswith("[")):
            return None
        try:
            return json.loads(t)
        except Exception:
            return None

    # ---------- Публичные методы ----------

    async def get_balance(self) -> Dict[str, Any]:
        """
        getBalance -> обычно текст вида: ACCESS_BALANCE:123.45
        """
        raw = await self._call(action="getBalance")
        if isinstance(raw, dict):
            # В т.ч. ошибки HTTP/HTML
            if raw.get("error"):
                return raw
            return raw

        text = str(raw).strip()
        if text.startswith("ACCESS_BALANCE:"):
            _, val = text.split(":", 1)
            try:
                balance = float(val)
            except Exception:
                balance = None
            return {"balance": balance, "raw": text}

        return {"error": text, "raw": text}

    async def get_countries_raw(self) -> Dict[str, Any]:
        """
        ВАЖНО: на твоём endpoint getCountries возвращает BAD_ACTION,
        поэтому метод оставлен только для диагностики.
        """
        raw = await self._call(action="getCountries")
        if isinstance(raw, dict):
            return raw

        text = str(raw).strip()
        if text == "BAD_ACTION":
            return {"error": "NOT_SUPPORTED", "raw": text}

        return {"raw": text}

    async def get_numbers_status(self, country: Optional[int] = None) -> Dict[str, Any]:
        """
        getNumbersStatus -> JSON доступности номеров по сервисам.
        """
        raw = await self._call(action="getNumbersStatus", country=country)
        if isinstance(raw, dict):
            return raw

        text = str(raw).strip()
        return {"error": text, "raw": text} if self._looks_like_error(text) else {"raw": text}

    async def get_prices(self, service: Optional[str] = None, country: Optional[int] = None) -> Dict[str, Any]:
        """
        getPrices -> JSON цен.
        """
        raw = await self._call(action="getPrices", service=service, country=country)
        if isinstance(raw, dict):
            return raw

        text = str(raw).strip()
        return {"error": text, "raw": text} if self._looks_like_error(text) else {"raw": text}

    async def buy_number(self, *, service: str, country: int, max_price: Optional[float] = None) -> Dict[str, Any]:
        """
        getNumber -> покупка номера (РЕАЛЬНО списывает баланс).

        Успех: ACCESS_NUMBER:ID:NUMBER
        Ошибка: строка-код (NO_NUMBERS / NO_BALANCE / BAD_KEY / BAD_ACTION / BAD_SERVICE ...)
        """
        params: Dict[str, Any] = {
            "service": service,
            "country": int(country),
        }
        if max_price is not None:
            params["maxPrice"] = str(max_price)

        raw = await self._call(action="getNumber", **params)
        if isinstance(raw, dict):
            # HTTP/HTML или JSON-ошибка
            if raw.get("error"):
                return raw
            if raw.get("error"):
                return {"error": raw.get("error"), **raw}
            return raw

        text = str(raw).strip()
        if text.startswith("ACCESS_NUMBER:"):
            try:
                _, act_id, number = text.split(":", 2)
                act_id_int = int(act_id)
                return {
                    "id": act_id_int,
                    "activation_id": act_id_int,
                    "number": number,
                    "phone_number": number,
                    "raw": text,
                }
            except Exception:
                return {"error": "BAD_RESPONSE", "message": "Не удалось распарсить ACCESS_NUMBER", "raw": text}

        return {"error": text, "raw": text}

    async def get_status(self, activation_id: int) -> str:
        """
        getStatus -> строка STATUS_* или STATUS_OK:код / STATUS_OK:код:текст и т.п.
        """
        raw = await self._call(action="getStatus", id=int(activation_id))
        if isinstance(raw, dict):
            # В т.ч. HTTP/HTML — вернём raw как строку, чтобы не ломать вызывающий код
            return str(raw.get("raw") or raw)
        return str(raw)

    async def get_sms(self, activation_id: int) -> str:
        """
        Совместимость с текущим receive_sms_handler.py (там уже зовётся get_sms()).
        """
        return await self.get_status(activation_id)

    async def request_additional_sms(self, activation_id: int) -> str:
        """
        setStatus(status=3) -> повторный запрос SMS.

        По твоим тестам SMSFast отвечает BAD_STATUS — значит опция не поддерживается/не разрешена.
        """
        raw = await self._call(action="setStatus", id=int(activation_id), status=3)
        if isinstance(raw, dict):
            return str(raw.get("raw") or raw)
        return str(raw)

    async def cancel_activation(self, activation_id: int) -> str:
        """
        setStatus(status=8) -> отмена активации.
        """
        raw = await self._call(action="setStatus", id=int(activation_id), status=8)
        if isinstance(raw, dict):
            return str(raw.get("raw") or raw)
        return str(raw)

    async def finish_activation(self, activation_id: int) -> str:
        """
        setStatus(status=6) -> завершение активации.
        """
        raw = await self._call(action="setStatus", id=int(activation_id), status=6)
        if isinstance(raw, dict):
            return str(raw.get("raw") or raw)
        return str(raw)
