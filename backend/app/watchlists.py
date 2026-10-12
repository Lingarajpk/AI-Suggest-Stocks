"""Per-device watchlists, so each phone or browser gets alerts for its own stocks.

A device is identified by a random ID its browser generates once; no account is needed. Each entry
holds the device's symbols, its web-push subscription (if notifications are on) and whether it is
the owner's device (whose watchlist decides what goes to Telegram). Everything is kept in one small
JSON document in Redis when configured (survives restarts on free hosting), else in a local file.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.cache import Cache
from app.market_session import now_ist

log = logging.getLogger(__name__)

DOC_KEY = "watchlists:v1"
MAX_DEVICES = 300
MAX_SYMBOLS = 30
DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
SYMBOL_RE = re.compile(r"^[A-Z0-9&_-]{1,20}( [A-Z0-9&]{1,10})?$")


def valid_device(device: str) -> bool:
    return bool(DEVICE_RE.match(device or ""))


def clean_symbols(symbols: list[str]) -> list[str]:
    out: list[str] = []
    for s in symbols:
        sym = " ".join(str(s).upper().split())
        if SYMBOL_RE.match(sym) and sym not in out:
            out.append(sym)
    return out[:MAX_SYMBOLS]


class WatchlistStore:
    def __init__(self, path: Path, durable: Cache | None) -> None:
        self._path = path
        self._durable = durable
        self._devices: dict[str, dict] = {}

    async def load(self) -> None:
        doc = None
        if self._durable is not None:
            doc = await self._durable.get(DOC_KEY)
        if doc is None and self._path.exists():
            try:
                doc = json.loads(self._path.read_text())
            except ValueError:
                doc = None
        self._devices = doc or {}

    async def _save(self) -> None:
        if len(self._devices) > MAX_DEVICES:  # drop the longest-unused devices
            keep = sorted(self._devices.items(), key=lambda kv: kv[1].get("seen", ""), reverse=True)[:MAX_DEVICES]
            self._devices = dict(keep)
        if self._durable is not None:
            await self._durable.set(DOC_KEY, self._devices, 400 * 86400)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._devices))
        except OSError:
            pass

    def get(self, device: str) -> dict:
        d = self._devices.get(device) or {}
        return {"symbols": d.get("symbols", []), "push": bool(d.get("push")), "owner": bool(d.get("owner"))}

    def _entry(self, device: str) -> dict:
        e = self._devices.setdefault(device, {"symbols": []})
        e["seen"] = now_ist().isoformat()
        return e

    async def set_symbols(self, device: str, symbols: list[str]) -> list[str]:
        self._entry(device)["symbols"] = clean_symbols(symbols)
        await self._save()
        return self._devices[device]["symbols"]

    async def set_push(self, device: str, subscription: dict | None) -> None:
        self._entry(device)["push"] = subscription
        await self._save()

    async def set_owner(self, device: str, owner: bool) -> None:
        self._entry(device)["owner"] = owner
        await self._save()

    def all_symbols(self) -> list[str]:
        seen: list[str] = []
        for d in self._devices.values():
            for s in d.get("symbols", []):
                if s not in seen:
                    seen.append(s)
        return seen

    def owner_symbols(self) -> set[str] | None:
        """Symbols on owner devices (for Telegram), or None when no device is marked as the owner's."""
        owners = [d for d in self._devices.values() if d.get("owner")]
        if not owners:
            return None
        return {s for d in owners for s in d.get("symbols", [])}

    def push_everyone(self) -> list[tuple[str, dict]]:
        return [(dev, d["push"]) for dev, d in self._devices.items() if d.get("push")]

    def push_targets(self, symbol: str) -> list[tuple[str, dict]]:
        """(device, subscription) for every device that watches `symbol` and has push turned on."""
        return [(dev, d["push"]) for dev, d in self._devices.items() if d.get("push") and symbol in d.get("symbols", [])]
