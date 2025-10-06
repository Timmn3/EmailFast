from aiogram import Router, types
from aiogram.filters import Command
from loguru import logger
from app.dependencies import bot, ADMINS, USER_ACCESS_TO_THE_COMMAND, REFERRAL_PREFIX, REFERRAL_PREFIX_WHODI, \
    REFERRAL_PREFIX_SILOBUS
from app.db import models
from app.db.models import ReferralLink
import re

router = Router()


# ===========================
# Вспомогательные функции
# ===========================
def extract_link_number(link_code: str) -> int:
    """
    Извлечь порядковый номер из кода персональной ссылки.
    Пример: '1089138631_7' -> 7
    """
    match = re.search(r'_(\d+)$', link_code)
    return int(match.group(1)) if match else 0


async def _show_links(
    message: types.Message,
    ref_tg_id: int | None,
    human_name: str
) -> None:
    """
    Показать статистику по персональным ссылкам указанного реферера.
    """
    if ref_tg_id is None:
        await message.answer(f"Для {human_name} не задан REFERRAL_PREFIX в config.yaml.")
        return

    # Проверка прав
    if message.from_user.id not in ADMINS and message.from_user.id != USER_ACCESS_TO_THE_COMMAND:
        logger.bind(user_id=message.from_user.id, action=f"{human_name.lower()}_links_denied") \
              .log("USER_ACTION", f"Команда /{human_name.lower()}_links не может быть вызвана")
        return

    logger.bind(user_id=message.from_user.id, action=f"{human_name.lower()}_links") \
          .log("USER_ACTION", f"Команда /{human_name.lower()}_links вызвана")

    ref_user = await models.User.get_or_none(telegram_id=ref_tg_id)
    if not ref_user:
        await message.answer(f"Пользователь {human_name} не найден.")
        return

    links = await models.ReferralLink.filter(user=ref_user).all()
    links = sorted(links, key=lambda link: extract_link_number(link.link_code))

    if not links:
        await message.answer(f"У {human_name} пока нет реферальных ссылок.")
        return

    bot_username = (await bot.me()).username
    lines: list[str] = []

    for link in links:
        url = f"https://t.me/{bot_username}?start={link.link_code}"
        total_sum = f"{(link.total_payment_amount or 0):.2f}₽"
        lines.append(
            f"<b>Ссылка:</b> <code>{url}</code>\n"
            f"├ Запусков бота: <b>{link.total_starts}</b>\n"
            f"└ Оплат: <b>{link.total_pays}</b> (на сумму <b>{total_sum}</b>)\n"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


async def _create_next_link(
    message: types.Message,
    ref_tg_id: int | None,
    human_name: str
) -> None:
    """
    Создать следующую по порядку персональную ссылку для указанного реферера.
    Результат: <REFERRAL_PREFIX>_<N+1>
    """
    if ref_tg_id is None:
        await message.answer(f"Для {human_name} не задан REFERRAL_PREFIX в config.yaml.")
        return

    if message.from_user.id not in ADMINS:
        return

    logger.bind(user_id=message.from_user.id, action=f"create_{human_name.lower()}_links") \
          .log("USER_ACTION", f"Команда /create_{human_name.lower()}_links вызвана")

    ref_user = await models.User.get_or_none(telegram_id=ref_tg_id)
    if not ref_user:
        await message.answer(f"{human_name} не найден в базе данных.")
        return

    # Берём все ссылки и ищем максимальный суффикс
    user_links = await ReferralLink.filter(user=ref_user).all()

    max_number = 0
    for link in user_links:
        try:
            suffix = int(link.link_code.split('_')[-1])
            if suffix > max_number:
                max_number = suffix
        except (ValueError, IndexError):
            continue

    new_suffix = max_number + 1
    link_code = f"{ref_tg_id}_{new_suffix}"

    # Создаём или возвращаем существующую
    await ReferralLink.get_or_create_link(user=ref_user, link_code=link_code)

    await message.answer(f"Создана новая реферальная ссылка: <code>{link_code}</code>", parse_mode="HTML")


# ===========================
# Команды для Петра
# ===========================
@router.message(Command('petr_links'))
async def petr_links(message: types.Message):
    allowed_ids = set(ADMINS + [REFERRAL_PREFIX])
    if message.from_user.id not in allowed_ids:
        return
    else:
        await _show_links(message, REFERRAL_PREFIX, "Пётр")


@router.message(Command('create_petr_links'))
async def create_petr_links(message: types.Message):
    await _create_next_link(message, REFERRAL_PREFIX, "Пётр")


# ===========================
# Команды для Whodi
# ===========================
@router.message(Command('whodi_links'))
async def whodi_links(message: types.Message):
    allowed_ids = set(ADMINS + [REFERRAL_PREFIX_WHODI])
    if message.from_user.id not in allowed_ids:
        return
    else:
        await _show_links(message, REFERRAL_PREFIX_WHODI, "Whodi")


@router.message(Command('create_whodi_links'))
async def create_whodi_links(message: types.Message):
    await _create_next_link(message, REFERRAL_PREFIX_WHODI, "Whodi")


# ===========================
# Команды для Silobus
# ===========================

@router.message(Command('silobus_links'))
async def silobus_links(message: types.Message):
    # Разрешено Silobus (5201985063) и админам
    allowed_ids = set(ADMINS + [REFERRAL_PREFIX_SILOBUS])
    if message.from_user.id not in allowed_ids:
        await message.answer("❌ У вас нет доступа к этой команде.")
        return

    await _show_links(message, REFERRAL_PREFIX_SILOBUS, "Silobus")


@router.message(Command('create_silobus_links'))
async def create_silobus_links(message: types.Message):
    await _create_next_link(message, REFERRAL_PREFIX_SILOBUS, "Silobus")
