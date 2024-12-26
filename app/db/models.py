from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
import pytz
from aiogram import types
from tortoise.models import Model
from tortoise import fields, timezone


class StatusResponse(IntEnum):
    STATUS_WAIT_CODE = 1
    STATUS_WAIT_RETRY = 2
    STATUS_WAIT_RESEND = 3
    STATUS_CANCEL = 4
    STATUS_OK = 5


class ActivationResponse(Enum):
    ACCESS_READY = 1
    ACCESS_RETRY_GET = 2
    ACCESS_ACTIVATION = 3
    ACCESS_CANCEL = 4


class ActivationCode(Enum):
    SUCCESS = 1
    RETRY_GET = 3
    FINISH = 6
    CANCEL = 8


class PaymentMethod(Enum):
    LAVA = 'lava'
    PAYOK = 'payok'
    FREEKASSA = 'freekassa'
    YOOMONEY = 'yoomoney'
    ANYPAY = 'anypay'
    STREAMPAY = 'streampay'
    CKASSA = 'ckassa'


class User(Model):
    class Meta:
        table = "users"
        table_description = "Users"
        ordering = ["id"]

    id: int = fields.BigIntField(pk=True)
    telegram_id: int = fields.BigIntField(unique=True)
    full_name: str = fields.CharField(max_length=128)
    username: str = fields.CharField(max_length=64, null=True)
    mention: str = fields.CharField(max_length=64, null=True)
    balance: float = fields.FloatField(default=0)
    ref_balance: float = fields.FloatField(default=0)
    total_ref_earnings: float = fields.FloatField(default=0)
    refer_id: int = fields.BigIntField(null=True)
    bonus_end_at: datetime = fields.DatetimeField(null=True)
    in_channel: bool = fields.BooleanField(default=False)
    last_check_in: datetime = fields.DatetimeField(null=True)
    created_at: datetime = fields.DatetimeField(auto_now_add=True)
    discount_used: bool = fields.BooleanField(null=True)
    last_request_time: datetime = fields.DatetimeField(null=False)  # Новое поле

    @classmethod
    async def add_user(cls, user: types.User, refer: types.User = None):
        """
        Добавляет нового пользователя в базу данных.

        :param user: Объект пользователя из aiogram.
        :param refer: Объект пользователя, который пригласил нового пользователя (опционально).
        :return: Созданный объект пользователя.
        """
        current_time = datetime.now()  # Получаем текущее время в UTC
        new_user = await cls.create(
            telegram_id=user.id,
            full_name=user.full_name,
            username=user.username,
            mention=f'@{user.username}' if user.username else user.full_name,
            refer_id=refer.id if refer else None,
            last_request_time=current_time  # Устанавливаем текущее время в last_request_time
        )
        return new_user

    @classmethod
    async def get_user(cls, telegram_id: int):
        """
        Получает пользователя по его Telegram ID.

        :param telegram_id: Telegram ID пользователя.
        :return: Объект пользователя или None, если пользователь не найден.
        """
        return await cls.get_or_none(telegram_id=telegram_id)

    def __str__(self):
        return self.mention


class Country(Model):
    class Meta:
        table = "countries"
        table_description = "Countries"
        ordering = ["id"]

    id: int = fields.IntField(pk=True)
    country_id: int = fields.IntField(unique=True, index=True)
    name: str = fields.CharField(max_length=128, unique=True)

    def to_dict(self):
        """
        Преобразует объект страны в словарь.

        :return: Словарь с данными страны.
        """
        return {
            'country_id': self.country_id,
            'name': self.name
        }

    @classmethod
    async def add_country(cls, country_id: int, name: str):
        """
        Добавляет новую страну в базу данных.

        :param country_id: Уникальный идентификатор страны.
        :param name: Название страны.
        :return: Созданный объект страны.
        """
        country = await cls.create(
            country_id=country_id,
            name=name
        )
        return country

    @classmethod
    async def get_country_id_by_name(cls, name: str) -> int:
        """
        Получает уникальный идентификатор страны по её названию.

        :param name: Название страны.
        :return: Уникальный идентификатор страны или None, если страна не найдена.
        """
        country = await cls.get_or_none(name=name)
        if country:
            return country.country_id
        return 0

    @classmethod
    async def get_country_by_id(cls, country_id: int):
        """
        Получает страну по её уникальному идентификатору.

        :param country_id: Уникальный идентификатор страны.
        :return: Объект страны или None, если страна не найдена.
        """
        return await cls.get_or_none(country_id=country_id)

    @classmethod
    async def get_country_name_mapping(cls):
        """
        Получает словарь, где ключами являются идентификаторы стран, а значениями — их имена.

        :return: Словарь с идентификаторами стран в качестве ключей и именами стран в качестве значений.
        """
        countries = await cls.all()
        country_name_mapping = {country.country_id: country.name for country in countries}
        return country_name_mapping

    @classmethod
    async def get_country_id_list(cls):
        """
        Получает список всех идентификаторов стран.

        :return: Список идентификаторов стран.
        """
        return await cls.all().values_list('country_id', flat=True)

    @classmethod
    async def search_countries(cls, search_name: str):
        """
        Ищет страны по части названия.

        :param search_name: Часть названия страны для поиска.
        :return: Список имен стран, соответствующих поисковому запросу.
        """
        countries = await cls.filter(name__icontains=search_name).all()
        country_names = [country.name for country in countries]
        return country_names

    def __str__(self):
        return self.name


