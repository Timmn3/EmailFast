import argparse
import asyncio
import json

from app.services.smsfast_receive import SmsFastReceive


def _pp(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", type=int, required=True, help="ID страны в SMSFast")
    parser.add_argument("--service", type=str, default=None, help="Код сервиса SMSFast (например: tg / go / vk и т.д.)")
    parser.add_argument("--rps", type=float, default=1.0, help="Rate limit запросов в секунду")
    parser.add_argument("--buy", action="store_true", help="СДЕЛАТЬ РЕАЛЬНУЮ ПОКУПКУ НОМЕРА (спишет баланс)")
    parser.add_argument("--max_price", type=float, default=None, help="Максимальная цена (если поддерживается)")
    args = parser.parse_args()

    cli = SmsFastReceive(requests_per_second=args.rps)

    print("== getBalance ==")
    print(_pp(await cli.get_balance()))
    print()

    print("== getNumbersStatus ==")
    print(_pp(await cli.get_numbers_status(country=args.country)))
    print()

    if args.service:
        print("== getPrices ==")
        print(_pp(await cli.get_prices(service=args.service, country=args.country)))
        print()

    if args.buy:
        if not args.service:
            raise SystemExit("Для покупки нужен --service")

        print("== buyNumber (REAL) ==")
        buy = await cli.buy_number(service=args.service, country=args.country, max_price=args.max_price)
        print(_pp(buy))


if __name__ == "__main__":
    asyncio.run(main())
