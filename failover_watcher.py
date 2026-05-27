"""failover_watcher.py — failover daemon для EmailFast bot.

Запускается на резервном сервере s4 (REDACTED). Пингует health endpoint
основного сервера s3 (72.56.100.74:8080/health) каждые 10 секунд. При 30 сек
недоступности (3 подряд fail) + sanity-check пройден → стартует локальный
emailfast через supervisorctl и шлёт уведомление админам. При 3 минутах
стабильной работы основного (18 подряд ok) → останавливает локальный emailfast
и шлёт уведомление о возврате.

Self-identification через socket-трюк: если запущен на primary (s3) — молча
выходит с exit 0. В supervisor-конфиге `exitcodes=0` чтобы EXITED воспринимался
как нормальное завершение.
"""
import asyncio
import logging
import socket
import subprocess
import sys
from pathlib import Path

import aiohttp
import yaml

PRIMARY_HOST = "72.56.100.74"
BACKUP_HOST = "REDACTED"
HEALTH_URL = f"http://{PRIMARY_HOST}:8080/health"

PROBE_INTERVAL_SEC = 10
PROBE_TIMEOUT_SEC = 5
FAIL_THRESHOLD = 3
RECOVERY_THRESHOLD = 18

SANITY_HOSTS = [("8.8.8.8", 53), ("1.1.1.1", 53)]
SANITY_TIMEOUT_SEC = 3

SUPERVISOR_PROGRAM = "emailfast"
SUPERVISOR_TIMEOUT_SEC = 30

CONFIG_YAML_PATH = Path(__file__).resolve().parent / "app" / "config.yaml"
LOG_PATH = "/var/log/emailfast_watcher.log"


def _build_logger() -> logging.Logger:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(LOG_PATH))
    except (OSError, PermissionError):
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("failover_watcher")


log = _build_logger()


def detect_self_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def ensure_backup_role() -> None:
    try:
        ip = detect_self_ip()
    except OSError as e:
        log.warning(f"Cannot detect self IP ({e}), assuming backup")
        return
    log.info(f"Self IP: {ip}")
    if ip == PRIMARY_HOST:
        log.info("Running on primary host, watcher not needed, exiting")
        sys.exit(0)
    if ip != BACKUP_HOST:
        log.warning(
            f"Self IP {ip} matches neither primary ({PRIMARY_HOST}) "
            f"nor backup ({BACKUP_HOST}); proceeding as backup"
        )


def load_config() -> tuple[str, list[int]]:
    cfg = yaml.safe_load(CONFIG_YAML_PATH.read_text(encoding="utf-8"))
    token = cfg.get("API_TOKEN")
    admins = cfg.get("ADMINS", [])
    if not token:
        log.error("API_TOKEN missing in config.yaml, exiting")
        sys.exit(1)
    if not admins:
        log.error("ADMINS empty in config.yaml, exiting")
        sys.exit(1)
    return token, admins


async def sanity_internet_ok() -> bool:
    for host, port in SANITY_HOSTS:
        try:
            fut = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(fut, timeout=SANITY_TIMEOUT_SEC)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception as e:
            log.debug(f"sanity {host}:{port} failed: {e}")
    return False


async def probe_primary(session: aiohttp.ClientSession) -> bool:
    try:
        async with session.get(
            HEALTH_URL, timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_SEC)
        ) as r:
            return r.status == 200
    except Exception as e:
        log.debug(f"probe failed: {e}")
        return False


def _supervisor_sync(action: str) -> bool:
    try:
        res = subprocess.run(
            ["supervisorctl", action, SUPERVISOR_PROGRAM],
            capture_output=True,
            text=True,
            timeout=SUPERVISOR_TIMEOUT_SEC,
        )
        log.info(
            f"supervisorctl {action} {SUPERVISOR_PROGRAM}: "
            f"rc={res.returncode} stdout={res.stdout.strip()!r} "
            f"stderr={res.stderr.strip()!r}"
        )
        return res.returncode == 0
    except Exception as e:
        log.error(f"supervisorctl {action} error: {e}")
        return False


async def supervisor(action: str) -> bool:
    return await asyncio.to_thread(_supervisor_sync, action)