class CountryOnlinesim(Model):
    class Meta:
        table = "country_onlinesim"
        table_description = "Countries for Onlinesim"
        ordering = ["id"]

    id: int = fields.IntField(pk=True)
    country_id: int = fields.IntField(unique=True, index=True)
    name: str = fields.CharField(max_length=128, unique=True)

    @classmethod
    async def get_country_id_by_name(cls, name: str) -> int:
        """
        Получает уникальный идентификатор страны по её названию.

        :param name: Название страны.
        :return: Уникальный идентификатор страны или None, если страна не найдена.
        """
        country = await cls.get_or_none(name=name)
        if country:
            return country.country_id
        return 0

    @classmethod
    async def get_country_by_id(cls, country_id: int):
        """
        Получает страну по её уникальному идентификатору.

        :param country_id: Уникальный идентификатор страны.
        :return: Объект страны или None, если страна не найдена.
        """
        return await cls.get_or_none(country_id=country_id)

    @classmethod
    async def get_country_from_country_by_id(cls, country_id: int):
        """
        Получает страну по её уникальному идентификатору из CountryOnlinesim.

        :param country_id: Уникальный идентификатор страны.
        :return: Объект модели Country или None, если страна не найдена.
        """
        country_onlinesim = await cls.get_or_none(country_id=country_id)
        if not country_onlinesim:
            return None  # Страна не найдена в CountryOnlinesim

        # Ищем страну по имени в таблице Country
        country = await Country.get_or_none(name=country_onlinesim.name)
        return country

    @classmethod
    async def get_country_onlinesim(cls, country_id: int):
        """
        Получает страну по её уникальному идентификатору из CountryOnlinesim.

        :param country_id: Уникальный идентификатор страны.
        :return: Объект модели CountryOnlinesim или None, если страна не найдена.
        """
        country_onlinesim = await cls.get_or_none(country_id=country_id)
        if not country_onlinesim:
            return None  # Страна не найдена в CountryOnlinesim

        return country_onlinesim

    @classmethod
    async def get_country_name_mapping(cls):
        """
        Получает словарь, где ключами являются идентификаторы стран, а значениями — их имена.

        :return: Словарь с идентификаторами стран в качестве ключей и именами стран в качестве значений.
        """
        countries = await cls.all()
        country_name_mapping = {country.country_id: country.name for country in countries}
        return country_name_mapping

    @classmethod
    async def get_country_id_list(cls):
        """
        Получает список всех идентификаторов стран.

        :return: Список идентификаторов стран.
        """
        return await cls.all().values_list('country_id', flat=True)

    @classmethod
    async def search_countries(cls, search_name: str):
        """
        Ищет страны по части названия.

        :param search_name: Часть названия страны для поиска.
        :return: Список имен стран, соответствующих поисковому запросу.
        """
        countries = await cls.filter(name__icontains=search_name).all()
        country_names = [country.name for country in countries]
        return country_names

    def __str__(self):
        return self.name


