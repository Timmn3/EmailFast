from tortoise.exceptions import DoesNotExist
from app.db.models import CountriesOnlinesim, PriceOnlinesim
from loguru import logger

from app.services.bot_texts import SERVICE_ONLINESIM
from app.services.onlinesim.get_tariffs import fetch_tariffs_all, fetch_tariffs_all_countries


async def insert_services(country_id: int, services):
    try:
        # Получаем экземпляр CountryOnlinesim по country_id
        await CountriesOnlinesim.get(country_id=country_id)
    except DoesNotExist:
        return  # Прекращаем выполнение, если страна не найдена

    for service in services:
        # Обновляем данные сервиса или создаем новый, если он не существует
        updated_count = await PriceOnlinesim.filter(
            name=service["service"],
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
                name=service["service"],
                code=service["slug"]
            )


async def add_services():
    """
    Функция для получения и сохранения списка услуг для всех стран из API OnlineSim.
    """
    try:
        # Получаем список всех услуг по всем странам одной операцией
        all_services = await fetch_tariffs_all_countries()

        if "error" in all_services:
            logger.error(f"Ошибка при получении данных: {all_services['message']}")
            return

        # Обновляем услуги для каждой страны
        for country_id, services in all_services.items():
            await insert_services(country_id, services)

    except Exception as e:
        logger.error(f"Ошибка при обновлении услуг: {e}")



