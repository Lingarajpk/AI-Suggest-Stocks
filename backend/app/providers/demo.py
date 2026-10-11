"""DEMO MODE provider: deterministic synthetic prices for UI development.

Nothing produced here is market data. Every quote is tagged source="demo" and the
frontend shows a persistent DEMO DATA banner whenever this provider is active.
"""

import zlib
from datetime import date, datetime, time, timedelta

import numpy as np

from app.market_session import IST, SESSION_OPEN, is_session_open, now_ist
from app.models import Candle, Instrument, Quote, Timeframe
from app.providers.base import DASHBOARD_INDICES, TIMEFRAMES, MarketDataProvider, drop_incomplete
from app.sectors import SECTORS

HISTORY_START = date(2023, 1, 2)
HORIZON_DAYS = 365 * 8  # fixed generation horizon so past bars never change from one day to the next
BARS_PER_DAY = 25  # 09:15-15:30 in 15-minute bars
INDEX_BASE = {"NIFTY 50": 20000.0, "BANK NIFTY": 45000.0, "SENSEX": 66000.0}


def _seed(symbol: str) -> int:
    return zlib.crc32(symbol.encode())


class DemoProvider(MarketDataProvider):
    name = "demo"

    def __init__(self) -> None:
        self._series: dict[str, tuple[date, list[Candle]]] = {}
        self._five: dict[str, tuple[datetime, list[Candle]]] = {}

    async def resolve_equities(self, symbols: list[str]) -> tuple[list[Instrument], list[str]]:
        return [
            Instrument(
                symbol=s,
                name=f"{s} (demo)",
                exchange="NSE",
                segment="DEMO",
                instrument_key=f"DEMO|{s}",
                sector=SECTORS.get(s),
            )
            for s in symbols
        ], []

    async def resolve_indices(self) -> tuple[list[Instrument], list[str]]:
        return [
            Instrument(
                symbol=label, name=f"{label} (demo)", exchange="DEMO", segment="DEMO",
                instrument_key=f"DEMO|{label}", kind="index",
            )
            for label in DASHBOARD_INDICES
        ], []

    def _base_15m(self, inst: Instrument) -> list[Candle]:
        today = now_ist().date()
        cached = self._series.get(inst.instrument_key)
        if cached and cached[0] == today:
            return cached[1]

        seed = _seed(inst.symbol)
        # One independent stream per component, each drawn over a fixed horizon and then sliced,
        # so a bar's values never change as new days are added.
        rng = lambda k: np.random.default_rng([seed, k])  # noqa: E731
        all_days = [HISTORY_START + timedelta(days=i) for i in range(HORIZON_DAYS)]
        all_days = [d for d in all_days if d.weekday() < 5]
        days = [d for d in all_days if d <= today]
        total, n = len(all_days) * BARS_PER_DAY, len(days) * BARS_PER_DAY

        params = rng(0)
        price0 = INDEX_BASE.get(inst.symbol) or float(params.uniform(150, 4000))
        vol = 0.0022 if inst.kind == "index" else float(params.uniform(0.003, 0.0055))
        # Drift regimes of ~40 trading days so the demo shows a mix of trends.
        regimes = rng(1).normal(0, 0.0004, size=total // (40 * BARS_PER_DAY) + 1)
        drift = np.repeat(regimes, 40 * BARS_PER_DAY)[:n]
        rets = drift + rng(2).normal(0, vol, size=total)[:n]
        closes = price0 * np.exp(np.cumsum(rets))
        opens = np.concatenate([[price0], closes[:-1]])
        wick = np.abs(rng(3).normal(0, vol * 0.6, size=total)[:n])
        highs = np.maximum(opens, closes) * (1 + wick)
        lows = np.minimum(opens, closes) * (1 - wick)
        u_shape = np.tile(1.6 - np.sin(np.linspace(0, np.pi, BARS_PER_DAY)), len(days))
        volumes = rng(4).lognormal(11, 0.4, size=total)[:n] * u_shape * (0 if inst.kind == "index" else 1)

        candles = []
        i = 0
        for d in days:
            open_dt = datetime.combine(d, SESSION_OPEN, tzinfo=IST)
            for b in range(BARS_PER_DAY):
                candles.append(
                    Candle(
                        time=open_dt + timedelta(minutes=15 * b),
                        open=round(float(opens[i]), 2), high=round(float(highs[i]), 2),
                        low=round(float(lows[i]), 2), close=round(float(closes[i]), 2),
                        volume=round(float(volumes[i])),
                    )
                )
                i += 1
        self._series[inst.instrument_key] = (today, candles)
        return candles

    def _visible_15m(self, inst: Instrument) -> list[Candle]:
        now = now_ist()
        return [c for c in self._base_15m(inst) if c.time <= now]

    def _live_15m(self, inst: Instrument) -> list[Candle]:
        """Like _visible_15m, but the still-forming bar only contains the minutes that have happened."""
        now = now_ist()
        bars = self._visible_15m(inst)
        if bars and bars[-1].time + timedelta(minutes=15) > now:
            mins = [m for m in _split_minutes(bars[-1], _seed(inst.symbol)) if m.time <= now]
            if mins:
                bars[-1] = Candle(time=bars[-1].time, open=mins[0].open, high=max(m.high for m in mins),
                                  low=min(m.low for m in mins), close=mins[-1].close, volume=sum(m.volume for m in mins))
        return bars

    async def quotes(self, instruments: list[Instrument]) -> dict[str, Quote]:
        now = now_ist()
        out = {}
        for inst in instruments:
            bars = self._live_15m(inst)
            last = bars[-1]
            today = [c for c in bars if c.time.date() == last.time.date()]
            prev_close = next((c.close for c in reversed(bars) if c.time.date() < last.time.date()), None)
            price = last.close  # the 1-minute path up to now, never the bar's future close
            change = price - prev_close if prev_close else None
            out[inst.instrument_key] = Quote(
                instrument_key=inst.instrument_key, symbol=inst.symbol, last_price=price,
                change=change, change_pct=(change / prev_close * 100) if change is not None else None,
                open=today[0].open, high=max(c.high for c in today), low=min(c.low for c in today),
                prev_close=prev_close, volume=sum(c.volume for c in today) or None,
                last_trade_time=now if is_session_open(now) else last.time, fetched_at=now,
                freshness="demo", source="demo",
            )
        return out

    async def candles(self, instrument: Instrument, timeframe: Timeframe, include_partial: bool = False) -> list[Candle]:
        now = now_ist()
        bars = self._visible_15m(instrument) if timeframe == "1m" else self._live_15m(instrument)
        cutoff = now - timedelta(days=TIMEFRAMES[timeframe].lookback_days)
        bars = [c for c in bars if c.time >= cutoff]
        if timeframe == "1m":
            out = [m for c in bars for m in _split_minutes(c, _seed(instrument.symbol)) if m.time <= now]
        elif timeframe == "5m":
            out = self._five_minute(instrument, bars, now)
        elif timeframe == "15m":
            out = bars
        elif timeframe == "1h":
            out = _resample(bars, lambda c: c.time.replace(
                hour=9 + (c.time.hour * 60 + c.time.minute - 555) // 60, minute=15))
        else:
            out = _resample(bars, lambda c: datetime.combine(c.time.date(), time(0), tzinfo=IST))
        return out if include_partial else drop_incomplete(out, timeframe, now)

    def _five_minute(self, inst: Instrument, bars: list[Candle], now: datetime) -> list[Candle]:
        """5-minute bars built from the 1-minute path (cached per minute)."""
        stamp = now.replace(second=0, microsecond=0)
        hit = self._five.get(inst.instrument_key)
        if hit and hit[0] == stamp:
            return list(hit[1])
        seed = _seed(inst.symbol)
        minutes = [m for c in bars for m in _split_minutes(c, seed) if m.time <= now]
        out = _resample(minutes, _bucket_5m)
        self._five[inst.instrument_key] = (stamp, out)
        return list(out)


def _bucket_5m(c: Candle) -> datetime:
    return c.time.replace(minute=c.time.minute - c.time.minute % 5)


def _split_minutes(bar: Candle, seed: int) -> list[Candle]:
    """Deterministically split a demo 15-minute bar into 15 one-minute bars ending at its close."""
    rng = np.random.default_rng(seed ^ int(bar.time.timestamp()))
    path = bar.open + (bar.close - bar.open) * np.linspace(0, 1, 16)
    path[1:-1] += rng.normal(0, (bar.high - bar.low) * 0.15 + 1e-9, 14)
    path = np.clip(path, bar.low, bar.high)
    vols = rng.dirichlet(np.ones(15)) * bar.volume
    out = []
    for i in range(15):
        o, c = float(path[i]), float(path[i + 1])
        out.append(Candle(
            time=bar.time + timedelta(minutes=i), open=round(o, 2), close=round(c, 2),
            high=round(max(o, c), 2), low=round(min(o, c), 2), volume=round(float(vols[i])),
        ))
    return out


def _resample(bars: list[Candle], bucket) -> list[Candle]:
    groups: dict[datetime, list[Candle]] = {}
    for c in bars:
        groups.setdefault(bucket(c), []).append(c)
    return [
        Candle(
            time=t, open=g[0].open, high=max(c.high for c in g), low=min(c.low for c in g),
            close=g[-1].close, volume=sum(c.volume for c in g),
        )
        for t, g in groups.items()
    ]
