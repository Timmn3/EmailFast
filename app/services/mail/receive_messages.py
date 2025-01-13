import aiohttp
import asyncio
from mailtm import Email


async def fetch_messages(mail_token):
    """
    Получить все сообщения для существующего токена.
    """
    email = Email()
    email.token = mail_token

    url = "https://api.mail.tm/messages"
    headers = {
        'Authorization': f'Bearer {email.token}',
        'Content-Type': 'application/json'
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            data = await response.json()
            return data.get('hydra:member', [])


async def mark_as_read(mail_token, message_id):
    """
    Отметить письмо как прочитанное.
    """
    url = f"https://api.mail.tm/messages/{message_id}"
    headers = {
        'Authorization': f'Bearer {mail_token}',
        'Content-Type': 'application/merge-patch+json'
    }
    payload = {"seen": True}

    async with aiohttp.ClientSession() as session:
        async with session.patch(url, headers=headers, json=payload) as response:
            response.raise_for_status()
            return await response.json()


async def get_unread_messages(mail_token: str) -> list:
    """
    Возвращает список информации о непрочитанных сообщениях для заданного токена.

    :param mail_token: Токен для доступа к почте.
    :return: Список словарей с информацией о непрочитанных сообщениях.
    """
    messages = await fetch_messages(mail_token)
    unread_messages = []

    if messages:
        for msg in messages:
            if not msg['seen']:
                unread_messages.append({
                    "id": msg['id'],  # Добавляем ID сообщения
                    "from": f"{msg['from']['name']} ({msg['from']['address']})",
                    "subject": msg['subject'],
                    "content": msg['intro'],
                })
                await mark_as_read(mail_token, msg['id'])

    return unread_messages


async def main():
    # Ваш токен
    token = "REDACTED"

    unread_messages = await get_unread_messages(token)
    if unread_messages:
        for msg in unread_messages:
            print(f"Message ID: {msg['id']}")
            print(f"От кого: {msg['from']}")
            print(f"Тема: {msg['subject']}")
            print(f"Содержание: {msg['content']}")
            print("-" * 40)
    else:
        print("Нет новых писем.")


if __name__ == "__main__":
    asyncio.run(main())
