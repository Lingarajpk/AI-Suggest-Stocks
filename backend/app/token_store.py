"""Server-side storage for the Upstox access token. The token never leaves the backend.

The token is kept in memory, in a local file and, when REDIS_URL is set, in Redis. Hosts like
Render's free tier wipe local files on every restart or sleep, so Redis is what lets the token
survive until it expires (03:30 IST) instead of forcing a new login after each restart.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from app.cache import Cache
from app.market_session import IST, next_upstox_token_expiry, now_ist

log = logging.getLogger(__name__)

DURABLE_KEY = "auth:upstox:token"


class TokenStore:
    def __init__(self, path: Path, env_token: str = "", durable: Cache | None = None) -> None:
        self._path = path
        self._durable = durable
        self._env_token = env_token
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._user_id: str | None = None
        self._user_name: str | None = None
        self._source = "none"
        self._invalid_reason: str | None = None
        self._load_file()

    # ---- loading -------------------------------------------------------------
    def _apply(self, raw: dict) -> bool:
        expires_at = datetime.fromisoformat(raw["expires_at"])
        if expires_at <= now_ist():
            return False
        self._token, self._expires_at = raw["access_token"], expires_at
        self._user_id, self._user_name = raw.get("user_id"), raw.get("user_name")
        self._source = raw.get("source", "oauth")
        self._invalid_reason = None
        return True

    def _load_file(self) -> None:
        if self._path.exists():
            try:
                if self._apply(json.loads(self._path.read_text())):
                    return
            except Exception as exc:
                log.warning("ignoring unreadable token file: %s", exc)
        if self._env_token:
            # Issue time is unknown for an env token; it stays usable until Upstox rejects it.
            self._token, self._source = self._env_token, "env"

    async def restore(self) -> None:
        """At startup: take the token from Redis when the local file did not survive a restart."""
        if self._durable is None or (self._token and self._source != "env"):
            return
        try:
            raw = await self._durable.get(DURABLE_KEY)
            if raw and self._apply(raw):
                self._write_file(raw)
                log.info("Upstox token restored from Redis (expires %s)", self._expires_at)
        except Exception as exc:  # Redis down: carry on with whatever we have
            log.warning("could not restore Upstox token from Redis: %s", exc)

    # ---- access --------------------------------------------------------------
    def get(self) -> str | None:
        if self._token and self._expires_at and self._expires_at <= now_ist():
            self.invalidate("expired")
        return self._token

    @property
    def user_id(self) -> str | None:
        return self._user_id if self.get() else None

    async def set(self, token: str, issued_at: datetime | None = None, expires_at: datetime | None = None,
                  user_id: str | None = None, user_name: str | None = None, source: str = "oauth") -> None:
        self._token = token
        self._expires_at = expires_at or next_upstox_token_expiry(issued_at or now_ist())
        self._user_id, self._user_name = user_id, user_name
        self._source = source
        self._invalid_reason = None
        raw = {"access_token": token, "expires_at": self._expires_at.isoformat(), "user_id": user_id,
               "user_name": user_name, "source": source}
        self._write_file(raw)
        if self._durable is not None:
            ttl = max(60, int((self._expires_at - now_ist()).total_seconds()))
            try:
                await self._durable.set(DURABLE_KEY, raw, ttl)
            except Exception as exc:
                log.warning("could not save Upstox token to Redis: %s", exc)

    def _write_file(self, raw: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(raw))
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def invalidate(self, reason: str) -> None:
        """Drop the token locally. Redis keeps its copy until it expires; restore() checks expiry and a
        rejected token is simply rejected again, so there is no need to reach Redis from sync code."""
        self._token = None
        self._invalid_reason = reason
        self._path.unlink(missing_ok=True)

    def status(self) -> dict:
        token = self.get()
        name = self._user_name or ""
        return {
            "authenticated": token is not None,
            "source": self._source if token else "none",
            "expires_at": self._expires_at.astimezone(IST).isoformat() if token and self._expires_at else None,
            "invalid_reason": self._invalid_reason,
            # First name only: enough for the owner to see whose account feeds the site.
            "connected_as": name.split()[0].title() if token and name else None,
            "durable": self._durable is not None,
        }
