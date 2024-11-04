from pyonlinesim import OnlineSMS
from tortoise.exceptions import DoesNotExist
from app.db.models import CountryOnlinesim, ServiceOnlinesim
from app.dependencies import API_KEY_ONLINESIM


async def insert_services(country_id: int, services):
    try:
        # Получаем экземпляр CountryOnlinesim по country_id
        country_instance = await CountryOnlinesim.get(country_id=country_id)
    except DoesNotExist:
        print(f"Страна с id {country_id} не найдена. Прекращаем добавление сервисов.")
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
            print(f"Добавлен новый сервис: {new_service.service_name} (ID: {new_service.id})")
        else:
            print(f"Обновлен сервис: {service.service} для страны: {country_instance.name}")


async def add_services():
    client = OnlineSMS(api_key=API_KEY_ONLINESIM)

    country_id = 7  # Здесь country_id, который вы хотите использовать
    services = await client.get_services(country=str(country_id))

    await insert_services(country_id, services)
