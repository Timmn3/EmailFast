from __future__ import annotations

from datetime import timedelta
from typing import Optional
from app.services.mail.firstmail_imap import fetch_firstmail_messages_async
from loguru import logger
from tortoise import timezone
from tortoise.transactions import in_transaction

from app.db import models


def parse_rental_accounts_text(raw_text: str) -> list[tuple[str, str]]:
    """
    Парсит текст со списком почт в формате:

    email1@example.com
    password1

    email2@example.com
    password2

    Возвращает список кортежей:
    [
        ("email1@example.com", "password1"),
        ("email2@example.com", "password2"),
    ]

    Важно:
    - пустые строки игнорируются
    - ожидается строгая структура: email, затем password
    """
    if not raw_text or not raw_text.strip():
        return []

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if len(lines) % 2 != 0:
        raise ValueError(
            "Некорректный список почт: должно быть чётное количество непустых строк "
            "(email / password / email / password ...)."
        )

    result: list[tuple[str, str]] = []

    for i in range(0, len(lines), 2):
        email = lines[i]
        password = lines[i + 1]

        if "@" not in email:
            raise ValueError(f"Строка не похожа на email: {email}")

        result.append((email, password))

    return result


async def import_rental_accounts_from_text(
    raw_text: str,
    default_account_type: str = "limited",
) -> dict:
    """
    Импортирует почтовые аккаунты в пул из текстового блока.

    Логика:
    - если email новый -> создаём запись в rental_email_accounts
    - если email уже существует -> обновляем пароль и account_type
    - queue_order новым аккаунтам ставим в конец очереди

    :param raw_text: текст с email/password парами
    :param default_account_type: тип аккаунта по умолчанию
    :return: словарь со статистикой импорта
    """
    pairs = parse_rental_accounts_text(raw_text)

    if not pairs:
        return {
            "created": 0,
            "updated": 0,
            "total": 0,
        }

    created = 0
    updated = 0

    max_queue_order = await models.RentalEmailAccount.get_max_queue_order()

    for email, password in pairs:
        account = await models.RentalEmailAccount.get_or_none(email=email)

        if account:
            account.password = password
            account.account_type = default_account_type
            account.is_enabled = True
            await account.save(update_fields=["password", "account_type", "is_enabled"])
            updated += 1
            continue

        max_queue_order += 1

        await models.RentalEmailAccount.create(
            email=email,
            password=password,
            account_type=default_account_type,
            is_enabled=True,
            is_reserved=False,
            queue_order=max_queue_order,
        )
        created += 1

    logger.info(
        "Импорт пула арендных почт завершён: created=%s updated=%s total=%s",
        created,
        updated,
        len(pairs),
    )

    return {
        "created": created,
        "updated": updated,
        "total": len(pairs),
    }

async def issue_rental_email(
    user: models.User,
    days: int,
    is_free_week: bool = False,
    initial_old_messages_id: Optional[list] = None,
) -> models.RentalEmailLease:
    """
    Выдаёт пользователю следующий свободный почтовый ящик из пула.

    Важно:
    - функция делает атомарное резервирование через транзакцию;
    - один аккаунт не может быть выдан двум пользователям одновременно;
    - сама инициализация IMAP здесь НЕ делается, а выполняется
      отдельным шагом сразу после создания аренды.
    """
    if days <= 0:
        raise ValueError("Срок аренды должен быть больше 0 дней.")

    old_messages = list(initial_old_messages_id or [])
    now = timezone.now()
    expire_at = now + timedelta(days=days)

    async with in_transaction() as conn:
        account = (
            await models.RentalEmailAccount
            .select_for_update()
            .using_db(conn)
            .filter(is_enabled=True, is_reserved=False)
            .order_by("queue_order", "id")
            .first()
        )

        if not account:
            raise RuntimeError("Нет свободных почтовых ящиков для аренды.")

        account.is_reserved = True
        account.last_assigned_at = now
        await account.save(using_db=conn, update_fields=["is_reserved", "last_assigned_at"])

        lease = await models.RentalEmailLease.create(
            using_db=conn,
            user=user,
            account=account,
            email=account.email,
            old_messages_id=old_messages,
            is_initialized=False,
            is_active=True,
            notification_sent=False,
            is_free_week=is_free_week,
            days=days,
            expire_at=expire_at,
        )

    await lease.fetch_related("account", "user")

    logger.bind(
        user_id=getattr(user, "telegram_id", None),
        action="issue_rental_email",
    ).log(
        "USER_ACTION",
        f"Выдан арендный ящик: lease_id={lease.id} account_id={lease.account_id} email={lease.email} days={days}"
    )

    return lease

