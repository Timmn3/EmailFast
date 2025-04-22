import json
from locale import currency
from loguru import logger
import aiohttp
from smsactivate.api import SMSActivateAPI
from yarl import URL


class SMSActivateAPIAsync(SMSActivateAPI):

    def __init__(self, api_key):
        super().__init__(api_key)
        self.__api_url = 'https://api.sms-activate.io/stubs/handler_api.php'
        self.__CODES = self._SMSActivateAPI__CODES

        self.__RENT_CODES = self._SMSActivateAPI__RENT_CODES

        self.__ERRORS = self._SMSActivateAPI__ERRORS

    def version(self):
        return "1.5"

    def check_error(self, response):
        if self.__ERRORS.get(response) == None:
            return False
        return True

    def get_error(self, error):
        return self.__ERRORS.get(error)

    def __debugLog(self, data):
        if self.debug_mode:
            print('[Debug]', data)

    def response(self, action, response):
        self.__debugLog(response)
        if self.check_error(response):
            return {"error": response, "message": self.get_error(response)}
        elif not str(response):
            return {"error": response, "message": "Server error, try again"}

        if action == "getNumbersStatus":
            result = json.loads(response)
            return result

        elif action == "getBalance":
            response = str(response[15:])
            result = {"balance": response}
            return result

        elif action == "getBalanceAndCashBack":
            response = str(response[15:])
            result = {"balance": response}
            return result

        elif action == "getNumber":
            # response = str(response[14:])
            # data = response.split(":")
            # activation_id = int(data[0])
            # phone = int(data[1])
            # result = {"activation_id": activation_id, "phone": phone}
            # return result

            result = json.loads(response)
            return result

        elif action == "getNumberV2":
            result = json.loads(response)
            return result

        elif action == "getMultiServiceNumber":
            result = json.loads(response)
            return result

        elif action == "getPrices":
            result = json.loads(response)
            return result

        elif action == "getCountries":
            result = json.loads(response)
            return result

        elif action == "getQiwiRequisites":
            result = json.loads(response)
            return result

        elif action == "getAdditionalService":
            response = str(response[11:])
            data = response.split(":")
            id = int(data[0])
            phone = int(data[1])
            result = {"id": id, "phone": phone}
            return result

        elif action == "getRentServicesAndCountries":
            result = json.loads(response)
            return result

        elif action == "getRentNumber":
            result = json.loads(response)
            return result

        elif action == "getRentStatus":
            result = json.loads(response)
            return result

        elif action == "setRentStatus":
            result = json.loads(response)
            return result
        elif action == "getRentList":
            result = json.loads(response)
            return result

        elif action == "continueRentNumber":
            result = json.loads(response)
            return result

        elif action == "getContinueRentPriceNumber":
            result = json.loads(response)
            return result

        elif action == "getTopCountriesByService":
            result = json.loads(response)
            return result

        elif action == "getIncomingCallStatus":
            result = json.loads(response)
            return result

        elif action == "getOperators":
            result = json.loads(response)
            return result

        elif action == "getActiveActivations":
            result = json.loads(response)
            return result

        elif action == "createTaskForCall":
            result = json.loads(response)
            if 'msg' in result:
                result['message'] = result.pop('msg')
            return result
        elif action == "getOutgoingCalls":
            result = json.loads(response)
            return result
        elif action == "getExtraActivation":
            # Пример обработки ответа для getExtraActivation
            response = str(response[15:])
            data = response.split(":")
            if data[0] == "ACCESS_NUMBER":
                activation_id = int(data[1])
                phone = int(data[2])
                result = {"activation_id": activation_id, "phone": phone}
            else:
                result = {"error": response}
            return result

        elif action == "checkExtraActivation":
            # Пример обработки ответа для checkExtraActivation
            result = json.loads(response)
            return result
        else:
            return response

    def activationStatus(self, status):
        return {"status": status, "message": self.__CODES.get(status)}

    def rentStatus(self, status):
        return self.__RENT_CODES.get(status)

    async def get_request(self, url, params):
        # Создаем URL с параметрами
        full_url = URL(url).with_query(params)

        # Логгируем полный URL запроса
        logger.info(f"[SMSActivateAPI] Sending request: {full_url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(str(full_url)) as response:
                response_text = await response.text()
                logger.info(f"[SMSActivateAPI] Response: {response_text}")
                return response_text

    async def getBalance(self):
        payload = {'api_key': self.api_key, 'action': 'getBalance'}
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getBalance", data)

    async def getBalanceAndCashBack(self):
        payload = {'api_key': self.api_key, 'action': 'getBalanceAndCashBack'}
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getBalanceAndCashBack", data)

    async def getNumbersStatus(self, country=None, operator=None):
        payload = {'api_key': self.api_key, 'action': 'getNumbersStatus'}
        if country is not None:
            payload['country'] = country
        if operator:
            payload['operator'] = operator
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getNumbersStatus", data)

    async def getPricesVerification(self, service=None):
        payload = {'api_key': self.api_key, 'action': 'getPricesVerification'}
        if service:
            payload['service'] = service
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getPricesVerification", data)

    async def getNumber(self, service=None, forward=None, freePrice=None, maxPrice=None, phoneException=None,
                        operator=None, ref=None, country=None, verification=None):
        payload = {'api_key': self.api_key, 'action': 'getNumberV2'}
        if service:
            payload['service'] = service
        if forward:
            payload['forward'] = forward
        if freePrice:
            payload['freePrice'] = freePrice
        if maxPrice:
            payload['maxPrice'] = maxPrice
        if phoneException:
            payload['phoneException'] = phoneException
        if operator:
            payload['operator'] = operator
        if ref:
            payload['ref'] = ref
        if country is not None:
            payload['country'] = country
        if verification:
            payload['verification'] = verification

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getNumber", data)

    async def getNumberV2(self, service=None, forward=None, freePrice=None, maxPrice=None, phoneException=None,
                          operator=None,
                          ref=None, country=None, verification=None):
        payload = {'api_key': self.api_key, 'action': 'getNumberV2'}
        if service:
            payload['service'] = service
        if forward:
            payload['forward'] = forward
        if freePrice:
            payload['freePrice'] = freePrice
        if maxPrice:
            payload['maxPrice'] = maxPrice
        if phoneException:
            payload['phoneException'] = phoneException
        if operator:
            payload['operator'] = operator
        if ref:
            payload['ref'] = ref
        if country is not None:
            payload['country'] = country
        if verification:
            payload['verification'] = verification

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getNumberV2", data)

    async def getMultiServiceNumber(self, service=None, forward=None, operator=None, ref=None, country=None):
        payload = {'api_key': self.api_key, 'action': 'getMultiServiceNumber'}
        if service:
            payload['multiService'] = service
        if forward:
            payload['forward'] = forward
        if operator:
            payload['operator'] = operator
        if ref:
            payload['ref'] = ref
        if country is not None:
            payload['country'] = country
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getMultiServiceNumber", data)

    async def setStatus(self, id=None, forward=None, status=None, ):
        # print(f'Setting status: id: {id}, forward: {forward}, status: {status}')
        payload = {'api_key': self.api_key, 'action': 'setStatus'}
        if id:
            payload['id'] = id
        if forward:
            payload['forward'] = forward
        if status:
            payload['status'] = status
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("setStatus", data)

    async def getStatus(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getStatus'}
        if id:
            payload['id'] = id
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getStatus", data)

    async def getFullSms(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getFullSms'}
        if id:
            payload['id'] = id
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getFullSms", data)

    async def getPrices(self, service=None, country=None):
        payload = {'api_key': self.api_key, 'action': 'getPrices'}
        if service:
            payload['service'] = service
        if country is not None:
            payload['country'] = country
        data = await self.get_request(self.__api_url, params=payload)
        price = self.response("getPrices", data)
        return price

    async def getCountries(self):
        payload = {'api_key': self.api_key, 'action': 'getCountries'}
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getCountries", data)

    async def get_top_countries_by_service(self, service, free_price=True):
        payload = {
            'api_key': self.api_key,
            'action': 'getTopCountriesByService',
            'service': service,
            'freePrice': 'true' if free_price else 'false',
            # 'currency': '643' # присылает в рублях
        }
        data = await self.get_request(self.__api_url, params=payload)
        top_countries = self.response("getTopCountriesByService", data)
        return top_countries

    async def getAdditionalService(self, service=None, id=None):
        payload = {'api_key': self.api_key, 'action': 'getAdditionalService'}
        if service:
            payload['service'] = service
        if id:
            payload['id'] = id
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getAdditionalService", data)

    async def getQiwiRequisites(self):
        payload = {'api_key': self.api_key, 'action': 'getQiwiRequisites'}
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getQiwiRequisites", data)

    async def getRentServicesAndCountries(self, time=None, operator=None, country=None):
        payload = {'api_key': self.api_key, 'action': 'getRentServicesAndCountries'}
        if time:
            payload['time'] = time
        if operator:
            payload['operator'] = operator
        if country is not None:
            payload['country'] = country

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getRentServicesAndCountries", data)

    async def getRentNumber(self, service=None, time=None, operator=None, country=None, url=None):
        payload = {'api_key': self.api_key, 'action': 'getRentNumber'}
        if service:
            payload['service'] = service
        if time:
            payload['time'] = time
        if operator:
            payload['operator'] = operator
        if country is not None:
            payload['country'] = country
        if url:
            payload['url'] = url

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getRentNumber", data)

    async def getRentStatus(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getRentStatus'}
        if id:
            payload['id'] = id

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getRentStatus", data)

    async def setRentStatus(self, id=None, status=None):
        payload = {'api_key': self.api_key, 'action': 'setRentStatus'}
        if id:
            payload['id'] = id
        if status:
            payload['status'] = status

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("setRentStatus", data)

    async def getRentList(self):
        payload = {'api_key': self.api_key, 'action': 'getRentList'}
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getRentList", data)

    async def continueRentNumber(self, id=None, time=None):
        payload = {'api_key': self.api_key, 'action': 'continueRentNumber'}
        if id:
            payload['id'] = id
        if time:
            payload['rent_time'] = time

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("continueRentNumber", data)

    async def getContinueRentPriceNumber(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getContinueRentPriceNumber'}
        if id:
            payload['id'] = id

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getContinueRentPriceNumber", data)

    async def getTopCountriesByService(self, service=None, freePrice=None):
        payload = {'api_key': self.api_key, 'action': 'getTopCountriesByService'}
        if service:
            payload['service'] = service
        if freePrice:
            payload['freePrice'] = freePrice

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getTopCountriesByService", data)

    async def getIncomingCallStatus(self, id=None):
        payload = {'api_key': self.api_key, 'action': 'getIncomingCallStatus'}
        if id:
            payload['activationId'] = id

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getIncomingCallStatus", data)

    async def getOperators(self, country=None):
        payload = {'api_key': self.api_key, 'action': 'getOperators'}
        if country is not None:
            payload['country'] = country

        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getOperators", data)

    async def getActiveActivations(self):
        payload = {'api_key': self.api_key, 'action': 'getActiveActivations'}
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getActiveActivations", data)

    async def createTaskForCall(self, activationId):
        payload = {'api_key': self.api_key, 'action': 'createTaskForCall'}
        payload['activationId'] = activationId
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("createTaskForCall", data)

    async def getOutgoingCalls(self, activationId=None, date=None):
        payload = {'api_key': self.api_key, 'action': 'getOutgoingCalls'}
        if activationId is not None:
            payload['activationId'] = activationId
        if date is not None:
            payload['date'] = date
        data = await self.get_request(self.__api_url, params=payload)
        return self.response("getOutgoingCalls", data)

    async def getExtraActivation(self, activationId):
        """
        Дополнительная активация номера.

        Если вы совершили успешную активацию на номере, то вы можете совершить её повторно.

        Параметры:
        - activationId: ID родительской активации

        Возвращает:
        - JSON объект с результатом активации или ошибкой
        """
        # Формируем payload с необходимыми параметрами
        payload = {'api_key': self.api_key, 'action': 'getExtraActivation', 'activationId': activationId}

        # Выполняем асинхронный GET запрос к API
        data = await self.get_request(self.__api_url, params=payload)

        # Обрабатываем и возвращаем ответ API
        return self.response("getExtraActivation", data)

    async def checkExtraActivation(self, activationId):
        """
        Получить цену за дополнительную активацию номера.

        Вы можете узнать доступность номера для совершения дополнительной активации и получить её стоимость.

        Параметры:
        - activationId: ID родительской активации

        Возвращает:
        - JSON объект с информацией о стоимости и доступности активации или ошибкой
        """
        # Формируем payload с необходимыми параметрами
        payload = {'api_key': self.api_key, 'action': 'checkExtraActivation', 'activationId': activationId}

        # Выполняем асинхронный GET запрос к API
        data = await self.get_request(self.__api_url, params=payload)

        # Обрабатываем и возвращаем ответ API
        return self.response("checkExtraActivation", data)
