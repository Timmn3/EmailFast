from tortoise.exceptions import DoesNotExist
from loguru import logger

from app.db.models import CountriesOnlinesim, PriceOnlinesim, ServicesOnlinesim
from app.services.onlinesim.get_tariffs import fetch_tariffs_all_countries


def _make_search_names(code: str, name: str) -> str:
    """
    Формирует строку для поиска (в нижнем регистре), чтобы находилось и по коду, и по названию.
    """
    code = (code or "").strip()
    name = (name or "").strip()
    base = f"{code} {name}".strip().lower()
    return base.replace("_", " ").replace("-", " ")


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


async def _sync_services_onlinesim_from_price() -> None:
    """
    Синхронизирует справочник services_onlinesim из price_onlinesim.
    Нужно, чтобы:
    - поиск сервисов работал (он ищет по services_onlinesim.search_names)
    - при аренде на OnlineSim корректно проставлялась связь service_2
    """
    try:
        services_db = await PriceOnlinesim.get_all_services()
        raw_services = services_db.get("services", [])

        services_data = []
        for s in raw_services:
            code = (s.get("code") or "").strip()
            name = (s.get("name") or "").strip()

            if not code:
                continue

            if not name:
                name = code

            services_data.append({
                "code": code,
                "name": name,
                "search_names": _make_search_names(code, name),
            })

        await ServicesOnlinesim.update_services(services_data)
        await ServicesOnlinesim.normalize_search_names()

        logger.info(f"services_onlinesim: синхронизация завершена, всего={len(services_data)}")
    except Exception as e:
        logger.error(f"Ошибка при синхронизации services_onlinesim: {e}")


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

        # ВАЖНО: после обновления price_onlinesim синхронизируем справочник сервисов
        await _sync_services_onlinesim_from_price()

    except Exception as e:
        logger.error(f"Ошибка при обновлении услуг: {e}")
