"""Short-lived cache: Redis when REDIS_URL is set, otherwise an in-process TTL dict."""

import json
import logging
import time
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Cache(Protocol):
    backend: str

    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...
    async def close(self) -> None: ...


class MemoryCache:
    backend = "memory"

    def __init__(self, max_items: int = 2000) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._max_items = max_items

    async def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            self._items.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if len(self._items) >= self._max_items:
            now = time.monotonic()
            self._items = {k: v for k, v in self._items.items() if v[0] >= now}
            if len(self._items) >= self._max_items:
                self._items.pop(next(iter(self._items)))
        self._items[key] = (time.monotonic() + ttl_seconds, value)

    async def close(self) -> None:
        self._items.clear()


class RedisCache:
    backend = "redis"

    def __init__(self, url: str) -> None:
        from redis.asyncio import Redis

        self._redis = Redis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> Any | None:
        try:
            raw = await self._redis.get(key)
        except Exception as exc:  # cache failures must never break data serving
            log.warning("redis get failed: %s", exc)
            return None
        return None if raw is None else json.loads(raw)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            await self._redis.set(key, json.dumps(value, default=str), ex=ttl_seconds)
        except Exception as exc:
            log.warning("redis set failed: %s", exc)

    async def close(self) -> None:
        await self._redis.aclose()


def create_cache(redis_url: str) -> Cache:
    if redis_url:
        return RedisCache(redis_url)
    return MemoryCache()
