import asyncio
from typing import Optional

from loguru import logger

from app.db.models import CountriesSmsFast, PriceSmsFast
from app.services.sms_fast.smsfast_client import get_smsfast_client

# Сколько запросов цен держим в полёте одновременно.
#
# Раньше 202 страны обходились строго по очереди, и при ответе API около 35с
# полный проход занимал порядка двух часов — задача работала непрерывно и
# успевала обновить лишь часть справочника. Частота запросов всё равно
# ограничена рейт-лимитером клиента (1 запрос в секунду), поэтому параллелизм
# не увеличивает нагрузку на API сверх неё, а только перестаёт ждать каждый
# ответ впустую.
SMSFAST_PRICES_CONCURRENCY = 10

async def update_smsfast_prices() -> None:
    """
    Обходит все страны из справочника SMSFast и обновляет цены в PriceSmsFast.

    Для каждой страны делает один запрос getPrices(country=<id>), который
    возвращает цены по всем сервисам. Сохраняет стоимость и количество номеров.

    Запросы идут параллельно (до SMSFAST_PRICES_CONCURRENCY в полёте), а ответы
    обрабатываются по мере готовности. Частота обращений к API при этом та же:
    её держит рейт-лимитер клиента.
    """
    smsfast_client = get_smsfast_client()

    # Получаем список ID стран из БД
    try:
        country_ids = await CountriesSmsFast.all().values_list("country_id", flat=True)
    except Exception as e:
        logger.opt(exception=e).error("Failed to load countries_smsfast from DB")
        return

    semaphore = asyncio.Semaphore(SMSFAST_PRICES_CONCURRENCY)

    async def fetch_prices(country_id):
        """
        Тянет цены одной страны.

        Ошибку наружу не пускает: сбой по одной стране не должен ронять
        весь проход — поведение то же, что было у continue в цикле.
        """
        async with semaphore:
            try:
                # API: возвращает словарь вида {"<country_id>": {"<service_code>": {"price": "...", "count": "..."} } }
                return country_id, await smsfast_client.get_prices(country=country_id)
            except Exception as e:
                logger.opt(exception=e).error(f"SMSFast: getPrices failed for country {country_id}")
                return country_id, None

    tasks = [asyncio.create_task(fetch_prices(country_id)) for country_id in country_ids]

    try:
        # Обрабатываем по мере готовности: запись в БД идёт параллельно с
        # загрузкой остальных стран, и все ответы не копятся в памяти разом.
        for finished in asyncio.as_completed(tasks):
            country_id, prices_raw = await finished

            if not isinstance(prices_raw, dict):
                continue

            services_payload = prices_raw.get(str(country_id))
            if not isinstance(services_payload, dict):
                continue

            rows = []
            for service_code, payload in services_payload.items():
                # payload может содержать 'price' или 'cost'
                price_raw = None
                count_raw = None

                if isinstance(payload, dict):
                    price_raw = payload.get("price") or payload.get("cost")
                    count_raw = payload.get("count") or payload.get("totalCount") or payload.get("numbers")

                if price_raw is None:
                    # Нет цены — пропускаем
                    continue

                try:
                    price_value = float(price_raw)
                except (TypeError, ValueError):
                    continue

                count_value: Optional[int] = None
                if count_raw is not None:
                    try:
                        count_value = int(count_raw)
                    except (TypeError, ValueError):
                        count_value = None

                rows.append(PriceSmsFast(
                    country=country_id,
                    service_code=service_code,
                    price=price_value,
                    count=count_value,
                ))

            if rows:
                # Пачкой, а не по одной записи: БД стоит на отдельном сервере
                # (RTT около 40мс), а поштучный add_or_update_price делал на
                # каждый сервис два запроса — SELECT и UPDATE. При сотне сервисов
                # на страну это давало больше восьми секунд сетевых ожиданий, и
                # именно запись, а не запросы к API, растягивала проход на часы.
                # on_conflict опирается на уникальный индекс (country, service_code).
                await PriceSmsFast.bulk_create(
                    rows,
                    update_fields=["price", "count"],
                    on_conflict=["country", "service_code"],
                )

            logger.info(f"SMSFast prices loaded for country {country_id}")

    finally:
        # Если проход оборвали (например, по потолку времени в guard_job),
        # незавершённые запросы не должны продолжать жить в фоне.
        for task in tasks:
            if not task.done():
                task.cancel()
