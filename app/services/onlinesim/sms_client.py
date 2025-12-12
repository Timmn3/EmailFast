import inspect

from pyonlinesim import OnlineSMS as _OnlineSMS

from app.services.onlinesim.rate_limiter import ONLINESIM_RATE_LIMITER


class OnlineSMS:
    """
    Обертка над pyonlinesim.OnlineSMS, чтобы ограничить вызовы API OnlineSim до 2 req/sec.
    """

    def __init__(self, api_key: str):
        self._client = _OnlineSMS(api_key=api_key)

    def __getattr__(self, name: str):
        attr = getattr(self._client, name)

        # Если это async-метод — ставим rate limit перед вызовом
        if callable(attr) and inspect.iscoroutinefunction(attr):
            async def _wrapped(*args, **kwargs):
                await ONLINESIM_RATE_LIMITER.acquire()
                return await attr(*args, **kwargs)

            return _wrapped

        return attr
