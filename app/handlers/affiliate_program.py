from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from app import dependencies
from app.db import models
from app.services import bot_texts as bt
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from app.services.payments.cryptomus import create_a_payout
from app.services.qr_code import generate_qr_code
from loguru import logger
router = Router()

back_mk = types.InlineKeyboardMarkup(
    inline_keyboard=[
        [
            types.InlineKeyboardButton(text=bt.BACK_BTN, callback_data='withdraw')
        ]
    ]
)


choice_cryptocurrency_kb = types.InlineKeyboardMarkup(
    inline_keyboard=[
        [
            types.InlineKeyboardButton(text="USDT", callback_data='choice_cryptocurrency:USDT'),
            types.InlineKeyboardButton(text="BTC", callback_data='choice_cryptocurrency:BTC'),
            types.InlineKeyboardButton(text="ETH", callback_data='choice_cryptocurrency:ETH')
        ],
        [
            types.InlineKeyboardButton(text="TON", callback_data='choice_cryptocurrency:TON'),
            types.InlineKeyboardButton(text="BNB", callback_data='choice_cryptocurrency:BNB'),
            types.InlineKeyboardButton(text="DAI", callback_data='choice_cryptocurrency:DAI')
        ],
        [
            types.InlineKeyboardButton(text="TRX", callback_data='choice_cryptocurrency:TRX'),
            types.InlineKeyboardButton(text="AVAX", callback_data='choice_cryptocurrency:AVAX')
        ],
    ]
)

network_selection_kb = types.InlineKeyboardMarkup(
    inline_keyboard=[
        [
            types.InlineKeyboardButton(text="TRON", callback_data='network_selection:TRON'),
            types.InlineKeyboardButton(text="TON", callback_data='network_selection:TON'),
            types.InlineKeyboardButton(text="BTC", callback_data='network_selection:BTC')
        ],
        [
            types.InlineKeyboardButton(text="BSC", callback_data='network_selection:BSC'),
            types.InlineKeyboardButton(text="ETH", callback_data='network_selection:ETH'),
        ]
    ]
)


confirm_conclusion_btn = types.InlineKeyboardMarkup(
    inline_keyboard=[
        [
            types.InlineKeyboardButton(text="☑️Подтвердить вывод", callback_data='confirm_withdrawal')
        ],
        [
            types.InlineKeyboardButton(text="🔙Отменить", callback_data='cancel_withdrawal')
        ]
    ]
)


class AffiliateState(StatesGroup):
    enter_withdraw_amount = State()
    enter_withdraw_requisites = State()

class ChoiceOfCryptocurrency(StatesGroup):
    enter_amount = State()
    enter_choice_cryptocurrency = State()
    enter_network = State()

async def send_affiliate_message(m: types.Message, user_id: int = None):
    if not user_id:
        user_id = m.from_user.id

    me = await m.bot.me()
    link = f'https://t.me/{me.username}?start={user_id}'
    qr_code_bytes = await generate_qr_code(link)
    user = await models.User.get_user(user_id)
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.SHARE_LINK_BTN,
                                           switch_inline_query_chosen_chat=types.SwitchInlineQueryChosenChat(
                                               query=bt.SHARE_BOT_TEXT.format(
                                                   link=link,
                                                   mention=user.mention
                                               ),
                                               allow_user_chats=True,
                                               allow_group_chats=True,
                                               allow_channel_chats=True
                                           ))
            ],
            [
                types.InlineKeyboardButton(text=bt.WITHDRAW_BTN, callback_data='withdraw')
            ]
        ]
    )
    ref = await models.User.filter(refer_id=user.id).all()
    ref_count = len(ref)
    payment_count = 0
    for r in ref:
        payment_count += (await models.Payment.filter(user=r, is_success=True).all().count())

    await m.answer_photo(
        photo=types.BufferedInputFile(qr_code_bytes.read(), filename='qr_code.png'),
        caption=bt.AFFILIATE_PROGRAM_TEXT.format(link=link, ref_balance=round(user.ref_balance),
                                                 ref_count=ref_count, ref_balance_total=round(user.total_ref_earnings),
                                                 payment_count=payment_count),
        reply_markup=mk
    )