async def initialize_rental_email_lease(lease_id: int) -> bool:
    """
    Инициализирует арендованный FirstMail-ящик сразу после аренды.

    Логика:
    - подключаемся к IMAP;
    - текущий снимок писем сохраняем в old_messages_id;
    - даже если писем нет, всё равно помечаем аренду как инициализированную.

    :param lease_id: ID аренды
    :return: True если инициализация выполнена успешно, иначе False
    """
    try:
        lease = (
            await models.RentalEmailLease
            .filter(id=lease_id, is_active=True)
            .prefetch_related("account", "user")
            .first()
        )

        if not lease:
            logger.warning("initialize_rental_email_lease: аренда не найдена lease_id={}", lease_id)
            return False

        if lease.is_initialized:
            return True

        result = await fetch_firstmail_messages_async(
            email_addr=lease.account.email,
            password=lease.account.password,
            known_uids=[],
            limit=1,
            is_initialized=False,
        )

        lease.old_messages_id = result.get("updated_old_uids", []) or []
        lease.is_initialized = True
        await lease.save(update_fields=["old_messages_id", "is_initialized"])

        logger.bind(
            user_id=getattr(lease.user, "telegram_id", None),
            action="initialize_rental_email_lease",
        ).log(
            "USER_ACTION",
            f"Инициализирован арендный ящик: lease_id={lease.id} email={lease.email} old_uids={len(lease.old_messages_id)}"
        )

        return True

    except Exception as e:
        logger.opt(exception=e).error(
            f"Ошибка инициализации арендного ящика lease_id={lease_id}: {e}"
        )
        return False

async def _finalize_rental_email_change(old_lease_id: int) -> None:
    """
    Финализирует успешную смену арендованного FirstMail-ящика.

    После успешной инициализации нового ящика:
    - старый аккаунт освобождается;
    - старый аккаунт уходит в конец очереди;
    - старая аренда уже остаётся неактивной.
    """
    now = timezone.now()

    async with in_transaction() as conn:
        old_lease = (
            await models.RentalEmailLease
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=old_lease_id)
        )

        if not old_lease:
            logger.warning(
                "Финализация смены аренды: старая аренда не найдена old_lease_id={}",
                old_lease_id,
            )
            return

        old_account = (
            await models.RentalEmailAccount
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=old_lease.account_id)
        )

        if not old_account:
            logger.warning(
                "Финализация смены аренды: старый аккаунт не найден old_lease_id={} account_id={}",
                old_lease_id,
                old_lease.account_id,
            )
            return

        max_queue_account = (
            await models.RentalEmailAccount
            .all()
            .using_db(conn)
            .order_by("-queue_order", "-id")
            .first()
        )
        max_queue_order = max_queue_account.queue_order if max_queue_account else 0

        old_account.is_reserved = False
        old_account.queue_order = max_queue_order + 1
        old_account.released_at = now
        await old_account.save(
            using_db=conn,
            update_fields=["is_reserved", "queue_order", "released_at"],
        )

async def _rollback_rental_email_change(old_lease_id: int, new_lease_id: int) -> None:
    """
    Откатывает смену аренды, если новый FirstMail-ящик не удалось инициализировать.

    Логика отката:
    - новая аренда деактивируется;
    - новый аккаунт освобождается и уходит в конец очереди;
    - старая аренда возвращается в активное состояние;
    - старый аккаунт остаётся закреплённым за пользователем.
    """
    now = timezone.now()

    async with in_transaction() as conn:
        old_lease = (
            await models.RentalEmailLease
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=old_lease_id)
        )
        new_lease = (
            await models.RentalEmailLease
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=new_lease_id)
        )

        new_account = None
        if new_lease:
            new_account = (
                await models.RentalEmailAccount
                .select_for_update()
                .using_db(conn)
                .get_or_none(id=new_lease.account_id)
            )

        old_account = None
        if old_lease:
            old_account = (
                await models.RentalEmailAccount
                .select_for_update()
                .using_db(conn)
                .get_or_none(id=old_lease.account_id)
            )

        if new_lease and new_lease.is_active:
            new_lease.is_active = False
            await new_lease.save(using_db=conn, update_fields=["is_active"])

        if new_account:
            max_queue_account = (
                await models.RentalEmailAccount
                .all()
                .using_db(conn)
                .order_by("-queue_order", "-id")
                .first()
            )
            max_queue_order = max_queue_account.queue_order if max_queue_account else 0

            new_account.is_reserved = False
            new_account.queue_order = max_queue_order + 1
            new_account.released_at = now
            await new_account.save(
                using_db=conn,
                update_fields=["is_reserved", "queue_order", "released_at"],
            )

        if old_lease and not old_lease.is_active:
            old_lease.is_active = True
            await old_lease.save(using_db=conn, update_fields=["is_active"])

        if old_account and not old_account.is_reserved:
            old_account.is_reserved = True
            await old_account.save(using_db=conn, update_fields=["is_reserved"])

