import aiohttp
import asyncio


async def fetch_tariffs(country, filter_service):
    """
    Асинхронная функция для получения цены и идентификатора услуги (slug) по указанной стране
    и фильтру услуги через API OnlineSim.
    """
    url = "https://onlinesim.io/api/getTariffs.php"
    params = {
        "locale_price": "RUB",
        "country": country,
        "filter_service": filter_service
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                services = data.get("services", {})

                for service_info in services.values():
                    price = service_info.get("price")
                    slug = service_info.get("slug")
                    count = service_info.get("count", 0)

                    if price and slug and count > 0:
                        return {"price": price, "slug": slug}

                return None  # Если все count == 0 или нет подходящих данных
            else:
                return {"error": response.status, "message": await response.text()}


async def fetch_tariffs_all(country):
    """
    Асинхронная функция для получения списка цен и идентификаторов услуг (slug) по указанной стране
    и фильтру услуги через API OnlineSim.

    Args:
        country (int): Код страны, для которой нужно получить тарифы (например, 7 для России).

    Returns:
        list: Список словарей с ключами "price", "slug" и "service", если запрос успешен.
              Например, [{"price": "58.50", "slug": "telegram", "service": "Telegram"},
                         {"price": "60.00", "slug": "whatsapp", "service": "WhatsApp"}]
        dict: Словарь с ключами "error" и "message", если запрос завершился с ошибкой.
    """
    url = "https://onlinesim.io/api/getTariffs.php"
    params = {
        "locale_price": "RUB",
        "country": country
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()

                services = data.get("services", {})
                print(services)  # Вывод для отладки
                results = []
                for service_info in services.values():
                    price = service_info.get("price")
                    slug = service_info.get("slug")
                    service = service_info.get("service")  # Добавляем поле "service"

                    if price and slug and service:
                        results.append({"price": price, "slug": slug, "service": service})
                return results if results else None
            else:
                return {"error": response.status, "message": await response.text()}


import aiohttp

LIST_OF_SERVICES = {
    'groupme': 'GroupMe', 'openai': 'ChatGPT | OpenAI', 'iost': 'IOST', 'redbook': 'RedBook',
    'binance': 'Binance', 'battle_net': 'Battle.net Blizzard', 'airbnb': 'Airbnb', 'bitget': 'Bitget',
    'gemini': 'Gemini.com', 'netflix': 'Netflix', 'wog_ua': 'WOG.ua', 'lino_network': 'lino_network',
    '3223': 'Facebook', 'luban': 'Luban', 'kucoinplay': 'Kucoin', 'mamba': 'Мамба', 'happn': 'Happn',
    'amazon': 'Amazon', 'shopee': 'Shopee', 'tencentqq': 'QQ', 'imo': 'imo', 'seosprint': 'Seosprint',
    'naver': 'NAVER', 'badoo': 'Badoo', 'hqtrivia': 'HQ Trivia', 'monese': 'monese', 'drom': 'Дром',
    'justdating': 'JustDating', 'bitstamp': 'Bitstamp', 'gameflip': 'Gameflip', 'viber': 'Viber',
    'weibo': 'Weibo', 'fastfriend': 'ДругВокруг', 'google': 'Google (Youtube, Gmail)', 'ctrip': 'Ctrip',
    'jiayuan': 'Jiayuan', 'crypto': 'Crypto.com', 'kakaotalk': 'KakaoTalk', 'wolt': 'Wolt',
    'bilibili': 'bilibili', 'hey_plus': 'HeyPlus', 'ebay': 'eBay|Kleinanzeigen.', 'linkedin': 'LinkedIn',
    'instagram': 'Instagram', 'yalla': 'Yalla', 'apple': 'Apple', 'careem': 'Careem', 'mailru': 'Mail.ru',
    'amap': 'amap', 'coinbase': 'Coinbase', 'blablacar': 'BlaBlaCar', 'odklru': 'Одноклассники',
    'discord': 'Discord', 'uber': 'Uber', 'chsi': 'CHSI', 'tinder': 'Tinder', 'youla': 'Юла',
    'meetme': 'MeetMe', 'huobi': 'Huobi Global', 'rambler': 'Рамблер', 'telegram': 'Telegram',
    'signal': 'Signal', 'lianxin': 'Lianxin', 'whatsapp': 'WhatsApp', 'soul': 'Soul',
    'microsoft': 'Microsoft', 'ftx': 'ftx.com', 'vkcom': 'ВКонтакте + Mail.ru',
    'foodora': 'Foodora', 'appbonus': 'AppBonus', 'aol': 'AOL', 'bolt': 'Bolt',
    'olx': 'OLX', 'suomi24': 'Suomi24', 'hinge': 'Hinge', 'bumble': 'Bumble', 'livescore': 'LiveScore',
    'miliao': 'Miliao', 'beget': 'Beget', 'yahoo': 'Yahoo', 'yandex': 'Яндекс', 'steam': 'Steam',
    'suno': 'Suno', 'lyft': 'Lyft', 'linemessenger': 'LINE', 'playerauctions': 'PlayerAuctions',
    'snapchat': 'Snapchat', 'icq': 'ICQ', 'paopao': 'PaoPao', 'twitter': 'Twitter|X',
    'alibaba': 'Alibaba', 'wechat': 'WeChat', 'ultra_io': 'Ultra_io', 'tiktok': 'TikTok (ТикТок)',
    'tantan': 'TanTan', 'gett': 'Gett', 'taximaxim': 'taxiMaxim', 'bet365': 'bet365', 'jd': 'JD.com',
    'electroneum': 'Electroneum', 'esportal': 'esportal', 'nike': 'Nike', 'whatnot': 'Whatnot',
    'foodpanda': 'Foodpanda'
}


async def fetch_tariffs_all_countries():
    """
    Асинхронная функция для получения списка цен по всем странам и сервисам через API OnlineSim,
    добавляя названия сервисов из LIST_OF_SERVICES.
    """
    url = "https://onlinesim.io/api/price-list-data"
    params = {"type": "receive", "locale_price": "RUB"}  # Добавляем параметр RUB

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                country_services = data.get("list", {})

                formatted_data = {}
                for country, services in country_services.items():
                    formatted_data[country] = [
                        {
                            "price": price,
                            "slug": slug,
                            "service": LIST_OF_SERVICES.get(slug, slug.capitalize())
                        }
                        for slug, price in services.items()
                    ]

                return formatted_data
            else:
                return {"error": response.status, "message": await response.text()}


# Пример вызова функции
if __name__ == '__main__':
    result = asyncio.run(fetch_tariffs_all_countries())
    print(result)
