"""Upstox Developer API provider.

Endpoints used (verify against https://upstox.com/developer/api-documentation/):
  GET  /v2/login/authorization/dialog                     OAuth login page
  POST /v2/login/authorization/token                      exchange auth code for access token
  GET  /v2/market-quote/quotes?instrument_key=...         full market quotes (<=500 keys)
  GET  /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
  GET  /v3/historical-candle/intraday/{key}/{unit}/{interval}
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote as urlquote
from urllib.parse import urlencode

import httpx

from app.cache import Cache
from app.config import Settings
from app.market_session import IST, SESSION_OPEN, is_session_open, is_trading_weekday, now_ist
from app.models import Candle, Instrument, Quote, Timeframe
from app.providers.base import (
    DASHBOARD_INDICES,
    TIMEFRAMES,
    MarketDataProvider,
    NotAuthenticated,
    ProviderError,
    RateLimited,
    drop_incomplete,
)
from app.providers.upstox_instruments import InstrumentMaster
from app.ratelimit import RateLimiter
from app.sectors import SECTORS
from app.token_store import TokenStore

log = logging.getLogger(__name__)

# timeframe -> (unit, interval, max days per request)
UPSTOX_INTERVALS: dict[str, tuple[str, int, int]] = {
    "1m": ("minutes", 1, 28),
    "15m": ("minutes", 15, 28),
    "1h": ("hours", 1, 85),
    "1d": ("days", 1, 3000),
}
MAX_QUOTE_KEYS = 500


def parse_quote(payload: dict, fetched_at: datetime, stale_after: int, source_symbol: str) -> Quote:
    last_price = float(payload["last_price"])
    net_change = payload.get("net_change")
    change = float(net_change) if net_change is not None else None
    prev_close = last_price - change if change is not None else None
    change_pct = (change / prev_close * 100) if change is not None and prev_close else None
    ohlc = payload.get("ohlc") or {}

    ltt_raw = payload.get("last_trade_time")
    last_trade_time = None
    if ltt_raw not in (None, "", "0", 0):
        last_trade_time = datetime.fromtimestamp(int(ltt_raw) / 1000, tz=timezone.utc).astimezone(IST)

    if not is_session_open(fetched_at):
        freshness = "market_closed"
    elif last_trade_time is None or (fetched_at - last_trade_time).total_seconds() > stale_after:
        freshness = "stale"
    else:
        freshness = "polled"

    return Quote(
        instrument_key=payload["instrument_token"],
        symbol=source_symbol,
        last_price=last_price,
        change=change,
        change_pct=change_pct,
        open=ohlc.get("open"),
        high=ohlc.get("high"),
        low=ohlc.get("low"),
        prev_close=prev_close,
        volume=payload.get("volume"),
        last_trade_time=last_trade_time,
        fetched_at=fetched_at,
        freshness=freshness,
        source="upstox",
    )


def parse_candles(rows: list[list]) -> list[Candle]:
    """Upstox candle rows: [timestamp, open, high, low, close, volume, open_interest]."""
    out = []
    for row in rows:
        out.append(
            Candle(
                time=datetime.fromisoformat(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5] or 0),
                oi=float(row[6]) if len(row) > 6 and row[6] is not None else None,
            )
        )
    return out


def merge_candles(*batches: list[Candle]) -> list[Candle]:
    by_time: dict[datetime, Candle] = {}
    for batch in batches:
        for c in batch:
            by_time[c.time] = c
    return [by_time[t] for t in sorted(by_time)]


class UpstoxAuth:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

    def login_url(self, state: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self._settings.upstox_api_key,
            "redirect_uri": self._settings.upstox_redirect_uri,
            "state": state,
        }
        return f"{self._settings.upstox_base_url}/v2/login/authorization/dialog?{urlencode(params)}"

    async def exchange_code(self, code: str) -> str:
        resp = await self._http.post(
            f"{self._settings.upstox_base_url}/v2/login/authorization/token",
            data={
                "code": code,
                "client_id": self._settings.upstox_api_key,
                "client_secret": self._settings.upstox_api_secret.get_secret_value(),
                "redirect_uri": self._settings.upstox_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        body = resp.json() if resp.content else {}
        token = body.get("access_token")
        if resp.status_code != 200 or not token:
            errors = body.get("errors") or [{}]
            raise ProviderError("upstox_token_exchange_failed", errors[0].get("message", f"HTTP {resp.status_code}"))
        return token


class UpstoxProvider(MarketDataProvider):
    name = "upstox"

    def __init__(self, settings: Settings, http: httpx.AsyncClient, tokens: TokenStore, cache: Cache) -> None:
        self._settings = settings
        self._http = http
        self._tokens = tokens
        self._cache = cache
        self._limiter = RateLimiter(
            settings.upstox_max_requests_per_second, settings.upstox_max_requests_per_minute
        )
        self.master = InstrumentMaster(settings.upstox_instruments_url, settings.data_dir / "instruments", http)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        token = self._tokens.get()
        if not token:
            raise NotAuthenticated()
        await self._limiter.acquire()
        try:
            resp = await self._http.get(
                f"{self._settings.upstox_base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError("provider_unreachable", f"Upstox request failed: {exc.__class__.__name__}") from exc
        if resp.status_code == 401:
            self._tokens.invalidate("rejected_by_upstox")
            raise NotAuthenticated("Upstox rejected the access token (expired or revoked). Log in again.")
        if resp.status_code == 429:
            raise RateLimited()
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError("provider_bad_response", f"Non-JSON response (HTTP {resp.status_code})") from exc
        if resp.status_code >= 400 or body.get("status") != "success":
            err = (body.get("errors") or [{}])[0]
            raise ProviderError(
                err.get("errorCode") or err.get("error_code") or "provider_error",
                err.get("message") or f"Upstox HTTP {resp.status_code}",
                502 if resp.status_code >= 500 else 400,
            )
        return body.get("data") or {}

    # ---- instruments -------------------------------------------------
    async def resolve_equities(self, symbols: list[str]) -> tuple[list[Instrument], list[str]]:
        await self.master.ensure_loaded()
        resolved, missing = [], []
        for sym in symbols:
            r = self.master.find_equity(sym)
            if r is None:
                missing.append(sym)
                continue
            resolved.append(
                Instrument(
                    symbol=sym,
                    name=r.get("name") or sym,
                    exchange=r.get("exchange") or "NSE",
                    segment=r["segment"],
                    instrument_key=r["instrument_key"],
                    isin=r.get("isin"),
                    sector=SECTORS.get(sym),
                )
            )
        return resolved, missing

    async def resolve_indices(self) -> tuple[list[Instrument], list[str]]:
        await self.master.ensure_loaded()
        resolved, missing = [], []
        for label in DASHBOARD_INDICES:
            r = self.master.find_index(label)
            if r is None:
                missing.append(label)
                continue
            resolved.append(
                Instrument(
                    symbol=label,
                    name=r.get("name") or label,
                    exchange=r.get("exchange") or r["segment"].split("_")[0],
                    segment=r["segment"],
                    instrument_key=r["instrument_key"],
                    kind="index",
                )
            )
        return resolved, missing

    # ---- quotes ------------------------------------------------------
    async def quotes(self, instruments: list[Instrument]) -> dict[str, Quote]:
        by_key = {i.instrument_key: i for i in instruments}
        keys = list(by_key)
        out: dict[str, Quote] = {}
        for start in range(0, len(keys), MAX_QUOTE_KEYS):
            chunk = keys[start : start + MAX_QUOTE_KEYS]
            data = await self._get("/v2/market-quote/quotes", {"instrument_key": ",".join(chunk)})
            fetched_at = now_ist()
            # Response is keyed by "SEGMENT:SYMBOL"; the instrument key is in instrument_token.
            for payload in data.values():
                key = payload.get("instrument_token")
                if key in by_key and payload.get("last_price") is not None:
                    out[key] = parse_quote(payload, fetched_at, self._settings.stale_after_seconds, by_key[key].symbol)
        return out

    # ---- candles -----------------------------------------------------
    async def candles(self, instrument: Instrument, timeframe: Timeframe, include_partial: bool = False) -> list[Candle]:
        """History (cached for the day) merged with today's intraday candles (cached briefly)."""
        spec = TIMEFRAMES[timeframe]
        now = now_ist()
        unit, interval, max_span = UPSTOX_INTERVALS[timeframe]
        key = urlquote(instrument.instrument_key, safe="")

        hist_key = f"candles:upstox:hist:{instrument.instrument_key}:{timeframe}:{now.date()}"
        cached = await self._cache.get(hist_key)
        if cached is not None:
            history = [Candle.model_validate(c) for c in cached]
        else:
            end = now.date()
            start = end - timedelta(days=spec.lookback_days)
            requests = []
            chunk_end = end
            while chunk_end >= start:
                chunk_start = max(start, chunk_end - timedelta(days=max_span - 1))
                requests.append(f"/v3/historical-candle/{key}/{unit}/{interval}/{chunk_end}/{chunk_start}")
                chunk_end = chunk_start - timedelta(days=1)
            history = merge_candles(*[parse_candles((await self._get(p)).get("candles", [])) for p in requests])
            await self._cache.set(hist_key, [c.model_dump(mode="json") for c in history], 6 * 3600)

        today: list[Candle] = []
        # Today's bars come from the intraday endpoint, during and after the session.
        if spec.bar_minutes and is_trading_weekday(now.date()) and now.time() >= SESSION_OPEN:
            intra_key = f"candles:upstox:intra:{instrument.instrument_key}:{timeframe}"
            cached = await self._cache.get(intra_key)
            if cached is not None:
                today = [Candle.model_validate(c) for c in cached]
            else:
                data = await self._get(f"/v3/historical-candle/intraday/{key}/{unit}/{interval}")
                today = parse_candles(data.get("candles", []))
                ttl = spec.cache_seconds if is_session_open(now) else 900
                await self._cache.set(intra_key, [c.model_dump(mode="json") for c in today], ttl)

        merged = merge_candles(history, today)
        return merged if include_partial else drop_incomplete(merged, timeframe, now)
