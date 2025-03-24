import asyncio
from mailtm import Email

async def create_mail():
    await asyncio.sleep(3000)  # Асинхронная задержка
    test = Email()
    test.register()
    return test.address, test.token