class ServiceOnlinesim(Model):
    class Meta:
        table = "service_onlinesim"
        table_description = "Services by Country for Onlinesim"
        ordering = ["id"]

    id = fields.IntField(pk=True)  # ID сервиса
    country = fields.IntField(max_length=255, null=False)  # Название страны без связи
    price = fields.DecimalField(max_digits=10, decimal_places=2)  # Цена сервиса
    service_name = fields.CharField(max_length=255, null=False)  # Название сервиса
    slug = fields.CharField(max_length=50, null=False)  # Короткое название сервиса

    @classmethod
    async def add_service(cls, country: int, price: float, service_name: str, slug: str):
        """
        Добавляет новый сервис в базу данных для указанной страны.

        :param country: Название страны, к которой относится сервис.
        :param price: Цена сервиса.
        :param service_name: Название сервиса.
        :param slug: Короткое название сервиса.
        :return: Созданный объект сервиса.
        """
        service = await cls.create(
            country=country,
            price=price,
            service_name=service_name,
            slug=slug
        )
        return service

    @classmethod
    async def get_services_by_name(cls, service_name: str):
        """
        Получает список стран и цен для указанного сервиса.

        :param service_name: Название сервиса для поиска.
        :return: Список словарей с данными стран и цен.
        """
        return await cls.select().where(cls.service_name == service_name).dicts()

    @classmethod
    async def get_slug_by_service_name(cls, service_name: str):
        """
        Получает slug для указанного сервиса.

        :param service_name: Название сервиса для поиска.
        :return: Slug или None, если сервис не найден.
        """
        service = await cls.get_or_none(service_name=service_name)
        return service.slug if service else None

    @classmethod
    async def update_service(cls, country: str, service_name: str, price: float):
        """
        Обновляет цену сервиса для указанной страны.

        :param country: Название страны.
        :param service_name: Название сервиса.
        :param price: Новая цена сервиса.
        """
        service = await cls.get_or_none(service_name=service_name, country=country)
        if service:
            service.price = price
            await service.save()

    @classmethod
    async def get_price_by_country_and_service(cls, country: str, service_name: str):
        """
        Получает цену для указанного сервиса в указанной стране.

        :param country: Название страны.
        :param service_name: Название сервиса.
        :return: Цена сервиса или None, если сервис не найден.
        """
        service = await cls.get_or_none(country=country, service_name=service_name)
        return service.price if service else None

    @classmethod
    async def get_service_data(cls, slug: str) -> dict:
        """
        Получает данные по сервису: для каждой страны возвращает её название и цену на указанный сервис.

        :param slug: Название сервиса для поиска.
        :return: Словарь, где ключами являются названия стран, а значениями — цены на указанный сервис.
        """
        # Получаем все записи, соответствующие slug
        services = await cls.filter(slug=slug).all()

        # Получаем словарь country_id -> country_name из CountryOnlinesim
        country_name_mapping = await CountryOnlinesim.get_country_name_mapping()

        # Формируем результат в виде словаря
        result = {}
        for service in services:
            country_name = country_name_mapping.get(service.country)  # Получаем название страны по country_id
            if country_name:
                result[country_name] = service.price  # Добавляем в словарь

        return result

    @classmethod
    async def get_service(cls, service_code: str, country_id: int):
        """
        Получает сервис по его короткому названию (slug) и стране.

        :param service_code: Название сервиса.
        :param country_id: ID страны.
        :return: Объект сервиса или None, если сервис не найден.
        """
        return await cls.get_or_none(slug=service_code, country=country_id)


class Service(Model):
    class Meta:
        table = "services"
        table_description = "Services"
        ordering = ["id"]

    id: int = fields.IntField(pk=True)
    code: str = fields.CharField(max_length=128, unique=True, index=True)
    name: str = fields.CharField(max_length=128, null=True)
    search_names: str = fields.TextField(null=True)
    used_count: int = fields.IntField(default=0)

    @classmethod
    async def add_service(cls, code: str, name: str, search_names: str):
        """
        Добавляет новый сервис в базу данных.

        :param code: Уникальный код сервиса.
        :param name: Название сервиса.
        :param search_names: Поисковые названия сервиса.
        :return: Созданный объект сервиса.
        """
        service = await cls.create(
            code=code,
            name=name,
            search_names=search_names
        )
        return service

    @classmethod
    async def get_service(cls, code: str):
        """
        Получает сервис по его уникальному коду.

        :param code: Уникальный код сервиса.
        :return: Объект сервиса или None, если сервис не найден.
        """
        return await cls.get_or_none(code=code)

    @classmethod
    async def search_service(cls, search_name: str):
        """
        Ищет сервисы по части названия.

        :param search_name: Часть названия сервиса для поиска.
        :return: Список объектов сервисов, соответствующих поисковому запросу.
        """
        return await cls.filter(search_names__icontains=search_name).all()

    @classmethod
    async def get_codes_list(cls):
        """
        Получает список всех кодов сервисов.

        :return: Список кодов сервисов.
        """
        return await cls.all().values_list('code', flat=True)

    @classmethod
    async def get_services(cls):
        """
        Получает список всех названий сервисов.

        :return: Список названий сервисов.
        """
        services = await cls.all().values('code', 'name')
        return {"services": list(services)}

    @classmethod
    async def get_all_services_data(cls):
        """
        Получает все данные по всем сервисам.

        :return: Список словарей с данными всех сервисов.
        """
        services = await cls.all().values('id', 'code', 'name', 'search_names')
        return {"services": list(services)}

    @classmethod
    async def normalize_search_names(cls):
        """
        Приводит все строки search_names к нижнему регистру и записывает обратно в базу данных.
        """
        services = await cls.all()
        for service in services:
            if service.search_names:
                normalized_search_names = service.search_names.lower()
                service.search_names = normalized_search_names
                await service.save()

    @classmethod
    async def update_services(cls, services_data: list):
        """
        Обновляет сервисы в базе данных на основе предоставленного списка данных.

        :param services_data: Список словарей с данными для обновления сервисов.
        :return: Список обновленных объектов сервисов и список id объектов, которые не были найдены.
        """
        updated_services = []

        for data in services_data:
            # Проверяем, существует ли сервис с таким code
            existing_service = await cls.get_or_none(code=data['code'])

            if existing_service:
                # Обновляем существующий сервис
                existing_service.name = data['name']
                existing_service.search_names = data['search_names']
                await existing_service.save()
                updated_services.append(existing_service)
            else:
                # Создаем новый объект сервиса, если code не найден
                new_service = cls(
                    code=data['code'],
                    name=data['name'],
                    search_names=data['search_names'],
                )
                await new_service.save()

    @classmethod
    async def get_code_by_name(cls, name: str):
        """
        Получает уникальный код сервиса по его имени.

        :param name: Название сервиса для поиска.
        :return: Код сервиса или None, если сервис не найден.
        """
        service = await cls.get_or_none(name=name)
        if service:
            return service.code
        return None

    @classmethod
    async def get_service_by_id(cls, service_id: int):
        """
        Получает строку всех значений по-указанному id.

        :param service_id: ID сервиса.
        :return: Строка со всеми значениями сервиса или None, если сервис не найден.
        """
        service = await cls.get_or_none(id=service_id)
        if service:
            return f"ID: {service.id}, Code: {service.code}, Name: {service.name}, Search Names: {service.search_names}"
        return None

    @classmethod
    async def get_service_name_by_id(cls, service_id: int):
        """
        Получает название сервиса по его ID.

        :param service_id: ID сервиса.
        :return: Название сервиса или None, если сервис не найден.
        """
        service = await cls.get_or_none(id=service_id)
        if service:
            return service.name
        return None


