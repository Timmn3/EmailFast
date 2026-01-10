from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from app import dependencies
from app.db import models
from app.dependencies import REFERRAL_PREFIX
from app.services import bot_texts as bt
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from app.services.payments.cryptomus import create_a_payout
from app.services.qr_code import generate_qr_code
from loguru import logger
from tortoise.functions import Count
from aiogram.utils.keyboard import InlineKeyboardBuilder


router = Router()

# --- Клавиатуры ---
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

# --- Состояния ---
class AffiliateState(StatesGroup):
    enter_withdraw_amount = State()
    enter_withdraw_requisites = State()

class ChoiceOfCryptocurrency(StatesGroup):
    enter_amount = State()
    enter_choice_cryptocurrency = State()
    enter_network = State()


# --- Вспомогательные функции ---
async def send_affiliate_message(m: types.Message, user_id: int = None):
    try:
        if not user_id:
            user_id = m.from_user.id

        logger.bind(user_id=user_id, action="send_affiliate_message").log("USER_ACTION", "Запрос на отправку реферального сообщения")
        loading_msg = await m.answer("⏳Идёт загрузка, ожидайте...")
        me = await m.bot.me()
        link = f'https://t.me/{me.username}?start={user_id}'
        qr_code_bytes = await generate_qr_code(link)
        user = await models.User.get_user(user_id)

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])

        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(
                text=bt.SHARE_LINK_BTN,
                switch_inline_query_chosen_chat=types.SwitchInlineQueryChosenChat(
                    query=bt.SHARE_BOT_TEXT.format(
                        link=link,
                        mention=user.mention
                    ),
                    allow_user_chats=True,
                    allow_group_chats=True,
                    allow_channel_chats=True
                )
            )
        ])

        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(text=bt.WITHDRAW_BTN, callback_data='withdraw')
        ])

        if user.disable_ref_notifications:
            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(
                    text="🔔 Включить уведомления о рефералах",
                    callback_data=f"enable_ref_notify:{user_id}"
                )
            ])

        ref = await models.User.filter(refer_id=user.id).all()
        ref_count = len(ref)
        payment_count = 0
        for r in ref:
            payment_count += (await models.Payment.filter(user=r, is_success=True).all().count())
        logger.bind(user_id=user_id, action="send_affiliate_message").log("USER_ACTION", f"Рефералы: {ref_count}, платежей: {payment_count}")

        referral_stats = (
            await models.Payment.filter(
                user__refer_id=user.id,
                is_success=True
            )
            .group_by("user_id")
            .annotate(payment_count=Count("id"))
            .values("user_id", "payment_count")
        )

        repeat_payment_users = sum(
            max(0, stat["payment_count"] - 1) for stat in referral_stats if stat["payment_count"] > 1)

        if user_id == REFERRAL_PREFIX:

            await m.answer_photo(
                photo=types.BufferedInputFile(qr_code_bytes.read(), filename='qr_code.png'),
                caption=bt.AFFILIATE_PROGRAM_TEXT_SHORT.format(
                    link=link,
                    ref_count=ref_count,
                    payment_count=payment_count,
                ),
                reply_markup=keyboard
            )

        else:

            await m.answer_photo(
                photo=types.BufferedInputFile(qr_code_bytes.read(), filename='qr_code.png'),
                caption=bt.AFFILIATE_PROGRAM_TEXT.format(
                    link=link,
                    ref_balance=round(user.ref_balance),
                    ref_count=ref_count,
                    ref_balance_total=round(user.total_ref_earnings),
                    payment_count=payment_count,
                    repeat_payment_count=repeat_payment_users
                ),
                reply_markup=keyboard
            )

        await loading_msg.delete()
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /send_affiliate_message: {e}")

# --- Хэндлеры ---
@router.message(F.text == bt.AFFILIATE_PROGRAM_BTN)
async def affiliate_program(message: types.Message):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action="affiliate_program").log("USER_ACTION", "Пользователь открыл Партнерская программа")
        # logger.bind(user_id=user_id, action="affiliate_program").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        # logger.bind(user_id=user_id, action="affiliate_program").log("USER_ACTION", f"Результат из БД: пользователь {user_id} найден")
        sub = await check_subscribe(user)
        if not sub:
            await send_subscribe_msg(user)
            return
        await send_affiliate_message(message)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /affiliate_program: {e}")

@router.callback_query(F.data == 'affiliate_program')
async def affiliate_program(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="callback_affiliate_program").log("USER_ACTION", "Пользователь вернулся к Партнерская программа")
        await call.message.delete()
        await send_affiliate_message(call.message)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /callback_affiliate_program: {e}")

