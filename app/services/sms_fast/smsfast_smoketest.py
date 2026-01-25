import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.sms_fast.smsfast_receive import SmsFastReceive

# Топ-страны и сервисы берем с сайта документации (как "популярные" примеры):
# страны: https://smsfast.guru/doc/countries  (вверху часто US/UK/TR/PH/TH/KZ/DE/ZA/HK/CO)
# сервисы: https://smsfast.guru/doc/services (tg/wa/vk/go и т.д.)
TOP_COUNTRIES_FALLBACK: List[int] = [187, 16, 62, 4, 52, 2, 43, 31, 14, 33]
TOP_SERVICES_FALLBACK: List[str] = ["tg", "wa", "go", "vk", "fb"]


@dataclass(frozen=True)
class Candidate:
    service: str
    country: int
    count: int


def _pp(obj: Any, pretty: bool) -> str:
    if pretty:
        return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(obj, ensure_ascii=False)


def _title(s: str) -> None:
    print("\n" + ("=" * 80))
    print(s)
    print(("=" * 80))


def _as_int(x: Any) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def _parse_numbers_status(data: Dict[str, Any]) -> List[Candidate]:
    """
    Нормализуем getNumbersStatus к списку (service, country, count).

    Поддерживаем 2 распространённых формата:
    1) {"tg_43": 12, "wa_187": 3, ...}  -> service_country
    2) {"tg": 12, "wa": 3, ...}        -> если API уже отфильтрован по country
    """
    out: List[Candidate] = []

    for k, v in data.items():
        if k in ("raw", "error"):
            continue

        cnt = _as_int(v)
        if cnt is None:
            continue

        sk = str(k)
        if "_" in sk:
            # tg_43
            service, tail = sk.rsplit("_", 1)
            c = _as_int(tail)
            if c is None:
                continue
            out.append(Candidate(service=service, country=c, count=cnt))
        else:
            # tg (страна неизвестна — обозначим как -1)
            out.append(Candidate(service=sk, country=-1, count=cnt))

    return out


def _pick_best(
    candidates: List[Candidate],
    preferred_services: Iterable[str],
    preferred_countries: Iterable[int],
) -> Optional[Candidate]:
    pref_services = list(preferred_services)
    pref_countries = list(preferred_countries)

    # 1) сначала ищем среди preferred_services + preferred_countries
    for svc in pref_services:
        best: Optional[Candidate] = None
        for c in candidates:
            if c.count <= 0:
                continue
            if c.service != svc:
                continue
            if c.country in pref_countries:
                if best is None or c.count > best.count:
                    best = c
        if best is not None:
            return best

    # 2) затем ищем просто по preferred_services (страна любая)
    for svc in pref_services:
        best = None
        for c in candidates:
            if c.count <= 0:
                continue
            if c.service != svc:
                continue
            if best is None or c.count > best.count:
                best = c
        if best is not None and best.country != -1:
            return best

    # 3) затем вообще любой с count>0
    best = None
    for c in candidates:
        if c.count <= 0:
            continue
        if best is None or c.count > best.count:
            best = c
    if best is not None and best.country != -1:
        return best

    return None


async def _probe_actions(cli: SmsFastReceive, pretty: bool) -> None:
    """
    Проверяем, какие action вообще принимаются (без покупки).
    Логика: если ответ != BAD_ACTION, значит action существует (даже если не хватает параметров).
    """
    _title("🧪 Probe actions (SUPPORTED vs BAD_ACTION)")

    actions = [
        ("getBalance", {}),
        ("getNumbersStatus", {}),
        ("getPrices", {}),
        ("getNumber", {}),          # тут ожидаем BAD_SERVICE/NO_NUMBERS и т.п. но не BAD_ACTION
        ("getStatus", {}),          # ожидаем NO_ACTIVATION/NO_ID/и т.п.
        ("setStatus", {}),          # ожидаем BAD_STATUS/NO_ID/и т.п.
        ("getCountries", {}),       # у тебя сейчас BAD_ACTION (ожидаемо)
        ("getServices", {}),
        ("getOperators", {}),
        ("getFullSms", {}),
        ("getAdditionalService", {}),
    ]

    call = getattr(cli, "_call")  # приватно, но для smoke-test ок
    results = {}
    for action, params in actions:
        try:
            res = await call(action=action, **params)
        except Exception as e:
            res = {"error": f"EXCEPTION: {e}"}
        supported = "SUPPORTED" if str(res) != "BAD_ACTION" and not (isinstance(res, dict) and res.get("raw") == "BAD_ACTION") else "BAD_ACTION"
        results[action] = {"status": supported, "response": res}

    print(_pp(results, pretty=pretty))


