from datetime import timedelta
from typing import Union
import logging
from aiogram import types, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram_dialog import DialogManager, StartMode
import asyncio

from app.db import models
from app.dependencies import bot, REFERRAL_PREFIX
from app.dialogs.personal_cabinet.states import PersonalMenu
from app.dialogs.receive_email.states import ReceiveEmailMenu
from app.dialogs.receive_sms.selected import send_country_info
from app.services import bot_texts as bt
from app.services.bot_texts import RENT_EMAIL_WEEK, RENT_EMAIL_MONTH, RENT_EMAIL_SIX_MONTHS, RENT_EMAIL_YEAR
from app.services.keyboards import start_kb, send_main_menu
from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.mail.temp_mail_tm import create_mail
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from loguru import logger
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.db.models import ReferralLink

from app.services.periodic_tasks import balance_replenishment_notification
from app.services.rental_email_pool import (
    change_rental_email_lease,
    get_firstmail_change_cooldown_remaining_for_lease,
    build_firstmail_change_cooldown_message,
)

router = Router()


@router.message(F.text == '/id')
async def get_id(message: types.Message):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='get_id').log(
            "USER_ACTION",
            f"Пользователь запросил свой ID"
        )
        await message.answer(text=str(message.chat.id))
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /id: {e}")


