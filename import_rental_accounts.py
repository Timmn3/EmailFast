import asyncio
import argparse
from pathlib import Path

from app.db.database import init_db, close_db
from app.services.rental_email_pool import import_rental_accounts_from_text


async def async_main(file_path: str, account_type: str) -> None:
    """
    Одноразовый импорт списка арендных почт в таблицу rental_email_accounts.

    Формат входного файла:
    email1@example.com
    password1

    email2@example.com
    password2

    Пустые строки допускаются.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    raw_text = path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise ValueError(f"Файл пустой: {path}")

    await init_db()
    try:
        result = await import_rental_accounts_from_text(
            raw_text=raw_text,
            default_account_type=account_type,
        )
        print("=== IMPORT RESULT ===")
        print(f"created={result['created']}")
        print(f"updated={result['updated']}")
        print(f"total={result['total']}")
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

    args = parser.parse_args()
    asyncio.run(async_main(args.file_path, args.account_type))

# python .\import_rental_accounts.py .\accounts.txt --account-type limited
if __name__ == "__main__":
    main()