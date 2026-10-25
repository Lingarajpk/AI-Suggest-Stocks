"""Public NSE files the market board needs that Upstox doesn't provide.

  fo_ban_list   Stocks in the F&O ban period (open interest above 95% of the market-wide position limit):
                only trades that reduce positions are allowed in their futures and options.

NSE serves these from its archive site; it sometimes refuses non-browser clients, so every failure
degrades to "unknown" rather than an error. Fetched at most once an hour.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

log = logging.getLogger(__name__)

BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept": "text/csv,*/*", "Referer": "https://www.nseindia.com/"}
TTL = 3600
_cache: tuple[float, dict] | None = None


def parse_ban(text: str) -> dict:
    """'Securities in Ban For Trade Date 24-OCT-2026:' then lines like '1,IDEA'."""
    date = None
    m = re.search(r"Trade Date\s+([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4})", text)
    if m:
        date = m.group(1).upper()
    symbols = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and re.fullmatch(r"[A-Z0-9&\-]+", parts[1] or ""):
            symbols.append(parts[1])
    return {"trade_date": date, "symbols": symbols}


async def fo_ban_list() -> dict:
    """{"status": "ok" | "unavailable", "trade_date", "symbols"}"""
    global _cache
    if _cache and time.monotonic() - _cache[0] < TTL:
        return _cache[1]
    try:
        async with httpx.AsyncClient(timeout=8, headers=HEADERS, follow_redirects=True) as http:
            resp = await http.get(BAN_URL)
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}")
        out = {"status": "ok", **parse_ban(resp.text)}
    except Exception as exc:
        log.info("F&O ban list unavailable: %s", exc)
        out = {"status": "unavailable", "trade_date": None, "symbols": [], "message": "NSE's ban list could not be fetched just now."}
    _cache = (time.monotonic(), out)
    return out