@router.callback_query(F.data == 'start')
@router.message(Command('start'))
async def start(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager,
                command: CommandObject):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='start').log(
            "USER_ACTION",
            f"Пользователь начал взаимодействие с ботом"
        )

        user = await models.User.get_user(user_id)
        if not user:
            refer_id = command.args

            # --- Добавлено: обработка персональных ссылок Petr (1939379478) ---
            if refer_id and refer_id.startswith(f"{REFERRAL_PREFIX}_"):
                link_code = refer_id
                petr_user = await models.User.get_or_none(telegram_id=REFERRAL_PREFIX)
                if petr_user:
                    referral_link = await ReferralLink.get_or_create_link(user=petr_user, link_code=link_code)

                    # добавляем нового пользователя в таблицу users
                    user = await models.User.add_user(
                        message.from_user,
                        refer=petr_user,
                        referral_link_code=link_code  # ✅ передаём персональную ссылку
                    )

                    # увеличиваем total_starts (если пользователь первый раз)
                    referral_link.total_starts += 1
                    await referral_link.save()

                    # отправляем уведомление Petr, если уведомления не отключены
                    if petr_user and not petr_user.disable_ref_notifications:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔕 Отключить уведомление",
                                                  callback_data=f"disable_notify:{petr_user.telegram_id}")]
                        ])
                        await bot.send_message(
                            chat_id=petr_user.telegram_id,
                            text=f"📈 У Вас новый реферал (https://t.me/emailfastbot?start={link_code})\n└ Аккаунт: {user.telegram_id}",
                            reply_markup=keyboard
                        )

            # --- Стандартная обработка обычных реферальных ID ---
            elif refer_id and refer_id.isdigit():
                logger.bind(user_id=user_id, action='start').log(
                    "USER_ACTION",
                    f"Обнаружен реферальный ID: {refer_id}"
                )
                refer_id = int(refer_id)
                refer = await models.User.get_or_none(telegram_id=refer_id)
                user = await models.User.add_user(message.from_user, refer)

                if refer and not refer.disable_ref_notifications:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔕 Отключить уведомление",
                                              callback_data=f"disable_notify:{refer.telegram_id}")]
                    ])
                    await bot.send_message(
                        chat_id=refer.telegram_id,
                        text=f"📈 У Вас новый реферал\n└ Аккаунт: {user.telegram_id}",
                        reply_markup=keyboard
                    )

            else:
                logger.bind(user_id=user_id, action='start').log(
                    "USER_ACTION",
                    f"Реферальный ID отсутствует или некорректен"
                )
                user = await models.User.add_user(message.from_user)

        if isinstance(message, types.CallbackQuery):
            logger.bind(user_id=user_id, action='start').log(
                "USER_ACTION",
                f"Удаление сообщения после нажатия кнопки 'start'"
            )
            await message.message.delete()

        # ✅ Гейт по пользовательскому соглашению
        if not getattr(user, "terms_accepted", False):
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅Я ознакомился", callback_data="terms_accept")]
            ])
            terms_url = "https://telegra.ph/Polzovatelskoe-soglashenie-EmailFast-01-21"
            await message.answer(
                f'Перед использованием сервиса ознакомьтесь с <a href="{terms_url}">пользовательским соглашением</a>.',
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

            return


        sub = await check_subscribe(user)
        if not sub:
            logger.bind(user_id=user_id, action='start').log(
                "USER_ACTION",
                f"Подписка не оформлена"
            )
            await send_subscribe_msg(user)
            return

        logger.bind(user_id=user_id, action='start').log(
            "USER_ACTION",
            f"Отправка главного меню"
        )
        await send_main_menu(message, bt.MAIN_MENU, parse_mode="HTML", remove_reply_kb=True)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /start: {e}")



@router.callback_query(F.data.startswith("disable_notify:"))
async def disable_notify_callback(callback: types.CallbackQuery):
    try:
        _, user_id = callback.data.split(":")
        user = await models.User.get_or_none(telegram_id=int(user_id))
        if user:
            user.disable_ref_notifications = True
            await user.save()
            await callback.message.edit_reply_markup()
            await callback.answer("Уведомления о рефералах отключены.", show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error("Ошибка при отключении уведомлений")


@router.callback_query(F.data.startswith("enable_ref_notify:"))
async def enable_ref_notify(callback: types.CallbackQuery):
    print("Уведомления о рефералах включены.")
    try:
        _, user_id = callback.data.split(":")
        user = await models.User.get_or_none(telegram_id=int(user_id))
        if user:
            user.disable_ref_notifications = False
            await user.save()
            await callback.answer("Уведомления о рефералах включены.", show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error("Ошибка при включении уведомлений")



@router.callback_query(F.data == 'check_subscribe')
async def check_subscribe_handler(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        # logger.bind(user_id=user_id, action='check_subscribe').log(
        #     "USER_ACTION",
        #     f"Проверка подписки пользователя"
        # )

        user = await models.User.get_user(user_id)
        sub = await check_subscribe(user)
        if sub:
            # logger.bind(user_id=user_id, action='check_subscribe').log(
            #     "USER_ACTION",
            #     f"Подписка подтверждена"
            # )
            await call.message.delete()
            await send_main_menu(call.message, bt.MAIN_MENU, parse_mode="HTML")
        else:
            # logger.bind(user_id=user_id, action='check_subscribe').log(
            #     "USER_ACTION",
            #     f"Подписка не найдена"
            # )
            await call.answer(text='Вы не подписаны на канал', show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере check_subscribe: {e}")


@router.callback_query(F.data == "personal_cabinet")
@router.message(Command("account"))
@router.message(F.text == bt.PERSONAL_CABINET_BTN)
async def personal_cabinet(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager):
    """
    Открывает личный кабинет.

    Поддерживает:
    - inline-кнопку главного меню;
    - старую текстовую reply-кнопку;
    - команду /account.
    """
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='personal_cabinet').log(
            "USER_ACTION",
            "Переход в личный кабинет"
        )

        user = await models.User.get_user(user_id)
        sub = await check_subscribe(user)
        if not sub:
            logger.bind(user_id=user_id, action='personal_cabinet').log(
                "USER_ACTION",
                "Подписка не оформлена"
            )
            await send_subscribe_msg(user)
            if isinstance(message, types.CallbackQuery):
                await message.answer()
            return

        logger.bind(user_id=user_id, action='personal_cabinet').log(
            "USER_ACTION",
            "Запуск диалога PersonalMenu.user_info"
        )
        await dialog_manager.start(PersonalMenu.user_info, mode=StartMode.RESET_STACK)

        if isinstance(message, types.CallbackQuery):
            await message.answer()

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере personal_cabinet: {e}")
        if isinstance(message, types.CallbackQuery):
            try:
                await message.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
            except Exception:
                pass

@router.callback_query(F.data.startswith('mail:'))
async def mail_info(call: types.CallbackQuery):
    """
    Показывает карточку арендованного FirstMail-ящика.

    Важно:
    - здесь `mail_id` фактически является lease_id;
    - для FirstMail используем отдельные callback-префиксы,
      чтобы не задевать старую mail.tm-ветку.
    """
    try:
        user_id = call.from_user.id
        lease_id = int(call.data.split(':')[1])

        logger.bind(user_id=user_id, action='mail_info').log(
            "USER_ACTION",
            f"Пользователь запросил информацию об арендованной почте lease_id={lease_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        lease = await models.RentalEmailLease.get_or_none(
            id=lease_id,
            user=user,
            is_active=True
        )

        if not lease:
            logger.bind(user_id=user_id, action='mail_info').log(
                "USER_ACTION",
                f"Аренда с lease_id={lease_id} не найдена"
            )
            await call.answer("Арендованный ящик не найден.", show_alert=True)
            return

        msg_text = bt.PAID_EMAIL_INFO.format(
            email=lease.email,
            expire_at=lease.expire_at.strftime('%d.%m.%Y')
        )

        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Продлить аренду",
                        callback_data=f'extend_rental_email:{lease.id}',
                        icon_custom_emoji_id="5397916757333654639",
                        style="success",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="Сменить Email",
                        callback_data=f'change_rental_email:{lease.id}',
                        icon_custom_emoji_id="5390863029464213754",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.BACK_BTN,
                        callback_data='my_rent_emails'
                    )
                ]
            ]
        )

        logger.bind(user_id=user_id, action='mail_info').log(
            "USER_ACTION",
            f"Отображение информации об арендованной почте '{lease.email}'"
        )

        await call.message.edit_text(text=msg_text, reply_markup=mk)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере mail_info: {e}")


