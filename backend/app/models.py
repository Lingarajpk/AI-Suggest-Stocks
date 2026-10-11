from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Timeframe = Literal["1m", "5m", "15m", "1h", "1d"]
DataSource = Literal["upstox", "demo"]
# polled: fetched from the REST quote API during market hours and recent enough
# stale: market open but last trade older than STALE_AFTER_SECONDS
# market_closed: last available price outside the regular session
Freshness = Literal["polled", "stale", "market_closed", "unavailable", "demo"]


class Instrument(BaseModel):
    symbol: str
    name: str
    exchange: str
    segment: str
    instrument_key: str
    isin: str | None = None
    sector: str | None = None
    kind: Literal["equity", "index"] = "equity"


class Quote(BaseModel):
    instrument_key: str
    symbol: str
    last_price: float
    change: float | None
    change_pct: float | None
    open: float | None
    high: float | None
    low: float | None
    prev_close: float | None
    volume: float | None
    last_trade_time: datetime | None
    fetched_at: datetime
    freshness: Freshness
    source: DataSource


class Candle(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float | None = None
