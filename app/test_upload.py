import asyncio
import re

from app.db.models import ServicesOnlinesim

# Список предустановленных сервисов (без кавычек)
PREDEFINED_SERVICES = {
    "ВКонтакте + Mail.ru", "Facebook", "Одноклассники", "Google (Youtube, Gmail)",
    "Viber", "WhatsApp", "Telegram", "Instagram", "WeChat", "Steam", "Uber",
    "Microsoft", "Twitter|X", "Gett", "OLX", "MeetMe", "LINE", "Yahoo", "QQ",
    "taxiMaxim", "TanTan", "Tinder", "Мамба", "KakaoTalk", "AOL", "LinkedIn",
    "Bolt", "Airbnb", "lino_network", "LiveScore", "Яндекс", "Apple", "ICQ",
    "Рамблер", "ДругВокруг", "Дром", "eBay|Kleinanzeigen.", "HQ Trivia", "Yalla",
    "Amazon", "Discord", "NAVER", "Юла", "Seosprint", "BlaBlaCar", "Alibaba",
    "imo", "Snapchat", "IOST", "AppBonus", "Gameflip", "HeyPlus", "WOG.ua",
    "Beget", "Careem", "Electroneum", "Jiayuan", "Kucoin", "Bumble", "Hinge",
    "Coinbase", "Happn", "Binance", "Gemini.com", "ChatGPT | OpenAI", "ftx.com",
    "Huobi Global", "Crypto.com", "Bitstamp", "GroupMe", "Netflix", "Foodpanda",
    "Ultra_io", "esportal", "PlayerAuctions", "Foodora", "bet365", "Suomi24",
    "Suno", "Nike", "Miliao", "Shopee", "Soul", "PaoPao", "Lyft", "TikTok (ТикТок)",
    "Lianxin", "JustDating", "Weibo", "Battle.net Blizzard", "JD.com", "RedBook",
    "bilibili", "Bitget", "Luban", "CHSI", "amap", "Whatnot", "Ctrip", "Mail.ru",
    "monese", "Signal", "Wolt", "Badoo"
}

async def load_services_from_file(filename: str):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            # Убираем кавычки и пробелы у названий сервисов
            service_names = {re.sub(r'^"|"$', '', line.strip()) for line in file if line.strip()}

        new_services = service_names - PREDEFINED_SERVICES  # Исключаем предустановленные сервисы

        if not new_services:
            print("✅ Все сервисы уже есть в списке.")
            return

        # Получаем существующие коды из базы (в нижнем регистре)
        existing_codes = set(map(str.lower, await ServicesOnlinesim.get_codes_list()))

        # Фильтруем только те сервисы, которых еще нет в базе
        unique_services = {name for name in new_services if name.lower() not in existing_codes}

        if not unique_services:
            print("✅ Все сервисы уже есть в базе, добавлять нечего.")
            return

        # Добавляем новые сервисы в базу
        for name in unique_services:
            name_clean = re.sub(r'^"|"$', '', name)  # Убираем кавычки, если они есть
            code = name_clean.lower()  # Код и search_names — в нижнем регистре
            search_names = name_clean.lower()

            # Проверяем еще раз, вдруг сервис уже появился (если параллельно работает другой процесс)
            if await ServicesOnlinesim.get_or_none(code=code):
                continue

            await ServicesOnlinesim.add_service(code=code, name=name_clean, search_names=search_names)

        print(f"🎉 Успешно добавлено {len(unique_services)} новых сервисов!")

    except Exception as e:
        print(f"❌ Ошибка: {e}")