class Mail(Model):
    class Meta:
        table = "mails"
        table_description = "Mails"
        ordering = ["id"]

    id: int = fields.BigIntField(pk=True)
    user: User = fields.ForeignKeyField('models.User', related_name='mails')
    email: str = fields.CharField(max_length=128, unique=True, index=True)
    old_messages_id: list = fields.JSONField(default=[])
    is_paid_mail: bool = fields.BooleanField(default=False)
    is_active: bool = fields.BooleanField(default=True)
    created_at: datetime = fields.DatetimeField(auto_now_add=True)
    expire_at: datetime = fields.DatetimeField()
    notification_sent: bool = fields.BooleanField(default=False)

    @classmethod
    async def add_mail(cls, user: User, email: str):
        """
        Добавляет новую почту в базу данных.

        :param user: Объект пользователя, которому принадлежит почта.
        :param email: Адрес электронной почты.
        :return: Созданный объект почты.
        """
        expire_at = timezone.now() + timedelta(minutes=30)
        mail = await cls.create(
            user=user,
            email=email,
            expire_at=expire_at
        )
        return mail

    @classmethod
    async def get_mail(cls, mail_id: str):
        """
        Получает почту по её уникальному идентификатору.

        :param mail_id: Уникальный идентификатор почты.
        :return: Объект почты или None, если почта не найдена.
        """
        return await cls.get_or_none(id=mail_id)

    @classmethod
    async def get_user_mails(cls, user: User):
        """
        Получает все почты пользователя.

        :param user: Объект пользователя.
        :return: Список объектов почт, принадлежащих пользователю.
        """
        return await cls.filter(user=user).all()

    @classmethod
    async def get_expired_mails(cls):
        """
        Получает все истекшие почты.

        :return: Список объектов истекших почт.
        """
        return await cls.filter(expire_at__lte=timezone.now()).all()


class Letter(Model):
    class Meta:
        table = "letters"
        table_description = "Letters"
        ordering = ["id"]

    id: int = fields.BigIntField(pk=True)
    user: User = fields.ForeignKeyField('models.User', related_name='letters')
    mail: Mail = fields.ForeignKeyField('models.Mail', related_name='letters')
    text: str = fields.TextField()
    created_at: datetime = fields.DatetimeField(auto_now_add=True)

    @classmethod
    async def add_letter(cls, user: User, mail: Mail, text: str):
        """
        Добавляет новое письмо в базу данных.

                :param user: Объект пользователя, который отправил письмо.
                :param mail: Объект почты, к которой относится письмо.
                :param text: Текст письма.
                :return: Созданный объект письма.
                """
        letter = await cls.create(
            user=user,
            mail=mail,
            text=text
        )
        return letter

    @classmethod
    async def get_letter(cls, letter_id: int):
        """
        Получает письмо по его уникальному идентификатору.

        :param letter_id: Уникальный идентификатор письма.
        :return: Объект письма или None, если письмо не найдено.
        """
        return await cls.get_or_none(id=letter_id)

    @classmethod
    async def get_user_letters(cls, user: User):
        """
        Получает все письма пользователя.

        :param user: Объект пользователя.
        :return: Список объектов писем, принадлежащих пользователю.
        """
        return await cls.filter(user=user).all()