async def _find_working_pair(cli: SmsFastReceive, pretty: bool) -> Tuple[int, str]:
    """
    Пытаемся найти рабочую пару country+service.
    """
    _title("🔎 Поиск country+service (по availability / fallback)")

    # 1) пробуем общий getNumbersStatus()
    raw = await cli.get_numbers_status(country=None)
    print("getNumbersStatus():")
    print(_pp(raw, pretty=pretty))

    candidates = _parse_numbers_status(raw if isinstance(raw, dict) else {})
    best = _pick_best(
        candidates=candidates,
        preferred_services=TOP_SERVICES_FALLBACK,
        preferred_countries=TOP_COUNTRIES_FALLBACK,
    )

    if best is not None:
        print(f"\n✅ Выбрано из availability: country={best.country}, service={best.service}, count={best.count}")
        return best.country, best.service

    # 2) если не получилось — перебор стран из fallback, и смотрим getNumbersStatus(country=...)
    for country in TOP_COUNTRIES_FALLBACK:
        raw2 = await cli.get_numbers_status(country=country)
        candidates2 = _parse_numbers_status(raw2 if isinstance(raw2, dict) else {})

        # тут формат может стать {"tg": 5, ...} -> country будет -1, поэтому задаём country вручную
        for svc in TOP_SERVICES_FALLBACK:
            for c in candidates2:
                if c.service == svc and c.count > 0:
                    print(f"\n✅ Выбрано из getNumbersStatus(country={country}): country={country}, service={svc}, count={c.count}")
                    return country, svc

        print(f"ℹ️ country={country}: не нашли count>0 для {TOP_SERVICES_FALLBACK}")

    # 3) если вообще нигде count>0 — берём дефолт (tg + первая страна) и будем пытаться купить
    fallback_country = TOP_COUNTRIES_FALLBACK[0]
    fallback_service = TOP_SERVICES_FALLBACK[0]
    print(f"\n⚠️ Не нашли availability >0. Fallback: country={fallback_country}, service={fallback_service}")
    return fallback_country, fallback_service


async def _try_buy(cli: SmsFastReceive, country: int, service: str, pretty: bool) -> Dict[str, Any]:
    _title("📌 getPrices(country, service)")
    prices = await cli.get_prices(country=country, service=service)
    print(_pp(prices, pretty=pretty))

    _title("📲 getNumber (покупка номера)")
    res = await cli.buy_number(service=service, country=country)
    print(_pp(res, pretty=pretty))
    return res


async def _poll_status(cli: SmsFastReceive, activation_id: int, pretty: bool, polls: int, delay: float) -> str:
    last = ""
    for i in range(1, polls + 1):
        _title(f"⏳ getStatus poll {i}/{polls} (sleep={delay}s)")
        st = await cli.get_status(activation_id)
        last = st
        print(st)
        await asyncio.sleep(delay)
        # если код пришёл — дальше не мучаем
        if st.startswith("STATUS_OK"):
            break
    return last


async def _lifecycle(cli: SmsFastReceive, activation_id: int, pretty: bool) -> None:
    # подождём чуть-чуть, посмотрим статусы
    await _poll_status(cli, activation_id, pretty=pretty, polls=5, delay=2.0)

    _title("🔁 setStatus(3) -> запрос доп. SMS")
    r3 = await cli.request_additional_sms(activation_id)
    print(r3)

    # по опыту у некоторых совместимых API есть ранний запрет на отмену (например первые 2 минуты)
    _title("🛑 setStatus(8) -> cancel (попытка 1)")
    r8 = await cli.cancel_activation(activation_id)
    print(r8)

    if "EARLY_CANCEL_DENIED" in r8:
        _title("⏱️ EARLY_CANCEL_DENIED -> ждём 125 секунд и пробуем отмену ещё раз")
        await asyncio.sleep(125)
        _title("🛑 setStatus(8) -> cancel (попытка 2)")
        r8b = await cli.cancel_activation(activation_id)
        print(r8b)

    _title("✅ getStatus (финальный)")
    st_final = await cli.get_status(activation_id)
    print(st_final)

    # если отмена не прошла — попробуем завершить (status=6), чтобы не оставлять хвост
    if not ("STATUS_CANCEL" in st_final or "ACCESS_CANCEL" in st_final):
        _title("🏁 setStatus(6) -> finish (на всякий случай)")
        r6 = await cli.finish_activation(activation_id)
        print(r6)
        _title("✅ getStatus (после finish)")
        print(await cli.get_status(activation_id))


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="SMSFast smoke-test (без аргументов = полный прогон, включая покупку номера) 🧪",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--pretty", action="store_true", help="Красивый JSON вывод.")
    parser.add_argument("--api-key", type=str, default=None, help="Переопределить API ключ (иначе берётся из dependencies).")
    parser.add_argument("--api-url", type=str, default=None, help="Переопределить API URL (иначе берётся из dependencies).")
    parser.add_argument("--rps", type=float, default=1.0, help="Лимит запросов в секунду.")
    parser.add_argument("--timeout", type=float, default=25.0, help="HTTP timeout total.")

    # Чтобы всё же можно было запустить безопасно, если нужно
    parser.add_argument("--no-buy", action="store_true", help="Не покупать номер (только безопасные проверки).")

    args = parser.parse_args()

    try:
        async with SmsFastReceive(
            requests_per_second=args.rps,
            api_key=args.api_key,
            api_url=args.api_url,
            timeout_total=args.timeout,
        ) as cli:
            await _probe_actions(cli, pretty=args.pretty)

            _title("✅ getBalance")
            balance = await cli.get_balance()
            print(_pp(balance, pretty=args.pretty))

            if args.no_buy:
                _title("🧊 no-buy режим: заканчиваем после безопасных проверок")
                raw = await cli.get_numbers_status(country=None)
                print(_pp(raw, pretty=args.pretty))
                return 0

            country, service = await _find_working_pair(cli, pretty=args.pretty)

            buy_res = await _try_buy(cli, country=country, service=service, pretty=args.pretty)

            if isinstance(buy_res, dict) and buy_res.get("error"):
                _title("❌ Покупка не удалась")
                return 2

            activation_id = buy_res.get("activation_id") or buy_res.get("id")
            if not activation_id:
                _title("❌ Не нашли activation_id в ответе покупки")
                return 2

            await _lifecycle(cli, int(activation_id), pretty=args.pretty)
            return 0

    except KeyboardInterrupt:
        print("\n⛔ Прервано пользователем")
        return 130
    except Exception as e:
        print(f"\n❌ Smoke-test упал: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