async def notify_admins(token: str, admins: list[int], text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with aiohttp.ClientSession() as s:
        for admin_id in admins:
            try:
                async with s.post(
                    url,
                    json={"chat_id": admin_id, "text": text, "parse_mode": "HTML"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status != 200:
                        body = await r.text()
                        log.warning(
                            f"notify {admin_id}: status={r.status} body={body}"
                        )
            except Exception as e:
                log.error(f"notify {admin_id} failed: {e}")


FAILOVER_TEXT = (
    f"\U0001F534 <b>Failover</b>\n\n"
    f"Основной сервер <code>{PRIMARY_HOST}</code> недоступен.\n"
    f"Бот запущен на резервном <code>{BACKUP_HOST}</code>."
)

RECOVERY_TEXT = (
    f"\U0001F7E2 <b>Восстановление</b>\n\n"
    f"Основной сервер <code>{PRIMARY_HOST}</code> снова работает.\n"
    f"Резервный <code>{BACKUP_HOST}</code> отключён, бот вернулся на основной."
)


async def run() -> None:
    token, admins = load_config()
    log.info(f"Loaded config: {len(admins)} admin(s)")

    async with aiohttp.ClientSession() as session:
        initial_ok = await probe_primary(session)

        if initial_ok:
            state = "primary"
            log.info("Primary OK at start, ensuring local emailfast is stopped")
            await supervisor("stop")
            fail_count = 0
            ok_count = 0
        else:
            log.warning("Primary not reachable at start, running sanity check")
            if await sanity_internet_ok():
                log.error(
                    "FAILOVER at startup: primary down + sanity ok, "
                    "starting local emailfast"
                )
                if await supervisor("start"):
                    state = "backup"
                    fail_count = 0
                    ok_count = 0
                    await notify_admins(token, admins, FAILOVER_TEXT)
                else:
                    log.error(
                        "supervisorctl start failed at startup, "
                        "defaulting to primary state, will retry in loop"
                    )
                    state = "primary"
                    fail_count = FAIL_THRESHOLD - 1
                    ok_count = 0
            else:
                log.error(
                    "Sanity check failed at startup (backup has no internet), "
                    "defaulting to primary state"
                )
                state = "primary"
                fail_count = 0
                ok_count = 0

        while True:
            await asyncio.sleep(PROBE_INTERVAL_SEC)
            ok = await probe_primary(session)

            if state == "primary":
                if ok:
                    if fail_count:
                        log.info(f"Primary back to ok (after {fail_count} fail(s))")
                    fail_count = 0
                else:
                    fail_count += 1
                    log.warning(
                        f"Primary probe failed ({fail_count}/{FAIL_THRESHOLD})"
                    )
                    if fail_count >= FAIL_THRESHOLD:
                        if not await sanity_internet_ok():
                            log.warning(
                                "Sanity check failed (no internet on backup), "
                                "skipping failover"
                            )
                            fail_count = 0
                        else:
                            log.error("FAILOVER: primary down, starting backup")
                            if await supervisor("start"):
                                state = "backup"
                                ok_count = 0
                                await notify_admins(token, admins, FAILOVER_TEXT)
                            else:
                                log.error(
                                    "supervisorctl start failed, will retry"
                                )
                                fail_count = FAIL_THRESHOLD - 1
            else:
                if ok:
                    ok_count += 1
                    log.info(
                        f"Primary recovering ({ok_count}/{RECOVERY_THRESHOLD})"
                    )
                    if ok_count >= RECOVERY_THRESHOLD:
                        log.info("RECOVERY: primary stable, stopping backup")
                        if await supervisor("stop"):
                            state = "primary"
                            fail_count = 0
                            await notify_admins(token, admins, RECOVERY_TEXT)
                        else:
                            log.error("supervisorctl stop failed, will retry")
                            ok_count = RECOVERY_THRESHOLD - 1
                else:
                    if ok_count:
                        log.info(f"Primary still down (after {ok_count} ok)")
                    ok_count = 0


def main() -> None:
    ensure_backup_role()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Interrupted")


if __name__ == "__main__":
    main()
