"""Resolves trading symbols to Upstox instrument keys using Upstox's published instrument master.

Instrument keys (e.g. ``NSE_EQ|<ISIN>``) are taken from the downloaded file, never guessed.
"""

import gzip
import json
import logging
from datetime import date
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

KEEP_SEGMENTS = {"NSE_EQ", "BSE_EQ", "NSE_INDEX", "BSE_INDEX"}

# Candidate names to look up in the master file for each dashboard index.
INDEX_CANDIDATES = {
    "NIFTY 50": ("NSE_INDEX", ("NIFTY 50", "NIFTY")),
    "BANK NIFTY": ("NSE_INDEX", ("NIFTY BANK", "BANKNIFTY", "BANK NIFTY")),
    "SENSEX": ("BSE_INDEX", ("SENSEX", "S&P BSE SENSEX", "BSE SENSEX")),
}


def _norm(value: str | None) -> str:
    return " ".join((value or "").upper().split())


class InstrumentMaster:
    def __init__(self, url_template: str, cache_dir: Path, http: httpx.AsyncClient) -> None:
        self._url_template = url_template
        self._cache_dir = cache_dir
        self._http = http
        self._records: list[dict] = []
        self._loaded_for: date | None = None

    async def ensure_loaded(self) -> None:
        today = date.today()
        if self._loaded_for == today and self._records:
            return
        records: list[dict] = []
        for exchange in ("NSE", "BSE"):
            records.extend(await self._load_exchange(exchange, today))
        self._records = records
        self._loaded_for = today
        log.info("instrument master loaded: %d records", len(records))

    async def _load_exchange(self, exchange: str, today: date) -> list[dict]:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_dir / f"{exchange}-{today.isoformat()}.json.gz"
        if not path.exists():
            url = self._url_template.format(exchange=exchange)
            resp = await self._http.get(url, timeout=60)
            resp.raise_for_status()
            path.write_bytes(resp.content)
            for old in self._cache_dir.glob(f"{exchange}-*.json.gz"):
                if old != path:
                    old.unlink(missing_ok=True)
        rows = json.loads(gzip.decompress(path.read_bytes()))
        return [r for r in rows if r.get("segment") in KEEP_SEGMENTS]

    def find_equity(self, symbol: str, segment: str = "NSE_EQ") -> dict | None:
        target = _norm(symbol)
        for r in self._records:
            if r.get("segment") == segment and _norm(r.get("trading_symbol")) == target:
                if r.get("instrument_type") in (None, "EQ"):
                    return r
        return None

    def find_index(self, label: str) -> dict | None:
        segment, candidates = INDEX_CANDIDATES[label]
        pool = [r for r in self._records if r.get("segment") == segment]
        for cand in candidates:
            for field in ("name", "trading_symbol"):
                for r in pool:
                    if _norm(r.get(field)) == cand:
                        return r
        return None