@router.message(F.text == bt.AFFILIATE_PROGRAM_BTN)
async def affiliate_program(message: types.Message):
    user = await models.User.get_user(message.from_user.id)
    sub = await check_subscribe(user)
    if not sub:
        await send_subscribe_msg(user)
        return

    await send_affiliate_message(message)


@router.callback_query(F.data == 'affiliate_program')
async def affiliate_program(call: types.CallbackQuery):
    await call.message.delete()
    await send_affiliate_message(call.message)


@router.callback_query(F.data == 'withdraw')
async def withdraw(call: types.CallbackQuery):
    await call.message.delete()
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            # [
            #     types.InlineKeyboardButton(
            #         text=bt.ON_BANK_CARD_BTN,
            #         callback_data='on_bank_card'
            #     )
            # ],
            [
                types.InlineKeyboardButton(
                    text=bt.ON_CRYPTOCURRENCY_BTN,
                    callback_data='cryptocurrency'
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=bt.ON_BALANCE_BTN,
                    callback_data='on_balance'
                )
            ],
            [
                types.InlineKeyboardButton(text=bt.BACK_BTN,
                                           callback_data='affiliate_program')
            ]
        ]
    )
    await call.message.answer(text=bt.WITHDRAW_TEXT, reply_markup=mk)
    await call.answer()