def get_extend_rental_email_kb(lease_id: int, is_free_week: bool):
    """
    Клавиатура продления для FirstMail-аренды.

    Отдельный префикс callback_data нужен, чтобы не смешивать
    новую логику RentalEmailLease со старой Mail/mail.tm веткой.
    """
    builder = InlineKeyboardBuilder()

    if is_free_week:
        builder.button(text=bt.RENT_EMAIL_WEEK_BTN, callback_data=f'extend_rental_email_week:{lease_id}')
    builder.button(text=bt.RENT_EMAIL_MONTH_BTN, callback_data=f'extend_rental_email_month:{lease_id}')
    builder.button(text=bt.RENT_EMAIL_SIX_MONTHS_BTN, callback_data=f'extend_rental_email_six_months:{lease_id}')
    builder.button(text=bt.RENT_EMAIL_YEAR_BTN, callback_data=f'extend_rental_email_year:{lease_id}')
    builder.button(text=bt.BACK_BTN, callback_data=f'mail:{lease_id}')

    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data.startswith('extend_rental_email:'))
async def extend_rental_email(call: types.CallbackQuery):
    """
    Показывает варианты продления именно для арендованного FirstMail-ящика.
    """
    try:
        user_id = call.from_user.id
        lease_id = int(call.data.split(':')[1])

        logger.bind(user_id=user_id, action="extend_rental_email").log(
            "USER_ACTION",
            f"Пользователь начал продление FirstMail lease_id={lease_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        lease = await models.RentalEmailLease.get_or_none(
            id=lease_id,
            user=user,
            is_active=True,
        )

        if not lease:
            await call.answer("Арендованный ящик не найден.", show_alert=True)
            return

        keyboard = get_extend_rental_email_kb(lease.id, False)
        await call.message.edit_reply_markup(reply_markup=keyboard)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_rental_email: {e}")


@router.callback_query(F.data.startswith('extend_rental_email_'))
async def extend_rental_email_confirm(call: types.CallbackQuery, state: FSMContext):
    """
    Подтверждение выбора срока продления для FirstMail-аренды.
    """
    try:
        user_id = call.from_user.id
        callback_data = call.data.split(':')  # ['extend_rental_email_month', '123']
        data_key = callback_data[0]

        rent_data = {
            'extend_rental_email_week': RENT_EMAIL_WEEK,
            'extend_rental_email_month': RENT_EMAIL_MONTH,
            'extend_rental_email_six_months': RENT_EMAIL_SIX_MONTHS,
            'extend_rental_email_year': RENT_EMAIL_YEAR,
        }

        if data_key not in rent_data:
            logger.bind(user_id=user_id, action="extend_rental_email_confirm").log(
                "USER_ACTION",
                f"Ошибка: неизвестный ключ срока аренды: {data_key}"
            )
            await call.answer("Неверный формат запроса.", show_alert=True)
            return

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer("Пользователь не найден.", show_alert=True)
            return

        price, _, rent_text = rent_data[data_key]
        if user.balance < price:
            logger.bind(user_id=user_id, action="extend_rental_email_confirm").log(
                "USER_ACTION",
                "Ошибка: недостаточно средств"
            )
            await call.answer(text='Недостаточно средств', show_alert=True)
            return

        lease_id = int(callback_data[1])
        lease = await models.RentalEmailLease.get_or_none(
            id=lease_id,
            user=user,
            is_active=True,
        )

        if not lease:
            logger.bind(user_id=user_id, action="extend_rental_email_confirm").log(
                "USER_ACTION",
                f"Ошибка: аренда с lease_id={lease_id} не найдена"
            )
            await call.answer("Арендованный ящик не найден.", show_alert=True)
            return

        msg_text = bt.CONFIRM_EXTEND_EMAIL.format(
            email=lease.email,
            rent_text=rent_text,
            cost=price
        )

        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=bt.CONFIRM_BTN,
                        callback_data=f'confirm_{data_key}:{lease_id}'
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.BACK_BTN,
                        callback_data=f'mail:{lease_id}'
                    )
                ]
            ]
        )

        await call.message.edit_text(text=msg_text, reply_markup=mk)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_rental_email_confirm: {e}")
        await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data.startswith('confirm_extend_rental_email_'))
