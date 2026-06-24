from datetime import timedelta
from typing import Union
import html
import asyncio
from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram_dialog import DialogManager, StartMode
from app.db import models
from app.services import bot_texts as bt
from app.services.bot_texts import RENT_EMAIL_WEEK, RENT_EMAIL_MONTH, RENT_EMAIL_SIX_MONTHS, \
    RENT_EMAIL_YEAR

from app.services.low_balance import check_low_balance, send_low_balance_alert
from app.services.mail.temp_mail_tm import create_mail
from app.services.need_subscribe import check_subscribe, send_subscribe_msg
from loguru import logger


from app.dependencies import FREE_EMAIL_PROVIDER

from app.services.rental_email_pool import (
    change_free_firstmail_assignment,
    get_active_free_firstmail_for_user,
    get_free_firstmail_assignment_for_user,
    issue_free_firstmail,
    pull_free_firstmail_messages,
    pull_rental_email_messages,
)

router = Router()



def _build_free_firstmail_text(
    assignment: models.FreeFirstMailAssignment,
    new_messages_count: int | None = None,
) -> str:
    """
    Формирует текст карточки для бесплатного FirstMail-ящика.

    Важно:
    - это отдельная ветка от legacy mail.tm;
    - срок действия бесплатного FirstMail не ограничен;
    - смена ящика платная: 50 ₽;
    - оформление меняем только для этого экрана.
    """
    email_text = html.escape(assignment.email)

    text = (
        '<b>Ваш Email <tg-emoji emoji-id="5197474438970363734">⤵️</tg-emoji></b>\n'
        f"{email_text}\n\n"
        '<tg-emoji emoji-id="5258258882022612173">⏲</tg-emoji>Срок действия Email неограничен\n'
        '<tg-emoji emoji-id="5257965174979042426">⏲</tg-emoji>Как только на почту придет сообщение, оно сразу же отобразится в боте\n\n'
        'Аренда Email позволяет бесплатно менять адрес почты 2 раза в сутки.\n'
        'Подходит для регистрации во множестве сервисов. В отличие от временной почты, вероятность приёма писем выше'
    )

    if new_messages_count is not None:
        if new_messages_count == 0:
            text += "\n\n<i>Новых писем пока нет.</i>"
        else:
            text += f"\n\n<b>Найдено новых писем:</b> {new_messages_count}"

    return text


def _build_free_firstmail_markup(
    assignment_id: int,
    show_back: bool = False,
) -> types.InlineKeyboardMarkup:
    """
    Формирует клавиатуру для бесплатного FirstMail-ящика.

    Важно:
    - бесплатный ящик можно платно менять;
    - отсюда должен быть доступ к аренде FirstMail;
    - отсюда должен быть доступ к списку арендованных ящиков;
    - premium emoji и стиль задаём локально, чтобы не менять другие экраны.
    """
    inline_keyboard = [
        [
            types.InlineKeyboardButton(
                text="Сменить Email (50₽)",
                callback_data=f"change_free_firstmail:{assignment_id}",
                icon_custom_emoji_id="5390863029464213754",
            )
        ],
        [
            types.InlineKeyboardButton(
                text="Арендовать Email",
                callback_data="rent_email",
                icon_custom_emoji_id="5397916757333654639",
                style="success",
            )
        ],
        [
            types.InlineKeyboardButton(
                text="Мои Email",
                callback_data="my_rent_emails",
                icon_custom_emoji_id="5406631276042002796",
            )
        ],
        [                                               # ← ДОБАВИТЬ
            types.InlineKeyboardButton(
                text="Назад",
                callback_data="back_to_main_from_email",
                icon_custom_emoji_id="5258236805890710909",  # ⬅️
            )
        ],
    ]

    return types.InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

@router.callback_query(F.data == 'back_to_main_from_email')
async def back_to_main_from_email(call: types.CallbackQuery):
    """
    Возвращает пользователя в главное меню из экрана бесплатного FirstMail.
    """
    try:
        await call.message.delete()
        from app.services.keyboards import send_main_menu
        from app.services import bot_texts as bt
        await send_main_menu(call.message, bt.MAIN_MENU, parse_mode="HTML")
        await call.answer()
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в back_to_main_from_email: {e}")

