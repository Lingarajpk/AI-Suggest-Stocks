"""Orchestrates provider data, indicators and signals for the API layer."""

import asyncio
import logging
from datetime import date

from app.analysis import indicators as ta
from app.analysis.history import signal_history
from app.analysis.signals import evaluate
from app.config import Settings
from app.market_session import now_ist
from app.models import Instrument, Quote, Timeframe
from app.providers.base import MarketDataProvider, ProviderError

log = logging.getLogger(__name__)

CHART_SERIES = ("ema20", "ema50", "ema200", "bb_upper", "bb_lower", "vwap")
SNAPSHOT_FIELDS = (
    "ema9", "ema20", "ema50", "ema200", "rsi14", "macd", "macd_signal", "macd_hist", "atr14",
    "bb_upper", "bb_mid", "bb_lower", "adx14", "plus_di", "minus_di", "vwap", "rel_volume", "roc10",
)


class MarketService:
    def __init__(self, provider: MarketDataProvider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings
        self._universe: tuple[date, list[Instrument], list[str]] | None = None
        self._indices: tuple[date, list[Instrument], list[str]] | None = None
        self._quote_lock = asyncio.Lock()
        self._last_quotes: dict[str, Quote] = {}

    async def universe(self) -> tuple[list[Instrument], list[str]]:
        today = now_ist().date()
        if not self._universe or self._universe[0] != today:
            resolved, missing = await self.provider.resolve_equities(self.settings.universe_symbols)
            self._universe = (today, resolved, missing)
        return self._universe[1], self._universe[2]

    async def indices(self) -> tuple[list[Instrument], list[str]]:
        today = now_ist().date()
        if not self._indices or self._indices[0] != today:
            resolved, missing = await self.provider.resolve_indices()
            self._indices = (today, resolved, missing)
        return self._indices[1], self._indices[2]

    async def instrument(self, symbol: str) -> Instrument | None:
        instruments, _ = await self.universe()
        return next((i for i in instruments if i.symbol == symbol.upper()), None)

    async def refresh_quotes(self) -> dict[str, Quote]:
        """One upstream call for universe + indices, shared by REST and WebSocket clients."""
        async with self._quote_lock:
            fresh_enough = self._last_quotes and all(
                (now_ist() - q.fetched_at).total_seconds() < 2 for q in self._last_quotes.values()
            )
            if fresh_enough:
                return self._last_quotes
            equities, _ = await self.universe()
            idx, _ = await self.indices()
            self._last_quotes = await self.provider.quotes(equities + idx)
            return self._last_quotes

    def cached_quote(self, key: str) -> Quote | None:
        return self._last_quotes.get(key)

    async def analyse(self, inst: Instrument, timeframe: Timeframe, with_series: bool) -> dict:
        candles = await self.provider.candles(inst, timeframe)
        df = ta.to_frame(candles)
        quote = self.cached_quote(inst.instrument_key)
        freshness = quote.freshness if quote else None
        if df.empty:
            sig = evaluate(df, df, timeframe, now_ist(), freshness)
            return {"signal": sig, "snapshot": {}, "candles": [], "series": {}}
        ind = ta.compute(df, intraday=timeframe != "1d")
        sig = evaluate(df, ind, timeframe, now_ist(), freshness)
        last = ind.iloc[-1]
        result = {
            "signal": sig,
            "snapshot": {k: ta.clean(last.get(k)) for k in SNAPSHOT_FIELDS},
            "last_close": float(df["close"].iloc[-1]),
            "history": signal_history(df, ind, timeframe, with_markers=with_series),
        }
        if with_series:
            result["candles"] = [c.model_dump(mode="json") for c in candles]
            result["series"] = {
                name: [
                    {"time": t.isoformat(), "value": ta.clean(val)}
                    for t, val in ind[name].items()
                    if ta.clean(val) is not None
                ]
                for name in CHART_SERIES
            }
            result["oscillators"] = {
                name: [{"time": t.isoformat(), "value": ta.clean(val)} for t, val in ind[name].items() if ta.clean(val) is not None]
                for name in ("rsi14", "macd", "macd_signal", "macd_hist")
            }
        return result

    async def scan(self, timeframe: Timeframe) -> dict:
        instruments, missing = await self.universe()
        quotes_error = None
        try:
            await self.refresh_quotes()
        except ProviderError as exc:
            if exc.http_status == 401:
                raise
            quotes_error = exc.message

        sem = asyncio.Semaphore(4)

        async def one(inst: Instrument) -> dict:
            row = {"instrument": inst.model_dump(), "quote": None, "analysis": None, "error": None}
            q = self.cached_quote(inst.instrument_key)
            row["quote"] = q.model_dump(mode="json") if q else None
            try:
                async with sem:
                    row["analysis"] = await self.analyse(inst, timeframe, with_series=False)
            except ProviderError as exc:
                if exc.http_status == 401:
                    raise
                row["error"] = exc.message
            except Exception as exc:  # keep one bad instrument from failing the scan
                log.exception("analysis failed for %s", inst.symbol)
                row["error"] = f"Analysis failed: {exc.__class__.__name__}"
            return row

        rows = await asyncio.gather(*(one(i) for i in instruments))

        counts = {"Strong Bullish": 0, "Bullish": 0, "Neutral": 0, "Bearish": 0, "Strong Bearish": 0, "no_trade": 0, "unavailable": 0}
        sectors: dict[str, list[float]] = {}
        for r in rows:
            a = r["analysis"]
            if not a or a["signal"]["score"] is None:
                counts["unavailable"] += 1
                continue
            counts[a["signal"]["signal"]] += 1
            if a["signal"]["status"] == "no_trade":
                counts["no_trade"] += 1
            sec = r["instrument"].get("sector") or "Other"
            sectors.setdefault(sec, []).append(a["signal"]["score"])

        return {
            "timeframe": timeframe,
            "generated_at": now_ist().isoformat(),
            "source": self.provider.name,
            "scanned": len(instruments),
            "unresolved_symbols": missing,
            "quotes_error": quotes_error,
            "counts": counts,
            "sector_strength": sorted(
                ({"sector": s, "avg_score": round(sum(v) / len(v), 1), "count": len(v)} for s, v in sectors.items()),
                key=lambda x: -x["avg_score"],
            ),
            "rows": rows,
        }

    async def today(self, limit: int = 5) -> dict:
        """Rank today's setups: daily trend signal confirmed by 15-minute momentum.

        A stock is listed only when the daily signal passes all NO TRADE checks and the
        intraday score does not contradict it. This is a ranking of current technical
        conditions, not a forecast that the price will rise or fall today.
        """
        daily, intraday = await self.scan("1d"), await self.scan("15m")
        intra = {r["instrument"]["symbol"]: r.get("analysis") for r in intraday["rows"]}
        upside, downside = [], []
        for r in daily["rows"]:
            a = r["analysis"]
            if not a or a["signal"]["status"] != "ok":
                continue
            d_score = a["signal"]["score"]
            ia = intra.get(r["instrument"]["symbol"])
            i_score = ia["signal"]["score"] if ia and ia["signal"]["score"] is not None else None
            if i_score is not None and (i_score > 0) != (d_score > 0):
                continue  # intraday momentum disagrees with the daily trend
            hist = a["history"]
            side = "buy" if d_score > 0 else "sell"
            base_up = hist["base_rate_up_pct"]
            item = {
                "instrument": r["instrument"],
                "quote": r["quote"],
                "last_close": a.get("last_close"),
                "daily_signal": a["signal"]["signal"],
                "daily_score": d_score,
                "intraday_score": i_score,
                "combined_score": round(0.6 * d_score + 0.4 * i_score, 1) if i_score is not None else d_score,
                "scenario": a["signal"]["scenario"],
                "risks": a["signal"]["risks"],
                "track_record": {
                    **hist[side],
                    "horizon_label": hist["horizon_label"],
                    "base_rate_pct": base_up if side == "buy" or base_up is None else round(100 - base_up, 1),
                },
            }
            (upside if d_score > 0 else downside).append(item)
        upside.sort(key=lambda x: -x["combined_score"])
        downside.sort(key=lambda x: x["combined_score"])
        return {
            "generated_at": now_ist().isoformat(),
            "source": self.provider.name,
            "session": daily.get("session"),
            "considered": daily["scanned"],
            "upside": upside[:limit],
            "downside": downside[:limit],
            "method": "Daily rule-based signal (60%) + latest 15-minute score (40%); listed only when both agree and no NO TRADE rule applies.",
        }
