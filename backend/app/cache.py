"""In-process TTL cache for short-lived data, and a Redis store (REDIS_URL) for what must survive restarts."""

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
    """In-process TTL cache. Values are kept as JSON text, like Redis: several times smaller than Python objects
    (candle histories are big) and every reader gets its own copy. Bounded by item count and total size; the
    oldest entries go first."""

    backend = "memory"

    def __init__(self, max_items: int = 2000, max_bytes: int = 0) -> None:
        self._items: dict[str, tuple[float, str]] = {}
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._bytes = 0

    async def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, raw = item
        if expires_at < time.monotonic():
            self._drop(key)
            return None
        return json.loads(raw)

    def _drop(self, key: str) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            self._bytes -= len(item[1])

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        raw = json.dumps(value, default=str)
        self._drop(key)
        if len(self._items) >= self._max_items or (self._max_bytes and self._bytes + len(raw) > self._max_bytes):
            now = time.monotonic()
            for k in [k for k, (exp, _) in self._items.items() if exp < now]:
                self._drop(k)
            while self._items and (len(self._items) >= self._max_items or (self._max_bytes and self._bytes + len(raw) > self._max_bytes)):
                self._drop(next(iter(self._items)))  # oldest first (dicts keep insertion order)
        self._items[key] = (time.monotonic() + ttl_seconds, raw)
        self._bytes += len(raw)

    def status(self) -> dict:
        return {"items": len(self._items), "mb": round(self._bytes / 1e6, 1)}

    async def close(self) -> None:
        self._items.clear()
        self._bytes = 0


class RedisCache:
    """Durable store. Failures (Redis down, quota used up) never break the app: reads return nothing, writes are
    skipped, and the problem is logged once per outage instead of on every call."""

    backend = "redis"

    def __init__(self, url: str) -> None:
        from redis.asyncio import Redis

        self._redis = Redis.from_url(url, decode_responses=True)
        self.ops = 0  # commands sent since start (shown in /api/health)
        self.error: str | None = None
        self.error_since: float | None = None

    def _failed(self, op: str, exc: Exception) -> None:
        msg = str(exc)[:200]
        if self.error is None:
            log.warning("redis %s failed (logged once until it recovers): %s", op, msg)
            self.error_since = time.time()
        self.error = msg

    def _ok(self) -> None:
        if self.error is not None:
            log.info("redis is reachable again")
        self.error = self.error_since = None

    async def get(self, key: str) -> Any | None:
        self.ops += 1
        try:
            raw = await self._redis.get(key)
        except Exception as exc:  # cache failures must never break data serving
            self._failed("get", exc)
            return None
        self._ok()
        return None if raw is None else json.loads(raw)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.ops += 1
        try:
            await self._redis.set(key, json.dumps(value, default=str), ex=ttl_seconds)
        except Exception as exc:
            self._failed("set", exc)
            return
        self._ok()

    def status(self) -> dict:
        # Health is public: a category only (the full message, which can name the host, is in the log).
        problem = None if self.error is None else "quota used up" if "limit" in self.error.lower() else "unreachable"
        return {"backend": "redis", "ok": self.error is None, "problem": problem, "commands_since_start": self.ops}

    async def close(self) -> None:
        await self._redis.aclose()


def create_cache(redis_url: str) -> Cache:
    if redis_url:
        return RedisCache(redis_url)
    return MemoryCache()
