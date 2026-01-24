"""Единая точка доступа к клиенту SMSFast.

Зачем нужен singleton:
- В проекте есть polling (periodic_tasks.check_sms) и ручные действия (handler). Если
  в каждом месте создавать новый SmsFastReceive, то локальный RateLimiter будет
  сбрасываться и реального лимита запросов не получится.

Клиент создаётся один раз на процесс и переиспользуется во всех местах.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from app.dependencies import SMSFAST_API_KEY, SMSFAST_API_URL

try:
    from app.dependencies import SMSFAST_RPS  # type: ignore
except Exception:  # pragma: no cover
    SMSFAST_RPS = 1.0

from app.services.smsfast_receive import SmsFastReceive

_smsfast_client: Optional[SmsFastReceive] = None


def get_smsfast_client() -> SmsFastReceive:
    """Возвращает singleton-клиент SMSFast."""
    global _smsfast_client

    if _smsfast_client is None:
        logger.info(f"Init SMSFast client: url={SMSFAST_API_URL}, rps={SMSFAST_RPS}")
        _smsfast_client = SmsFastReceive(
            api_key=SMSFAST_API_KEY,
            api_url=SMSFAST_API_URL,
            requests_per_second=float(SMSFAST_RPS or 1.0),
        )
    return _smsfast_client