class Activation(Model):
    class Meta:
        table = "activations"
        table_description = "Activations"
        ordering = ["id"]

    id: int = fields.BigIntField(pk=True)
    user: User = fields.ForeignKeyField('models.User', related_name='activations')
    activation_id: int = fields.BigIntField(unique=True, index=True)
    country: Country = fields.ForeignKeyField('models.Country', related_name='activations')
    service: Service = fields.ForeignKeyField('models.Service', related_name='activations')
    cost: float = fields.FloatField()
    phone_number: str = fields.CharField(max_length=32)
    sms_text: str = fields.TextField(null=True)
    status: StatusResponse = fields.IntEnumField(StatusResponse, default=StatusResponse.STATUS_WAIT_CODE)
    created_at: datetime = fields.DatetimeField(auto_now_add=True)
    activation_expire_at: datetime = fields.DatetimeField(null=True)

    @classmethod
    async def add_activation(cls, user: User, activation_id: int, country: Country, cost: float,
                             service: Service, phone_number: str, activation_expire_at: datetime):
        """
        Добавляет новую активацию в базу данных.

        :param user: Объект пользователя, который инициировал активацию.
        :param activation_id: Уникальный идентификатор активации.
        :param country: Объект страны, связанной с активацией.
        :param cost: Стоимость активации.
        :param service: Объект сервиса, связанного с активацией.
        :param phone_number: Номер телефона, используемый для активации.
        :param activation_expire_at: Время истечения активации.
        :return: Созданный объект активации.
        """
        activation = await cls.create(
            user=user,
            activation_id=activation_id,
            country=country,
            service=service,
            cost=cost,
            phone_number=phone_number,
            activation_expire_at=activation_expire_at
        )
        return activation

    @classmethod
    async def get_activation(cls, activation_id: int):
        """
        Получает активацию по её уникальному идентификатору.

        :param activation_id: Уникальный идентификатор активации.
        :return: Объект активации или None, если активация не найдена.
        """
        return await cls.get_or_none(activation_id=activation_id)

    @classmethod
    async def get_user_activations(cls, user: User):
        """
        Получает все активации пользователя.

        :param user: Объект пользователя.
        :return: Список объектов активаций, принадлежащих пользователю.
        """
        return await cls.filter(user=user).all()

    @classmethod
    async def get_expired_activations(cls):
        """
        Получает все истекшие активации (аренды, чей срок истек).

        :return: Список объектов истекших аренд.
        """
        utc_now = datetime.now(pytz.utc)
        return await cls.filter(activation_expire_at=utc_now, status=StatusResponse.STATUS_WAIT_CODE).all().prefetch_related('user')

    @classmethod
    async def get_active_activations(cls):
        """
        Получает все активные активации.

        :return: Список объектов активных активаций.
        """
        return await cls.filter(activation_expire_at__gt=timezone.now()).all()

    @classmethod
    async def get_active_activation(cls, user_id: int):
        """
        Получает активную активацию пользователя по его user_id, если она активна.

        :param user_id: Идентификатор пользователя.
        :return: Объект активации пользователя, если она активна, или None, если активация не найдена или истекла.
        """
        # Получаем текущее время в UTC
        utc_now = datetime.now(pytz.utc)

        # Получаем все активные активации пользователя
        active_activations = await cls.filter(user_id=user_id, activation_expire_at__gt=utc_now).all()

        # Если есть активные активации, возвращаем их
        if active_activations:
            return active_activations[-1]

        # Если активных активаций нет, возвращаем None
        return None

    @classmethod
    async def delete_user_activations(cls, user_id: int):
        """
        Удаляет все активации пользователя по его user_id.

        :param user_id: Идентификатор пользователя.
        :return: Количество удалённых записей.
        """
        deleted_count = await cls.filter(user_id=user_id).delete()
        return deleted_count