async def confirm_extend_rental_email(call: types.CallbackQuery):
    """
    Финально продлевает аренду FirstMail-ящика.

    Важно:
    - сбрасываем флаг уведомления об истечении аренды, потому что срок изменился;
    - если пользователь вручную продлил бесплатную неделю раньше её окончания,
      дополнительное уведомление о завершении бесплатной недели больше не нужно.
    """
    try:
        user_id = call.from_user.id
        lease_id = int(call.data.split(':')[1])

        logger.bind(user_id=user_id, action="confirm_extend_rental_email").log(
            "USER_ACTION",
            f"Подтверждение продления FirstMail lease_id={lease_id}"
        )

        lease = await models.RentalEmailLease.get_or_none(id=lease_id, is_active=True)
        if not lease:
            await call.answer("Арендованный ящик не найден.", show_alert=True)
            return

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer("Пользователь не найден.", show_alert=True)
            return

        raw_key = call.data.split(':')[0]  # confirm_extend_rental_email_month
        data_key = raw_key.replace('confirm_extend_rental_', 'rent_')

        rent_data = {
            'rent_email_week': RENT_EMAIL_WEEK,
            'rent_email_month': RENT_EMAIL_MONTH,
            'rent_email_six_months': RENT_EMAIL_SIX_MONTHS,
            'rent_email_year': RENT_EMAIL_YEAR,
        }

        if data_key not in rent_data:
            logger.bind(user_id=user_id, action="confirm_extend_rental_email").log(
                "USER_ACTION",
                f"Ошибка: неизвестный ключ срока аренды: {data_key}"
            )
            await call.answer("Неверный срок аренды.", show_alert=True)
            return

        price, days, rent_text = rent_data[data_key]

        if user.balance < price:
            logger.bind(user_id=user_id, action="confirm_extend_rental_email").log(
                "USER_ACTION",
                "Ошибка: недостаточно средств"
            )
            await call.answer(text='Недостаточно средств', show_alert=True)
            return

        lease.expire_at += timedelta(days=days)
        lease.expiration_notified = False

        update_fields = ['expire_at', 'expiration_notified']

        # Если пользователь вручную продлил ящик,
        # отдельное напоминание о завершении бесплатной недели больше не нужно.
        if lease.free_week_expires_at and not lease.free_week_notified:
            lease.free_week_notified = True
            update_fields.append('free_week_notified')

        await lease.save(update_fields=update_fields)

        low_balance = await check_low_balance(user, price)
        user.balance -= price
        await user.save(update_fields=['balance'])

        logger.bind(user_id=user_id, action="confirm_extend_rental_email").log(
            "USER_ACTION",
            f"FirstMail lease_id={lease_id} продлён на {days} дней"
        )

        msg_text = bt.EXTEND_EMAIL_SUCCESS.format(
            email=lease.email,
            rent_text=rent_text
        )
        await call.message.edit_text(text=msg_text)
        await call.answer()
        await asyncio.sleep(2)

        if low_balance:
            await send_low_balance_alert(user)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /confirm_extend_rental_email: {e}")
        await call.answer("Произошла ошибка.", show_alert=True)

