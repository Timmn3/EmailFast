import asyncio
from mailtm import Email

async def create_mail():
    test = Email()
    test.register()
    return test.address, test.token