class Payment(Model):
    class Meta:
        # Метаданные для модели Payment
        table = "payments"  # Название таблицы в базе данных
        table_description = "Payments"  # Описание таблицы
        ordering = ["id"]  # Поле для сортировки записей по умолчанию

    # Поля модели Payment
    id: int = fields.BigIntField(pk=True)  # Уникальный идентификатор платежа (первичный ключ)
    user: User = fields.ForeignKeyField('models.User', related_name='payments')  # Связь с моделью User (внешний ключ)
    method: PaymentMethod = fields.CharEnumField(PaymentMethod, max_length=16)  # Метод оплаты (перечисление)
    amount: float = fields.FloatField()  # Сумма платежа
    order_id: str = fields.CharField(max_length=128, unique=True, index=True,
                                     null=True)  # Идентификатор заказа (уникальный)
    invoice_id: str = fields.CharField(max_length=128, unique=True, index=True,
                                       null=True)  # Идентификатор счета (уникальный)
    continue_data: dict = fields.JSONField(null=True)  # Дополнительные данные для продолжения платежа (JSON)
    is_success: bool = fields.BooleanField(default=False)  # Флаг успешности платежа
    created_at: datetime = fields.DatetimeField(
        auto_now_add=True)  # Дата и время создания записи (автоматически устанавливается при создании)

    @classmethod
    async def create_payment(cls, user: User, method: PaymentMethod, amount: float, continue_data: dict = None):
        """
        Создает новый платеж в базе данных.

        :param user: Объект пользователя, который инициировал платеж.
        :param method: Метод оплаты.
        :param amount: Сумма платежа.
        :param continue_data: Дополнительные данные для продолжения платежа (опционально).
        :return: Созданный объект платежа.
        """
        payment = await cls.create(
            user=user,
            method=method,
            amount=amount,
            continue_data=continue_data
        )
        return payment

    @classmethod
    async def get_last_payment_amount(cls, user_id: int) -> float:
        """
        Возвращает сумму последнего платежа для пользователя по его user_id.

        :param user_id: ID пользователя.
        :return: Сумма последнего платежа или None, если платежей нет.
        """
        last_payment = await cls.filter(user_id=user_id).order_by('-created_at').first()
        return last_payment.amount if last_payment else None

    @classmethod
    async def get_payments(cls, method, hours=5):
        """
        Получает все платежи, выполненные через указанный метод, которые не были успешными и были созданы в последние 'hours' часов.

        :param method: Метод платежной системы (PaymentMethod.LAVA, PaymentMethod.FREEKASSA, PaymentMethod.YOOMONEY)
        :param hours: Количество часов в прошлом, за которые нужно получить платежи (по умолчанию 5)
        :return: Список объектов платежей.
        """
        return await cls.filter(
            method=method,
            is_success=False,
            created_at__gt=timezone.now() - timedelta(hours=hours)
        ).all().prefetch_related('user')

    @classmethod
    async def get_lava_payments(cls):
        """
        Получает все платежи, выполненные через метод LAVA, которые не были успешными и были созданы в последние 5 часов

        :return: Список объектов платежей.
        """
        return await cls.filter(method=PaymentMethod.LAVA, is_success=False,
                                created_at__gt=timezone.now() - timedelta(hours=5),
                                order_id__isnull=False).all().prefetch_related('user')

    @classmethod
    async def get_freekassa_payments(cls):
        """
        Получает все платежи, выполненные через метод FREEKASSA, которые не были успешными и были созданы в последние 5 часов

        :return: Список объектов платежей.
        """
        return await cls.filter(method=PaymentMethod.FREEKASSA, is_success=False,
                                created_at__gt=timezone.now() - timedelta(hours=5)).all().prefetch_related('user')

    @classmethod
    async def get_yoomoney_payments(cls):
        """
        Получает все платежи, выполненные через метод YOOMONEY, которые не были успешными и были созданы в последние 5 часов

        :return: Список объектов платежей.
        """
        return await cls.filter(method=PaymentMethod.YOOMONEY, is_success=False,
                                created_at__gt=timezone.now() - timedelta(hours=5)).all().prefetch_related('user')

    @classmethod
    async def get_anypay_payments(cls):
        """
        Получает все платежи, выполненные через метод ANYPAY, которые не были успешными и были созданы в последние 5 часов

        :return: Список объектов платежей.
        """
        return await cls.filter(method=PaymentMethod.ANYPAY, is_success=False,
                                created_at__gt=timezone.now() - timedelta(hours=5)).all().prefetch_related('user')

    @classmethod
    async def get_streampay_payments(cls):
        """
        Получает все платежи, выполненные через метод STREAMPAY, которые не были успешными и были созданы в последние 5 часов

        :return: Список объектов платежей.
        """
        return await cls.filter(method=PaymentMethod.STREAMPAY, is_success=False,
                                created_at__gt=timezone.now() - timedelta(hours=5)).all().prefetch_related('user')

    @classmethod
    async def get_ckassa_payments(cls):
        """
        Получает все платежи, выполненные через метод CKASSA, которые не были успешными и были созданы в последние 5 часов

        :return: Список объектов платежей.
        """
        return await cls.filter(method=PaymentMethod.CKASSA, is_success=False,
                                created_at__gt=timezone.now() - timedelta(hours=5)).all().prefetch_related('user')


class Withdraw(Model):
    class Meta:
        table = "withdraws"
        table_description = "Withdraws"
        ordering = ["id"]

    id: int = fields.BigIntField(pk=True)
    user: User = fields.ForeignKeyField('models.User', related_name='withdraws')
    requisites: str = fields.TextField()
    amount: float = fields.FloatField()
    is_success: bool = fields.BooleanField(null=True)
    created_at: datetime = fields.DatetimeField(auto_now_add=True)

    @classmethod
    async def add_withdraw(cls, user: User, requisites: str, amount: float):
        """
        Добавляет новый запрос на вывод средств в базу данных.

        :param user: Объект пользователя, который инициировал запрос на вывод средств.
        :param requisites: Реквизиты для вывода средств.
        :param amount: Сумма для вывода.
        :return: Созданный объект запроса на вывод средств.
        """
        withdraw = await cls.create(
            user=user,
            requisites=requisites,
            amount=amount
        )
        return withdraw

    @classmethod
    async def get_withdraw(cls, withdraw_id: int):
        """
        Получает запрос на вывод средств по его уникальному идентификатору.

        :param withdraw_id: Уникальный идентификатор запроса на вывод средств.
        :return: Объект запроса на вывод средств или None, если запрос не найден.
        """
        return await cls.get_or_none(id=withdraw_id)


