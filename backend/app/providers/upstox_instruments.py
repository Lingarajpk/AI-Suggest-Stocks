"""Resolves trading symbols to Upstox instrument keys using Upstox's published instrument master.

Instrument keys (e.g. ``NSE_EQ|<ISIN>``) are taken from the downloaded file, never guessed.
"""

import gzip
import json
import logging
from datetime import date, datetime
from pathlib import Path

import httpx

from app.market_session import IST

log = logging.getLogger(__name__)

KEEP_SEGMENTS = {"NSE_EQ", "BSE_EQ", "NSE_INDEX", "BSE_INDEX", "NCD_FO"}

# Candidate names to look up in the master file for each dashboard index.
INDEX_CANDIDATES = {
    "NIFTY 50": ("NSE_INDEX", ("NIFTY 50", "NIFTY")),
    "BANK NIFTY": ("NSE_INDEX", ("NIFTY BANK", "BANKNIFTY", "BANK NIFTY")),
    "SENSEX": ("BSE_INDEX", ("SENSEX", "S&P BSE SENSEX", "BSE SENSEX")),
    "INDIA VIX": ("NSE_INDEX", ("INDIA VIX", "INDIAVIX")),
    "NIFTY IT": ("NSE_INDEX", ("NIFTY IT",)),
    "NIFTY AUTO": ("NSE_INDEX", ("NIFTY AUTO",)),
    "NIFTY PHARMA": ("NSE_INDEX", ("NIFTY PHARMA",)),
    "NIFTY FMCG": ("NSE_INDEX", ("NIFTY FMCG",)),
    "NIFTY METAL": ("NSE_INDEX", ("NIFTY METAL",)),
    "NIFTY ENERGY": ("NSE_INDEX", ("NIFTY ENERGY",)),
    "NIFTY REALTY": ("NSE_INDEX", ("NIFTY REALTY",)),
    "NIFTY MEDIA": ("NSE_INDEX", ("NIFTY MEDIA",)),
    "NIFTY PSU BANK": ("NSE_INDEX", ("NIFTY PSU BANK",)),
    "NIFTY FIN SERVICE": ("NSE_INDEX", ("NIFTY FIN SERVICE", "NIFTY FINANCIAL SERVICES", "NIFTY FINSRV25 50")),
    "NIFTY INFRA": ("NSE_INDEX", ("NIFTY INFRA", "NIFTY INFRASTRUCTURE")),
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

    def search(self, query: str, limit: int = 12) -> list[dict]:
        """NSE equities whose symbol or company name matches, best matches first."""
        q = _norm(query)
        if not q:
            return []
        scored = []
        for r in self._records:
            if r.get("segment") != "NSE_EQ" or r.get("instrument_type") not in (None, "EQ"):
                continue
            sym, name = _norm(r.get("trading_symbol")), _norm(r.get("name"))
            if sym == q:
                rank = 0
            elif sym.startswith(q):
                rank = 1
            elif name.startswith(q):
                rank = 2
            elif q in name:
                rank = 3
            elif q in sym:
                rank = 4
            else:
                continue
            scored.append((rank, len(sym), sym, name))
        scored.sort()
        return [{"symbol": sym, "name": name.title(), "exchange": "NSE"} for _, _, sym, name in scored[:limit]]

    def find_index(self, label: str) -> dict | None:
        if label not in INDEX_CANDIDATES:
            return None
        segment, candidates = INDEX_CANDIDATES[label]
        pool = [r for r in self._records if r.get("segment") == segment]
        for cand in candidates:
            for field in ("name", "trading_symbol"):
                for r in pool:
                    if _norm(r.get(field)) == cand:
                        return r
        return None

    def currency_contracts(self, pair: str, kinds: tuple[str, ...]) -> list[dict]:
        """NSE currency derivatives of one pair (e.g. USDINR), FUT or CE/PE, with the expiry as YYYY-MM-DD."""
        out = []
        for r in self._records:
            if r.get("segment") == "NCD_FO" and r.get("name") == pair and r.get("instrument_type") in kinds and r.get("expiry"):
                out.append({**r, "expiry": _expiry_date(r["expiry"])})
        return out


def _expiry_date(value) -> str:
    """The master file gives expiries in epoch milliseconds (end of the expiry day, IST)."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, IST).date().isoformat()
    return str(value)[:10]
