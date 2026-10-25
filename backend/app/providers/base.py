"""Provider-neutral market-data interface so other licensed providers can be added later."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.market_session import EQUITY, IST, MarketHours
from app.models import Candle, Instrument, Quote, Timeframe

# Indices the dashboard asks for. Availability depends on the provider and entitlement.
DASHBOARD_INDICES = ("NIFTY 50", "BANK NIFTY", "SENSEX")
# Context-only indices for the market board (not on the dashboard): volatility and the NSE sector indices.
VIX_INDEX = "INDIA VIX"
SECTOR_INDICES = ("BANK NIFTY", "NIFTY IT", "NIFTY AUTO", "NIFTY PHARMA", "NIFTY FMCG", "NIFTY METAL", "NIFTY ENERGY",
                  "NIFTY REALTY", "NIFTY MEDIA", "NIFTY PSU BANK", "NIFTY FIN SERVICE", "NIFTY INFRA")


class ProviderError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class NotAuthenticated(ProviderError):
    def __init__(self, message: str = "Live data is offline: Upstox is not connected. The site owner needs to reconnect it.") -> None:
        super().__init__("provider_not_authenticated", message, 401)


class RateLimited(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_rate_limited", "Upstox rate limit reached; retry shortly.", 503)


@dataclass(frozen=True)
class TimeframeSpec:
    bar_minutes: int  # 0 for daily and weekly
    lookback_days: int
    cache_seconds: int
    weekly: bool = False


TIMEFRAMES: dict[str, TimeframeSpec] = {
    "1m": TimeframeSpec(bar_minutes=1, lookback_days=5, cache_seconds=15),
    "3m": TimeframeSpec(bar_minutes=3, lookback_days=10, cache_seconds=20),
    "5m": TimeframeSpec(bar_minutes=5, lookback_days=20, cache_seconds=30),
    "15m": TimeframeSpec(bar_minutes=15, lookback_days=45, cache_seconds=60),
    "30m": TimeframeSpec(bar_minutes=30, lookback_days=90, cache_seconds=120),
    "1h": TimeframeSpec(bar_minutes=60, lookback_days=120, cache_seconds=300),
    "1d": TimeframeSpec(bar_minutes=0, lookback_days=900, cache_seconds=1800),
    # Weekly candles are built from daily ones, so the current week is always up to date.
    "1w": TimeframeSpec(bar_minutes=0, lookback_days=900, cache_seconds=1800, weekly=True),
}


def parse_holidays(rows: list[dict]) -> dict[str, tuple[dict[date, str], dict[date, tuple[time, time, str]]]]:
    """Upstox /v2/market/holidays rows -> {exchange: (closed {date: name}, special sessions {date: (open, close, name)})}.
    Settlement holidays do not stop trading and are skipped. A trading day outside Monday-Friday (a special
    Saturday session) or a SPECIAL_TIMING day (Muhurat trading) replaces the normal hours with its own."""
    out: dict[str, tuple[dict, dict]] = {}
    for r in rows or []:
        kind = r.get("holiday_type")
        try:
            d = date.fromisoformat(str(r.get("date"))[:10])
        except ValueError:
            continue
        if kind not in ("TRADING_HOLIDAY", "SPECIAL_TIMING"):
            continue
        name = r.get("description") or "Exchange holiday"
        for ex in r.get("closed_exchanges") or []:
            out.setdefault(ex, ({}, {}))[0][d] = name
        if kind == "SPECIAL_TIMING" or d.weekday() >= 5:
            for o in r.get("open_exchanges") or []:
                try:
                    start = datetime.fromtimestamp(int(o["start_time"]) / 1000, IST).time()
                    end = datetime.fromtimestamp(int(o["end_time"]) / 1000, IST).time()
                except (KeyError, TypeError, ValueError):
                    continue
                out.setdefault(o.get("exchange"), ({}, {}))[1][d] = (start, end, name)
    return out


def front_month(futures: list[dict], today: date, roll_days: int = 2) -> dict | None:
    """The monthly future to follow: the nearest one that is more than `roll_days` days from expiry
    (so the pair switches to the next month two days before the current one expires).
    `futures` rows need "expiry" (YYYY-MM-DD) and "weekly"."""
    monthly = sorted((f for f in futures if not f.get("weekly")), key=lambda f: f["expiry"])
    return next((f for f in monthly if (date.fromisoformat(f["expiry"]) - today).days > roll_days), None)


def resample(candles: list[Candle], bucket) -> list[Candle]:
    """Merge consecutive candles that share bucket(candle) (the new bar's start time)."""
    groups: dict[datetime, list[Candle]] = {}
    for c in candles:
        groups.setdefault(bucket(c), []).append(c)
    return [
        Candle(time=t, open=g[0].open, high=max(c.high for c in g), low=min(c.low for c in g),
               close=g[-1].close, volume=sum(c.volume for c in g))
        for t, g in groups.items()
    ]


def week_start(c: Candle) -> datetime:
    """Monday 00:00 IST of the candle's week."""
    d = c.time.astimezone(IST).date()
    return datetime.combine(d - timedelta(days=d.weekday()), time(0), tzinfo=IST)


def to_weekly(daily: list[Candle]) -> list[Candle]:
    """Weekly candles from daily ones; the oldest week is dropped as the history may start mid-week."""
    weeks = resample(daily, week_start)
    return weeks[1:] if len(weeks) > 1 else weeks


def session_bucket(minutes: int, hours: MarketHours = EQUITY):
    """Bucket intraday candles into `minutes`-long bars aligned to the session open (09:15 for equities)."""
    def bucket(c: Candle) -> datetime:
        t = c.time.astimezone(IST)
        since_open = t.hour * 60 + t.minute - hours.open_minutes
        start = hours.open_minutes + (since_open // minutes) * minutes
        return t.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
    return bucket


def live_bar_start(now: datetime, minutes: int, hours: MarketHours = EQUITY) -> datetime:
    """Start of the intraday bar forming at `now` (bars are aligned to the session open)."""
    open_at = now.replace(hour=hours.open.hour, minute=hours.open.minute, second=0, microsecond=0)
    if now < open_at:
        return open_at
    return open_at + timedelta(minutes=int((now - open_at).total_seconds() // 60 // minutes * minutes))


def drop_incomplete(candles: list[Candle], timeframe: Timeframe, now: datetime, hours: MarketHours = EQUITY) -> list[Candle]:
    """Remove a still-forming last candle so indicators only use completed bars."""
    if not candles:
        return candles
    last = candles[-1]
    start = last.time.astimezone(IST)
    close_of_day = datetime.combine(start.date(), hours.close, tzinfo=IST)
    spec = TIMEFRAMES[timeframe]
    if spec.weekly:  # a week completes at Friday's close
        end = close_of_day + timedelta(days=4 - start.weekday())
    else:
        end = close_of_day if spec.bar_minutes == 0 else min(start + timedelta(minutes=spec.bar_minutes), close_of_day)
    return candles if end <= now else candles[:-1]


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    async def resolve_equities(self, symbols: list[str]) -> tuple[list[Instrument], list[str]]:
        """Return (resolved instruments, unresolved symbols)."""

    @abstractmethod
    async def resolve_indices(self) -> tuple[list[Instrument], list[str]]:
        """Return (resolved dashboard indices, unavailable index names)."""

    @abstractmethod
    async def quotes(self, instruments: list[Instrument]) -> dict[str, Quote]:
        """Quotes keyed by instrument_key. Missing keys mean no quote was returned."""

    @abstractmethod
    async def candles(self, instrument: Instrument, timeframe: Timeframe, include_partial: bool = False,
                      lookback_days: int | None = None) -> list[Candle]:
        """Candles oldest first. The still-forming last candle is included only if include_partial.
        `lookback_days` overrides the timeframe's usual history (the Time Machine wants ~60 sessions of 5-minute bars)."""

    async def search(self, query: str, limit: int = 12) -> list[dict]:
        """Equities matching a symbol or company-name query: [{symbol, name, exchange}]."""
        return []

    async def resolve_index(self, label: str) -> Instrument | None:
        """One index by label (dashboard, VIX_INDEX or a SECTOR_INDICES name); None if unavailable."""
        found, _ = await self.resolve_indices()
        return next((i for i in found if i.symbol == label), None)

    async def option_contracts(self, instrument: Instrument) -> list[dict]:
        """Option contracts on an index: [{expiry: "YYYY-MM-DD", lot_size}] (one per expiry is enough)."""
        raise ProviderError("not_supported", "Option chains are not available from this data source", 404)

    async def option_chain(self, instrument: Instrument, expiry: str) -> list[dict]:
        """Raw option chain rows in Upstox's /v2/option/chain format."""
        raise ProviderError("not_supported", "Option chains are not available from this data source", 404)

    async def resolve_currencies(self, pairs: list[str]) -> tuple[list[Instrument], list[str]]:
        """The front-month future of each currency pair (USDINR, EURINR, GBPINR, JPYINR)."""
        raise ProviderError("not_supported", "Currency futures are not available from this data source", 404)

    async def currency_option_chain(self, pair: str, expiry: str | None, around: float) -> tuple[list[str], list[dict]]:
        """(upcoming option expiries, raw chain rows near `around` for `expiry` or the nearest one) in the
        /v2/option/chain format, without greeks (the forex service computes them)."""
        raise ProviderError("not_supported", "Currency options are not available from this data source", 404)

    async def market_holidays(self) -> list[dict]:
        """This year's exchange holidays in Upstox's /v2/market/holidays format (empty if unknown)."""
        return []

    async def close(self) -> None:
        return None
