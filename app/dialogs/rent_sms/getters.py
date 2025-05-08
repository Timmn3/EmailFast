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
            logger.opt(exception=e).error(f"Ошибка в обработчике {func.__name__}: {e}")
            raise  # Повторно вызываем ошибку, чтобы не скрывать её от фреймворка
    return wrapper


@log_exceptions
async def get_rent_countries(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список стран для аренды с ценами для услуги.

    :param dialog_manager: Менеджер диалогов.
    :param middleware_data: Дополнительные данные из middleware.
    :return: dict - Данные о странах для аренды с увеличенными ценами.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        logger.bind(user_id=user_id, action='get_rent_countries').log(
            "USER_ACTION",
            "Запрос списка стран для аренды номера"
        )

        api_client = OnlineSimRentAPI()
        tariffs = await api_client.get_tariffs()

        if not tariffs:
            logger.bind(user_id=user_id, action='get_rent_countries').log(
                "USER_ACTION",
                "Не удалось получить тарифы для аренды номеров"
            )
            return {"rent_countries": []}

        all_countries = await CountriesOnlinesim.all().values("country_id", "name")
        country_map = {str(country["country_id"]): country["name"] for country in all_countries}

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

        dialog_manager.dialog_data["rent_countries"] = countries

        logger.bind(user_id=user_id, action='get_rent_countries').log(
            "USER_ACTION",
            f"Получено {len(countries)} стран для аренды"
        )

        return {"rent_countries": countries}
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_rent_countries: {e}")
        return {"rent_countries": []}


async def get_country_details(dialog_manager: DialogManager, **kwargs):
    """
    Получает информацию о выбранной стране для отображения в деталях.

    :param dialog_manager: Менеджер диалогов.
    :param kwargs: Дополнительные параметры.
    :return: dict с деталями выбранной страны.
    """
    try:
        user_id = dialog_manager.event.from_user.id
        logger.bind(user_id=user_id, action='get_country_details').log(
            "USER_ACTION",
            "Запрос информации по выбранной стране"
        )

        selected_country = dialog_manager.dialog_data.get("selected_country")
        if selected_country is None:
            selected_country = dialog_manager.start_data.get("selected_country")

        if not selected_country or "tariffs" not in selected_country:
            logger.bind(user_id=user_id, action='get_country_details').log(
                "USER_ACTION",
                "Детали страны недоступны или не найдены"
            )
            return {"country": "Неизвестно", "tariffs": []}

        tariffs = sorted(
            [{"days": get_day_string(int(days)), "price": price} for days, price in selected_country["tariffs"].items()],
            key=lambda x: x["price"]
        )

        logger.bind(user_id=user_id, action='get_country_details').log(
            "USER_ACTION",
            f"Отображение деталей страны: {selected_country['country']}"
        )

        return {
            "country": selected_country["country"],
            "tariffs": tariffs,
        }
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в get_country_details: {e}")
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
    """
    Обработчик кнопки "Отмена". Завершает текущее состояние диалога.
    :param c: CallbackQuery.
    :param button: Кнопка.
    :param dialog_manager: Менеджер диалогов.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='cancel_btn').log(
            "USER_ACTION",
            "Пользователь отменил операцию"
        )
        await dialog_manager.done()
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в cancel_btn: {e}")