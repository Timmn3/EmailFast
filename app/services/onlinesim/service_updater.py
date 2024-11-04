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
        # Проверяем, существует ли уже сервис с таким именем для данной страны
        existing_service = await ServiceOnlinesim.filter(service_name=service.service, country=country_instance.id).first()

        if existing_service:
            # Если сервис существует, обновляем его данные
            existing_service.price = service.price
            existing_service.slug = service.slug
            await existing_service.save()
            print(f"Обновлен сервис: {existing_service.service_name} (ID: {existing_service.id})")
        else:
            # Если сервис не существует, создаем новый
            new_service = await ServiceOnlinesim.create(
                country=country_instance,  # Ссылаемся на экземпляр страны
                price=service.price,
                service_name=service.service,
                slug=service.slug
            )
            print(f"Добавлен новый сервис: {new_service.service_name} (ID: {new_service.id})")


async def add_services():
    client = OnlineSMS(api_key=API_KEY_ONLINESIM)

    country_id = 7
    services = await client.get_services(country=str(country_id))

    await insert_services(country_id, services)
