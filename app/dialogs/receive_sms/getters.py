from aiogram_dialog import DialogManager
from app.db import models
from app.services.sms_receive import SmsReceive
from loguru import logger


async def get_countries_service(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список стран с ценами для услуги.

    Аргументы:
    dialog_manager (DialogManager): Менеджер диалогов.
    **middleware_data: Дополнительные данные middleware.

    Возвращает:
    dict: Словарь с отфильтрованными странами и кодом услуги.
    """
    ctx = dialog_manager.current_context()
    countries_with_prices = ctx.start_data.get("countries_with_prices") if ctx.start_data else None
    service_code = ctx.start_data.get("service_code")
    search_name = ctx.dialog_data.get('search_name')

    if countries_with_prices is not None:
        # Фильтровать страны по поисковому имени, если оно существует.
        if search_name:
            filtered_countries = [{"id": idx, "country": item['country'], "price": item['price']} for idx, item in
                                  enumerate(countries_with_prices) if item['country'] == search_name]
        else:
            filtered_countries = [{"id": idx, "country": item['country'], "price": item['price']} for idx, item in
                                  enumerate(countries_with_prices)]
    else:
        filtered_countries = []

    data = {
        "countries": filtered_countries,
        "service_code": service_code
    }
    return data


async def get_services(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список услуг для указанной страны.

    Аргументы:
    dialog_manager (DialogManager): Менеджер диалогов.
    **middleware_data: Дополнительные данные middleware.

    Возвращает:
    dict: Словарь с отфильтрованными услугами.
    """
    ctx = dialog_manager.current_context()
    country_id = ctx.start_data.get("country_id")
    if country_id is None:
        return {"services": []}

    search_service_name = ctx.dialog_data.get("search_service_name")
    if search_service_name is not None:
        find_services = await models.Service.search_service(search_service_name)
        find_services_codes = list(map(lambda x: x.code, find_services))
    else:
        find_services_codes = None

    sms = SmsReceive()
    services = await sms.get_services_by_country_id(country_id=country_id)
    services_list = []
    for service in services:
        # Пропустить услуги с количеством меньше 5.
        if service['count'] < 5:
            continue

        service_obj = await models.Service.get_service(code=service['code'])
        if service_obj is None:
            continue

        # Пропустить услуги, не найденные в find_services_codes.
        if find_services_codes is not None and service_obj.code not in find_services_codes:
            continue

        service_data = {
            'code': service['code'],
            'cost': service['cost'],
            'name': service_obj.name
        }

        services_list.append(service_data)

    data = {
        "services": services_list
    }
    return data


async def get_services_2(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список всех услуг либо из контекста, либо из базы данных.

    Аргументы:
    dialog_manager (DialogManager): Менеджер диалогов.
    **middleware_data: Дополнительные данные middleware.

    Возвращает:
    list: Список услуг.
    """
    ctx = dialog_manager.current_context()
    services_data = ctx.dialog_data.get('services', [])
    if services_data:
        return services_data
    else:
        services_db = await models.Service.get_services()
        return services_db


async def get_other_service(dialog_manager: DialogManager, **middleware_data):
    """
    Получает другую услугу для указанной страны.

    Аргументы:
    dialog_manager (DialogManager): Менеджер диалогов.
    **middleware_data: Дополнительные данные middleware.

    Возвращает:
    dict: Словарь с услугами.
    """
    pass
    # ctx = dialog_manager.current_context()
    # country_id = ctx.start_data.get("country_id")
    # if country_id is None:
    #     return {"services": []}
    #
    # sms = SmsReceive()
    # services = await sms.get_services_by_country_id(country_id=country_id)
    # services_list = []
    # for service in services:
    #     if service['code'] != 'ot':
    #         continue
    #
    #     service_obj = await models.Service.get_service(code=service['code'])
    #     service_data = {
    #         'code': service['code'],
    #         'cost': service['cost'],
    #         'name': service_obj.name
    #     }
    #
    #     services_list.append(service_data)
    #     break
    #
    # data = {
    #     "services": services_list
    # }
    # return data


async def get_need_balance(dialog_manager: DialogManager, **middleware_data):
    """
    Получает баланс пользователя и стоимость услуги.

    :param dialog_manager: Менеджер диалога.
    :param middleware_data: Дополнительные данные из промежуточного слоя.
    :return: Словарь с данными о стоимости и балансе.
    """
    # Получаем текущий контекст диалога
    ctx = dialog_manager.current_context()
    # Получаем стоимость услуги из данных диалога
    service_cost = ctx.dialog_data.get("service_cost")
    # Получаем пользователя по его идентификатору
    user = await models.User.get_user(dialog_manager.event.from_user.id)
    # Формируем словарь с данными о стоимости и балансе
    data = {
        "cost": service_cost,
        "balance": user.balance
    }
    return data


async def get_all_services(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список всех доступных услуг в определенной стране.

    :param dialog_manager: Менеджер диалога.
    :param middleware_data: Дополнительные данные из промежуточного слоя.
    :return: Словарь с данными о доступных услугах.
    """
    # Получаем текущий контекст диалога
    ctx = dialog_manager.current_context()
    # Получаем идентификатор страны из данных запуска диалога
    country_id = ctx.start_data.get("country_id")
    if country_id is None:
        # Если идентификатор страны не указан, возвращаем пустой список услуг
        return {"services": []}

    # Получаем имя услуги для поиска из данных диалога
    search_service_name = ctx.dialog_data.get("search_service_name")
    if search_service_name is not None:
        # Если указано имя услуги для поиска, выполняем поиск
        find_services = await models.Service.search_service(search_service_name)
        # Получаем список кодов найденных услуг
        find_services_codes = list(map(lambda x: x.code, find_services))
    else:
        find_services_codes = None

    # Создаем экземпляр класса SmsReceive для работы с SMS-сервисами
    sms = SmsReceive()
    # Получаем список услуг по идентификатору страны
    services = await sms.get_services_by_country_id(country_id=country_id)
    services_list = []
    for service in services:
        if service['count'] < 5:
            # Если количество доступных номеров меньше 5, пропускаем услугу
            continue

        # Получаем объект услуги из модели Service по коду услуги
        service_obj = await models.Service.get_service(code=service['code'])
        if service_obj is None:
            # Если объект услуги не найден, пропускаем услугу
            continue

        if find_services_codes is not None and service_obj.code not in find_services_codes:
            # Если указано имя услуги для поиска и код услуги не находится в списке найденных услуг, пропускаем услугу
            continue

        # Формируем словарь с данными об услуге
        service_data = {
            'code': service['code'],
            'cost': service['cost'],
            'name': service_obj.name
        }

        # Добавляем данные об услуге в список
        services_list.append(service_data)

    # Формируем словарь с данными о доступных услугах
    data = {
        "services": services_list
    }
    return data
