from typing import Union
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram_dialog import DialogManager, StartMode
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Select, Button
import re
from app.db import models
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.dialogs.receive_sms.scheduler_balance import start_balance_check
from app.dialogs.receive_sms.states import CountryMenu
from app.dialogs.rent_sms.scheduler_balance import start_balance_check_rent
from app.dialogs.rent_sms.selected import rent_number_in_days
from app.dialogs.rent_sms.states import RentCountryMenu
from app.handlers.affiliate_program import send_affiliate_message
from app.services import bot_texts as bt
from app.services.payments.anypay import AnypayAPI
from app.services.bot_texts import FOLLOW_THE_LINK_TO_PAY
from app.services.payments.ckassa import create_invoice_ckassa
from app.services.payments.cryptomus import link_to_cryptomus
from app.services.payments.freekassa import generate_fk_link
from app.services.payments.lava import LavaApi
from app.services.payments.streampay import create_payment_streampay
from loguru import logger


async def on_deposit(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Пополнить баланс".
    Переключает состояние диалога на меню пополнения баланса.
    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_deposit').log("USER_ACTION", "Переход к пополнению баланса")
        await manager.done()
        await manager.start(PersonalMenu.deposit)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_deposit: {e}")


async def affiliate(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик кнопки "Партнерская программа".
    Вызывает функцию send_affiliate_message с передачей сообщения из CallbackQuery.
    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='affiliate').log("USER_ACTION", "Открытие партнерской программы")
        await send_affiliate_message(m=c.message, user_id=user_id)
        await c.answer()
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в Открытие партнерской программы: {e}")


async def on_payment_method(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_payment_method').log("USER_ACTION", "Выбор способа оплаты")
        await send_payment_keyboard(c, manager=manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_payment_method: {e}")


async def on_deposit_new(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Новое пополнение".
    Переключает состояние диалога на меню пополнения баланса с сохранением текущих данных.
    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_deposit_new').log("USER_ACTION", "Запуск нового пополнения")
        ctx = manager.current_context()
        await manager.start(PersonalMenu.deposit, data=ctx.dialog_data)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_deposit_new: {e}")


async def on_deposit_state(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "пополнение с установленным значением".
    Переключает состояние диалога на меню пополнения баланса с сохранением текущих данных.
    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_deposit_state').log("USER_ACTION", "Выбрана фиксированная сумма")
        ctx = manager.current_context()
        price = ctx.dialog_data['cost']
        ctx.dialog_data['price'] = price
        await send_payment_keyboard(c, manager=manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_deposit_state: {e}")


async def on_deposit_price(c: types.CallbackQuery, widget: Select, manager: DialogManager, price_id: str):
    """
    Обработчик для выбора суммы пополнения.
    Отправляет клавиатуру для выбора способа оплаты.
    :param c: Объект CallbackQuery.
    :param widget: Объект Select.
    :param manager: Объект DialogManager.
    :param price_id: Идентификатор выбранной суммы.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_deposit_price').log("USER_ACTION", f"Выбрана сумма: {price_id}")
        await c.message.delete()
        price = bt.prices_data[int(price_id) - 1]['price']
        ctx = manager.current_context()
        ctx.dialog_data['price'] = price
        await send_payment_keyboard(c, manager=manager, price=price)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_deposit_price: {e}")


async def on_other_price(c: types.CallbackQuery, widget: Button, manager: DialogManager):
    """
    Обработчик для кнопки "Другая сумма".
    Переключает состояние диалога на ввод суммы пополнения.
    :param c: Объект CallbackQuery.
    :param widget: Объект Button.
    :param manager: Объект DialogManager.
    """
    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='on_other_price').log("USER_ACTION", "Выбрана произвольная сумма")
        await switch_state(manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_other_price: {e}")


async def switch_state(manager: DialogManager):
    # Получаем название группы состояний
    state = manager.current_context().state
    # Проверяем группу состояний и переключаемся на нужное состояние
    if state == 'PersonalMenu:deposit':
        await manager.switch_to(PersonalMenu.enter_amount)
    elif state == 'CountryMenu:deposit':
        await manager.switch_to(CountryMenu.enter_amount)
    elif state == 'RentCountryMenu:deposit':
        await manager.switch_to(RentCountryMenu.enter_amount)


async def on_enter_other_price(m: types.Message, widget: TextInput, manager: DialogManager, price_text: str):
    """
    Обработчик для ввода другой суммы пополнения.
    Проверяет корректность введенной суммы и отправляет клавиатуру для выбора способа оплаты.
    :param m: Объект Message.
    :param widget: Объект TextInput.
    :param manager: Объект DialogManager.
    :param price_text: Введенная сумма пополнения.
    """
    try:
        user_id = m.from_user.id
        logger.bind(user_id=user_id, action='on_enter_other_price').log("USER_ACTION", f"Введена сумма: {price_text}")

        if not price_text.isdigit():
            await m.answer(text='Сумма должна быть числом')
            await switch_state(manager)
            return
        price = int(price_text)
        if price < 50:
            await m.answer(text='Минимальная сумма - 50₽')
            await switch_state(manager)
            return
        ctx = manager.current_context()
        ctx.dialog_data['price'] = price
        await send_payment_keyboard(m, manager=manager, price=price)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в on_enter_other_price: {e}")


async def send_payment_keyboard(m: Union[types.Message, types.CallbackQuery], manager: DialogManager = None,
                                price: float = None):
    """
    Отправляет клавиатуру для выбора способа оплаты.
    :param m: Объект Message или CallbackQuery.
    :param manager: Объект DialogManager.
    :param price: Сумма пополнения.
    """

    user_id = m.from_user.id
    logger.bind(user_id=user_id, action='send_payment_keyboard').log("USER_ACTION", f"Формирование платежной ссылки")

    if manager:
        ctx = manager.current_context()
        price = float(ctx.dialog_data['price'])
        print(price)
        continue_data = ctx.start_data
    else:
        ctx = None
        continue_data = None

    user = await models.User.get_user(user_id)

    # формируем ссылку на оплату Lava
    try:
        lava = LavaApi()
        lava_payment = await models.Payment.create_payment(
            user=user,
            method=models.PaymentMethod.LAVA,
            amount=price,
            continue_data=continue_data
        )
        order_id = f'sms_email_:{lava_payment.id}'
        response = await lava.create_invoice(price, order_id=order_id)
        invoice_id = response['data']['id']
        lava_payment.invoice_id = invoice_id
        lava_payment.order_id = order_id
        await lava_payment.save()
        lava_url = response['data']['url']
    except Exception:
        lava_url = None

    payment_freekassa = await models.Payment.create_payment(
        user=user,
        method=models.PaymentMethod.FREEKASSA,
        amount=price,
        continue_data=continue_data
    )
    # формируем ссылку на оплату freekassa
    other_url = generate_fk_link(price, payment_freekassa.id)

    payment_cryptomus = await models.Payment.create_payment(
        user=user,
        method=models.PaymentMethod.CRYPTOMUS,
        amount=price,
        continue_data=continue_data
    )
    # формируем ссылку на оплату cryptomus
    cryptomus_url = link_to_cryptomus(price, payment_cryptomus.id)

    # payment_yoomoney = await models.Payment.create_payment(
    #     user=user,
    #     method=models.PaymentMethod.YOOMONEY,
    #     amount=price,
    #     continue_data=continue_data
    # )


    # формируем ссылку на оплату api_yoomoney
    # yoomoney_url = await create_yoomoney_url(price, payment_yoomoney.id)
    # --- StreamPay (bank card) ---
    streampay_url = ''
    if price >= 300:
        try:
            payment_streampay = await models.Payment.create_payment(
                user=user,
                method=models.PaymentMethod.STREAMPAY,
                amount=price,
                continue_data=continue_data
            )

            # ✅ external_id должен быть уникальным для каждого платежа
            external_id = f"sms_email_{payment_streampay.id}"

            payment_streampay.invoice_id, streampay_url = await create_payment_streampay(price, external_id)
            await payment_streampay.save()

        except Exception as e:
            # ✅ НЕ РОНЯЕМ UX: просто выключаем этот метод оплаты и пишем предупреждение
            logger.opt(exception=e).warning(
                f"StreamPay недоступен (создание инвойса). price={price}"
            )
            streampay_url = ''

    # --- CKassa ---
    if price >= 50:
        payment_ckassa = await models.Payment.create_payment(
            user=user,
            method=models.PaymentMethod.CKASSA,
            amount=price,
            continue_data=continue_data
        )
        payment_ckassa.invoice_id, ckassa_url = await create_invoice_ckassa(price, str(payment_ckassa.user.telegram_id))
        await payment_ckassa.save()
    else:
        ckassa_url = ''

    state = manager.current_context().state.group.__name__

    # Передаем URL-адреса в контекстное состояние
    country_id = ''
    service_code = ''
    service_price = ''
    rent_country_code = ''
    selected_country = ''
    day_index = ''
    retail_price = ''

    if state == 'CountryMenu':
        country_id = ctx.dialog_data.get('country_id', '')
        service_code = ctx.dialog_data.get('service_code', '')
        service_price = ctx.dialog_data.get('service_price')
        retail_price = ctx.dialog_data.get('retail_price', '')
        if price >= 300:
            await manager.start(CountryMenu.payment_method, mode=StartMode.NORMAL, data={})
        else:
            await manager.start(CountryMenu.payment_method_minimum_pay, mode=StartMode.NORMAL, data={})
    elif state == 'RentCountryMenu':
        if ctx and 'rent_country_code' in ctx.dialog_data:
            rent_country_code = ctx.dialog_data.get('rent_country_code', '')
            selected_country = ctx.dialog_data.get('selected_country', '')
            day_index = ctx.dialog_data.get('day_index', '')
        else:
            rent_country_code = ''
            selected_country = ''
            day_index = ''
        if price >= 300:
            await manager.start(RentCountryMenu.payment_method, mode=StartMode.NORMAL, data={})
        else:
            await manager.start(RentCountryMenu.payment_method_minimum_pay, mode=StartMode.NORMAL, data={})
    else:
        if price >= 300:
            await manager.start(PersonalMenu.payment_method, mode=StartMode.NORMAL, data={})
        else:
            await manager.start(PersonalMenu.payment_method_minimum_pay, mode=StartMode.NORMAL, data={})

    # Получаем текущий контекст и обновляем dialog_data
    ctx = manager.current_context()
    ctx.dialog_data.update({
        'ckassa_url': ckassa_url,
        'bank_card_url': streampay_url,
        'SBP': lava_url if lava_url is not None else other_url,
        'yoomoney_url': other_url,
        'stars': price,
        'crypto_url': cryptomus_url,
        'other_url': other_url,
        'price': price,
        'country_id': country_id,
        'service_code': service_code,
        'service_price': service_price,
        'rent_country_code': rent_country_code,
        'selected_country': selected_country,
        'day_index': day_index,
        'retail_price': retail_price,
    })


async def switch_to_payment(c: types.CallbackQuery, button: Button, manager: DialogManager):

    user_id = c.from_user.id

    current_context = manager.current_context()
    url_key = f"{button.widget_id}_url"
    url = current_context.dialog_data.get(url_key)
    url_pattern = re.compile(r'https?://[^\s]+')

    logger.bind(user_id=user_id, action='switch_to_payment').log("USER_ACTION", f"Переход к оплате {url}")

    if not url or not url_pattern.match(url):
        await c.answer(text=bt.ERROR_PAYMENT_METHOD, show_alert=True)
        return

    # Если URL корректный, продолжаем обработку
    web_app = types.WebAppInfo(url=url)
    price = current_context.dialog_data.get('price')

    # Создаем кнопку, которая откроет веб-приложение
    web_app_button = InlineKeyboardButton(text=f"Оплатить {price}₽", web_app=web_app)

    # Создаем экземпляр клавиатуры с кнопкой
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[web_app_button]])

    if manager.current_context().state.group.__name__ == 'CountryMenu':
        country_id = current_context.dialog_data.get('country_id')
        service_code = current_context.dialog_data.get('service_code')
        service_price = current_context.dialog_data.get('service_price')
        free_price_map = current_context.dialog_data.get('free_price_map')
        retail_price = current_context.dialog_data.get('retail_price')

        # Запускаем проверку баланса
        await start_balance_check(c.from_user.id, service_price, retail_price, free_price_map, country_id, service_code,
                                  c, manager)

    if manager.current_context().state.group.__name__ == 'RentCountryMenu':
        day_index = current_context.dialog_data.get('day_index')
        selected_country = current_context.dialog_data.get('selected_country')

        await start_balance_check_rent(c.from_user.id, price, day_index, selected_country, c, manager)

    if manager:
        await manager.reset_stack()

    # Отправляем сообщение с клавиатурой или редактируем существующее сообщение
    await c.message.edit_text(text=FOLLOW_THE_LINK_TO_PAY, reply_markup=keyboard)


async def send_payment_keyboard_anypay(c: types.CallbackQuery, button: Button, manager: DialogManager):
    """
        Отправляет клавиатуру для выбора способа оплаты.

        :param m: Объект Message или CallbackQuery.
        :param manager: Объект DialogManager.
        :param price: Сумма пополнения.
        """

    try:
        user_id = c.from_user.id
        logger.bind(user_id=user_id, action='send_payment_keyboard_anypay').log("USER_ACTION", "Формирование AnyPay-ссылки")

        ctx = manager.current_context()
        price = float(ctx.dialog_data['price']) if manager else 0
        continue_data = ctx.start_data if manager else None

        user = await models.User.get_user(c.from_user.id)
        payment_anypay = await models.Payment.create_payment(
            user=user,
            method=models.PaymentMethod.ANYPAY,
            amount=price,
            continue_data=continue_data
        )

        api = AnypayAPI()
        url_card = await api.create_payment(amount=price, desc=payment_anypay.id, method='card')
        url_sbp = await api.create_payment(amount=price, desc=payment_anypay.id, method='sbp')
        url_btc = await api.create_payment(amount=price, desc=payment_anypay.id, method='btc')

        state = manager.current_context().state.group.__name__
        ctx = manager.current_context()

        country_id = ''
        service_code = ''
        service_price = ''
        url_pattern = re.compile(r'https?://[^\s]+')

        if state == 'CountryMenu':
            country_id = ctx.dialog_data['country_id']
            service_code = ctx.dialog_data['service_code']
            service_price = ctx.dialog_data['service_price']
            if url_pattern.match(url_sbp):
                await manager.start(CountryMenu.payment_method_anypay, mode=StartMode.NORMAL, data={})
            else:
                await manager.start(CountryMenu.payment_method_anypay_min, mode=StartMode.NORMAL, data={})
        elif state == 'RentCountryMenu':
            if url_pattern.match(url_sbp):
                await manager.start(RentCountryMenu.payment_method_anypay, mode=StartMode.NORMAL, data={})
            else:
                await manager.start(RentCountryMenu.payment_method_anypay_min, mode=StartMode.NORMAL, data={})
        else:
            if url_pattern.match(url_sbp):
                await manager.start(PersonalMenu.payment_method_anypay, mode=StartMode.NORMAL, data={})
            else:
                await manager.start(PersonalMenu.payment_method_anypay_min, mode=StartMode.NORMAL, data={})

        ctx.dialog_data.update({
            'card_url': url_card,
            'sbp_url': url_sbp,
            'btc_url': url_btc,
            'price': price,
            'country_id': country_id,
            'service_code': service_code,
            'service_price': service_price
        })
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в send_payment_keyboard_anypay: {e}")