class PaymentLink(Model):
    class Meta:
        table = "payment_links"
        table_description = "PaymentLinks"
        ordering = ["id"]

    id: int = fields.BigIntField(pk=True)
    payment_link_id: str = fields.CharField(max_length=32, unique=True, index=True)
    user_id_list: list = fields.JSONField(default=[])
    amount: float = fields.FloatField()
    limit: int = fields.IntField()
    created_at: datetime = fields.DatetimeField(auto_now_add=True)

    @classmethod
    async def add_payment_link(cls, amount: float, limit: int, payment_link_id: str):
        """
        Добавляет новую ссылку на оплату в базу данных.

        :param amount: Сумма для оплаты.
        :param limit: Лимит использования ссылки.
        :param payment_link_id: Уникальный идентификатор ссылки на оплату.
        :return: Созданный объект ссылки на оплату.
        """
        payment_link = await cls.create(
            amount=amount,
            limit=limit,
            payment_link_id=payment_link_id
        )
        return payment_link

    @classmethod
    async def get_payment_link(cls, payment_link_id: str):
        """
        Получает ссылку на оплату по её уникальному идентификатору.

        :param payment_link_id: Уникальный идентификатор ссылки на оплату.
        :return: Объект ссылки на оплату или None, если ссылка не найдена.
        """
        return await cls.get_or_none(payment_link_id=payment_link_id)

    @classmethod
    async def get_payment_links(cls):
        """
        Получает все ссылки на оплату.

        :return: Список объектов ссылок на оплату.
        """
        return await cls.all()