@router.callback_query(F.data == 'rent_email')
async def rent_email_callback(call: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Открывает flow аренды FirstMail по inline-кнопке.

    Важно:
    - используется в том числе из карточки бесплатного FirstMail;
    - переводит пользователя в уже существующий flow аренды;
    - не зависит от legacy mail.tm.
    """
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action='rent_email_callback').log(
            "USER_ACTION",
            "Пользователь открыл аренду почты из inline-кнопки"
        )

        from app.dialogs.receive_email.selected import on_rent_email_check_discount
        await on_rent_email_check_discount(call, dialog_manager)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере rent_email_callback: {e}")
        try:
            await call.answer("Не удалось открыть аренду почты. Попробуйте позже.", show_alert=True)
        except Exception:
            pass

async def _show_free_firstmail_screen(
    event: Union[types.Message, types.CallbackQuery],
    text: str,
    reply_markup: types.InlineKeyboardMarkup,
) -> None:
    """
    Унифицированно показывает экран бесплатного FirstMail для Message/CallbackQuery.
    """
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text=text, reply_markup=reply_markup)
        await event.answer()
        return

    await event.answer(text=text, reply_markup=reply_markup)


async def try_handle_free_firstmail_receive_email(
    event: Union[types.Message, types.CallbackQuery],
    dialog_manager: DialogManager,
    user: models.User,
) -> bool:
    """
    Обрабатывает ветку бесплатного FirstMail по переключателю FREE_EMAIL_PROVIDER.

    Возвращает:
        True  -> запрос полностью обработан как free FirstMail;
        False -> нужно продолжать legacy-логику mail.tm ниже по коду.
    """
    if FREE_EMAIL_PROVIDER != "firstmail":
        return False

    user_id = event.from_user.id

    try:
        assignment = await get_active_free_firstmail_for_user(user)

        if not assignment:
            assignment = await issue_free_firstmail(user)

        text = _build_free_firstmail_text(assignment)
        markup = _build_free_firstmail_markup(
            assignment_id=assignment.id,
            show_back=isinstance(event, types.CallbackQuery),
        )

        await _show_free_firstmail_screen(
            event=event,
            text=text,
            reply_markup=markup,
        )

        logger.bind(
            user_id=user_id,
            action="try_handle_free_firstmail_receive_email",
        ).log(
            "USER_ACTION",
            f"Показан бесплатный FirstMail: assignment_id={assignment.id} email={assignment.email}"
        )
        return True

    except RuntimeError as e:
        logger.opt(exception=e).error(
            f"Ошибка в free FirstMail receive_email: user_id={user_id} err={e}"
        )

        if isinstance(event, types.CallbackQuery):
            await event.answer(str(e), show_alert=True)
        else:
            await event.answer(str(e))
        return True

    except Exception as e:
        logger.opt(exception=e).error(
            f"Неожиданная ошибка в free FirstMail receive_email: user_id={user_id} err={e}"
        )

        if isinstance(event, types.CallbackQuery):
            await event.answer("Не удалось открыть почтовый ящик. Попробуйте позже.", show_alert=True)
        else:
            await event.answer("Не удалось открыть почтовый ящик. Попробуйте позже.")
        return True


@router.callback_query(F.data.startswith("receive_my_free_firstmail:"))
async def receive_my_free_firstmail(call: types.CallbackQuery):
    """
    Ручное получение новых писем для бесплатного FirstMail-ящика.

    Важно:
    - используем отдельную модель FreeFirstMailAssignment;
    - письма читаются через общий helper pull_free_firstmail_messages();
    - old_messages_id / is_initialized обновляются в сервисном слое.
    """
    try:
        user_id = call.from_user.id
        assignment_id = int(call.data.split(":", 1)[1])

        logger.bind(user_id=user_id, action="receive_my_free_firstmail").log(
            "USER_ACTION",
            f"Ручная проверка бесплатного FirstMail assignment_id={assignment_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        assignment = await get_free_firstmail_assignment_for_user(
            assignment_id=assignment_id,
            user=user,
        )

        if not assignment or not assignment.is_active:
            await call.answer("Бесплатный почтовый ящик не найден.", show_alert=True)
            return

        assignment, messages = await pull_free_firstmail_messages(
            assignment_id=assignment.id,
            limit=5,
        )

        base_text = _build_free_firstmail_text(
            assignment=assignment,
            new_messages_count=len(messages),
        )
        mk = _build_free_firstmail_markup(
            assignment_id=assignment.id,
            show_back=True,
        )

        await call.message.edit_text(
            text=base_text,
            reply_markup=mk,
        )

        for message_obj in messages:
            from_text = html.escape(message_obj.from_header or "-")
            subject_text = html.escape(message_obj.subject or "(без темы)")
            content_text = html.escape((message_obj.content or "").strip() or "Нет текста в сообщении.")

            if len(content_text) > 3500:
                content_text = content_text[:3500] + "\n\n...[обрезано]"

            msg_text = (
                f'<tg-emoji emoji-id="5472239203590888751">📩</tg-emoji><b>Новое сообщение</b> на почту: <b>{html.escape(assignment.email)}</b>\n\n'
                f"<b>От кого:</b> {from_text}\n"
                f"<b>Тема:</b> {subject_text}\n\n"
                f"{content_text}"
            )

            await call.message.answer(msg_text)

        logger.bind(user_id=user_id, action="receive_my_free_firstmail").log(
            "USER_ACTION",
            f"Проверка бесплатного FirstMail завершена | assignment_id={assignment.id} new_messages={len(messages)}"
        )

    except ValueError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере receive_my_free_firstmail: {e}")
        await call.answer(str(e), show_alert=True)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере receive_my_free_firstmail: {e}")
        await call.answer("Не удалось получить письма. Попробуйте ещё раз позже.", show_alert=True)

@router.callback_query(F.data.startswith("change_free_firstmail:"))
async def change_free_firstmail(call: types.CallbackQuery):
    """
    Показывает подтверждение перед платной сменой бесплатного FirstMail-ящика.

    Сама смена выполняется в confirm_change_free_firstmail после подтверждения,
    «Отмена» возвращает карточку через cancel_change_free_firstmail.
    """
    try:
        user_id = call.from_user.id
        assignment_id = int(call.data.split(":", 1)[1])

        logger.bind(user_id=user_id, action="change_free_firstmail").log(
            "USER_ACTION",
            f"Запрос подтверждения платной смены бесплатного FirstMail: assignment_id={assignment_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        assignment = await get_free_firstmail_assignment_for_user(
            assignment_id=assignment_id,
            user=user,
        )

        if not assignment or not assignment.is_active:
            await call.answer("Бесплатный почтовый ящик не найден.", show_alert=True)
            return

        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Сменить Email",
                        callback_data=f"confirm_change_free_firstmail:{assignment_id}",
                        icon_custom_emoji_id="5390863029464213754",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="Отмена",
                        callback_data=f"cancel_change_free_firstmail:{assignment_id}",
                    )
                ],
            ]
        )

        await call.message.edit_text(text=bt.CONFIRM_CHANGE_EMAIL, reply_markup=mk)
        await call.answer()

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере change_free_firstmail: {e}")
        try:
            await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        except Exception:
            pass


@router.callback_query(F.data.startswith("cancel_change_free_firstmail:"))
async def cancel_change_free_firstmail(call: types.CallbackQuery):
    """
    Возвращает карточку бесплатного FirstMail-ящика после отмены подтверждения.
    """
    try:
        user_id = call.from_user.id
        assignment_id = int(call.data.split(":", 1)[1])

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        assignment = await get_free_firstmail_assignment_for_user(
            assignment_id=assignment_id,
            user=user,
        )

        if not assignment or not assignment.is_active:
            await call.answer("Бесплатный почтовый ящик не найден.", show_alert=True)
            return

        msg_text = _build_free_firstmail_text(assignment)
        mk = _build_free_firstmail_markup(
            assignment_id=assignment.id,
            show_back=True,
        )

        await call.message.edit_text(text=msg_text, reply_markup=mk)
        await call.answer()

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере cancel_change_free_firstmail: {e}")
        try:
            await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        except Exception:
            pass


@router.callback_query(F.data.startswith("confirm_change_free_firstmail:"))
async def confirm_change_free_firstmail(call: types.CallbackQuery, dialog_manager: DialogManager):
    """
    Меняет бесплатный FirstMail-ящик пользователя.

    Логика:
    - если на балансе хватает 50 ₽, списываем их и сразу меняем ящик;
    - если не хватает, открываем отдельный платёжный flow на 50 ₽;
    - после успешной смены показываем новую карточку ящика.

    Важно:
    - это отдельная ветка от арендованного FirstMail;
    - здесь нет продления, только платная смена;
    - PersonalMenu импортируем локально, чтобы не создавать цикл импортов
      через app.dialogs -> periodic_tasks -> get_email_handler.
    """
    try:
        user_id = call.from_user.id
        assignment_id = int(call.data.split(":", 1)[1])
        change_cost = 50.0

        logger.bind(user_id=user_id, action="confirm_change_free_firstmail").log(
            "USER_ACTION",
            f"Подтверждена платная смена бесплатного FirstMail: assignment_id={assignment_id}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer()
            return

        assignment = await get_free_firstmail_assignment_for_user(
            assignment_id=assignment_id,
            user=user,
        )

        if not assignment or not assignment.is_active:
            await call.answer("Бесплатный почтовый ящик не найден.", show_alert=True)
            return

        if float(user.balance or 0) < change_cost:
            from app.dialogs.personal_cabinet.states import PersonalMenu
            from app.dialogs.personal_cabinet.selected import send_payment_keyboard

            continue_data = {
                "action": "change_free_firstmail",
                "assignment_id": assignment.id,
                "price": change_cost,
            }

            await dialog_manager.start(
                PersonalMenu.deposit,
                data=continue_data,
                mode=StartMode.RESET_STACK,
            )
            dialog_manager.current_context().dialog_data["price"] = change_cost

            await send_payment_keyboard(
                call,
                manager=dialog_manager,
                price=change_cost,
            )

            await call.answer("Недостаточно средств. Открываю оплату на 50 ₽.", show_alert=False)
            return

        # Проверяем порог низкого баланса ДО списания,
        # чтобы корректно понять, пересечёт ли пользователь границу в 50 ₽ после оплаты.
        low_balance = await check_low_balance(user, change_cost)

        old_balance = float(user.balance or 0)
        user.balance = round(old_balance - change_cost, 2)
        await user.save(update_fields=["balance"])

        try:
            new_assignment = await change_free_firstmail_assignment(
                assignment_id=assignment.id,
                user=user,
            )
        except Exception:
            # Если смена ящика не удалась, возвращаем баланс назад.
            user.balance = old_balance
            await user.save(update_fields=["balance"])
            raise

        if low_balance:
            await send_low_balance_alert(user)

        msg_text = _build_free_firstmail_text(new_assignment)
        mk = _build_free_firstmail_markup(
            assignment_id=new_assignment.id,
            show_back=True,
        )

        await call.message.edit_text(
            text=msg_text,
            reply_markup=mk,
        )
        await call.answer("✅ Почтовый ящик успешно изменён", show_alert=False)

    except ValueError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере confirm_change_free_firstmail: {e}")
        await call.answer(str(e), show_alert=True)

    except RuntimeError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере confirm_change_free_firstmail: {e}")
        await call.answer(str(e), show_alert=True)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере confirm_change_free_firstmail: {e}")
        try:
            await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        except Exception:
            pass


@router.message(Command("get_email"))  # Обработка команды /get_email
@router.message(F.text == bt.RECEIVE_EMAIL_BTN)  # кнопка '📩Принять Email'
@router.callback_query(F.data == 'receive_email')  # Обработка коллбэк-запросов с данными 'receive_email'
async def receive_email(message: Union[types.Message, types.CallbackQuery], dialog_manager: DialogManager):
    """
    Открывает пользователю экран получения email.

    Важно:
    - при FREE_EMAIL_PROVIDER=firstmail уходим в новую ветку бесплатного FirstMail;
    - legacy mail.tm остаётся рабочей;
    - ReceiveEmailMenu импортируем локально, чтобы не создавать цикл импортов
      через app.dialogs -> periodic_tasks -> get_email_handler.
    """
    try:
        user_id = message.from_user.id
        logger.bind(user_id=user_id, action="receive_email").log(
            "USER_ACTION",
            "Пользователь запросил получение email"
        )

        user = await models.User.get_user(user_id)
        if not user:
            return

        sub = await check_subscribe(user)
        if not sub:
            logger.bind(user_id=user_id, action="receive_email").log(
                "USER_ACTION",
                "Подписка неактивна, отправляем сообщение"
            )
            await send_subscribe_msg(user)
            return

        handled_free_firstmail = await try_handle_free_firstmail_receive_email(
            event=message,
            dialog_manager=dialog_manager,
            user=user,
        )
        if handled_free_firstmail:
            return

        logger.bind(user_id=user_id, action="receive_email").log(
            "USER_ACTION",
            f"Запрос к БД: получение непрочитанных писем для {user_id}"
        )
        mails = await models.Mail.filter(
            user=user,
            is_paid_mail=False,
            is_active=True,
        ).all()
        logger.bind(user_id=user_id, action="receive_email").log(
            "USER_ACTION",
            f"Результат из БД: найдено писем={len(mails)}"
        )

        if len(mails) > 0:
            mail = mails[-1]
        else:
            if isinstance(message, types.CallbackQuery):
                temp_mail = message.message
                await temp_mail.edit_text(text=bt.CREATING_EMAIL)
            else:
                temp_mail = await message.answer(text=bt.CREATING_EMAIL)

            logger.bind(user_id=user_id, action="receive_email").log(
                "USER_ACTION",
                "Создание нового временного email"
            )
            try:
                email, token = await create_mail()
            except Exception as e:
                logger.opt(exception=e).error(f"Ошибка при создании email в /receive_email: {e}")
                await message.answer("В настоящее время сервис недоступен, попробуйте позже🙎‍♂️")
                return

            mail = await models.Mail.add_mail(user, email, token)
            logger.bind(user_id=user_id, action="receive_email").log(
                "USER_ACTION",
                f"Новый email создан: {email}"
            )

            # ✅ После первого созданного email включаем обязательную проверку подписки
            if not getattr(user, "channel_gate_enabled", True):
                user.channel_gate_enabled = True
                await user.save(update_fields=["channel_gate_enabled"])

            await temp_mail.delete()

        # Локальный импорт, чтобы не поднимать app.dialogs на уровне модуля.
        from app.dialogs.receive_email.states import ReceiveEmailMenu

        logger.bind(user_id=user_id, action="receive_email").log(
            "USER_ACTION",
            f"Запуск диалога с mail_id={mail.id}"
        )
        await dialog_manager.start(
            ReceiveEmailMenu.receive_email,
            data={"mail_id": mail.id},
            mode=StartMode.RESET_STACK
        )

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_email: {e}")

@router.callback_query(F.data == 'my_rent_emails')
async def my_rent_emails(call: types.CallbackQuery):
    """
    Показывает список активных арендованных FirstMail-ящиков пользователя.

    Важно:
    - используем новую модель RentalEmailLease;
    - callback_data оставляем в формате `mail:{lease_id}`,
      чтобы не ломать существующую навигацию.
    """
    try:
        user_id = call.from_user.id
        logger.bind(user_id=user_id, action="my_rent_emails").log(
            "USER_ACTION",
            "Пользователь открыл список арендованных почт"
        )

        user = await models.User.get_user(user_id)
        if not user:
            return

        leases = await models.RentalEmailLease.filter(
            user=user,
            is_active=True
        ).order_by("-id").all()

        logger.bind(user_id=user_id, action="my_rent_emails").log(
            "USER_ACTION",
            f"Результат из БД: найдено арендованных почт={len(leases)}"
        )

        if len(leases) == 0:
            logger.bind(user_id=user_id, action="my_rent_emails").log(
                "USER_ACTION",
                "Нет арендованных почт"
            )
            await call.answer(text='У вас нет арендованных почтовых ящиков', show_alert=True)
            return

        builder = InlineKeyboardBuilder()
        for lease in leases:
            builder.add(
                types.InlineKeyboardButton(
                    text=lease.email,
                    callback_data=f'mail:{lease.id}'
                )
            )

        builder.button(
            text=bt.BACK_BTN,
            callback_data='receive_email',
            icon_custom_emoji_id="5258236805890710909",  # ⬅️
        )
        builder.adjust(1)

        await call.message.edit_text(
            text=bt.CHOOSE_A_MAILBOX,
            reply_markup=builder.as_markup(), parse_mode="HTML"
        )

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /my_rent_emails: {e}")


@router.callback_query(F.data.startswith('receive_my_mail:'))
async def receive_my_mail(call: types.CallbackQuery):
    """
    Ручное получение новых писем для арендованного FirstMail-ящика.

    Важно:
    - используем общий helper, чтобы ручная проверка и scheduler
      не дублировали письма внутри одного процесса;
    - если ящик ещё не был инициализирован, helper выполнит
      тихую инициализацию через old_messages_id / is_initialized.
    """
    try:
        user_id = call.from_user.id
        lease_id = int(call.data.split(':')[1])

        logger.bind(user_id=user_id, action="receive_my_mail").log(
            "USER_ACTION",
            f"Ручная проверка FirstMail lease_id={lease_id}"
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
            await call.answer("Арендованный ящик не найден.", show_alert=True)
            return

        lease, messages = await pull_rental_email_messages(
            lease_id=lease.id,
            limit=5,
        )

        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=bt.BACK_BTN,
                        callback_data=f'mail:{lease.id}'
                    )
                ]
            ]
        )

        base_text = bt.MY_RENT_EMAIL.format(
            email=lease.email,
            expire_at=lease.expire_at.strftime('%d.%m.%Y')
        )

        if not messages:
            base_text += (
                "\n\n"
                "<i>Новых писем пока нет.</i>"
            )
        else:
            base_text += (
                "\n\n"
                f"<b>Найдено новых писем:</b> {len(messages)}"
            )

        await call.message.edit_text(
            text=base_text,
            reply_markup=mk
        )

        for message_obj in messages:
            from_text = html.escape(message_obj.from_header or "-")
            subject_text = html.escape(message_obj.subject or "(без темы)")
            content_text = html.escape((message_obj.content or "").strip() or "Нет текста в сообщении.")

            if len(content_text) > 3500:
                content_text = content_text[:3500] + "\n\n...[обрезано]"

            msg_text = (
                f'<tg-emoji emoji-id="5472239203590888751">📩</tg-emoji><b>Новое сообщение</b> на почту: <b>{html.escape(lease.email)}</b>\n\n'
                f"<b>От кого:</b> {from_text}\n"
                f"<b>Тема:</b> {subject_text}\n\n"
                f"{content_text}"
            )

            await call.message.answer(msg_text)

        logger.bind(user_id=user_id, action="receive_my_mail").log(
            "USER_ACTION",
            f"Проверка FirstMail завершена | lease_id={lease_id} new_messages={len(messages)}"
        )

    except ValueError as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_my_mail: {e}")
        await call.answer(str(e), show_alert=True)
    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /receive_my_mail: {e}")
        await call.answer("Не удалось получить письма. Попробуйте ещё раз позже.", show_alert=True)

@router.callback_query(F.data.startswith('extend_email:'))
async def extend_email(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(':')[1])
        user = await models.User.get_user(user_id)

        logger.bind(user_id=user_id, action="extend_email").log("USER_ACTION", f"Пользователь начал продление почты ID={mail_id}")

        mail = await models.Mail.filter(user=user).order_by('-id').first()

        keyboard = get_extend_email_kb(mail_id, False)
        await call.message.edit_reply_markup(reply_markup=keyboard)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_email: {e}")



def get_extend_email_kb(mail_id: int, is_free_week: bool):
    builder = InlineKeyboardBuilder()

    if is_free_week:
        builder.button(text=bt.RENT_EMAIL_WEEK_BTN, callback_data=f'extend_email_week:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_MONTH_BTN, callback_data=f'extend_email_month:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_SIX_MONTHS_BTN, callback_data=f'extend_email_six_months:{mail_id}')
    builder.button(text=bt.RENT_EMAIL_YEAR_BTN, callback_data=f'extend_email_year:{mail_id}')
    builder.button(text=bt.BACK_BTN, callback_data=f'mail:{mail_id}')

    builder.adjust(1)
    return builder.as_markup()

@router.callback_query(F.data.startswith('extend_email_'))
async def extend_email_confirm(call: types.CallbackQuery, state: FSMContext):
    try:
        user_id = call.from_user.id
        callback_data = call.data.split(':')  # ['extend_email_month', '6901']
        data_key = callback_data[0]  # например, 'extend_email_month'

        rent_data = {
            'extend_email_week': RENT_EMAIL_WEEK,
            'extend_email_month': RENT_EMAIL_MONTH,
            'extend_email_six_months': RENT_EMAIL_SIX_MONTHS,
            'extend_email_year': RENT_EMAIL_YEAR,
        }

        if data_key not in rent_data:
            logger.bind(user_id=user_id, action="extend_email_confirm").log(
                "USER_ACTION", f"Ошибка: неизвестный ключ срока аренды: {data_key}"
            )
            await call.answer("Неверный формат запроса.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="extend_email_confirm").log(
            "USER_ACTION", f"Пользователь выбрал срок: {data_key}"
        )

        user = await models.User.get_user(user_id)

        price, _, rent_text = rent_data[data_key]
        if user.balance < price:
            logger.bind(user_id=user_id, action="extend_email_confirm").log(
                "USER_ACTION", "Ошибка: недостаточно средств"
            )
            await call.answer(text='Недостаточно средств', show_alert=True)
            return

        mail_id = int(callback_data[1])
        logger.bind(user_id=user_id, action="extend_email_confirm").log(
            "USER_ACTION", f"Запрос к БД: получение почты ID={mail_id}"
        )
        mail = await models.Mail.get_or_none(id=mail_id)

        if not mail:
            logger.bind(user_id=user_id, action="extend_email_confirm").log(
                "USER_ACTION", f"Ошибка: почта с ID={mail_id} не найдена"
            )
            await call.answer("Почта не найдена.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="extend_email_confirm").log(
            "USER_ACTION", f"Результат из БД: почта={mail.email}"
        )

        msg_text = bt.CONFIRM_EXTEND_EMAIL.format(
            email=mail.email,
            rent_text=rent_text,
            cost=price
        )

        mk = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=bt.CONFIRM_BTN,
                        callback_data=f'confirm_{data_key}:{mail_id}'
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=bt.BACK_BTN,
                        callback_data=f'mail:{mail_id}'
                    )
                ]
            ]
        )

        await call.message.edit_text(text=msg_text, reply_markup=mk)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /extend_email_confirm: {e}")
        await call.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)



@router.callback_query(F.data.startswith('confirm_extend_email_'))
async def confirm_extend_email(call: types.CallbackQuery):
    try:
        user_id = call.from_user.id
        mail_id = int(call.data.split(':')[1])
        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Подтверждение продления почты ID={mail_id}"
        )

        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Запрос к БД: получение почты ID={mail_id}"
        )
        mail = await models.Mail.get_or_none(id=mail_id)

        if not mail:
            await call.answer("Почта не найдена.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Результат из БД: почта={mail.email}"
        )

        user = await models.User.get_user(user_id)
        if not user:
            await call.answer("Пользователь не найден.", show_alert=True)
            return

        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Результат из БД: баланс={user.balance}"
        )

        raw_key = call.data.split(':')[0]  # confirm_extend_email_month
        data_key = raw_key.replace('confirm_extend_', 'rent_')

        rent_data = {
            'rent_email_week': RENT_EMAIL_WEEK,
            'rent_email_month': RENT_EMAIL_MONTH,
            'rent_email_six_months': RENT_EMAIL_SIX_MONTHS,
            'rent_email_year': RENT_EMAIL_YEAR,
        }

        if data_key not in rent_data:
            logger.bind(user_id=user_id, action="confirm_extend_email").log(
                "USER_ACTION", f"Ошибка: неизвестный ключ срока аренды: {data_key}"
            )
            await call.answer("Неверный срок аренды.", show_alert=True)
            return

        price, days, rent_text = rent_data[data_key]

        if user.balance < price:
            logger.bind(user_id=user_id, action="confirm_extend_email").log(
                "USER_ACTION", "Ошибка: недостаточно средств"
            )
            await call.answer(text='Недостаточно средств', show_alert=True)
            return

        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Обновление срока аренды: +{days} дней"
        )
        mail.expire_at += timedelta(days=days)
        await mail.save(update_fields=['expire_at'])

        low_balance = await check_low_balance(user, price)
        user.balance -= price
        await user.save(update_fields=['balance'])
        logger.bind(user_id=user_id, action="confirm_extend_email").log(
            "USER_ACTION", f"Баланс обновлён: новый баланс={user.balance}"
        )

        msg_text = bt.EXTEND_EMAIL_SUCCESS.format(
            email=mail.email,
            rent_text=rent_text
        )
        await call.message.edit_text(text=msg_text)
        await call.answer()
        await asyncio.sleep(2)

        if low_balance:
            logger.bind(user_id=user_id, action="confirm_extend_email").log(
                "USER_ACTION", "Отправка уведомления о низком балансе"
            )
            await send_low_balance_alert(user)

    except Exception as e:
        logger.opt(exception=e).error(f"Ошибка в хэндлере /confirm_extend_email: {e}")
        await call.answer("Произошла ошибка.", show_alert=True)