async def change_rental_email_lease(
    lease_id: int,
    user: models.User,
) -> models.RentalEmailLease:
    """
    Меняет арендованный FirstMail-ящик пользователя на новый ящик из пула.

    Важно:
    - mail.tm здесь НЕ используется;
    - старая аренда временно деактивируется до инициализации нового ящика,
      чтобы защититься от двойных нажатий и гонок;
    - если новый ящик не удалось подготовить, выполняется откат на старую аренду;
    - expire_at, days, владелец и признак бесплатной недели сохраняются.
    """
    now = timezone.now()

    async with in_transaction() as conn:
        old_lease = (
            await models.RentalEmailLease
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=lease_id, user=user, is_active=True)
        )

        if not old_lease:
            raise ValueError("Арендованный ящик не найден.")

        old_account = (
            await models.RentalEmailAccount
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=old_lease.account_id)
        )

        if not old_account:
            raise RuntimeError("Не найден текущий почтовый аккаунт аренды.")

        new_account = (
            await models.RentalEmailAccount
            .select_for_update()
            .using_db(conn)
            .filter(is_enabled=True, is_reserved=False)
            .exclude(id=old_account.id)
            .order_by("queue_order", "id")
            .first()
        )

        if not new_account:
            raise RuntimeError("Нет свободных почтовых ящиков для смены.")

        new_account.is_reserved = True
        new_account.last_assigned_at = now
        await new_account.save(
            using_db=conn,
            update_fields=["is_reserved", "last_assigned_at"],
        )

        old_lease.is_active = False
        await old_lease.save(using_db=conn, update_fields=["is_active"])

        new_lease = await models.RentalEmailLease.create(
            using_db=conn,
            user=user,
            account=new_account,
            email=new_account.email,
            old_messages_id=[],
            is_initialized=False,
            is_active=True,
            notification_sent=old_lease.notification_sent,
            is_free_week=old_lease.is_free_week,
            days=old_lease.days,
            expire_at=old_lease.expire_at,
        )

    init_ok = await initialize_rental_email_lease(new_lease.id)
    if not init_ok:
        await _rollback_rental_email_change(old_lease.id, new_lease.id)
        raise RuntimeError("Не удалось подготовить новый почтовый ящик. Попробуйте ещё раз.")

    await _finalize_rental_email_change(old_lease.id)
    await new_lease.fetch_related("account", "user")

    logger.bind(
        user_id=getattr(user, "telegram_id", None),
        action="change_rental_email_lease",
    ).log(
        "USER_ACTION",
        f"Смена арендного ящика завершена: old_lease_id={old_lease.id} new_lease_id={new_lease.id} email={new_lease.email}",
    )

    return new_lease

async def release_rental_email_lease(lease_id: int) -> bool:
    """
    Завершает аренду и возвращает почтовый ящик в конец очереди.

    Логика:
    - аренда помечается как неактивная
    - аккаунт освобождается
    - queue_order переносится в конец очереди, чтобы этот ящик
      не выдавался сразу повторно следующим пользователям

    :param lease_id: ID аренды
    :return: True если аренда была успешно завершена, иначе False
    """
    now = timezone.now()

    async with in_transaction() as conn:
        lease = (
            await models.RentalEmailLease
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=lease_id)
        )

        if not lease or not lease.is_active:
            return False

        account = (
            await models.RentalEmailAccount
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=lease.account_id)
        )

        if not account:
            logger.warning(
                "release_rental_email_lease: аккаунт не найден для lease_id=%s account_id=%s",
                lease.id,
                lease.account_id,
            )
            return False

        max_queue_account = (
            await models.RentalEmailAccount
            .all()
            .using_db(conn)
            .order_by("-queue_order", "-id")
            .first()
        )
        max_queue_order = max_queue_account.queue_order if max_queue_account else 0

        lease.is_active = False
        await lease.save(using_db=conn, update_fields=["is_active"])

        account.is_reserved = False
        account.queue_order = max_queue_order + 1
        account.released_at = now
        await account.save(
            using_db=conn,
            update_fields=["is_reserved", "queue_order", "released_at"],
        )

    logger.info(
        "Аренда завершена и ящик возвращён в конец очереди: lease_id=%s account_id=%s",
        lease_id,
        lease.account_id,
    )
    return True

async def get_user_active_rental_leases(user: models.User) -> list[models.RentalEmailLease]:
    """
    Возвращает активные аренды пользователя.
    """
    return await models.RentalEmailLease.filter(
        user=user,
        is_active=True,
    ).prefetch_related("account").all()


async def get_rental_lease_for_user(
    lease_id: int,
    user: models.User,
) -> Optional[models.RentalEmailLease]:
    """
    Возвращает аренду пользователя по ID, если она принадлежит ему.
    """
    return await models.RentalEmailLease.get_or_none(
        id=lease_id,
        user=user,
    ).prefetch_related("account")