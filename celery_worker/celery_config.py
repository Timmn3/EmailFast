import sys
from celery import Celery

# Определяем брокер Redis
REDIS_URL = "redis://localhost:6379/0"

# Проверяем ОС
if sys.platform.startswith("win"):
    worker_pool = "solo"  # Windows поддерживает только одиночный режим
else:
    worker_pool = "prefork"  # Linux использует многопроцессорный режим

celery_app = Celery(
    "celery_worker",  # Имя должно совпадать с пакетом
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Настройки Celery
celery_app.conf.update(
    task_routes={"celery_worker.tasks.send_message_batch": {"queue": "broadcast"}},
    worker_pool=worker_pool,  # Автоматическое переключение режима работы
    broker_connection_retry_on_startup=True,  # сохраняем ретраи коннекта при старте (Celery 6.0+)
)

# Функция для избежания циклического импорта
def import_tasks():
    from celery_worker import tasks  # Импортируем все задачи

# Вызываем после создания `celery_app`
import_tasks()
