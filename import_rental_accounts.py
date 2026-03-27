import asyncio
import argparse
from pathlib import Path

from app.db.database import init_db, close_db
from app.services.rental_email_pool import import_rental_accounts_from_text


def normalize_accounts_text(raw_text: str) -> str:
    """
    Нормализует входной текст аккаунтов к legacy-формату, который ожидает
    import_rental_accounts_from_text():

    email1@example.com
    password1

    email2@example.com
    password2

    Поддерживаемые входные форматы:
    1) Старый:
       email1@example.com
       password1

       email2@example.com
       password2

    2) Новый:
       email1@example.com:password1
       email2@example.com:password2

    Пустые строки допускаются и игнорируются.
    """
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return ""

    # Новый формат: каждая запись в одной строке email:password
    if all(":" in line for line in lines):
        normalized_chunks: list[str] = []

        for index, line in enumerate(lines, start=1):
            email, password = line.split(":", 1)
            email = email.strip()
            password = password.strip()

            if not email or not password:
                raise ValueError(
                    f"Некорректная строка #{index} в формате email:password: {line!r}"
                )

            normalized_chunks.append(f"{email}\n{password}")

        return "\n\n".join(normalized_chunks)

    # Старый формат: email / password попарно
    if len(lines) % 2 != 0:
        raise ValueError(
            "Некорректный файл аккаунтов: для старого формата количество "
            "непустых строк должно быть чётным, либо каждая строка должна "
            "быть в формате email:password."
        )

    return "\n".join(lines)


async def async_main(file_path: str, account_type: str) -> None:
    """
    Одноразовый импорт списка арендных почт в таблицу rental_email_accounts.

    Поддерживаемые форматы входного файла:

    1) Старый:
    email1@example.com
    password1

    email2@example.com
    password2

    2) Новый:
    email1@example.com:password1
    email2@example.com:password2

    Пустые строки допускаются.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    raw_text = path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise ValueError(f"Файл пустой: {path}")

    normalized_text = normalize_accounts_text(raw_text)
    if not normalized_text.strip():
        raise ValueError(f"После нормализации файл пустой: {path}")

    await init_db()
    try:
        result = await import_rental_accounts_from_text(
            raw_text=normalized_text,
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