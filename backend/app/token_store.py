"""Server-side storage for the Upstox access token. The token never leaves the backend."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from app.market_session import IST, next_upstox_token_expiry, now_ist

log = logging.getLogger(__name__)


class TokenStore:
    def __init__(self, path: Path, env_token: str = "") -> None:
        self._path = path
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._source = "none"
        self._invalid_reason: str | None = None
        self._load(env_token)

    def _load(self, env_token: str) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                expires_at = datetime.fromisoformat(raw["expires_at"])
                if expires_at > now_ist():
                    self._token, self._expires_at, self._source = raw["access_token"], expires_at, "oauth"
                    return
            except Exception as exc:
                log.warning("ignoring unreadable token file: %s", exc)
        if env_token:
            # Issue time is unknown for an env token; it stays usable until Upstox rejects it.
            self._token, self._source = env_token, "env"

    def get(self) -> str | None:
        if self._token and self._expires_at and self._expires_at <= now_ist():
            self.invalidate("expired")
        return self._token

    def set(self, token: str, issued_at: datetime | None = None) -> None:
        self._token = token
        self._expires_at = next_upstox_token_expiry(issued_at or now_ist())
        self._source = "oauth"
        self._invalid_reason = None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"access_token": token, "expires_at": self._expires_at.isoformat()})
        )
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def invalidate(self, reason: str) -> None:
        self._token = None
        self._invalid_reason = reason
        self._path.unlink(missing_ok=True)

    def status(self) -> dict:
        token = self.get()
        return {
            "authenticated": token is not None,
            "source": self._source if token else "none",
            "expires_at": self._expires_at.astimezone(IST).isoformat() if token and self._expires_at else None,
            "invalid_reason": self._invalid_reason,
        }
