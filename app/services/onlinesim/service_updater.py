from tortoise.exceptions import DoesNotExist
from app.db.models import CountriesOnlinesim, PriceOnlinesim
from loguru import logger

from app.services.bot_texts import SERVICE_ONLINESIM
from app.services.onlinesim.get_tariffs import fetch_tariffs, fetch_tariffs_all


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
            code=service["slug"]
        )

        # Проверяем, был ли обновлён существующий сервис
        if updated_count == 0:
            # Если сервис не был обновлён, создаем новый
            await PriceOnlinesim.create(
                country=country_id,
                price=float(service["price"]),
                service_name=service["service"],
                code=service["slug"]
            )


async def add_services():
    """
    Функция для получения и сохранения списка услуг для всех стран из API OnlineSim.
    """
    # Получаем список всех стран из CountryOnlinesim
    countries = await CountriesOnlinesim.all()
    print(len(countries))

    # Проходим по каждой стране
    for country in countries:
        country_id = country.country_id  # Извлекаем country_id

        try:
            # Получаем список всех услуг одной операцией
            result_services = await fetch_tariffs_all(country_id)

            if not result_services:  # Проверяем, что результат не пустой
                continue

            services = [
                {"price": service["price"], "service": service["slug"], "slug": service["slug"]}
                for service in result_services
            ]
        except Exception as e:
            logger.error(f"Не удалось получить услуги для страны с id {country_id}: {e}")
            continue  # Переходим к следующей стране, если возникла ошибка

        # Добавляем или обновляем услуги для данной страны
        await insert_services(country_id, services)


