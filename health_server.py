import asyncio
import logging
import os
import sys

from aiohttp import ClientSession, ClientTimeout, web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("health_server")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN env variable is not set")
    sys.exit(1)

PORT = int(os.environ.get("HEALTH_PORT", "8080"))
GETME_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
GETME_TIMEOUT = ClientTimeout(total=5)


async def health_handler(request: web.Request) -> web.Response:
    try:
        async with ClientSession(timeout=GETME_TIMEOUT) as session:
            async with session.get(GETME_URL) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("ok"):
                    username = data.get("result", {}).get("username", "")
                    return web.json_response({"status": "ok", "username": username})
                logger.warning(f"getMe non-ok: status={resp.status} body={data}")
    except Exception as e:
        logger.error(f"getMe failed: {e}")
    return web.json_response({"status": "error"}, status=503)


def main() -> None:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    logger.info(f"Health server listening on 0.0.0.0:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
