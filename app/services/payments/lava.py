import hmac
import json
import hashlib
import aiohttp

from app import dependencies


class LavaApi:
    def __init__(self):
        self.SHOP_ID = dependencies.LAVA_SHOP_ID
        self.SECRET_KEY = dependencies.LAVA_SECRET_KEY
        self.url = 'https://api.lava.ru/business/invoice/{endpoint}'

    @staticmethod
    def sort_dict(data: dict):
        sorted_tuple = sorted(data.items(), key=lambda x: x[0])
        return dict(sorted_tuple)

    def get_sign(self, data: dict):
        json_str = json.dumps(data).encode()
        return hmac.new(bytes(self.SECRET_KEY, 'UTF-8'), json_str, hashlib.sha256).hexdigest()

    def get_headers(self, data: dict):
        sign = self.get_sign(data)
        return {'Signature': sign, 'Accept': 'application/json'}

    async def _response(self, url: str, data: dict):
        try:
            async with aiohttp.ClientSession(headers=self.get_headers(data)) as session:
                async with session.post(url, json=data, ssl=False) as response:
                    return json.loads(await response.text())
        except Exception:
            pass

    async def create_invoice(self, amount: float, order_id: str):
        endpoint = 'create'
        data = {
            'shopId': self.SHOP_ID,
            'sum': amount,
            'orderId': order_id
        }
        data = self.sort_dict(data)
        # {'data': {'id': '25f6b3c0-9c37-4189-a6be-f2b41d1c962d', 'amount': 10, 'expired': '2023-11-02 18:49:21', 'status': 1, 'shop_id': 'a573a7c3-b7f4-46e7-8ed5-10a5e426dab0', 'url': 'https://pay.lava.ru/invoice/25f6b3c0-9c37-4189-a6be-f2b41d1c962d?lang=ru', 'comment': None, 'merchantName': 'Email Fast', 'exclude_service': None, 'include_service': None}, 'status': 200, 'status_check': True}
        return await self._response(self.url.format(endpoint=endpoint), data)

    async def get_invoice_status(self, order_id: str, invoice_id: str):
        endpoint = 'status'
        data = {
            'shopId': self.SHOP_ID,
            'orderId': order_id,
            'invoiceId': invoice_id
        }
        data = self.sort_dict(data)
        # {'data': {'status': 'created', 'error_message': None, 'id': '25f6b3c0-9c37-4189-a6be-f2b41d1c962d', 'shop_id': 'a573a7c3-b7f4-46e7-8ed5-10a5e426dab0', 'amount': 10, 'expire': '2023-11-02 18:49:21', 'order_id': '2', 'fail_url': 'https://t.me/emailfastbot', 'success_url': 'https://t.me/emailfastbot', 'hook_url': 'http://emailfast.site:5000/lava_good', 'custom_fields': None, 'include_service': None, 'exclude_service': None}, 'status': 200, 'status_check': True}
        # {'data': {'status': 'success', 'error_message': None, 'id': 'f9768502-84c9-4786-9a64-c86245c95f21', 'shop_id': 'a573a7c3-b7f4-46e7-8ed5-10a5e426dab0', 'amount': 10, 'expire': '2023-11-02 18:53:59', 'order_id': '3', 'fail_url': 'https://t.me/emailfastbot', 'success_url': 'https://t.me/emailfastbot', 'hook_url': 'http://emailfast.site:5000/lava_good', 'custom_fields': None, 'include_service': None, 'exclude_service': None}, 'status': 200, 'status_check': True}
        return await self._response(self.url.format(endpoint=endpoint), data)
