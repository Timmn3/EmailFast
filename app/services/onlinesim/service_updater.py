from pyonlinesim import OnlineSMS
from tortoise.exceptions import DoesNotExist
from app.db.models import CountryOnlinesim, ServiceOnlinesim
from app.dependencies import API_KEY_ONLINESIM
from loguru import logger

async def insert_services(country_id: int, services):
    try:
        # Получаем экземпляр CountryOnlinesim по country_id
        country_instance = await CountryOnlinesim.get(country_id=country_id)
    except DoesNotExist:
        return  # Прекращаем выполнение, если страна не найдена

    for service in services.services:
        # Обновляем данные сервиса или создаем новый, если он не существует
        updated_count = await ServiceOnlinesim.filter(
            service_name=service.service,
            country=country_id # Используем название страны для фильтрации
        ).update(
            price=float(service.price),
            slug=service.slug
        )

        # Проверяем, был ли обновлён существующий сервис
        if updated_count == 0:
            # Если сервис не был обновлён, создаем новый
            new_service = await ServiceOnlinesim.create(
                country=country_id,  # Ссылаемся на название страны
                price=float(service.price),
                service_name=service.service,
                slug=service.slug
            )


async def add_services():
    client = OnlineSMS(api_key=API_KEY_ONLINESIM)

    # Получаем список всех стран из CountryOnlinesim
    countries = await CountryOnlinesim.all()

    # Проходим по каждой стране
    for country in countries:
        country_id = country.country_id  # Извлекаем country_id

        try:
            # Получаем услуги для текущей страны
            services = await client.get_services(country=str(country_id))
        except Exception as e:
            logger.error(f"Не удалось получить услуги для страны с id {country_id}: {e}")
            continue  # Переходим к следующей стране, если возникла ошибка

        # Добавляем или обновляем услуги для данной страны
        await insert_services(country_id, services)


