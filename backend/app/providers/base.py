"""Provider-neutral market-data interface so other licensed providers can be added later."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.market_session import IST, SESSION_CLOSE
from app.models import Candle, Instrument, Quote, Timeframe

# Indices the dashboard asks for. Availability depends on the provider and entitlement.
DASHBOARD_INDICES = ("NIFTY 50", "BANK NIFTY", "SENSEX")


class ProviderError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class NotAuthenticated(ProviderError):
    def __init__(self, message: str = "Upstox is not connected. Log in via /api/auth/upstox/login.") -> None:
        super().__init__("provider_not_authenticated", message, 401)


class RateLimited(ProviderError):
    def __init__(self) -> None:
        super().__init__("provider_rate_limited", "Upstox rate limit reached; retry shortly.", 503)


@dataclass(frozen=True)
class TimeframeSpec:
    bar_minutes: int  # 0 for daily
    lookback_days: int
    cache_seconds: int


TIMEFRAMES: dict[str, TimeframeSpec] = {
    "1m": TimeframeSpec(bar_minutes=1, lookback_days=5, cache_seconds=15),
    "15m": TimeframeSpec(bar_minutes=15, lookback_days=45, cache_seconds=60),
    "1h": TimeframeSpec(bar_minutes=60, lookback_days=120, cache_seconds=300),
    "1d": TimeframeSpec(bar_minutes=0, lookback_days=900, cache_seconds=1800),
}


def drop_incomplete(candles: list[Candle], timeframe: Timeframe, now: datetime) -> list[Candle]:
    """Remove a still-forming last candle so indicators only use completed bars."""
    if not candles:
        return candles
    last = candles[-1]
    start = last.time.astimezone(IST)
    close_of_day = datetime.combine(start.date(), SESSION_CLOSE, tzinfo=IST)
    spec = TIMEFRAMES[timeframe]
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
    async def candles(self, instrument: Instrument, timeframe: Timeframe, include_partial: bool = False) -> list[Candle]:
        """Candles oldest first. The still-forming last candle is included only if include_partial."""

    async def close(self) -> None:
        return None
