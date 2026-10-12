"""Web push: notifications to a phone or desktop even when the site isn't open.

Uses the standard Web Push protocol (VAPID). The key pair is created once and kept in Redis (or a
local file) unless VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are set. On iPhone, web push only works for
a site added to the Home Screen (iOS 16.4+).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.cache import Cache

log = logging.getLogger(__name__)
KEY_NAME = "push:vapid:v1"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_vapid() -> dict:
    private = ec.generate_private_key(ec.SECP256R1())
    raw_private = private.private_numbers().private_value.to_bytes(32, "big")
    raw_public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"public": _b64url(raw_public), "private": _b64url(raw_private)}


class PushSender:
    def __init__(self, contact: str, path: Path, durable: Cache | None, public: str = "", private: str = "") -> None:
        self._contact = contact
        self._path = path
        self._durable = durable
        self.keys: dict | None = {"public": public, "private": private} if public and private else None

    async def load(self) -> None:
        """Use configured keys, else stored ones, else create and store a new pair."""
        if self.keys:
            return
        stored = None
        if self._durable is not None:
            stored = await self._durable.get(KEY_NAME)
        if not stored and self._path.exists():
            try:
                stored = json.loads(self._path.read_text())
            except ValueError:
                stored = None
        if not stored:
            stored = generate_vapid()
            log.info("created a new web-push key pair")
        self.keys = stored
        if self._durable is not None:
            await self._durable.set(KEY_NAME, stored, 10 * 365 * 86400)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(stored))
        except OSError:
            pass

    @property
    def public_key(self) -> str | None:
        return self.keys["public"] if self.keys else None

    async def send(self, subscription: dict, payload: dict) -> bool:
        """Deliver one notification. Returns False when the subscription is gone (unsubscribe it)."""
        if not self.keys:
            return True
        from pywebpush import WebPushException, webpush

        def work() -> bool:
            try:
                webpush(subscription_info=subscription, data=json.dumps(payload), vapid_private_key=self.keys["private"],
                        vapid_claims={"sub": self._contact}, ttl=600, timeout=10)
                return True
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):
                    return False
                log.warning("web push failed (HTTP %s)", status)
                return True
            except Exception as exc:  # network trouble: keep the subscription
                log.warning("web push failed: %s", exc.__class__.__name__)
                return True

        return await asyncio.to_thread(work)
