"""Sliding-window limiter that keeps outbound provider calls under configured limits."""

import asyncio
import time
from collections import deque


class RateLimiter:
    def __init__(self, per_second: int, per_minute: int) -> None:
        self._windows = [(1.0, per_second, deque()), (60.0, per_minute, deque())]
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                wait = 0.0
                for span, limit, stamps in self._windows:
                    while stamps and now - stamps[0] >= span:
                        stamps.popleft()
                    if len(stamps) >= limit:
                        wait = max(wait, span - (now - stamps[0]))
                if wait <= 0:
                    for _, _, stamps in self._windows:
                        stamps.append(now)
                    return
                await asyncio.sleep(wait)
