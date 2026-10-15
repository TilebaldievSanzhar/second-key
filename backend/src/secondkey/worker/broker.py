"""Брокер очереди.

Redis Streams с consumer group: задача подтверждается после выполнения, упавший воркер
не теряет её — другой воркер заберёт неподтверждённое после idle_timeout.
В тестах — InMemoryBroker(await_inplace): задача выполняется прямо в вызове kiq().

Запуск: taskiq worker secondkey.worker.broker:broker secondkey.worker.tasks
        taskiq scheduler secondkey.worker.broker:scheduler secondkey.worker.tasks
"""

from __future__ import annotations

from taskiq import AsyncBroker, InMemoryBroker, SmartRetryMiddleware, TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.schedule_sources import LabelScheduleSource

from secondkey.config import settings
from secondkey.observability import configure_logging, log


def _make_broker() -> AsyncBroker:
    if settings.env == "test":
        return InMemoryBroker(await_inplace=True)
    from taskiq_redis import RedisStreamBroker

    return RedisStreamBroker(
        url=settings.redis_url,
        queue_name="secondkey:tasks",
        consumer_group_name="secondkey-workers",
        idle_timeout=10 * 60 * 1000,  # мс: после этого чужая неподтверждённая задача переходит живому воркеру
        maxlen=10_000,
    )


broker = _make_broker().with_middlewares(
    # повторы только для задач с меткой retry_on_error (сеть внешних источников), с растущей паузой
    SmartRetryMiddleware(
        default_retry_count=3, default_delay=30, use_jitter=True, use_delay_exponent=True, max_delay_exponent=600
    ),
)
scheduler = TaskiqScheduler(broker, sources=[LabelScheduleSource(broker)])


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _startup(state: TaskiqState) -> None:
    from secondkey.services.storage import get_storage

    configure_logging(settings.log_level, settings.log_json)
    await get_storage().ensure_ready()
    log.info("worker.startup", env=settings.env)


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _shutdown(state: TaskiqState) -> None:
    from secondkey.db.session import dispose_engine

    await dispose_engine()
