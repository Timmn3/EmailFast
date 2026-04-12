from aiogram_dialog import DialogManager
from app.db import models
from app.services import bot_texts as bt
from app.services.sms_receive import SmsReceive
from loguru import logger


async def get_countries_service(dialog_manager: DialogManager, **middleware_data):
    """
    Получает список стран с ценами для услуги.

    Аргументы:
    dialog_manager (DialogManager): Менеджер диалогов.
    **middleware_data: Дополнительные данные middleware.

    Возвращает:
    dict: Словарь с отфильтрованными странами, кодом услуги
    и текстом заголовка окна выбора страны.
    """
    try:
        ctx = dialog_manager.current_context()

        # Проверяем, существует ли start_data и является ли она словарем
        if not ctx.start_data or not isinstance(ctx.start_data, dict):
            return {
                "countries": [],
                "service_code": None,
                "select_country_text": bt.SELECT_COUNTRY,
            }

        countries_with_prices = ctx.start_data.get("countries_with_prices")
        service_code = ctx.start_data.get("service_code")
        search_name = ctx.dialog_data.get("search_name") if ctx.dialog_data else None

        # Текст предупреждения показываем только для Telegram
        if service_code == "telegram":
            select_country_text = (
                "Алгоритмы сервисов могут опознать подозрительную активность, "
                "не принять код после ввода, заморозить аккаунт или наложить временный бан.\n\n"
                "Указанные ограничения не являются основанием для возврата.\n\n"
                'Изучите <a href="https://telegra.ph/Rekomendacii-dlya-registracii-Telegram-03-14">рекомендации</a>, '
                'как минимизировать риски и выберите страну<tg-emoji emoji-id="5197474438970363734">\u2935\ufe0f</tg-emoji>'
            )
        else:
            select_country_text = bt.SELECT_COUNTRY

        # Фильтровать страны по поисковому имени, если оно существует
        if countries_with_prices is not None:
            if search_name:
                filtered_countries = [
                    {"id": idx, "country": item["country"], "price": item["price"]}
                    for idx, item in enumerate(countries_with_prices)
                    if item["country"] == search_name
                ]
            else:
                filtered_countries = [
                    {"id": idx, "country": item["country"], "price": item["price"]}
                    for idx, item in enumerate(countries_with_prices)
                ]
        else:
            filtered_countries = []

        # Проверяем, в избранном ли текущий сервис
        user = await models.User.get_user(dialog_manager.event.from_user.id)
        service_name = service_code or ""
        if service_code:
            svc_obj = await models.ServicesSmsActivate.get_service(code=service_code)
            if svc_obj is None:
                svc_obj = await models.ServicesOnlinesim.get_service(code=service_code)
            if svc_obj:
                service_name = svc_obj.name
        is_fav = await models.FavoriteService.is_favorite(user.id, service_code) if service_code else False

        data = {
            "countries": filtered_countries,
            "service_code": service_code,
            "select_country_text": select_country_text,
            "show_add_favorite": bool(service_code) and not is_fav,
            "show_remove_favorite": bool(service_code) and is_fav,
            "service_name": service_name,
        }
        return data

    except Exception as e:
        logger.error(e)

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
        find_services = await models.ServicesSmsActivate.search_service(search_service_name)
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

        service_obj = await models.ServicesSmsActivate.get_service(code=service['code'])
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


async def service_is_smsactivate():
    return await models.AdminSettings.get_setting_value("sms_rental_service") == "SMS_Activate"


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
        if await service_is_smsactivate():
            services_db = await models.ServicesSmsActivate.get_services()
        else:
            services_db = await models.PriceOnlinesim.get_all_services()
            services_db["services"].extend([
                # {"code": "ts", "name": "PayPal"},
                {"code": "ot", "name": "Любой другой"}
            ])

        # Сортировка: приоритетные первыми, Telegram первым на 2-й странице (позиция 11)
        PAGE_SIZE = 10
        priority_codes = {"google", "vkcom", "whatsapp"}
        hidden_codes = {"samokat", "x5id", "magnit"}
        all_services = [s for s in services_db["services"] if s["code"] not in hidden_codes]

        priority_services = [s for s in all_services if s["code"] in priority_codes]
        telegram_services = [s for s in all_services if s["code"] == "telegram"]
        other_services = [s for s in all_services if s["code"] not in priority_codes and s["code"] != "telegram"]

        # Заполняем 1-ю страницу до PAGE_SIZE, потом Telegram, потом остальные
        fill_count = PAGE_SIZE - len(priority_services)
        first_page_others = other_services[:fill_count]
        rest_others = other_services[fill_count:]

        services_db["services"] = priority_services + first_page_others + telegram_services + rest_others
        return services_db


async def get_favorites(dialog_manager: DialogManager, **middleware_data):
    """
    Getter для окна избранных сервисов.
    Возвращает список избранных сервисов текущего пользователя.
    """
    user_id = dialog_manager.event.from_user.id
    user = await models.User.get_user(user_id)
    favs = await models.FavoriteService.get_favorites(user.id)
    return {
        "services": [{"code": f["service_code"], "name": f["service_name"]} for f in favs]
    }



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
        "cost": int(service_cost) if service_cost is not None else service_cost,
        "balance": int(user.balance)
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
        find_services = await models.ServicesSmsActivate.search_service(search_service_name)
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
        service_obj = await models.ServicesSmsActivate.get_service(code=service['code'])
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


async def get_show_smsfast_other_button(dialog_manager: "DialogManager", **middleware_data):
    """
    Возвращает флаг, нужно ли показывать кнопку "Любой другой" в ошибке поиска сервиса.

    Показываем только когда реально можем обработать "ot" через SMSFast:
    - либо SMSFast выбран как основной провайдер (sms_rental_service == "SMS_Fast")
    - либо включён частичный режим SMSFast (smsfast_enabled == true/1/yes/on)

    :return: dict вида {"show_smsfast_other": bool}
    """
    from app.db import models

    try:
        smsfast_active = (await models.AdminSettings.get_setting_value("sms_rental_service") == "SMS_Fast")

        smsfast_enabled_val = await models.AdminSettings.get_setting_value("smsfast_enabled")
        smsfast_enabled = str(smsfast_enabled_val or "").strip().lower() in (
            "1", "true", "yes", "y", "on", "enable", "enabled"
        )

        return {"show_smsfast_other": bool(smsfast_active or smsfast_enabled)}
    except Exception:
        # Безопасный дефолт: не показываем кнопку
        return {"show_smsfast_other": False}
