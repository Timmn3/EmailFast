import asyncio
from mailtm import Email
import requests
from loguru import logger

async def create_mail(retries=3, delay=10):
    for attempt in range(retries):
        try:
            test = Email()
            test.register()
            return test.address, test.token
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:  # Слишком много запросов
                logger.error(f"Попытка {attempt + 1}: Превышен лимит запросов. Ожидание {delay} сек...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Ошибка: {e}")
                break
        except Exception as e:
            logger.error(f"Неизвестная ошибка: {e}")
            break
    return None, None  # Если не удалось создать почту

# Использование:
# address, token = await create_mail()
