from tortoise.exceptions import DoesNotExist
from app.db.models import CountriesOnlinesim, PriceOnlinesim
from loguru import logger

from app.services.bot_texts import SERVICE_ONLINESIM
from app.services.onlinesim.get_tariffs import fetch_tariffs


async def insert_services(country_id: int, services):
    try:
        # Получаем экземпляр CountryOnlinesim по country_id
        await CountriesOnlinesim.get(country_id=country_id)
    except DoesNotExist:
        return  # Прекращаем выполнение, если страна не найдена

    for service in services:
        # Обновляем данные сервиса или создаем новый, если он не существует
        updated_count = await PriceOnlinesim.filter(
            service_name=service["service"],
            country=country_id
        ).update(
            price=float(service["price"]),
            slug=service["slug"]
        )

        # Проверяем, был ли обновлён существующий сервис
        if updated_count == 0:
            # Если сервис не был обновлён, создаем новый
            await PriceOnlinesim.create(
                country=country_id,
                price=float(service["price"]),
                service_name=service["service"],
                slug=service["slug"]
            )


async def add_services():
    # Получаем список всех стран из CountryOnlinesim
    countries = await CountriesOnlinesim.all()

    # Проходим по каждой стране
    for country in countries:
        country_id = country.country_id  # Извлекаем country_id

        try:
            # Получаем список услуг с использованием fetch_tariffs
            services = []
            for service in SERVICE_ONLINESIM:  # Укажите нужные сервисы
                result = await fetch_tariffs(country_id, service)

                if result is None:  # Проверяем, что результат не None
                    # logger.warning(f"Получен пустой результат для страны {country_id} и сервиса {service}")
                    continue

                if "price" in result and "slug" in result:
                    services.append({
                        "price": result["price"],
                        "service": service,
                        "slug": result["slug"]
                    })
        except Exception as e:
            logger.error(f"Не удалось получить услуги для страны с id {country_id}: {e}")
            continue  # Переходим к следующей стране, если возникла ошибка

        # Добавляем или обновляем услуги для данной страны
        await insert_services(country_id, services)