@router.callback_query(F.data == 'on_bank_card')
async def on_bank_card(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(withdraw_method=call.data)
    await state.set_state(AffiliateState.enter_withdraw_amount)
    await call.message.edit_text(text=bt.ENTER_WITHDRAW_AMOUNT, reply_markup=back_mk)

@router.callback_query(F.data == 'cryptocurrency')
async def on_cryptocurrency(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(withdraw_method=call.data)
    await state.set_state(AffiliateState.enter_withdraw_amount)
    await call.message.edit_text(text=bt.ENTER_WITHDRAW_AMOUNT, reply_markup=back_mk)

@router.callback_query(F.data == 'on_balance')
async def on_balance(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(withdraw_method=call.data)
    await state.set_state(AffiliateState.enter_withdraw_amount)
    await call.message.edit_text(text=bt.ENTER_WITHDRAW_AMOUNT, reply_markup=back_mk)


@router.message(AffiliateState.enter_withdraw_amount)
async def enter_withdraw_amount(m: types.Message, state: FSMContext):
    data = await state.get_data()
    withdraw_method = data.get('withdraw_method')

    if not m.text.isdigit():
        await m.answer(text='Сумма должна быть числом', reply_markup=back_mk)
        return

    amount = int(m.text)
    if amount < 100 and withdraw_method == 'on_bank_card':
        await m.answer(text='Минимальная сумма вывода - 100₽', reply_markup=back_mk)
        return

    if amount < 300 and withdraw_method == 'cryptocurrency':
        await m.answer(text=f'Минимальная сумма вывода: {bt.MIN_CRYPT_AMOUNT}₽', reply_markup=back_mk)
        return

    user = await models.User.get_user(m.from_user.id)
    if user.ref_balance < amount:
        await m.answer(text='Недостаточно средств', reply_markup=back_mk)
        return

    if withdraw_method == 'on_bank_card':
        await m.answer(text=bt.ENTER_WITHDRAW_CARD, reply_markup=back_mk)
        await state.set_state(AffiliateState.enter_withdraw_requisites)
        await state.update_data(amount=amount)

    elif withdraw_method == 'cryptocurrency':
        await state.clear()
        await state.set_state(ChoiceOfCryptocurrency.enter_amount)
        await state.update_data(amount=amount)
        await m.answer(text=f'В какой криптовалюте Вы хотите получить выплату?', reply_markup=choice_cryptocurrency_kb)

    elif withdraw_method == 'on_balance':
        user.ref_balance -= amount
        user.balance += amount
        await user.save()
        await m.answer(text=f'Вывод {amount}₽ на баланс бота подтвержден', reply_markup=back_mk)
        await state.clear()


@router.callback_query(F.data.startswith('choice_cryptocurrency:'))
async def choice_cryptocurrency(call: types.CallbackQuery, state: FSMContext):
    # Извлечение выбранной криптовалюты из callback_data
    cryptocurrency = call.data.split(':')[1]  # Берем вторую часть строки после "choice_cryptocurrency:"

    # Обновляем данные состояния
    await state.update_data(withdraw_cryptocurrency=cryptocurrency)

    # Устанавливаем состояние
    await state.set_state(ChoiceOfCryptocurrency.enter_choice_cryptocurrency)

    # Редактируем сообщение
    await call.message.edit_text(
        text=f'Вы выбрали криптовалюту: {cryptocurrency}\nТеперь выберите сеть.',
        reply_markup=network_selection_kb  # Здесь используйте вашу клавиатуру выбора сети
    )


@router.callback_query(F.data.startswith('network_selection:'))
async def network_selection(call: types.CallbackQuery, state: FSMContext):
    # Извлечение выбранной сети из callback_data
    network = call.data.split(':')[1]
    # Обновляем данные состояния
    await state.update_data(withdraw_network=network)

    # Устанавливаем состояние
    await state.set_state(ChoiceOfCryptocurrency.enter_network)

    # Редактируем сообщение
    await call.message.edit_text(
        text=f'Вы выбрали сеть: {network}\nОтправьте адрес вашего крипто-кошелька для вывода средств⤵️'
    )

@router.message(ChoiceOfCryptocurrency.enter_network)
async def enter_wallet_address(message: types.Message, state: FSMContext):
    # Получаем введенный пользователем текст (номер кошелька)
    address = message.text
    await state.update_data(address=address)
    # Получаем все данные из состояния
    user_data = await state.get_data()
    currency = user_data.get('withdraw_cryptocurrency')
    network = user_data.get('withdraw_network')
    amount = user_data.get('amount')

    await message.answer(
        text=(
            f"💸 *Ваш запрос на вывод средств:*\n\n"
            f"💳 **Сумма:** `{amount}₽`\n"
            f"🌍 **Валюта:** `{currency}`\n"
            f"🔗 **Сеть:** `{network}`\n"
            f"📥 **Кошелек:** `{address}`\n\n"
            f"⚠️ *Обратите внимание:*\n"
            f"При выводе средств на криптокошелёк сервис может взимать комиссию.\n\n"
            f"✅ Подтвердите запрос, чтобы продолжить."
        ),
        parse_mode="Markdown",
        reply_markup=confirm_conclusion_btn
    )
    await state.clear()

@router.callback_query(F.data == 'cancel_withdrawal')
async def cancel_withdrawal(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()
    await state.clear()
    await call.message.answer(text="Вывод средств отменен🙅‍♂️")


@router.callback_query(F.data == 'confirm_withdrawal')
async def confirm_withdrawal(call: types.CallbackQuery, state: FSMContext):
    await call.message.delete()

    user_data = await state.get_data()
    user = await models.User.get_user(call.from_user.id)
    amount = user_data.get('amount')
    currency = user_data.get('withdraw_cryptocurrency')
    address = user_data.get('address')
    network = user_data.get('withdraw_network')

    # создаем новый платеж в базе данных
    payout = await models.PayOut.create_payout(
        user=user,
        amount=amount,
        currency=currency,
        address=address,
        network=network,

    )

    # Создает выплату через Cryptomus API
    pay = await create_a_payout(amount=str(amount), to_currency=currency, order_id=payout.id, address=address, network=network)
    # pay = {'uuid': '9e18e3b5-e3ab-4d55-8f61-d816528d9a4b', 'amount': 350.0, 'currency': 'RUB', 'network': 'tron', 'address': 'TEdewRYzTptRMjsx74PHbaf2iYfYtKndjm', 'txid': None, 'status': 'process', 'is_final': False, 'balance': 11.40881915, 'payer_currency': 'USDT', 'payer_amount': 3.4450955, 'order_id': '6', 'commission': '3.00000000', 'merchant_amount': '3.44509550'}
    if pay:
        user.ref_balance -= amount
        await user.save()
        try:
            # Получаем комиссию, по умолчанию 3
            commission = getattr(pay, 'commission', 3)
        except Exception as e:
            logger.error(e)
            commission = None  # Устанавливаем None, если произошла ошибка

        # Формируем текст уведомления
        message_text = (
            f"✅ *Заявка на вывод средств успешно завершена!*\n\n"
            f"💳 **Сумма:** `{amount}₽`\n"
            f"📥 **Кошелек:** `{address}`"
        )
        if commission is not None:  # Если комиссия есть, добавляем её в сообщение
            message_text += f"\n💸 **Комиссия:** `{commission}$`"

        # Отправляем сообщение с парсингом Markdown
        await call.message.answer(
            text=message_text,
            parse_mode="Markdown"
        )


@router.message(AffiliateState.enter_withdraw_requisites)
async def enter_withdraw_requisites(m: types.Message, state: FSMContext):
    await state.set_state()
    data = await state.get_data()
    amount = data.get('amount')
    requisites = m.text
    await state.update_data(requisites=requisites)
    user = await models.User.get_user(m.from_user.id)
    if user.ref_balance < amount:
        await m.answer(text='Недостаточно средств', reply_markup=back_mk)
        return

    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.CONFIRM_BTN, callback_data='confirm_withdraw')
            ],
            [
                types.InlineKeyboardButton(text=bt.BACK_BTN, callback_data='withdraw')
            ]
        ]
    )
    await m.answer(text=bt.WITHDRAW_INFO.format(amount=amount, requisites=requisites), reply_markup=mk)


