import asyncio
import time


class RateLimiter:
    """
    Простой rate limiter по принципу "не чаще N запросов в секунду" (глобально на процесс).

    Важно:
    - Ограничение действует в рамках одного процесса.
    - Если у тебя несколько процессов/воркеров — лимит будет на каждый процесс отдельно.
    """

    def __init__(self, requests_per_second: float):
        if requests_per_second <= 0:
            raise ValueError("requests_per_second должен быть > 0")

        self._min_interval = 1.0 / float(requests_per_second)
        self._lock = asyncio.Lock()
        self._next_allowed_ts = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_s = self._next_allowed_ts - now
            if wait_s > 0:
                await asyncio.sleep(wait_s)
                now = time.monotonic()

            self._next_allowed_ts = now + self._min_interval


# OnlineSim: 2 запроса в секунду => 0.5 сек между запросами
ONLINESIM_RATE_LIMITER = RateLimiter(requests_per_second=2)