@router.callback_query(F.data == 'withdraw')
async def withdraw(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        if user_id == REFERRAL_PREFIX:
            await call.message.answer("Возможность вывода средств заблокирована, обратитесь в поддержку")
            return

        logger.bind(user_id=user_id, action="withdraw").log("USER_ACTION", "Пользователь выбрал способ вывода средств")
        if state:
            await state.clear()
        await call.message.delete()
        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
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
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /withdraw: {e}")

@router.callback_query(F.data == 'on_bank_card')
async def on_bank_card(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="on_bank_card").log("USER_ACTION", "Выбран вывод на банковскую карту")
        await state.update_data(withdraw_method=call.data)
        await state.set_state(AffiliateState.enter_withdraw_amount)
        await call.message.edit_text(text=bt.ENTER_WITHDRAW_AMOUNT, reply_markup=back_mk)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /on_bank_card: {e}")

@router.callback_query(F.data == 'cryptocurrency')
async def on_cryptocurrency(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="on_cryptocurrency").log("USER_ACTION", "Выбран вывод на криптовалюту")
        await state.update_data(withdraw_method=call.data)
        await state.set_state(AffiliateState.enter_withdraw_amount)
        await call.message.edit_text(text=bt.ENTER_WITHDRAW_AMOUNT, reply_markup=back_mk)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /on_cryptocurrency: {e}")

@router.callback_query(F.data == 'on_balance')
async def on_balance(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="on_balance").log("USER_ACTION", "Выбран вывод на баланс бота")
        await state.update_data(withdraw_method=call.data)
        await state.set_state(AffiliateState.enter_withdraw_amount)
        await call.message.edit_text(text=bt.ENTER_WITHDRAW_AMOUNT, reply_markup=back_mk)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /on_balance: {e}")

@router.message(AffiliateState.enter_withdraw_amount)
async def enter_withdraw_amount(m: types.Message, state: FSMContext):
    try:
        user_id = m.from_user.id
        logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", "Пользователь ввёл сумму для вывода")
        data = await state.get_data()
        withdraw_method = data.get('withdraw_method')
        amount_text = m.text
        logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", f"Введено: сумма={amount_text}, метод={withdraw_method}")
        if not amount_text.isdigit():
            logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", "Ошибка: сумма не является числом")
            await m.answer(text='Сумма должна быть числом', reply_markup=back_mk)
            return
        amount = int(amount_text)
        if amount < 100 and withdraw_method == 'on_bank_card':
            logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", "Ошибка: сумма меньше минимальной для карты")
            await m.answer(text='Минимальная сумма вывода - 100₽', reply_markup=back_mk)
            return
        if amount < 300 and withdraw_method == 'cryptocurrency':
            logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", "Ошибка: сумма меньше минимальной для крипты")
            await m.answer(text=f'Минимальная сумма вывода: {bt.MIN_CRYPT_AMOUNT}₽', reply_markup=back_mk)
            return
        # logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", f"Результат из БД: баланс={user.ref_balance}")
        if user.ref_balance < amount:
            logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", "Ошибка: недостаточно средств")
            await m.answer(text='Недостаточно средств', reply_markup=back_mk)
            return
        if withdraw_method == 'on_bank_card':
            logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", "Переход к вводу реквизитов карты")
            await m.answer(text=bt.ENTER_WITHDRAW_CARD, reply_markup=back_mk)
            await state.set_state(AffiliateState.enter_withdraw_requisites)
            await state.update_data(amount=amount)
        elif withdraw_method == 'cryptocurrency':
            logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", "Переход к выбору криптовалюты")
            await state.clear()
            await state.set_state(ChoiceOfCryptocurrency.enter_amount)
            await state.update_data(amount=amount)
            await m.answer(text=f'В какой криптовалюте Вы хотите получить выплату?', reply_markup=choice_cryptocurrency_kb)
        elif withdraw_method == 'on_balance':
            logger.bind(user_id=user_id, action="enter_withdraw_amount").log("USER_ACTION", f"Выполняется вывод {amount}₽ на баланс")
            user.ref_balance -= amount
            user.balance += amount
            await user.save()
            await m.answer(text=f'Вывод {amount}₽ на баланс бота подтвержден', reply_markup=back_mk)
            await state.clear()
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /enter_withdraw_amount: {e}")

# --- Остальные хэндлеры аналогично оборачиваются в try/except и логгируются по шаблону выше ---

# Пример для одного из ключевых хэндлеров:
@router.callback_query(F.data.startswith('network_selection:'))
async def network_selection(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        network = call.data.split(':')[1]
        logger.bind(user_id=user_id, action="network_selection").log("USER_ACTION", f"Выбрана сеть: {network}")
        await state.update_data(withdraw_network=network)
        await state.set_state(ChoiceOfCryptocurrency.enter_network)
        await call.message.edit_text(
            text=f'Вы выбрали сеть: {network}\nОтправьте адрес вашего крипто-кошелька для вывода средств⤵️'
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /network_selection: {e}")

@router.message(ChoiceOfCryptocurrency.enter_network)
async def enter_wallet_address(message: types.Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action="enter_wallet_address").log("USER_ACTION", "Пользователь ввёл адрес кошелька")
        address = message.text
        await state.update_data(address=address)
        user_data = await state.get_data()
        currency = user_data.get('withdraw_cryptocurrency')
        network = user_data.get('withdraw_network')
        amount = user_data.get('amount')
        logger.bind(user_id=user_id, action="enter_wallet_address").log(
            "USER_ACTION",
            f"Запрос на вывод средств: сумма={amount}, валюта={currency}, сеть={network}, адрес={address}"
        )
        await message.answer(
            text=(
                f"💸 *Ваш запрос на вывод средств:*"
                f"\n💳 **Сумма:** `{amount}₽`"
                f"\n🌍 **Валюта:** `{currency}`"
                f"\n🔗 **Сеть:** `{network}`"
                f"\n📥 **Кошелек:** `{address}`"
                f"\n⚠️ *Обратите внимание:*"
                f"\nПри выводе средств на криптокошелёк сервис может взимать комиссию."
                f"\n✅ Подтвердите запрос, чтобы продолжить."
            ),
            parse_mode="Markdown",
            reply_markup=confirm_conclusion_btn
        )
        await state.clear()
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /enter_wallet_address: {e}")

@router.callback_query(F.data == 'cancel_withdrawal')
async def cancel_withdrawal(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="cancel_withdrawal").log("USER_ACTION", "Пользователь отменил вывод средств")
        await call.message.delete()
        await state.clear()
        await call.message.answer(text="Вывод средств отменен🙅‍♂️")
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /cancel_withdrawal: {e}")

@router.callback_query(F.data == 'confirm_withdrawal')
async def confirm_withdrawal(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="confirm_withdrawal").log("USER_ACTION", "Пользователь подтвердил вывод средств")
        await call.message.delete()
        user_data = await state.get_data()
        amount = user_data.get('amount')
        currency = user_data.get('withdraw_cryptocurrency')
        address = user_data.get('address')
        network = user_data.get('withdraw_network')

        # logger.bind(user_id=user_id, action="confirm_withdrawal").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="confirm_withdrawal").log("USER_ACTION", f"Результат из БД: баланс={user.ref_balance}")

        # Создаем новый платеж в базе данных
        logger.bind(user_id=user_id, action="confirm_withdrawal").log("USER_ACTION", "Запрос к БД: создание выплаты")
        payout = await models.PayOut.create_payout(
            user=user,
            amount=amount,
            currency=currency,
            address=address,
            network=network,
        )
        logger.bind(user_id=user_id, action="confirm_withdrawal").log("USER_ACTION", f"Выплата создана: ID={payout.id}")

        # Выполняем выплату через Cryptomus API
        logger.bind(user_id=user_id, action="confirm_withdrawal").log("USER_ACTION", "Вызов Cryptomus API для выплаты")
        pay = await create_a_payout(amount=str(amount), to_currency=currency, order_id=payout.id, address=address, network=network)

        if pay:
            user.ref_balance -= amount
            await user.save()
            logger.bind(user_id=user_id, action="confirm_withdrawal").log("USER_ACTION", f"Баланс обновлен: новый баланс={user.ref_balance}")

            try:
                commission = getattr(pay, 'commission', 3)
                logger.bind(user_id=user_id, action="confirm_withdrawal").log("USER_ACTION", f"Комиссия: {commission}")
            except Exception as e:
                logger.opt(exception=e).error(f"Ошибка получения комиссии в /confirm_withdrawal: {e}")
                commission = None

            message_text = (
                f"✅ *Заявка на вывод средств успешно завершена!*"
                f"\n💳 **Сумма:** `{amount}₽`"
                f"\n📥 **Кошелек:** `{address}`"
            )

            if commission is not None:
                message_text += f"\n💸 **Комиссия:** `{commission}$`"

            await call.message.answer(
                text=message_text,
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /confirm_withdrawal: {e}")

@router.message(AffiliateState.enter_withdraw_requisites)
async def enter_withdraw_requisites(m: types.Message, state: FSMContext):
    try:
        user_id = m.from_user.id
        logger.bind(user_id=user_id, action="enter_withdraw_requisites").log("USER_ACTION", "Пользователь ввёл реквизиты карты")
        data = await state.get_data()
        amount = data.get('amount')
        requisites = m.text
        logger.bind(user_id=user_id, action="enter_withdraw_requisites").log("USER_ACTION", f"Введено: реквизиты={requisites}, сумма={amount}")
        await state.update_data(requisites=requisites)

        # logger.bind(user_id=user_id, action="enter_withdraw_requisites").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="enter_withdraw_requisites").log("USER_ACTION", f"Результат из БД: баланс={user.ref_balance}")

        if user.ref_balance < amount:
            logger.bind(user_id=user_id, action="enter_withdraw_requisites").log("USER_ACTION", "Ошибка: недостаточно средств")
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
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /enter_withdraw_requisites: {e}")

@router.callback_query(F.data == 'confirm_withdraw')
async def confirm_withdraw(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", "Пользователь подтвердил заявку на вывод")
        await call.answer()
        data = await state.get_data()
        amount = float(data.get('amount'))
        requisites = data.get('requisites')

        # logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", f"Запрос к БД: получение пользователя {user_id}")
        user = await models.User.get_user(user_id)
        logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", f"Результат из БД: баланс={user.ref_balance}")

        if user.ref_balance < amount:
            logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", "Ошибка: недостаточно средств")
            await call.message.answer(text='Недостаточно средств', reply_markup=back_mk)
            return

        user.ref_balance -= amount
        await user.save()
        logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", f"Баланс обновлен: новый баланс={user.ref_balance}")

        if user.ref_balance < 0:
            user.ref_balance += amount
            await user.save()
            logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", "Ошибка: отрицательный баланс, откат изменений")
            return

        logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", "Запрос к БД: создание заявки на вывод")
        withdraw_obj = await models.Withdraw.add_withdraw(user, requisites, amount)
        logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", f"Заявка создана: ID={withdraw_obj.id}")

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

        logger.bind(user_id=user_id, action="confirm_withdraw").log("USER_ACTION", "Отправка сообщения админу")
        await call.bot.send_message(
            chat_id=dependencies.WITHDRAW_CHAT_ID,
            text=bt.WITHDRAW_INFO.format(amount=amount, requisites=requisites),
            reply_markup=mk
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /confirm_withdraw: {e}")

@router.callback_query(F.data.startswith('admin_withdraw_decline:'))
async def admin_withdraw_decline(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="admin_withdraw_decline").log("USER_ACTION", "Админ отклонил заявку на вывод")
        withdraw_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="admin_withdraw_decline").log("USER_ACTION", f"Запрос к БД: получение заявки ID={withdraw_id}")
        withdraw_obj = await models.Withdraw.get_or_none(id=withdraw_id)
        if not withdraw_obj:
            logger.bind(user_id=user_id, action="admin_withdraw_decline").log("USER_ACTION", "Ошибка: заявка не найдена")
            await call.answer()
            return

        await withdraw_obj.fetch_related('user')
        withdraw_obj.is_success = False
        await withdraw_obj.save()
        logger.bind(user_id=user_id, action="admin_withdraw_decline").log("USER_ACTION", f"Заявка ID={withdraw_id} отклонена")

        withdraw_obj.user.ref_balance += withdraw_obj.amount
        await withdraw_obj.user.save()
        logger.bind(user_id=user_id, action="admin_withdraw_decline").log("USER_ACTION", f"Баланс пользователя восстановлен: +{withdraw_obj.amount}")

        await call.message.edit_text(text=call.message.html_text + '\n<b>❌ Заявка отклонена</b>')
        await call.bot.send_message(
            chat_id=withdraw_obj.user.telegram_id,
            text=f'Ваша заявка на вывод {withdraw_obj.amount}₽ отклонена'
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /admin_withdraw_decline: {e}")

@router.callback_query(F.data.startswith('admin_withdraw:'))
async def admin_withdraw(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="admin_withdraw").log("USER_ACTION", "Админ подтвердил заявку на вывод")
        withdraw_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="admin_withdraw").log("USER_ACTION", f"Запрос к БД: получение заявки ID={withdraw_id}")
        withdraw_obj = await models.Withdraw.get_or_none(id=withdraw_id)
        if not withdraw_obj:
            logger.bind(user_id=user_id, action="admin_withdraw").log("USER_ACTION", "Ошибка: заявка не найдена")
            await call.answer()
            return

        await withdraw_obj.fetch_related('user')
        withdraw_obj.is_success = True
        await withdraw_obj.save()
        logger.bind(user_id=user_id, action="admin_withdraw").log("USER_ACTION", f"Заявка ID={withdraw_id} подтверждена")

        await call.message.edit_text(text=call.message.html_text + '\n<b>✅ Заявка подтверждена</b>')
        await call.bot.send_message(
            chat_id=withdraw_obj.user.telegram_id,
            text=f'Ваша заявка на вывод {withdraw_obj.amount}₽ подтверждена'
        )
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /admin_withdraw: {e}")