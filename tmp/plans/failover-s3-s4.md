# EmailFast — Failover между s3 и s4

## Контекст

Боевой бот EmailFast крутится на **s3 (72.56.100.74)** под supervisor (`emailfast`). Иногда сервер падает целиком — Мишаня вручную идёт на **s4 (REDACTED)** и поднимает бот там. Задача — автоматизировать переключение:

- При падении s3 → автоматически стартовать бот на s4 + уведомить ADMINS (1687225894, 1089138631, 808667695)
- При восстановлении s3 → остановить бот на s4 (cooldown 3 мин для стабильности) + уведомить ADMINS

**ТЕКУЩАЯ СИТУАЦИЯ:** s3 сейчас **недоступен**. Деплой нужен на s4 (`git push production`). После деплоя watcher через ~30 сек сам стартанёт `emailfast` на s4 и пришлёт уведомление о failover.

**Критичный нюанс архитектуры:** бот использует Telegram long polling ([main.py:194](main.py#L194)). Два процесса с одним токеном одновременно работать НЕ могут — Telegram отдаёт ошибку `Conflict: terminated by other getUpdates request`. Failover должен быть active-passive с жёстким взаимным исключением.

**Дополнительные требования от Мишани:**
1. Код одинаковый на s3 и s4 (общий git remote) → watcher должен **сам определять** что он на s4, и **молча выходить** если запущен на s3
2. **Sanity-check** добавить сразу (чтобы не было ложного failover при потере интернета у s4)

**Параметры (из обсуждения):**
- Watcher живёт на s4 (на s3 запустится, увидит "я primary" и выйдет с exit 0)
- Код на s4 уже синхронизирован вручную
- Порог failover: 30 сек (3 неудачные пробы по 10 сек)
- Возврат: cooldown 3 минуты стабильной работы s3

## Архитектура

```
   ┌──────────────────────┐         ┌───────────────────────┐
   │  s3 (72.56.100.74)   │         │  s4 (REDACTED)   │
   │                      │         │                       │
   │  emailfast (RUNNING) │         │  emailfast (STOPPED)  │
   │  health_server :8080 │◄────────│  failover_watcher.py  │
   │                      │  пинг   │  (RUNNING постоянно)  │
   │  failover_watcher.py │         │                       │
   │  → exit 0 (я primary)│         │                       │
   └──────────────────────┘         └───────────────────────┘
```

### Self-identification

Watcher на старте определяет свой публичный IP через стандартный socket-трюк (без внешних зависимостей):

```python
def detect_self_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))  # не шлёт реальные пакеты, только route lookup
        return s.getsockname()[0]
    finally:
        s.close()
```

Если результат начинается с `72.56.100.74` → лог `"Running on primary host, watcher not needed, exiting"` и `sys.exit(0)`. Supervisor с `autorestart=true` будет пытаться перезапустить — добавим `startretries=1` и `exitcodes=0`, чтобы supervisor увидел нормальный выход и оставил программу в `EXITED`.

Если IP не похож ни на 72.56.100.74, ни на REDACTED — fallback: всё равно работаем как backup (на случай если IP-адрес сменился, например через NAT). В лог — warning.

### Sanity-check перед failover

Перед тем как объявить s3 недоступным, watcher проверяет что у самого s4 работает интернет — TCP-connect на `8.8.8.8:53` и `1.1.1.1:53` с таймаутом 3 сек. Если **оба** не отвечают → значит у s4 проблемы с сетью, failover делать **нельзя** (бесполезно, резервный бот тоже не сможет общаться с Telegram). В таком случае:
- Сбрасываем `fail_count`
- Пишем в лог `WARNING: sanity check failed (s4 has no internet), skipping failover`
- НЕ шлём уведомление админам (всё равно не дойдёт)

Sanity-check вызывается **только** в момент достижения порога failover, не на каждой пробе — не нужно нагружать.

## Изменения

### 1. Новый файл: `failover_watcher.py` (в корне проекта)

Самодостаточный демон без зависимости от структуры основного бота — работает даже если у бота сломались импорты.

**Логика:**
- На старте: `detect_self_ip()` → если primary, exit 0
- Загружает `API_TOKEN` и `ADMINS` из `app/config.yaml` (через `yaml.safe_load`, не через `app.dependencies` чтобы не цеплять aiogram)
- На старте: `supervisorctl stop emailfast` (на всякий случай гарантируем что бот выключен)
- Состояние: `primary` (бот живёт на s3) или `backup` (бот живёт на s4)
- Цикл каждые 10 сек: `aiohttp.GET http://72.56.100.74:8080/health` с таймаутом 5 сек
- В `primary`: счётчик `fail_count`. При 3 подряд fail → **sanity_check()** → если ok → `supervisorctl start emailfast` + alert + переход в `backup`
- В `backup`: счётчик `ok_count`. При 18 подряд ok (3 мин) → `supervisorctl stop emailfast` + alert + переход в `primary`
- Любой ok сбрасывает `fail_count`; любой fail сбрасывает `ok_count`

**Алерты админам** — прямой POST к Telegram Bot API (`sendMessage`), не через aiogram. HTML parse_mode.

- Failover:
  ```
  🔴 <b>Failover</b>
  
  Основной сервер <code>72.56.100.74</code> недоступен.
  Бот запущен на резервном <code>REDACTED</code>.
  ```
- Recovery:
  ```
  🟢 <b>Восстановление</b>
  
  Основной сервер <code>72.56.100.74</code> снова работает.
  Резервный <code>REDACTED</code> отключён, бот вернулся на основной.
  ```

`supervisorctl` через `asyncio.to_thread(subprocess.run, ...)` с таймаутом 30 сек.

Логи — `logging` stdlib в `/var/log/emailfast_watcher.log` (через FileHandler, без Loguru — минимум зависимостей).

Зависимости: только `aiohttp` и `pyyaml` — обе уже в `requirements.txt`.

### 2. Supervisor-конфиг на s4

Файл `/etc/supervisor/conf.d/emailfast.conf` (или текущий конфиг `emailfast` на s4):

```ini
[program:emailfast]
command=/srv/emailfast/.venv/bin/python3 /srv/emailfast/main.py
directory=/srv/emailfast
autostart=false               ; ← КЛЮЧЕВОЕ: watcher решает когда запускать
autorestart=true
stdout_logfile=/var/log/emailfast.log
stderr_logfile=/var/log/emailfast.err.log
stopasgroup=true
killasgroup=true

[program:emailfast_watcher]
command=/srv/emailfast/.venv/bin/python3 /srv/emailfast/failover_watcher.py
directory=/srv/emailfast
autostart=true
autorestart=true
startretries=3
exitcodes=0                   ; ← нужно чтобы supervisor не перезапускал watcher на s3
stdout_logfile=/var/log/emailfast_watcher.log
stderr_logfile=/var/log/emailfast_watcher.err.log
```

После: `supervisorctl reread && supervisorctl update`.

**На s3 этот же конфиг тоже можно положить** — watcher автоматически детектит что он primary и выйдет с exit 0, supervisor увидит exitcode=0 и оставит в EXITED. Но `emailfast` на s3 должен остаться с **`autostart=true`** (на s3 бот должен стартовать сам). Это значит конфиг между s3 и s4 всё-таки **различается** одной строкой (`autostart` у emailfast).

### 3. Проверки/настройка на s3 (когда поднимется)

- `health_server.py` уже слушает `0.0.0.0:8080` (commit `a2f6663`) — убедиться что он запущен через supervisor
- Файрвол открыт для входящих TCP/8080 с REDACTED:
  ```bash
  ufw allow from REDACTED to any port 8080
  ```
- Снаружи доступен: `curl http://72.56.100.74:8080/health` с s4 возвращает 200
- `autostart=true` у `emailfast` (бот при загрузке сам стартует)

### 4. Деплой (в текущей ситуации — s3 недоступен)

1. **Локально:** написать `failover_watcher.py`, проверить синтаксис `python3 -m py_compile failover_watcher.py`
2. **Commit + push на production remote:** `git push production master`
   - Push дойдёт только до s4 (s3 недоступен). Когда s3 поднимется, дотянуть отдельно.
3. **SSH на s4:** `ssh root@REDACTED`
4. **Обновить supervisor-конфиг** на s4 (см. п.2)
5. **`supervisorctl reread && supervisorctl update`**
6. **Через ~30 сек:**
   - В логе `/var/log/emailfast_watcher.log` — 3 fail-пробы, sanity ok, старт emailfast
   - `supervisorctl status emailfast` → RUNNING
   - Все 3 админа получают `🔴 Failover` сообщение
7. **Когда s3 поднимется:**
   - `git push production master` (теперь и до s3 дойдёт)
   - На s3 настроить supervisor-конфиг (health_server должен слушать :8080, autostart=true для emailfast)
   - Открыть файрвол 8080
   - Watcher на s4 через 3 минуты успешных проб остановит локальный бот и пришлёт `🟢 Восстановление`

## Критичные файлы

- [main.py](main.py) — точка входа основного бота (НЕ меняем)
- [health_server.py](health_server.py) — health endpoint (НЕ меняем)
- [app/config.yaml](app/config.yaml) — `API_TOKEN`, `ADMINS` (читаем из watcher через yaml.safe_load)
- [app/services/notify_admins.py](app/services/notify_admins.py) — для справки, watcher НЕ использует (свой код алерта)
- **НОВЫЙ:** `failover_watcher.py` в корне репы
- supervisor-конфиг на s4 (вне репы, правится руками)

## Известные ограничения

1. **Health врёт при падении только `main.py`:** [health_server.py:24-35](health_server.py#L24-L35) проверяет Telegram `getMe`, а не polling-процесс. Если на s3 health_server жив, а main.py упал — watcher НЕ сделает failover. Для текущей задачи "сервер не работает целиком" — это ОК. Если нужно ловить и падение бота — отдельной задачей: добавить heartbeat-файл от main.py.
2. **Split-brain при односторонней потере связи:** если s4 не видит s3, но s3 жив и его видит остальной мир, sanity-check (ping 8.8.8.8/1.1.1.1) пройдёт, watcher на s4 запустит бот → Conflict в polling. Полностью устранить можно только через quorum (3-я нода) или через явное "fencing" (например watcher на s4 предварительно делает SSH-stop на s3) — оба решения выходят за рамки задачи.
3. **Scheduler в момент перехода:** APScheduler с jobstore в БД — общая БД через PG, оба сервера видят одни и те же jobs. При переключении возможна короткая (миллисекунды) race — два процесса не запустят одну job дважды (jobstore хранит lock'и).
4. **PostgreSQL `REDACTED`** — единая точка отказа, failover для БД в эту задачу не входит.

## Верификация

### Локально (без серверов)

1. Синтаксис: `python3 -m py_compile failover_watcher.py` ✓
2. Прогон `detect_self_ip()` локально — должен вернуть IP домашней машины, watcher выйдет с exit 0 в primary-detection (на самом деле IP не совпадёт с PRIMARY_HOST, и упадёт в fallback "работаю как backup") — для локального теста можно временно захардкодить состояние, но это опционально.

### На s4 после деплоя (s3 недоступен)

1. `supervisorctl status` показывает:
   - `emailfast_watcher RUNNING`
   - `emailfast STOPPED` (на момент сразу после `update`)
2. Через ~30 сек после старта watcher:
   - `tail -f /var/log/emailfast_watcher.log` — видны fail-пробы
   - `supervisorctl status emailfast` → RUNNING
3. Все 3 админа получают `🔴 Failover` в Telegram
4. Бот реально отвечает на сообщения (написать боту → получить ответ)

### Когда s3 вернётся в строй

1. Доступность с s4: `curl http://72.56.100.74:8080/health` → 200
2. В логе watcher — счёт ok_count растёт
3. Через 3 минуты (`ok_count >= 18`):
   - `supervisorctl status emailfast` на s4 → STOPPED
   - Все 3 админа получают `🟢 Восстановление`
   - Бот на s3 отвечает на сообщения (один экземпляр работает)
4. **Проверить что на s4 watcher не пытается снова стартовать бот** (state = primary, fail_count = 0)

### Тест self-identification (когда оба сервера живы)

На s3 положить `failover_watcher.py` (через git pull) и (опционально) добавить программу в supervisor. Запустить вручную: `python3 failover_watcher.py` → должен сразу написать в лог `"Running on primary host, exiting"` и завершиться с exit 0.

## Что НЕ входит в задачу

- Heartbeat-файл от main.py (только когда health-server остался жив, но бот упал)
- Webhook вместо polling
- Авто-sync кода (ручной git push)
- Failover для PostgreSQL
- Quorum / fencing для борьбы со split-brain
