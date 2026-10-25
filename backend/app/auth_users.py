"""Site login: a few named users with hashed passwords, and signed session cookies.

Users come from the APP_USERS env var: "Name:pbkdf2_sha256.iterations.salt.hash,Other:...". No plaintext passwords
are stored anywhere; make a hash with `python -m app.auth_users hash`. When APP_USERS is empty the site is open
(local development and tests).

A session is "base64(json {u, exp, k}).hmac" signed with SESSION_SECRET. The browser gets it as an HttpOnly cookie
through the site's own /api proxy; the live-quotes websocket, which connects to the backend directly, uses a
short-lived token of kind "ws" instead.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

log = logging.getLogger(__name__)

COOKIE = "ais_session"
SESSION_DAYS = 30
WS_TOKEN_SECONDS = 600
ITERATIONS = 240_000
MAX_FAILURES = 5  # wrong passwords per user or IP within the window before logins are refused
FAILURE_WINDOW = 15 * 60


def hash_password(password: str, iterations: int = ITERATIONS, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"pbkdf2_sha256.{iterations}.{salt}.{digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split(".")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256" or not iterations.isdigit():
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(check, digest)


def parse_users(spec: str) -> dict[str, tuple[str, str]]:
    """{lower-cased name: (display name, hash)} from "Name:hash,Name2:hash2"."""
    users: dict[str, tuple[str, str]] = {}
    for part in (spec or "").split(","):
        name, sep, stored = part.strip().partition(":")
        if sep and name.strip() and stored.strip():
            users[name.strip().lower()] = (name.strip(), stored.strip())
    return users


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Auth:
    def __init__(self, users_spec: str, secret: str) -> None:
        self.users = parse_users(users_spec)
        if self.users and not secret:
            log.warning("SESSION_SECRET is not set: using a random one, so everyone is logged out on every restart")
        self._key = (secret or secrets.token_hex(32)).encode()
        self._failures: dict[str, list[float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.users)

    # ---- passwords and lockout ----
    def _recent(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._failures.get(key, []) if now - t < FAILURE_WINDOW]
        if hits:
            self._failures[key] = hits
        else:
            self._failures.pop(key, None)
        return hits

    def locked(self, username: str, ip: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return any(len(self._recent(k, now)) >= MAX_FAILURES for k in (f"u:{username.lower()}", f"ip:{ip}"))

    def check(self, username: str, password: str, ip: str, now: float | None = None) -> str | None:
        """The user's display name if the password is right, else None (and the failure is counted)."""
        now = time.time() if now is None else now
        entry = self.users.get(username.strip().lower())
        if entry and verify_password(password, entry[1]):
            self._failures.pop(f"u:{username.strip().lower()}", None)
            return entry[0]
        if not entry:
            verify_password(password, hash_password("x", salt="0" * 32))  # same work either way: no user enumeration
        for k in (f"u:{username.strip().lower()}", f"ip:{ip}"):
            self._failures.setdefault(k, []).append(now)
        return None

    # ---- signed tokens ----
    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def issue(self, user: str, kind: str = "session", seconds: int = SESSION_DAYS * 86400, now: float | None = None) -> str:
        now = time.time() if now is None else now
        body = _b64(json.dumps({"u": user, "exp": int(now + seconds), "k": kind}, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def verify(self, token: str | None, kind: str = "session", now: float | None = None) -> str | None:
        """The user in a valid, unexpired token of this kind, else None. Removed users are refused."""
        if not token or "." not in token:
            return None
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(body)):
            return None
        try:
            data = json.loads(_unb64(body))
        except (ValueError, json.JSONDecodeError):
            return None
        now = time.time() if now is None else now
        if data.get("k") != kind or data.get("exp", 0) < now:
            return None
        user = str(data.get("u") or "")
        return self.users[user.lower()][0] if user.lower() in self.users else None


if __name__ == "__main__":  # python -m app.auth_users hash  → prompts for a password, prints the hash
    import getpass
    import sys

    if sys.argv[1:2] == ["hash"]:
        print(hash_password(getpass.getpass("Password: ")))
    else:
        print("usage: python -m app.auth_users hash")