@router.callback_query(F.data.startswith("change_rental_email:"))
async def change_rental_email(call: types.CallbackQuery):
    """
    Меняет именно арендованный FirstMail-ящик на новый аккаунт из пула.

    Важно:
    - mail.tm здесь не используется;
    - cooldown 24 часа действует отдельно для каждой активной аренды;
    - если cooldown ещё не закончился у конкретной аренды, сразу показываем понятный alert;
    - новый аккаунт инициализируется внутри сервисного слоя;
    - если инициализация не удалась, старая аренда восстанавливается.
    """
    lease_id: int | None = None

    try:
        user_id = call.from_user.id
        lease_id = int(call.data.split(":", 1)[1])

        logger.bind(user_id=user_id, action="change_rental_email").log(
            "USER_ACTION",
            f"Запрос смены FirstMail lease_id={lease_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        lease = await models.RentalEmailLease.get_or_none(
            id=lease_id,
            user=user,
            is_active=True,
        )

        if not lease:
            await call.answer("Арендованный ящик не найден.", show_alert=True)
            return

        # Мягкая UX-проверка cooldown по конкретной active lease,
        # чтобы пользователь сразу получил понятное сообщение.
        cooldown_remaining = await get_firstmail_change_cooldown_remaining_for_lease(lease)
        if cooldown_remaining:
            cooldown_message = build_firstmail_change_cooldown_message(cooldown_remaining)

            logger.bind(user_id=user_id, action="change_rental_email").log(
                "USER_ACTION",
                f"Смена FirstMail заблокирована cooldown: lease_id={lease_id} "
                f"remaining_seconds={int(cooldown_remaining.total_seconds())}"
            )

            await call.answer(cooldown_message, show_alert=True)
            return

        new_lease = await change_rental_email_lease(
            lease_id=lease.id,
            user=user,
        )

        msg_text = bt.PAID_EMAIL_INFO.format(
            email=new_lease.email,
            expire_at=new_lease.expire_at.strftime("%d.%m.%Y")
        )
        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=bt.EXTEND_EMAIL_BTN,
                        callback_data=f"extend_rental_email:{new_lease.id}",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.CHANGE_EMAIL_BTN,
                        callback_data=f"change_rental_email:{new_lease.id}",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.BACK_BTN,
                        callback_data="my_rent_emails"
                    )
                ],
            ]
        )

        await call.message.edit_text(text=msg_text, reply_markup=mk)
        await call.answer("✅ Почта успешно изменена", show_alert=False)

    except ValueError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере change_rental_email: {e}")
        await call.answer(str(e), show_alert=True)

    except RuntimeError as e:
        error_text = str(e)

        # Для cooldown это не ошибка приложения, а ожидаемое ограничение.
        if error_text.startswith("Сменить ящик можно не чаще 1 раза в 24 часа."):
            logger.bind(
                user_id=call.from_user.id,
                action="change_rental_email",
            ).log(
                "USER_ACTION",
                f"Смена FirstMail заблокирована сервисным cooldown: lease_id={lease_id}"
            )
            await call.answer(error_text, show_alert=True)
            return

        logger.opt(exception=e).error(f"Ошибка в хэндлере change_rental_email: {e}")
        await call.answer(error_text, show_alert=True)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере change_rental_email: {e}")
        try:
            await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        except Exception:
            pass

