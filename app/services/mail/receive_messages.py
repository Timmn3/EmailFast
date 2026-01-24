import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import aiohttp
from mailtm import Email


# --- Настройки лимитов (можно переопределять через env) ---
MAILTM_MIN_INTERVAL_SEC = float(os.getenv("MAILTM_MIN_INTERVAL_SEC", "0.35"))  # пауза между запросами
MAILTM_MAX_CONCURRENCY = int(os.getenv("MAILTM_MAX_CONCURRENCY", "2"))  # параллельные запросы
MAILTM_MAX_RETRIES = int(os.getenv("MAILTM_MAX_RETRIES", "5"))  # ретраи при 429/временных ошибках
MAILTM_BACKOFF_BASE_SEC = float(os.getenv("MAILTM_BACKOFF_BASE_SEC", "1.5"))  # базовый backoff


# --- Глобальные примитивы для ограничения запросов ---
_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()

_rate_lock = asyncio.Lock()
_last_request_ts: float = 0.0

_sem = asyncio.Semaphore(MAILTM_MAX_CONCURRENCY)


async def _get_session() -> aiohttp.ClientSession:
    """
    Возвращает общий aiohttp.ClientSession для mail.tm,
    чтобы не создавать новый на каждый запрос.
    """
    global _session

    if _session and not _session.closed:
        return _session

    async with _session_lock:
        if _session and not _session.closed:
            return _session

        connector = aiohttp.TCPConnector(
            limit=MAILTM_MAX_CONCURRENCY,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        timeout = aiohttp.ClientTimeout(total=30)
        _session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return _session


async def _throttle() -> None:
    """
    Глобальный троттлинг: гарантируем минимальный интервал между запросами к mail.tm.
    """
    global _last_request_ts

    async with _rate_lock:
        now = time.monotonic()
        delta = now - _last_request_ts
        if delta < MAILTM_MIN_INTERVAL_SEC:
            await asyncio.sleep(MAILTM_MIN_INTERVAL_SEC - delta)
        _last_request_ts = time.monotonic()


def _retry_after_seconds(headers: aiohttp.typedefs.LooseHeaders) -> Optional[float]:
    """
    Достаём Retry-After (секунды), если сервер его прислал.
    """
    try:
        ra = None
        if isinstance(headers, dict):
            ra = headers.get("Retry-After") or headers.get("retry-after")
        if not ra:
            return None
        return float(ra)
    except Exception:
        return None


async def _request_json(
    method: str,
    url: str,
    headers: Dict[str, str],
    json_data: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Универсальный запрос к mail.tm с:
    - семафором параллельности
    - троттлингом
    - retry/backoff на 429 и временные ошибки
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAILTM_MAX_RETRIES + 1):
        try:
            async with _sem:
                await _throttle()
                session = await _get_session()

                async with session.request(method, url, headers=headers, json=json_data) as response:
                    if response.status == 429:
                        retry_after = _retry_after_seconds(response.headers)
                        wait_s = retry_after if retry_after is not None else (MAILTM_BACKOFF_BASE_SEC * attempt)
                        await asyncio.sleep(wait_s)
                        continue

                    response.raise_for_status()
                    return await response.json()

        except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
            # временные сетевые ошибки — ретраим
            last_exc = e
            await asyncio.sleep(MAILTM_BACKOFF_BASE_SEC * attempt)
            continue

        except aiohttp.ClientResponseError as e:
            # 5xx можно ретраить, остальное — пробрасываем
            last_exc = e
            if 500 <= (e.status or 0) <= 599:
                await asyncio.sleep(MAILTM_BACKOFF_BASE_SEC * attempt)
                continue
            raise

        except Exception as e:
            last_exc = e
            raise

    # если ретраи закончились — отдаём последнюю ошибку (сохранит .status если это ClientResponseError)
    if last_exc:
        raise last_exc
    raise RuntimeError("mail.tm request failed without exception (unexpected)")


async def fetch_messages(mail_token: str) -> List[Dict[str, Any]]:
    """
    Получить все сообщения для существующего токена.
    """
    email = Email()
    email.token = mail_token

    url = "https://api.mail.tm/messages"
    headers = {
        "Authorization": f"Bearer {email.token}",
        "Content-Type": "application/json",
    }

    data = await _request_json("GET", url, headers=headers)
    return data.get("hydra:member", []) if isinstance(data, dict) else []


async def fetch_full_message(mail_token: str, message_id: str) -> str:
    """
    Получить полное сообщение по его ID.
    """
    url = f"https://api.mail.tm/messages/{message_id}"
    headers = {
        "Authorization": f"Bearer {mail_token}",
        "Content-Type": "application/json",
    }

    data = await _request_json("GET", url, headers=headers)
    if isinstance(data, dict):
        return data.get("text", "Нет текста в сообщении.")
    return "Нет текста в сообщении."


async def mark_as_read(mail_token: str, message_id: str) -> Dict[str, Any]:
    """
    Отметить письмо как прочитанное.
    """
    url = f"https://api.mail.tm/messages/{message_id}"
    headers = {
        "Authorization": f"Bearer {mail_token}",
        "Content-Type": "application/merge-patch+json",
    }
    payload = {"seen": True}

    data = await _request_json("PATCH", url, headers=headers, json_data=payload)
    return data if isinstance(data, dict) else {}


async def get_unread_messages(mail_token: str) -> List[Dict[str, str]]:
    """
    Возвращает список информации о непрочитанных сообщениях для заданного токена.

    :param mail_token: Токен для доступа к почте.
    :return: Список словарей с информацией о непрочитанных сообщениях.
    """
    messages = await fetch_messages(mail_token)
    unread_messages: List[Dict[str, str]] = []

    for msg in messages or []:
        try:
            if msg.get("seen"):
                continue

            msg_id = msg.get("id")
            if not msg_id:
                continue

            unread_messages.append(
                {
                    "id": msg_id,
                    "from": f"{msg.get('from', {}).get('name')} ({msg.get('from', {}).get('address')})",
                    "subject": msg.get("subject", ""),
                    "content": msg.get("intro", ""),
                }
            )

            # Важно: mark_as_read тоже проходит через limiter/retry,
            # иначе можно быстро словить 429 при пачке писем.
            await mark_as_read(mail_token, msg_id)

        except Exception:
            # Не даём одному письму сломать обработку остальных
            continue

    return unread_messages