@router.callback_query(F.data == 'confirm_withdraw')
async def confirm_withdraw(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    amount = float(data.get('amount'))
    requisites = data.get('requisites')
    user = await models.User.get_user(call.from_user.id)
    if user.ref_balance < amount:
        await call.message.answer(text='Недостаточно средств', reply_markup=back_mk)
        return

    user.ref_balance -= amount
    await user.save()
    if user.ref_balance < 0:
        user.ref_balance += amount
        await user.save()
        return

    withdraw_obj = await models.Withdraw.add_withdraw(user, requisites, amount)
    await call.message.edit_text(text='Заявка на вывод отправлена')
    mk = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=bt.CONFIRM_BTN, callback_data=f'admin_withdraw:{withdraw_obj.id}')
            ],
            [
                types.InlineKeyboardButton(text=bt.DECLINE_BTN,
                                           callback_data=f'admin_withdraw_decline:{withdraw_obj.id}')
            ]
        ]
    )
    await call.bot.send_message(
        chat_id=dependencies.WITHDRAW_CHAT_ID,
        text=bt.WITHDRAW_INFO.format(amount=amount, requisites=requisites),
        reply_markup=mk
    )


@router.callback_query(F.data.startswith('admin_withdraw_decline:'))
async def admin_withdraw_decline(call: types.CallbackQuery):
    withdraw_id = int(call.data.split(':')[1])
    withdraw_obj = await models.Withdraw.get_or_none(id=withdraw_id)
    if not withdraw_obj:
        await call.answer()
        return

    await withdraw_obj.fetch_related('user')
    withdraw_obj.is_success = False
    await withdraw_obj.save()
    withdraw_obj.user.ref_balance += withdraw_obj.amount
    await withdraw_obj.user.save()
    await call.message.edit_text(text=call.message.html_text + '\n\n<b>❌ Заявка отклонена</b>')
    await call.bot.send_message(
        chat_id=withdraw_obj.user.telegram_id,
        text=f'Ваша заявка на вывод {withdraw_obj.amount}₽ отклонена'
    )


@router.callback_query(F.data.startswith('admin_withdraw:'))
async def admin_withdraw(call: types.CallbackQuery):
    withdraw_id = int(call.data.split(':')[1])
    withdraw_obj = await models.Withdraw.get_or_none(id=withdraw_id)
    if not withdraw_obj:
        await call.answer()
        return

    await withdraw_obj.fetch_related('user')
    withdraw_obj.is_success = True
    await withdraw_obj.save()
    await call.message.edit_text(text=call.message.html_text + '\n\n<b>✅ Заявка подтверждена</b>')
    await call.bot.send_message(
        chat_id=withdraw_obj.user.telegram_id,
        text=f'Ваша заявка на вывод {withdraw_obj.amount}₽ подтверждена'
    )
