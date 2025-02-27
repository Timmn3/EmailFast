from aiogram_dialog import DialogManager
from loguru import logger
from sqlalchemy import false

from app.db.models import CountriesOnlinesim
from app.services.bot_texts import DOLLAR_ONLINESIM
from app.services.onlinesim.rent_number import OnlineSimRentAPI

def log_exceptions(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в обработчике {func.__name__}: {e}")
            raise  # Возможно, чтобы повторно вызвать ошибку и не скрывать её
    return wrapper

@log_exceptions
async def get_rent_countries(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список стран для аренды с ценами для услуги.

    :param dialog_manager: Менеджер диалогов.
    :param middleware_data: Дополнительные данные middleware.
    :return: dict - Данные о странах для аренды с увеличенными ценами.
    """
    api_client = OnlineSimRentAPI()
    tariffs = await api_client.get_tariffs()
    # Предзагрузка всех стран в память
    all_countries = await CountriesOnlinesim.all().values("country_id", "name")
    country_map = {str(country["country_id"]): country["name"] for country in all_countries}
    # Преобразование данных с увеличением цены
    countries = []
    for country_code, days in tariffs.items():
        country_name = country_map.get(country_code, country_code)
        base_price = list(days.values())[0] if days else 0
        increased_price = round(base_price * DOLLAR_ONLINESIM)

        countries.append({
            "id": country_code,
            "country": country_name,
            "price": increased_price,
            "tariffs": tariffs,
        })

    # Сохраняем список стран в dialog_data
    dialog_manager.dialog_data["rent_countries"] = countries

    return {"rent_countries": countries}


async def get_country_details(dialog_manager: DialogManager, **kwargs):
    """
    Получает информацию о выбранной стране для отображения в деталях.

    :param dialog_manager: Менеджер диалогов.
    :param kwargs: Дополнительные параметры.
    :return: dict с деталями выбранной страны.
    """
    # Данные о выбранной стране сохранены в dialog_data
    try:
        selected_country = dialog_manager.dialog_data.get("selected_country")
        if selected_country is None:
            selected_country = dialog_manager.start_data.get("selected_country")

        # Получаем тарифы
        tariffs = [
            {"days": get_day_string(int(days)), "price": price}
            for days, price in selected_country["tariffs"].items()
        ]

        return {
            "country": selected_country["country"],
            "tariffs": tariffs,
        }
    except:
        return {"country": "Неизвестно", "tariffs": []}



def get_day_string(days):
    """Возвращает строку с правильным склонением дня."""
    if days % 10 == 1 and days % 100 != 11:
        return f"{days} день"
    elif 2 <= days % 10 <= 4 and not (12 <= days % 100 <= 14):
        return f"{days} дня"
    else:
        return f"{days} дней"


# Обработчик для кнопки Cancel
async def cancel_btn(c, button, dialog_manager: DialogManager):
    await dialog_manager.done()

