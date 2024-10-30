# import asyncio
# import json
# import random
# from dataclasses import dataclass
# from string import ascii_lowercase, ascii_uppercase, digits
# from typing import List, Dict
#
# from aiohttp import ClientSession
#
#
# async def post_request(url, headers: dict, data=None):
#     async with ClientSession(headers=headers) as session:
#         async with session.post(url=url, data=data) as response:
#             return response.status, json.loads(await response.text())
#
#
# async def get_request(url, headers: dict):
#     async with ClientSession(headers=headers) as session:
#         async with session.get(url=url) as response:
#             return response.status, json.loads(await response.text())
#
#
# async def delete_request(url, headers: dict):
#     async with ClientSession(headers=headers) as session:
#         async with session.delete(url=url) as response:
#             return response.status
#
#
# class Account:
#     def __init__(self, id, address, password):
#         self.auth_headers = None
#         self.id_ = id
#         self.address = address
#         self.password = password
#         self.api_address = MailTm.api_address
#
#     async def set_jwt(self):
#         # Set the JWT
#         jwt = await MailTm._make_account_request("token",
#                                                  self.address, self.password)
#         self.auth_headers = {
#             "accept": "application/ld+json",
#             "Content-Type": "application/json",
#             "Authorization": "Bearer {}".format(jwt["token"])
#         }
#
#     async def get_messages(self, page=1):
#         r = await get_request("{}/messages?page={}".format(self.api_address, page),
#                               headers=self.auth_headers)
#
#         status_code = r[0]
#         json_data = r[1]
#         if status_code != 200:
#             raise Exception
#
#         messages = []
#         for message_data in json_data["hydra:member"]:
#             r = await get_request(
#                 f"{self.api_address}/messages/{message_data['id']}", headers=self.auth_headers)
#             if r[0] != 200:
#                 raise Exception
#
#             full_message_json = r[1]
#             text = full_message_json["text"]
#             html = full_message_json["html"]
#
#             messages.append(Message(
#                 message_data["id"],
#                 message_data["from"],
#                 message_data["to"],
#                 message_data["subject"],
#                 message_data["intro"],
#                 text,
#                 html,
#                 message_data))
#
#         return messages
#
#     async def delete_account(self):
#         r = await delete_request("{}/accounts/{}".format(self.api_address,
#                                                          self.id_), headers=self.auth_headers)
#
#         return r == 204
#
#     async def _get_existing_messages_id(self) -> List[int]:
#         old_messages = await self.get_messages()
#         return list(map(lambda m: m.id_, old_messages))
#
#
#
#
# class MailTm:
#     api_address = "https://api.mail.tm"
#
#     async def _get_domains_list(self):
#         headers = None
#         while True:
#             r = await get_request("{}/domains".format(self.api_address), headers=headers)
#             if r[0] == 200:
#                 response = r[1]
#                 domains = list(map(lambda x: x["domain"], response["hydra:member"]))
#                 return domains
#
#             await asyncio.sleep(2)
#
#     async def _generate_email(self, domain: str = None):
#         if not domain:
#             domains = await self._get_domains_list()
#             domain = random.choice(domains)
#
#         return ''.join(random.choices(ascii_lowercase, k=10)) + '@' + domain
#
#     def _generate_password(self, len: int = 8):
#         return ''.join(random.choices(ascii_uppercase + ascii_lowercase + digits, k=len))
#
#     async def create_account(self, password=None):
#         """Create and return a new account."""
#         address = await self._generate_email()
#         if not password:
#             password = self._generate_password(8)
#         response = await self._make_account_request("accounts", address, password)
#         account = Account(response["id"], response["address"], password)
#         await account.set_jwt()
#         return account
#
#     @staticmethod
#     async def _make_account_request(endpoint, address, password):
#         account = {"address": address, "password": password}
#         headers = {
#             "accept": "application/ld+json",
#             "Content-Type": "application/json"
#         }
#         r = await post_request("{}/{}".format(MailTm.api_address, endpoint),
#                                data=json.dumps(account), headers=headers)
#
#         if r[0] not in [200, 201]:
#             raise Exception
#
#         return r[1]

import json
import re
from dataclasses import dataclass

import aiohttp


@dataclass
class Message:
    id_: int
    from_: str
    subject: str
    text: str


class TempMail:
    def __init__(self):
        self.base_url = 'https://www.1secmail.com/api/v1/?'

    async def _response(self, url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                return await response.text()

    async def generate_email(self):
        url = self.base_url + 'action=genRandomMailbox&count=1'
        response = await self._response(url)
        # print(f'123R: {response}', flush=True)
        email_list = json.loads(response)
        if len(email_list) > 0:
            return email_list[0]

    async def get_message_ids(self, login: str, domain: str):
        url = self.base_url + f'action=getMessages&login={login}&domain={domain}'
        response = await self._response(url)
        # print(f'123R: {response}', flush=True)
        messages = json.loads(response)
        message_ids = [message['id'] for message in messages[:5]]
        return message_ids

    def clean_html_tags(self, text):
        clean_text = re.sub(r'<(?!\/?(b|i|a)\b)[^>]*>', '', text)  # Удаляем все теги, кроме b, i, a
        clean_text = clean_text.replace('<br>', '\n')  # Заменяем <br> на символ новой строки
        return clean_text

    async def read_message(self, login: str, domain: str, message_id: int):
        url = self.base_url + f'action=readMessage&login={login}&domain={domain}&id={message_id}'
        data = json.loads(await self._response(url))
        return Message(
            id_=data['id'],
            from_=data['from'],
            subject=data['subject'],
            text=self.clean_html_tags(data['htmlBody'])
        )