class Rent(Model):
    class Meta:
        table = "rents"
        table_description = "Rents"
        ordering = ["id"]

    id: int = fields.BigIntField(pk=True)
    user: "User" = fields.ForeignKeyField('models.User', related_name='rents')
    rent_id: int = fields.BigIntField(unique=True, index=True)
    country: "CountryOnlinesim" = fields.ForeignKeyField("models.CountryOnlinesim", related_name='rents')
    cost: float = fields.FloatField()
    phone_number: str = fields.CharField(max_length=32)
    sms_text: str = fields.TextField(null=True)
    status: StatusResponse = fields.IntEnumField(StatusResponse, default=StatusResponse.STATUS_WAIT_CODE)
    created_at: datetime = fields.DatetimeField(auto_now_add=True)
    rent_expire_at: datetime = fields.DatetimeField(null=True)
    autorenew: bool = fields.BooleanField(default=False)  # Поле для автопродления
    is_canceled: bool = fields.BooleanField(default=False)  # Поле для отслеживания отмены аренды
    is_notified: bool = fields.BooleanField(default=False)  # Поле для отслеживания отправки уведомления
    days: int = fields.IntField(default=0)  # Поле для отслеживания количества дней аренды
    purchase_count: int = fields.IntField(default=0)  # Поле для отслеживания количества покупок

    @classmethod
    async def add_rent(cls, user: "User", rent_id: int, country: "CountryOnlinesim", cost: float,
                       phone_number: str, rent_expire_at: datetime, sms_text: str = "",
                       autorenew: bool = False, is_canceled: bool = False, is_notified: bool = False, days: int = 0,
                       purchase_count: int = 0):
        """
        Добавляет новую аренду в базу данных.

        :param user: Объект пользователя, который инициировал аренду.
        :param rent_id: Уникальный идентификатор аренды.
        :param country: Объект страны, связанной с арендой.
        :param cost: Стоимость аренды.
        :param phone_number: Номер телефона, используемый для аренды.
        :param sms_text: СМС сообщение.
        :param rent_expire_at: Время истечения аренды.
        :param autorenew: Статус автопродления (по умолчанию False).
        :param is_canceled: Статус отмены аренды (по умолчанию False).
        :param is_notified: Статус уведомления (по умолчанию False).
        :param days: Количество дней аренды (по умолчанию 0).
        :param purchase_count: Счетчик покупок (по умолчанию 0).
        :return: Созданный объект аренды.
        """
        rent, created = await cls.update_or_create(
            rent_id=rent_id,  # Параметр для поиска аренды
            defaults={  # Параметры для обновления или создания аренды
                "user": user,
                "country": country,
                "cost": cost,
                "phone_number": phone_number,
                "sms_text": sms_text,
                "rent_expire_at": rent_expire_at,
                "autorenew": autorenew,
                "is_canceled": is_canceled,
                "is_notified": is_notified,
                "days": days,  # Устанавливаем значение нового поля
                "purchase_count": purchase_count,  # Устанавливаем значение счетчика покупок
            }
        )
        return rent


    @classmethod
    async def get_rent(cls, id: int):
        """
        Получает аренду по её уникальному идентификатору.

        :param id: Уникальный идентификатор аренды.
        :return: Объект аренды или None, если аренда не найдена.
        """
        return await cls.get_or_none(id=id).select_related('country')

    @classmethod
    async def get_user_rents(cls, user: "User"):
        """
        Получает все аренды пользователя.

        :param user: Объект пользователя.
        :return: Список объектов аренд, принадлежащих пользователю.
        """
        return await cls.filter(user=user).all()

    @classmethod
    async def get_expired_rents(cls):
        """
        Получает все истекшие аренды.

        :return: Список объектов истекших аренд.
        """
        utc_now = datetime.now(pytz.timezone("Europe/Moscow"))
        return await cls.filter(rent_expire_at__lte=utc_now,
                                status=StatusResponse.STATUS_WAIT_CODE).all().prefetch_related('user')

    @classmethod
    async def get_active_rent(cls, user_id: int):
        """
        Получает активные аренды пользователя по его user_id, если она активна и не отменена.

        :param user_id: Идентификатор пользователя.
        :return: Объекты аренды пользователя, если она активна и не отменена, или None, если аренда не найдена, истекла или отменена.
        """

        # Фильтруем аренды по user_id, дате окончания аренды и статусу отмены
        utc_now = datetime.now(pytz.timezone("Europe/Moscow"))
        active_rents = await cls.filter(user_id=user_id, rent_expire_at__gt=utc_now, is_canceled=False).all()

        if active_rents:
            return active_rents
        return None

    @classmethod
    async def delete_user_rents(cls, user_id: int):
        """
        Удаляет все аренды пользователя по его user_id.

        :param user_id: Идентификатор пользователя.
        :return: Количество удалённых записей.
        """
        deleted_count = await cls.filter(user_id=user_id).delete()
        return deleted_count

    @classmethod
    async def is_autorenew_enabled(cls, rent_id: int) -> bool:
        """
        Проверяет, включено ли автопродление для указанной аренды.

        :param rent_id: Уникальный идентификатор аренды.
        :return: True, если автопродление включено, иначе False.
        """
        rent = await cls.get_or_none(rent_id=rent_id)
        if rent:
            return rent.autorenew
        return False

    @classmethod
    async def get_country_by_rent(cls, id: int) -> "CountryOnlinesim":
        """
        Получает объект CountryOnlinesim, связанный с указанной арендой.

        :param id: Уникальный идентификатор аренды.
        :return: Объект CountryOnlinesim, связанный с арендой, или None, если аренда не найдена.
        """
        rent = await cls.get_or_none(id=id).prefetch_related('country')
        return rent.country if rent else None

    @classmethod
    async def get_all_active_rents(cls):
        """
        Получает список всех активных аренд.

        :return: Список объектов активных аренд.
        """
        utc_now = datetime.now(pytz.timezone("Europe/Moscow"))
        return await cls.filter(rent_expire_at__gte=utc_now).all().prefetch_related('user')

    @classmethod
    async def get_expired_activations(cls):
        """
        Получает все истекшие активации.

        :return: Список объектов истекших активаций.
        """
        utc_now = datetime.now(pytz.timezone("Europe/Moscow"))
        return await cls.filter(rent_expire_at__lt=utc_now, status=StatusResponse.STATUS_WAIT_CODE).all().prefetch_related('user')

    @classmethod
    async def get_rents_ending_soon(cls):
        """
        Получает список аренд, для которых срок истекает ровно через 5 часов,
        и уведомление еще не было отправлено.
        """
        utc_now = datetime.now(pytz.timezone("Europe/Moscow"))
        target_time = utc_now + timedelta(hours=5)

        # Фильтруем аренды, срок действия которых заканчивается через 5 часов, и уведомление еще не отправлено
        return await cls.filter(
            rent_expire_at__lte=target_time,
            rent_expire_at__gt=utc_now,
            is_canceled=False,
            is_notified=False,
            sms_text__isnull=False
        ).exclude(
            sms_text=""
        ).all().prefetch_related("user", "country")

    @classmethod
    async def get_rents_expire(cls):
        """
        Получает список аренд, для которых срок истекает ровно через 2 часа,
        и уведомление еще не было отправлено.
        """
        utc_now = datetime.now(pytz.timezone("Europe/Moscow"))
        target_time = utc_now + timedelta(hours=2)

        # Фильтруем аренды, срок действия которых заканчивается через 5 часов, и уведомление еще не отправлено
        return await cls.filter(
            rent_expire_at__lte=target_time,
            rent_expire_at__gt=utc_now,
            is_canceled=False,
            sms_text__isnull=False
        ).exclude(
            sms_text=""
        ).all().prefetch_related("user", "country")


    @classmethod
    async def close_rent_before_end(cls):
        """
        Получает список аренд, для которых срок истекает ровно через 5 минут
        """
        utc_now = datetime.now(pytz.timezone("Europe/Moscow"))
        target_time = utc_now + timedelta(minutes=5)

        # Фильтруем аренды, срок действия которых заканчивается через 5 минут, и уведомление еще не отправлено
        return await cls.filter(
            rent_expire_at__lte=target_time,
            rent_expire_at__gt=utc_now,
            is_canceled=False,
            autorenew=False,
            sms_text__isnull=False
        ).exclude(
            sms_text=""
        ).all().prefetch_related("user", "country")


    @classmethod
    async def inactive_rent(cls, tzid: int):
        """
        Получает аренду по её уникальному идентификатору tzid.

        :param tzid: Уникальный идентификатор аренды.
        :return: Объект аренды или None, если аренда не найдена.
        """
        return await cls.filter(rent_id=tzid, is_canceled=True, status=StatusResponse.STATUS_WAIT_CODE).first()