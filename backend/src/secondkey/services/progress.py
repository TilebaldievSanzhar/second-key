"""Прогресс прогонов: источник правды — БД, Redis pub/sub — доставка событий в SSE.

Если Redis недоступен (тесты, dev без Redis), событие просто не доставляется:
интерфейс перечитывает статус прогона и ничего не теряет.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from secondkey.config import settings
from secondkey.observability import log

_redis = None


def channel(run_id: uuid.UUID | str) -> str:
    return f"run:{run_id}"


async def get_redis():
    global _redis
    if _redis is None:
        from redis.asyncio import Redis

        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def publish(run_id: uuid.UUID, event: dict[str, Any]) -> None:
    if settings.env == "test":
        return
    try:
        r = await get_redis()
        await r.publish(channel(run_id), json.dumps(event, ensure_ascii=False, default=str))
    except Exception as exc:  # доставка событий не должна ронять прогон
        log.warning("progress.publish_failed", run_id=str(run_id), error=str(exc))
