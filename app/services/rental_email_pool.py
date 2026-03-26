from __future__ import annotations
import asyncio
from datetime import datetime, timedelta
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

_RENTAL_EMAIL_LEASE_LOCKS: dict[int, asyncio.Lock] = {}


def _get_rental_email_lease_lock(lease_id: int) -> asyncio.Lock:
    """
    Возвращает in-memory lock для конкретной аренды FirstMail-ящика.

    Нужен, чтобы ручная проверка и scheduler внутри одного процесса
    не обрабатывали один и тот же lease одновременно и не слали дубли.
    """
    lock = _RENTAL_EMAIL_LEASE_LOCKS.get(lease_id)
    if lock is None:
        lock = asyncio.Lock()
        _RENTAL_EMAIL_LEASE_LOCKS[lease_id] = lock
    return lock


def build_firstmail_change_cooldown_message(remaining: timedelta) -> str:
    """
    Формирует понятное сообщение для пользователя по cooldown смены FirstMail-ящика.
    """
    total_seconds = max(0, int(remaining.total_seconds()))
    total_minutes = max(1, (total_seconds + 59) // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"Сменить ящик можно не чаще 1 раза в 24 часа. Осталось: {hours} ч {minutes} мин."


async def get_firstmail_change_cooldown_remaining(user: models.User) -> Optional[timedelta]:
    """
    Возвращает оставшееся время cooldown на смену FirstMail-ящика.

    :param user: Пользователь
    :return: timedelta, если cooldown активен, иначе None
    """
    if not user.firstmail_change_available_at:
        return None

    remaining = user.firstmail_change_available_at - timezone.now()
    if remaining.total_seconds() <= 0:
        return None

    return remaining


async def get_firstmail_change_cooldown_remaining_for_lease(
    lease: models.RentalEmailLease,
) -> Optional[timedelta]:
    """
    Возвращает оставшееся время cooldown на смену конкретной аренды FirstMail.

    Важно:
    - источник бизнес-логики здесь уже lease.change_available_at;
    - NULL означает, что смена доступна прямо сейчас;
    - функция ничего не знает о legacy-поле пользователя.
    """
    if not lease.change_available_at:
        return None

    remaining = lease.change_available_at - timezone.now()
    if remaining.total_seconds() <= 0:
        return None

    return remaining

async def _get_rental_email_pool_tail_queue_order(conn) -> int:
    """
    Возвращает текущий хвост очереди пула FirstMail-аккаунтов.
    """
    max_queue_account = (
        await models.RentalEmailAccount
        .all()
        .using_db(conn)
        .order_by("-queue_order", "-id")
        .first()
    )
    return max_queue_account.queue_order if max_queue_account else 0


async def _pick_next_available_rental_email_account(
    conn,
    exclude_account_id: Optional[int] = None,
) -> Optional[models.RentalEmailAccount]:
    """
    Возвращает следующий доступный FirstMail-аккаунт из пула.

    Важно:
    - берём только не зарезервированные аккаунты;
    - берём только аккаунты, не выбывшие из ротации;
    - берём только аккаунты, не исчерпавшие лимит выдач.
    """
    query = (
        models.RentalEmailAccount
        .select_for_update()
        .using_db(conn)
        .filter(
            is_enabled=True,
            is_reserved=False,
            retired_at__isnull=True,
            times_issued__lt=2,
        )
    )

    if exclude_account_id is not None:
        query = query.exclude(id=exclude_account_id)

    return await query.order_by("queue_order", "id").first()

async def pull_rental_email_messages(
    lease_id: int,
    limit: int = 5,
) -> tuple[models.RentalEmailLease, list]:
    """
    Унифицированно получает новые письма для арендованного FirstMail-ящика.

    Важно:
    - используется и ручной проверкой, и scheduler;
    - защищает от дублей внутри одного процесса через lock по lease_id;
    - использует old_messages_id / is_initialized;
    - при первом неинициализированном чтении тихо инициализирует ящик.

    :param lease_id: ID аренды
    :param limit: максимум писем за один проход
    :return: (lease, messages)
    """
    lease_lock = _get_rental_email_lease_lock(lease_id)

    async with lease_lock:
        lease = (
            await models.RentalEmailLease
            .filter(id=lease_id, is_active=True)
            .prefetch_related("account", "user")
            .first()
        )

        if not lease:
            raise ValueError("Арендованный ящик не найден.")

        result = await fetch_firstmail_messages_async(
            email_addr=lease.account.email,
            password=lease.account.password,
            known_uids=lease.old_messages_id or [],
            limit=limit,
            is_initialized=lease.is_initialized,
        )

        updated_old_uids = result.get("updated_old_uids", lease.old_messages_id or [])
        initialized = result.get("initialized", lease.is_initialized)
        messages = result.get("messages", [])

        update_fields = []

        if updated_old_uids != (lease.old_messages_id or []):
            lease.old_messages_id = updated_old_uids
            update_fields.append("old_messages_id")

        if initialized != lease.is_initialized:
            lease.is_initialized = initialized
            update_fields.append("is_initialized")

        if update_fields:
            await lease.save(update_fields=update_fields)

        logger.bind(
            user_id=getattr(getattr(lease, "user", None), "telegram_id", None),
            action="pull_rental_email_messages",
        ).log(
            "USER_ACTION",
            f"FirstMail pull | lease_id={lease.id} initialized={lease.is_initialized} new_messages={len(messages)}"
        )

        return lease, messages

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
    Подготавливает пользователю следующий свободный почтовый ящик из пула.

    Важно:
    - функция делает атомарное резервирование через транзакцию;
    - один аккаунт не может быть выдан двум пользователям одновременно;
    - сама инициализация IMAP здесь НЕ делается, а выполняется
      отдельным шагом сразу после создания аренды;
    - лимит успешных выдач НЕ увеличивается на этом шаге, потому что
      неуспешная инициализация не должна "съедать" reuse-лимит.
    """
    if days <= 0:
        raise ValueError("Срок аренды должен быть больше 0 дней.")

    old_messages = list(initial_old_messages_id or [])
    now = timezone.now()
    expire_at = now + timedelta(days=days)
    free_week_expires_at = expire_at if is_free_week else None

    async with in_transaction() as conn:
        account = await _pick_next_available_rental_email_account(conn)

        if not account:
            raise RuntimeError("Нет свободных почтовых ящиков для аренды.")

        account.is_reserved = True
        account.last_assigned_at = now
        await account.save(
            using_db=conn,
            update_fields=["is_reserved", "last_assigned_at"],
        )

        lease = await models.RentalEmailLease.create(
            using_db=conn,
            user=user,
            account=account,
            email=account.email,
            old_messages_id=old_messages,
            is_initialized=False,
            is_active=True,
            notification_sent=False,
            expiration_notified=False,
            free_week_notified=False,
            is_free_week=is_free_week,
            days=days,
            expire_at=expire_at,
            free_week_expires_at=free_week_expires_at,
        )

    await lease.fetch_related("account", "user")

    logger.bind(
        user_id=getattr(user, "telegram_id", None),
        action="issue_rental_email",
    ).log(
        "USER_ACTION",
        f"Подготовлена FirstMail-аренда: lease_id={lease.id} account_id={lease.account_id} "
        f"email={lease.email} days={days}"
    )

    return lease

async def initialize_rental_email_lease(lease_id: int) -> bool:
    """
    Инициализирует арендованный FirstMail-ящик сразу после аренды.

    Логика:
    - подключаемся к IMAP;
    - текущий снимок писем сохраняем в old_messages_id;
    - даже если писем нет, всё равно помечаем аренду как инициализированную;
    - именно здесь фиксируем успешную выдачу аккаунта, потому что только
      после успешной инициализации можно считать, что выдача действительно состоялась.

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

        updated_old_uids = result.get("updated_old_uids", []) or []
        now = timezone.now()

        issued_now = False
        retired_now = False
        final_times_issued = None

        async with in_transaction() as conn:
            locked_lease = (
                await models.RentalEmailLease
                .select_for_update()
                .using_db(conn)
                .get_or_none(id=lease_id, is_active=True)
            )

            if not locked_lease:
                logger.warning(
                    "initialize_rental_email_lease: аренда исчезла или стала неактивной lease_id={}",
                    lease_id,
                )
                return False

            if locked_lease.is_initialized:
                return True

            locked_account = (
                await models.RentalEmailAccount
                .select_for_update()
                .using_db(conn)
                .get_or_none(id=locked_lease.account_id)
            )

            if not locked_account:
                logger.warning(
                    "initialize_rental_email_lease: аккаунт не найден lease_id={} account_id={}",
                    lease_id,
                    locked_lease.account_id,
                )
                return False

            locked_lease.old_messages_id = updated_old_uids
            locked_lease.is_initialized = True
            await locked_lease.save(
                using_db=conn,
                update_fields=["old_messages_id", "is_initialized"],
            )

            account_update_fields: list[str] = []

            if locked_account.times_issued < 2:
                locked_account.times_issued += 1
                account_update_fields.append("times_issued")
                issued_now = True

                if locked_account.times_issued >= 2:
                    if locked_account.retired_at is None:
                        locked_account.retired_at = now
                        account_update_fields.append("retired_at")
                        retired_now = True

                    if locked_account.retire_reason != "usage_limit_reached":
                        locked_account.retire_reason = "usage_limit_reached"
                        account_update_fields.append("retire_reason")
            else:
                # Самовосстановление на случай, если аккаунт уже достиг лимита,
                # но по историческим причинам не был помечен как выбывший из ротации.
                if locked_account.retired_at is None:
                    locked_account.retired_at = now
                    account_update_fields.append("retired_at")

                if locked_account.retire_reason != "usage_limit_reached":
                    locked_account.retire_reason = "usage_limit_reached"
                    account_update_fields.append("retire_reason")

            if account_update_fields:
                await locked_account.save(
                    using_db=conn,
                    update_fields=account_update_fields,
                )

            final_times_issued = locked_account.times_issued

        logger.bind(
            user_id=getattr(lease.user, "telegram_id", None),
            action="initialize_rental_email_lease",
        ).log(
            "USER_ACTION",
            f"Инициализирован арендный ящик: lease_id={lease.id} email={lease.email} "
            f"old_uids={len(updated_old_uids)}"
        )

        if issued_now:
            logger.bind(
                user_id=getattr(lease.user, "telegram_id", None),
                action="initialize_rental_email_lease",
            ).info(
                f"Успешная выдача FirstMail-аккаунта зафиксирована: "
                f"lease_id={lease.id} account_id={lease.account_id} email={lease.email} "
                f"times_issued={final_times_issued}"
            )

        if retired_now:
            logger.bind(
                user_id=getattr(lease.user, "telegram_id", None),
                action="initialize_rental_email_lease",
            ).log(
                "USER_ACTION",
                f"FirstMail-аккаунт выбыл из ротации по лимиту использований: "
                f"account_id={lease.account_id} email={lease.email} times_issued={final_times_issued}"
            )

        return True

    except Exception as e:
        logger.opt(exception=e).error(
            f"Ошибка инициализации арендного ящика lease_id={lease_id}: {e}"
        )
        return False

async def _finalize_rental_email_change(old_lease_id: int, user_id: int) -> None:
    """
    Финализирует успешную смену арендованного FirstMail-ящика.

    После успешной инициализации нового ящика:
    - старый аккаунт освобождается;
    - если он не выбыл из ротации, он уходит в конец очереди;
    - если аккаунт уже выбыл из ротации, он просто освобождается и
      больше не участвует в выборе из пула;
    - пользователю фиксируется новый cooldown на смену FirstMail-ящика.
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

        locked_user = (
            await models.User
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=user_id)
        )

        if not locked_user:
            logger.warning(
                "Финализация смены аренды: пользователь не найден user_id={}",
                user_id,
            )
            return

        account_update_fields = ["is_reserved", "released_at"]
        old_account.is_reserved = False
        old_account.released_at = now

        if old_account.retired_at is None:
            max_queue_order = await _get_rental_email_pool_tail_queue_order(conn)
            old_account.queue_order = max_queue_order + 1
            account_update_fields.append("queue_order")
        else:
            logger.bind(
                user_id=getattr(locked_user, "telegram_id", None),
                action="_finalize_rental_email_change",
            ).info(
                f"Освобождён FirstMail-аккаунт, уже выбывший из ротации: "
                f"account_id={old_account.id} email={old_account.email} "
                f"retire_reason={old_account.retire_reason}"
            )

        await old_account.save(
            using_db=conn,
            update_fields=account_update_fields,
        )

        locked_user.firstmail_change_available_at = now + timedelta(hours=24)
        await locked_user.save(
            using_db=conn,
            update_fields=["firstmail_change_available_at"],
        )

async def _rollback_rental_email_change(
    old_lease_id: int,
    new_lease_id: int,
    user_id: int,
    restore_firstmail_change_available_at: Optional[datetime],
) -> None:
    """
    Откатывает смену аренды, если новый FirstMail-ящик не удалось инициализировать.

    Логика отката:
    - новая аренда деактивируется;
    - новый аккаунт освобождается и уходит в конец очереди, если он не выбыл из ротации;
    - старая аренда возвращается в активное состояние;
    - старый аккаунт остаётся закреплённым за пользователем;
    - временно зарезервированный cooldown на смену ящика откатывается назад.

    Важно:
    reuse-лимит здесь не уменьшается, потому что он вообще не увеличивается
    до успешной инициализации нового ящика.
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

        locked_user = (
            await models.User
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=user_id)
        )

        if new_lease and new_lease.is_active:
            new_lease.is_active = False
            await new_lease.save(using_db=conn, update_fields=["is_active"])

        if new_account:
            account_update_fields = ["is_reserved", "released_at"]
            new_account.is_reserved = False
            new_account.released_at = now

            if new_account.retired_at is None:
                max_queue_order = await _get_rental_email_pool_tail_queue_order(conn)
                new_account.queue_order = max_queue_order + 1
                account_update_fields.append("queue_order")

            await new_account.save(
                using_db=conn,
                update_fields=account_update_fields,
            )

        if old_lease and not old_lease.is_active:
            old_lease.is_active = True
            await old_lease.save(using_db=conn, update_fields=["is_active"])

        if old_account and not old_account.is_reserved:
            old_account.is_reserved = True
            await old_account.save(using_db=conn, update_fields=["is_reserved"])

        if locked_user:
            locked_user.firstmail_change_available_at = restore_firstmail_change_available_at
            await locked_user.save(
                using_db=conn,
                update_fields=["firstmail_change_available_at"],
            )

            logger.bind(
                user_id=getattr(locked_user, "telegram_id", None),
                action="_rollback_rental_email_change",
            ).info(
                "Cooldown смены FirstMail-ящика откатан назад после неуспешной смены."
            )

            logger.bind(
                user_id=getattr(locked_user, "telegram_id", None),
                action="_rollback_rental_email_change",
            ).info(
                "Reuse-лимит FirstMail не изменялся, так как новый ящик не был успешно инициализирован."
            )