@router.callback_query(F.data.startswith("change_email:"))
async def change_email(call: types.CallbackQuery):
    """
    Меняет арендованный почтовый ящик на новый:
    - старый ящик деактивируется (is_active=False)
    - создаётся новый email через mail.tm (create_mail)
    - новый ящик сохраняет статус арендованного (is_paid_mail=True)
    - срок аренды переносится со старого ящика (expire_at не меняем)

    Важно:
    - Проверяем владельца почты (mail.user == текущий user), чтобы нельзя было подставить чужой mail_id.
    - Внешний вызов create_mail() делаем ДО транзакции, чтобы не держать блокировки БД.
    - Сохраняем флаг is_free_week, чтобы история использования бесплатной недели не терялась.
    """
    from tortoise.transactions import in_transaction

    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(":", 1)[1])

        logger.bind(user_id=user_id, action="change_email").log(
            "USER_ACTION",
            f"Запрос смены почты: mail_id={mail_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        # Берём текущую почту пользователя (обязательно проверяем владельца!)
        old_mail = await models.Mail.get_or_none(id=mail_id, user=user, is_paid_mail=True, is_active=True)
        if not old_mail:
            logger.bind(user_id=user_id, action="change_email").log(
                "USER_ACTION",
                f"Почта не найдена или не принадлежит пользователю: mail_id={mail_id}"
            )
            await call.answer("Почта не найдена.", show_alert=True)
            return

        old_expire_at = old_mail.expire_at

        # Лёгкий фидбек пользователю
        await call.answer("Создаю новый почтовый ящик…", show_alert=False)

        # Создаём новый email (внешний сервис) — НЕ внутри транзакции
        try:
            email, token = await create_mail()
        except Exception as e:
            logger.opt(exception=e).error(f"Ошибка create_mail() в change_email: {e}")
            await call.answer("Сервис временно недоступен, попробуйте позже🙎‍♂️", show_alert=True)
            return

        # Фиксируем смену в БД атомарно
        async with in_transaction() as conn:
            # Перепроверка и блокировка старого ящика
            old_mail_locked = await models.Mail.select_for_update().using_db(conn).get_or_none(
                id=mail_id, user=user, is_paid_mail=True
            )
            if not old_mail_locked or not old_mail_locked.is_active:
                await call.answer("Почта уже неактивна. Обновите список.", show_alert=True)
                return

            old_mail_locked.is_active = False
            await old_mail_locked.save(using_db=conn, update_fields=["is_active"])

            # Создаём новый арендованный ящик с тем же сроком аренды
            # и переносим признаки бесплатной недели/уведомления.
            new_mail = await models.Mail.create(
                using_db=conn,
                user=user,
                email=email,
                token=token,
                is_paid_mail=True,
                is_active=True,
                expire_at=old_mail_locked.expire_at,
                is_free_week=old_mail_locked.is_free_week,
                days=old_mail_locked.days,
                notification_sent=old_mail_locked.notification_sent,
            )

        logger.bind(user_id=user_id, action="change_email").log(
            "USER_ACTION",
            f"Почта изменена: old_mail_id={mail_id} -> new_mail_id={new_mail.id}, email={new_mail.email}"
        )

        msg_text = bt.PAID_EMAIL_INFO.format(
            email=new_mail.email,
            expire_at=new_mail.expire_at.strftime("%d.%m.%Y")
        )
        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=bt.EXTEND_EMAIL_BTN,
                        callback_data=f"extend_email:{new_mail.id}",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.CHANGE_EMAIL_BTN,
                        callback_data=f"change_email:{new_mail.id}",
                    )
                ],
                [
                    types.InlineKeyboardButton(text=bt.BACK_BTN, callback_data="my_rent_emails")
                ],
            ]
        )

        await call.message.edit_text(text=msg_text, reply_markup=mk)
        await call.answer("✅ Почта успешно изменена", show_alert=False)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере change_email: {e}")
        try:
            await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        except Exception:
            pass

