from typing import Optional

from loguru import logger

from app.db.models import CountriesSmsFast, PriceSmsFast
from app.services.sms_fast.smsfast_client import get_smsfast_client

async def update_smsfast_prices() -> None:
    """
    Обходит все страны из справочника SMSFast и обновляет цены в PriceSmsFast.

    Для каждой страны делает один запрос getPrices(country=<id>),
    который возвращает цены по всем сервисам. Сохраняет стоимость и количество номеров.
    """
    smsfast_client = get_smsfast_client()

    # Получаем список ID стран из БД
    try:
        country_ids = await CountriesSmsFast.all().values_list("country_id", flat=True)
    except Exception as e:
        logger.opt(exception=e).error("Failed to load countries_smsfast from DB")
        return

    for country_id in country_ids:
        try:
            # API: возвращает словарь вида {"<country_id>": {"<service_code>": {"price": "...", "count": "..."} } }
            prices_raw = await smsfast_client.get_prices(country=country_id)
        except Exception as e:
            logger.opt(exception=e).error(f"SMSFast: getPrices failed for country {country_id}")
            continue

        if not isinstance(prices_raw, dict):
            continue

        services_payload = prices_raw.get(str(country_id))
        if not isinstance(services_payload, dict):
            continue

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

            # Обновляем или добавляем запись в таблицу PriceSmsFast
            await PriceSmsFast.add_or_update_price(
                country=country_id,
                service_code=service_code,
                price=price_value,
                count=count_value
            )

        logger.info(f"SMSFast prices loaded for country {country_id}")