async def change_rental_email_lease(
    lease_id: int,
    user: models.User,
) -> models.RentalEmailLease:
    """
    Меняет арендованный FirstMail-ящик пользователя на новый ящик из пула.

    Важно:
    - mail.tm здесь НЕ используется;
    - cooldown на смену FirstMail хранится глобально на пользователе;
    - чтобы защититься от гонок, cooldown резервируется внутри транзакции
      ещё до инициализации нового ящика и откатывается назад при rollback;
    - старая аренда временно деактивируется до инициализации нового ящика;
    - если новый ящик не удалось подготовить, выполняется откат на старую аренду;
    - expire_at, days, владелец и признак бесплатной недели сохраняются;
    - новые флаги уведомлений и дата конца бесплатной недели тоже сохраняются.
    """
    now = timezone.now()
    previous_change_available_at: Optional[datetime] = None

    async with in_transaction() as conn:
        locked_user = (
            await models.User
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=user.id)
        )

        if not locked_user:
            raise ValueError("Пользователь не найден.")

        if (
            locked_user.firstmail_change_available_at
            and locked_user.firstmail_change_available_at > now
        ):
            remaining = locked_user.firstmail_change_available_at - now
            raise RuntimeError(build_firstmail_change_cooldown_message(remaining))

        previous_change_available_at = locked_user.firstmail_change_available_at

        # Временная блокировка cooldown на время смены.
        # Если смена не завершится успешно, значение будет откатано в rollback.
        locked_user.firstmail_change_available_at = now + timedelta(hours=24)
        await locked_user.save(
            using_db=conn,
            update_fields=["firstmail_change_available_at"],
        )

        old_lease = (
            await models.RentalEmailLease
            .select_for_update()
            .using_db(conn)
            .get_or_none(id=lease_id, user=locked_user, is_active=True)
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

        new_account = await _pick_next_available_rental_email_account(
            conn,
            exclude_account_id=old_account.id,
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
            user=locked_user,
            account=new_account,
            email=new_account.email,
            old_messages_id=[],
            is_initialized=False,
            is_active=True,
            notification_sent=old_lease.notification_sent,
            expiration_notified=old_lease.expiration_notified,
            free_week_notified=old_lease.free_week_notified,
            is_free_week=old_lease.is_free_week,
            days=old_lease.days,
            expire_at=old_lease.expire_at,
            free_week_expires_at=old_lease.free_week_expires_at,
        )

    init_ok = await initialize_rental_email_lease(new_lease.id)
    if not init_ok:
        await _rollback_rental_email_change(
            old_lease_id=old_lease.id,
            new_lease_id=new_lease.id,
            user_id=user.id,
            restore_firstmail_change_available_at=previous_change_available_at,
        )
        raise RuntimeError("Не удалось подготовить новый почтовый ящик. Попробуйте ещё раз.")

    await _finalize_rental_email_change(old_lease.id, user.id)
    await new_lease.fetch_related("account", "user")

    logger.bind(
        user_id=getattr(user, "telegram_id", None),
        action="change_rental_email_lease",
    ).log(
        "USER_ACTION",
        f"Смена арендного ящика завершена: old_lease_id={old_lease.id} "
        f"new_lease_id={new_lease.id} email={new_lease.email}"
    )

    return new_lease


async def release_rental_email_lease(lease_id: int) -> bool:
    """
    Завершает аренду и освобождает FirstMail-аккаунт.

    Логика:
    - аренда помечается как неактивная;
    - аккаунт освобождается;
    - если аккаунт ещё не выбыл из ротации, он переносится в конец очереди;
    - если аккаунт уже выбыл из ротации по лимиту использований,
      он просто освобождается и больше не возвращается в пул.

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
                "release_rental_email_lease: аккаунт не найден для lease_id={} account_id={}",
                lease.id,
                lease.account_id,
            )
            return False

        lease.is_active = False
        await lease.save(using_db=conn, update_fields=["is_active"])

        account.is_reserved = False
        account.released_at = now
        account_update_fields = ["is_reserved", "released_at"]

        if account.retired_at is None:
            max_queue_order = await _get_rental_email_pool_tail_queue_order(conn)
            account.queue_order = max_queue_order + 1
            account_update_fields.append("queue_order")
        else:
            logger.info(
                "FirstMail-аккаунт освобождён после выбытия из ротации: "
                f"account_id={account.id} email={account.email} retire_reason={account.retire_reason}"
            )

        await account.save(
            using_db=conn,
            update_fields=account_update_fields,
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