@router.callback_query(F.data.startswith('continue_payment:'))
async def continue_payment(call: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Продолжает отложенное действие после успешной оплаты.

    Поддерживает:
    - старый flow аренды email через continue_data['email'];
    - новый flow платной смены бесплатного FirstMail через continue_data['action'] == 'change_free_firstmail';
    - старый SMS-flow через service_code.

    Важно:
    - для free FirstMail здесь НЕ списываем деньги с баланса повторно;
    - считаем, что успешная оплата уже и есть оплата действия на 50 ₽.
    """
    try:
        user_id = call.from_user.id
        payment_id = int(call.data.split(':')[1])

        logger.bind(user_id=user_id, action='continue_payment').log(
            "USER_ACTION",
            f"Продолжение оплаты для платежа ID={payment_id}"
        )

        payment = await models.Payment.get_or_none(id=payment_id)
        if not payment:
            logger.bind(user_id=user_id, action='continue_payment').log(
                "USER_ACTION",
                f"Платёж с ID={payment_id} не найден"
            )
            await call.answer()
            return

        continue_data = payment.continue_data or {}

        if continue_data.get("action") == "change_free_firstmail":
            from app.handlers.get_email_handler import try_handle_free_firstmail_receive_email
            from app.services.rental_email_pool import change_free_firstmail_assignment

            logger.bind(user_id=user_id, action='continue_payment').log(
                "USER_ACTION",
                f"Продолжение платной смены бесплатного FirstMail по платежу ID={payment_id}"
            )

            user = await models.User.get_user(user_id)
            if not user:
                await call.answer("Пользователь не найден.", show_alert=True)
                return

            assignment_id = int(continue_data.get("assignment_id", 0))
            if assignment_id <= 0:
                await call.answer("Некорректные данные для продолжения оплаты.", show_alert=True)
                return

            new_assignment = await change_free_firstmail_assignment(
                assignment_id=assignment_id,
                user=user,
            )

            logger.bind(user_id=user_id, action='continue_payment').log(
                "USER_ACTION",
                f"Платная смена бесплатного FirstMail завершена: new_assignment_id={new_assignment.id}"
            )

            await call.answer("✅ Новый почтовый ящик подготовлен", show_alert=False)

            handled = await try_handle_free_firstmail_receive_email(
                event=call,
                dialog_manager=dialog_manager,
                user=user,
            )
            if handled:
                return

            return

        if 'email' in continue_data:
            logger.bind(user_id=user_id, action='continue_payment').log(
                "USER_ACTION",
                f"Старт диалога ReceiveEmailMenu.rent_email_confirm"
            )
            await dialog_manager.start(
                ReceiveEmailMenu.rent_email_confirm,
                data=continue_data,
                mode=StartMode.RESET_STACK
            )
            return

        logger.bind(user_id=user_id, action='continue_payment').log(
            "USER_ACTION",
            f"Вызов send_country_info для продолжения оплаты"
        )
        await send_country_info(continue_data['service_code'], call, dialog_manager)

    except ValueError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере continue_payment: {e}")
        await call.answer(str(e), show_alert=True)

    except RuntimeError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере continue_payment: {e}")
        await call.answer(str(e), show_alert=True)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере continue_payment: {e}")
        await call.answer("Не удалось продолжить оплату. Попробуйте позже.", show_alert=True)

@router.callback_query(F.data.startswith('bonus_price:'))
async def bonus_price(call: types.CallbackQuery, dialog_manager: DialogManager):
    try:
        user_id = call.from_user.id
        price = call.data.split(':')[1]
        logger.bind(user_id=user_id, action='bonus_price').log(
            "USER_ACTION",
            f"Выбрана сумма бонуса: {price}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            logger.bind(user_id=user_id, action='bonus_price').log(
                "USER_ACTION",
                f"Пользователь не найден"
            )
            return

        if price == 'other':
            logger.bind(user_id=user_id, action='bonus_price').log(
                "USER_ACTION",
                f"Запрос ввода произвольной суммы"
            )
            await call.message.edit_reply_markup()
            await dialog_manager.start(PersonalMenu.enter_amount, mode=StartMode.RESET_STACK)
        else:
            # сохраняем сумму в dialog_data до вызова send_payment_keyboard
            dialog_manager.current_context().dialog_data['price'] = float(price)

            logger.bind(user_id=user_id, action='bonus_price').log(
                "USER_ACTION",
                f"Отправка клавиатуры оплаты на сумму {price}"
            )
            from app.dialogs.personal_cabinet.selected import send_payment_keyboard
            await send_payment_keyboard(call, manager=dialog_manager, price=float(price))

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере bonus_price: {e}")



@router.message(Command('rent'))
async def rent(message: types.Message, dialog_manager: DialogManager):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='rent').log(
            "USER_ACTION",
            f"Пользователь начал процесс аренды"
        )
        from app.dialogs.receive_email.selected import on_rent_email_check_discount
        await on_rent_email_check_discount(message, dialog_manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /rent: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith('rental'))
async def process_rent_callback(message: types.Message, dialog_manager: DialogManager):
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action='process_rent_callback').log(
            "USER_ACTION",
            f"Обработка inline-кнопки 'Продлить аренду'"
        )
        from app.dialogs.receive_email.selected import on_rent_email_check_discount
        await on_rent_email_check_discount(message, dialog_manager)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере process_rent_callback: {e}")


@router.message(Command("test_notify"))
async def test_balance_notify(m: types.Message):
    user = await models.User.get_or_none(telegram_id=m.from_user.id)
    if not user:
        await m.answer("Пользователь не найден в базе.")
        return

    # Пример: создаём тестовый платеж (не сохраняем в БД, можно мокнуть)
    class DummyPayment:
        def __init__(self, user):
            self.user = user
            self.amount = 123.45

    payment = DummyPayment(user=user)

    # Название сервиса может быть "LAVA", "YOOMONEY", и т.д.
    await balance_replenishment_notification(payment, service="LAVA")
    await m.answer("Тестовое уведомление отправлено.")