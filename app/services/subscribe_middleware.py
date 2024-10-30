from aiogram import BaseMiddleware
from typing import Callable


class SubscribeMiddleware(BaseMiddleware):
    def __init__(self):
        pass

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        # Ждём указанное количество секунд и передаём управление дальше по цепочке
        # (это может быть как хэндлер, так и следующая мидлварь)
        await asyncio.sleep(self.sleep_sec)
        result = await handler(event, data)
        # Если в хэндлере сделать return, то это значение попадёт в result
        print(f"Handler was delayed by {self.sleep_sec} seconds")
        return result