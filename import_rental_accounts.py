import asyncio
import argparse
import sys
from pathlib import Path
from typing import Iterator

from app.db.database import init_db, close_db
from app.services.rental_email_pool import import_rental_accounts_from_text

try:
    from loguru import logger
except Exception:  # pragma: no cover
    logger = None


def configure_console_logging(show_service_logs: bool) -> None:
    """
    Настраивает вывод логов в консоль для одноразового импорт-скрипта.

    По умолчанию приглушаем INFO-логи сервиса, чтобы они не ломали progressbar.
    """
    if logger is None:
        return

    try:
        logger.remove()
        logger.add(
            sys.stderr,
            level="INFO" if show_service_logs else "WARNING",
            enqueue=False,
            backtrace=False,
            diagnose=False,
        )
    except Exception:
        # Если логгер уже настроен нестандартно и переопределение не удалось,
        # не падаем — просто продолжаем работу.
        pass


def render_progress(
    processed_accounts: int,
    total_accounts: int,
    processed_batches: int,
    total_batches: int,
    width: int = 40,
) -> str:
    """
    Возвращает текстовый progressbar для консоли.

    Пример:
    [##########----------] 500/2100 (23%) | batch 3/11
    """
    if total_accounts <= 0:
        total_accounts = 1

    ratio = processed_accounts / total_accounts
    filled = int(width * ratio)
    empty = width - filled
    percent = int(ratio * 100)

    return (
        f"[{'#' * filled}{'-' * empty}] "
        f"{processed_accounts}/{total_accounts} ({percent}%) "
        f"| batch {processed_batches}/{total_batches}"
    )


def print_progress(
    processed_accounts: int,
    total_accounts: int,
    processed_batches: int,
    total_batches: int,
    final: bool = False,
) -> None:
    """
    Обновляет progressbar в одной строке без лишних переносов.
    """
    line = render_progress(
        processed_accounts=processed_accounts,
        total_accounts=total_accounts,
        processed_batches=processed_batches,
        total_batches=total_batches,
    )

    # Очищаем хвост предыдущей строки через добивку пробелами.
    sys.stdout.write("\r" + line.ljust(120))
    sys.stdout.flush()

    if final:
        sys.stdout.write("\n")
        sys.stdout.flush()


def parse_accounts(raw_text: str) -> list[tuple[str, str]]:
    """
    Разбирает входной текст аккаунтов.

    Поддерживаемые форматы:

    1) Старый формат:
       email1@example.com
       password1

       email2@example.com
       password2

    2) Новый формат:
       email1@example.com:password1
       email2@example.com:password2

    Возвращает список кортежей:
    [
        (email, password),
        ...
    ]
    """
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return []

    # Новый формат: каждая запись в одной строке email:password
    if all(":" in line for line in lines):
        accounts: list[tuple[str, str]] = []

        for index, line in enumerate(lines, start=1):
            email, password = line.split(":", 1)
            email = email.strip()
            password = password.strip()

            if not email or not password:
                raise ValueError(
                    f"Некорректная строка #{index} в формате email:password: {line!r}"
                )

            accounts.append((email, password))

        return accounts

    # Старый формат: email / password попарно
    if len(lines) % 2 != 0:
        raise ValueError(
            "Некорректный файл аккаунтов: для старого формата количество "
            "непустых строк должно быть чётным, либо каждая строка должна "
            "быть в формате email:password."
        )

    accounts: list[tuple[str, str]] = []
    for index in range(0, len(lines), 2):
        email = lines[index].strip()
        password = lines[index + 1].strip()

        if not email or not password:
            raise ValueError(
                f"Некорректная пара строк: email={email!r}, password={password!r}"
            )

        accounts.append((email, password))

    return accounts


def build_legacy_text(accounts: list[tuple[str, str]]) -> str:
    """
    Преобразует список аккаунтов в legacy-формат, который ожидает
    import_rental_accounts_from_text():

    email1@example.com
    password1

    email2@example.com
    password2
    """
    chunks = [f"{email}\n{password}" for email, password in accounts]
    return "\n\n".join(chunks)


def chunk_accounts(
    accounts: list[tuple[str, str]],
    batch_size: int,
) -> Iterator[list[tuple[str, str]]]:
    """
    Разбивает список аккаунтов на батчи фиксированного размера.
    """
    if batch_size <= 0:
        raise ValueError("batch_size должен быть больше 0")

    for index in range(0, len(accounts), batch_size):
        yield accounts[index:index + batch_size]


async def async_main(
    file_path: str,
    account_type: str,
    batch_size: int,
    show_service_logs: bool,
) -> None:
    """
    Одноразовый импорт списка арендных почт в таблицу rental_email_accounts.

    Логика:
    - читаем файл
    - поддерживаем оба формата (старый и новый)
    - импортируем батчами
    - показываем progressbar в одной строке
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    raw_text = path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise ValueError(f"Файл пустой: {path}")

    accounts = parse_accounts(raw_text)
    if not accounts:
        raise ValueError(f"Не найдено ни одного аккаунта в файле: {path}")

    configure_console_logging(show_service_logs=show_service_logs)

    total_accounts = len(accounts)
    total_batches = (total_accounts + batch_size - 1) // batch_size

    print(f"Найдено аккаунтов: {total_accounts}")
    print(f"Размер батча: {batch_size}")
    print("Старт импорта...")

    total_created = 0
    total_updated = 0
    total_processed = 0
    processed_accounts = 0
    processed_batches = 0

    await init_db()
    try:
        print_progress(
            processed_accounts=0,
            total_accounts=total_accounts,
            processed_batches=0,
            total_batches=total_batches,
        )

        for batch in chunk_accounts(accounts, batch_size=batch_size):
            batch_text = build_legacy_text(batch)

            result = await import_rental_accounts_from_text(
                raw_text=batch_text,
                default_account_type=account_type,
            )

            total_created += int(result.get("created", 0))
            total_updated += int(result.get("updated", 0))
            total_processed += int(result.get("total", 0))

            processed_accounts += len(batch)
            processed_batches += 1

            print_progress(
                processed_accounts=processed_accounts,
                total_accounts=total_accounts,
                processed_batches=processed_batches,
                total_batches=total_batches,
            )

        print_progress(
            processed_accounts=processed_accounts,
            total_accounts=total_accounts,
            processed_batches=processed_batches,
            total_batches=total_batches,
            final=True,
        )

        print("=== IMPORT RESULT ===")
        print(f"created={total_created}")
        print(f"updated={total_updated}")
        print(f"total={total_processed}")
    finally:
        await close_db()


def main() -> None:
    """
    Точка входа для запуска из консоли.
    """
    parser = argparse.ArgumentParser(
        description="Импорт списка арендных почт FirstMail в rental_email_accounts"
    )
    parser.add_argument(
        "file_path",
        help="Путь к txt-файлу со списком email/password",
    )
    parser.add_argument(
        "--account-type",
        default="limited",
        choices=["limited", "timeless"],
        help="Тип импортируемых аккаунтов",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Размер батча для пакетного импорта",
    )
    parser.add_argument(
        "--show-service-logs",
        action="store_true",
        help="Показывать INFO-логи сервиса во время импорта",
    )

    args = parser.parse_args()
    asyncio.run(
        async_main(
            file_path=args.file_path,
            account_type=args.account_type,
            batch_size=args.batch_size,
            show_service_logs=args.show_service_logs,
        )
    )


# python .\import_rental_accounts.py .\accounts.txt --account-type limited --batch-size 500
if __name__ == "__main__":
    main()