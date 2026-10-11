"""Orchestrates provider data, indicators and signals for the API layer."""

import asyncio
import logging
from datetime import date, datetime, timedelta

from app.analysis import indicators as ta
from app.analysis import patterns as pt
from app.analysis import probability as pb
from app.analysis import strategies as sx
from app.analysis.history import HORIZON
from app.analysis.history import signal_history
from app.analysis.signals import evaluate
from app.config import Settings
from app.market_session import now_ist, session_state
from app.models import Instrument, Quote, Timeframe
from app.providers.base import MarketDataProvider, ProviderError, drop_incomplete

log = logging.getLogger(__name__)

# Intraday: the 5-minute chart times the entry, the 15-minute chart must agree on direction.
ENTRY_TF: Timeframe = "5m"
TREND_TF: Timeframe = "15m"
ENTRY_BUY, ENTRY_SELL = 55.0, 45.0
TREND_BUY, TREND_SELL = 52.0, 48.0
RULE_LEVEL = 20
CHART_SESSIONS = 2
INDEX_ALIASES = {"NIFTY": "NIFTY50", "NIFTYBANK": "BANKNIFTY"}


def _norm(symbol: str) -> str:
    return "".join(ch for ch in symbol.upper() if ch.isalnum())


def _lean(outlook: dict | None, score: float | None, buy_at: float, sell_at: float) -> tuple[int, str, float | None]:
    """+1 / -1 / 0 for one timeframe. Uses the combined model when it has shown skill on unseen
    data, otherwise falls back to the rule-based score (+/-20)."""
    if outlook and outlook.get("status") == "ok" and (outlook.get("test") or {}).get("skill_pct", 0) > 0:
        up = float(outlook["up_pct"])
        return (1 if up >= buy_at else -1 if up <= sell_at else 0), "model", up
    if score is None:
        return 0, "none", None
    return (1 if score >= RULE_LEVEL else -1 if score <= -RULE_LEVEL else 0), "rules", None


def _data_issues(sig: dict) -> list[str]:
    return [r for r in sig["no_trade_reasons"] if r.startswith("Only ") or "stale" in r or "quote is" in r]

CHART_SERIES = ("ema20", "ema50", "ema200", "bb_upper", "bb_lower", "vwap", "vwap_u1", "vwap_l1", "vwap_u2", "vwap_l2")
SNAPSHOT_FIELDS = (
    "ema9", "ema20", "ema50", "ema200", "rsi14", "macd", "macd_signal", "macd_hist", "atr14",
    "bb_upper", "bb_mid", "bb_lower", "adx14", "plus_di", "minus_di", "vwap", "rel_volume", "roc10",
    "sma20", "st_line", "st_dir",
)


class MarketService:
    def __init__(self, provider: MarketDataProvider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings
        self._universe: tuple[date, list[Instrument], list[str]] | None = None
        self._indices: tuple[date, list[Instrument], list[str]] | None = None
        self._quote_lock = asyncio.Lock()
        self._last_quotes: dict[str, Quote] = {}
        # (instrument_key, timeframe) -> (last candle time, bar count, detected patterns)
        self._patterns: dict[tuple[str, str], tuple[str, int, list[dict]]] = {}
        # timeframe -> (computed at, pooled stats payload)
        self._pattern_stats: dict[str, tuple[datetime, dict]] = {}
        self._stats_lock = asyncio.Lock()
        # timeframe -> (computed at, trained combined-outlook model or None)
        self._models: dict[str, tuple[datetime, dict | None]] = {}
        self._model_lock = asyncio.Lock()
        self._outlooks: dict[tuple[str, str], tuple[str, dict | None, dict]] = {}
        # (instrument_key, timeframe) -> (last candle time, bar count, strategies result)
        self._strategies: dict[tuple[str, str], tuple[str, int, dict]] = {}
        self._strategy_stats: dict[str, tuple[datetime, dict]] = {}
        self._strategy_lock = asyncio.Lock()
        # symbol -> (intraday verdict, candle it was first seen on, seen since startup only = not a fresh change)
        self._intraday_state: dict[str, tuple[str, str, bool]] = {}
        # Work that only changes when a candle completes: (instrument_key, timeframe, part) -> (stamp, value)
        self._memo: dict[tuple, tuple[tuple, object]] = {}

    def _memoized(self, key: tuple, stamp: tuple, fn):
        hit = self._memo.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
        value = fn()
        self._memo[key] = (stamp, value)
        return value

    def _frame(self, inst: Instrument, timeframe: Timeframe, candles: list):
        """(candles frame, indicators) for completed candles, rebuilt only when a candle completes."""
        if not candles:
            df = ta.to_frame(candles)
            return df, df, ()
        last = candles[-1]
        stamp = (last.time.isoformat(), len(candles), last.close, last.volume)

        def build():
            df = ta.to_frame(candles)
            return df, ta.compute(df, intraday=timeframe != "1d")

        df, ind = self._memoized((inst.instrument_key, timeframe, "frame"), stamp, build)
        return df, ind, stamp

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
        """A universe stock or a dashboard index ("NIFTY 50", "NIFTY50", "BANKNIFTY", "SENSEX" ...)."""
        instruments, _ = await self.universe()
        hit = next((i for i in instruments if i.symbol == symbol.upper()), None)
        if hit:
            return hit
        want = INDEX_ALIASES.get(_norm(symbol), _norm(symbol))
        idx, _ = await self.indices()
        return next((i for i in idx if _norm(i.symbol) == want), None)

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

    async def detect_patterns(self, inst: Instrument, timeframe: Timeframe, df, ind) -> list[dict]:
        """Chart patterns for completed candles, cached until a new candle completes."""
        if df.empty:
            return []
        key = (inst.instrument_key, timeframe)
        stamp = df.index[-1].isoformat()
        hit = self._patterns.get(key)
        if hit and hit[0] == stamp and hit[1] == len(df):
            return hit[2]
        items = await asyncio.to_thread(pt.detect, df, ind)
        self._patterns[key] = (stamp, len(df), items)
        return items

    async def run_strategies(self, inst: Instrument, timeframe: Timeframe, df, ind) -> dict:
        """Strategies, candlesticks and order-flow proxies for completed candles, cached per candle."""
        key = (inst.instrument_key, timeframe)
        stamp = df.index[-1].isoformat()
        hit = self._strategies.get(key)
        if hit and hit[0] == stamp and hit[1] == len(df):
            return hit[2]
        res = await asyncio.to_thread(sx.compute, df, ind, timeframe)
        self._strategies[key] = (stamp, len(df), res)
        return res

    async def strategy_stats(self, timeframe: Timeframe, max_age: timedelta = timedelta(minutes=30)) -> dict:
        """Each strategy's and candlestick's track record pooled over every stock in the universe."""
        async with self._strategy_lock:
            hit = self._strategy_stats.get(timeframe)
            if hit and now_ist() - hit[0] < max_age:
                return hit[1]
            frames, errors = await self._universe_frames(timeframe)
            h, h_label = HORIZON[timeframe]

            def work():
                return sx.merge_stats([sx.raw_stats(df, ind, res, h) for _, df, ind, _, res in frames])

            merged = await asyncio.to_thread(work)
            payload = {
                "timeframe": timeframe,
                "source": self.provider.name,
                "generated_at": now_ist().isoformat(),
                "instruments": len(frames),
                "unavailable": errors,
                "horizon_bars": h,
                "horizon_label": h_label,
                "rows": sx.rows(merged),
                "method": f"Hit rate = share of signals where price moved the signalled way over the next {h_label}, versus "
                          "the base rate (how often it moved that way on any candle). Trades use the main signal's rules: "
                          "entry at the signal close, stop 1.5 ATR, target 2.5 ATR, or exit on a trend reversal. "
                          "Pooled over the universe, in-sample, before costs and slippage.",
            }
            self._strategy_stats[timeframe] = (now_ist(), payload)
            return payload

    def cached_strategy_stats(self, timeframe: str) -> dict | None:
        hit = self._strategy_stats.get(timeframe)
        return hit[1] if hit else None

    async def pattern_stats(self, timeframe: Timeframe, max_age: timedelta = timedelta(minutes=30)) -> dict:
        """Per-pattern success rates pooled over every stock in the universe (the pattern 'training')."""
        async with self._stats_lock:
            hit = self._pattern_stats.get(timeframe)
            if hit and now_ist() - hit[0] < max_age:
                return hit[1]
            frames, errors = await self._universe_frames(timeframe)
            used = len(frames)
            merged = pt.merge_stats([pt.raw_stats(items) for _, _, _, items, _ in frames])
            rows = []
            for k, (name, bias, kind, stars) in pt.CATALOG.items():
                rows.append({"key": k, "name": name, "bias": bias, "kind": kind, "stars": stars, "rule": pt.RULES[k],
                             **(merged.get(k) or pt.finalize({"detected": 0, "failed": 0, "wins": 0, "losses": 0, "expired": 0,
                                                              "pnl": [], "vol_wins": 0, "vol_n": 0}))})
            payload = {
                "timeframe": timeframe,
                "source": self.provider.name,
                "generated_at": now_ist().isoformat(),
                "instruments": used,
                "unavailable": errors,
                "patterns": rows,
                "method": "Each pattern's breakout is entered at the breakout candle's close; success = the book's measured-move "
                          "target is reached before the pattern's stop. In-sample, before costs and slippage.",
            }
            self._pattern_stats[timeframe] = (now_ist(), payload)
            return payload

    async def _universe_frames(self, timeframe: Timeframe) -> tuple[list[tuple[Instrument, object, object, list[dict], dict]], list[str]]:
        """(instrument, candles frame, indicators, patterns, strategies) for every stock and index, completed candles only."""
        instruments, _ = await self.universe()
        try:
            idx, _ = await self.indices()
        except ProviderError as exc:
            if exc.http_status == 401:
                raise
            idx = []
        instruments = instruments + idx
        out, errors = [], []
        for inst in instruments:
            try:
                candles = drop_incomplete(await self.provider.candles(inst, timeframe, include_partial=True), timeframe, now_ist())
            except ProviderError as exc:
                if exc.http_status == 401:
                    raise
                errors.append(inst.symbol)
                continue
            df, ind, _ = self._frame(inst, timeframe, candles)
            if df.empty:
                continue
            out.append((inst, df, ind, await self.detect_patterns(inst, timeframe, df, ind), await self.run_strategies(inst, timeframe, df, ind)))
        return out, errors

    async def outlook_model(self, timeframe: Timeframe, max_age: timedelta = timedelta(minutes=30)) -> dict | None:
        """Combined-outlook model trained on every stock's history (time-split tested). Cached."""
        async with self._model_lock:
            hit = self._models.get(timeframe)
            if hit and now_ist() - hit[0] < max_age:
                return hit[1]
            frames, _ = await self._universe_frames(timeframe)
            h = HORIZON[timeframe][0]

            def work():
                data = [(pb.features(df, ind, items, res["features"]), pb.labels(df, h)) for _, df, ind, items, res in frames]
                return pb.build(data, h)

            built = await asyncio.to_thread(work)
            self._models[timeframe] = (now_ist(), built)
            return built

    async def technical_outlook(self, inst: Instrument, timeframe: Timeframe) -> dict:
        """Combined technical outlook for one stock (used by the news sidebar), cached per candle."""
        candles = drop_incomplete(await self.provider.candles(inst, timeframe, include_partial=True), timeframe, now_ist())
        df, ind, _ = self._frame(inst, timeframe, candles)
        if df.empty:
            return {"status": "not_available", "message": "No candle data."}
        key = (inst.instrument_key, timeframe)
        stamp = df.index[-1].isoformat()
        built = await self.outlook_model(timeframe)
        hit = self._outlooks.get(key)
        if hit and hit[0] == stamp and hit[1] is built:
            return hit[2]
        found = await self.detect_patterns(inst, timeframe, df, ind)
        res = await self.run_strategies(inst, timeframe, df, ind)
        out = pb.outlook(built, pb.features(df, ind, found, res["features"]), timeframe, True, [])
        self._outlooks[key] = (stamp, built, out)
        return out

    def cached_pattern_stats(self, timeframe: str) -> dict | None:
        hit = self._pattern_stats.get(timeframe)
        return hit[1] if hit else None

    async def analyse(self, inst: Instrument, timeframe: Timeframe, with_series: bool) -> dict:
        all_candles = await self.provider.candles(inst, timeframe, include_partial=True)
        # Signals use completed candles only; the forming one is returned separately for live charts.
        candles = drop_incomplete(all_candles, timeframe, now_ist())
        forming = all_candles[-1] if len(all_candles) > len(candles) else None
        df, ind, stamp = self._frame(inst, timeframe, candles)
        quote = self.cached_quote(inst.instrument_key)
        freshness = quote.freshness if quote else None
        if df.empty:
            sig = evaluate(df, df, timeframe, now_ist(), freshness)
            return {"signal": sig, "snapshot": {}, "candles": [], "series": {}, "patterns": {"current": []}, "strategies": None}
        sig = evaluate(df, ind, timeframe, now_ist(), freshness)
        last = ind.iloc[-1]
        found = await self.detect_patterns(inst, timeframe, df, ind)
        strat = await self.run_strategies(inst, timeframe, df, ind)
        result = {
            "signal": sig,
            "snapshot": {k: ta.clean(last.get(k)) for k in SNAPSHOT_FIELDS},
            "last_close": float(df["close"].iloc[-1]),
            "history": self._memoized((inst.instrument_key, timeframe, "history", with_series), stamp,
                                      lambda: signal_history(df, ind, timeframe, with_markers=with_series)),
            "patterns": {"current": pt.current(found)},
            "strategies": self._memoized((inst.instrument_key, timeframe, "strategies", with_series), stamp,
                                         lambda: sx.block(df, ind, strat, with_series)),
        }
        if with_series:
            # Data-quality NO TRADE reasons (not "weak evidence") also block the combined call.
            issues = [r for r in sig["no_trade_reasons"] if r.startswith("Only ") or "stale" in r or "quote is" in r]
            built = await self.outlook_model(timeframe)
            result["outlook"] = pb.outlook(built, pb.features(df, ind, found, strat["features"]), timeframe, not issues, issues)
            done = [p for p in found if p["status"] in pt.RESOLVED]
            result["patterns"]["recent"] = done[-30:]
            result["patterns"]["stock_stats"] = pt.merge_stats([pt.raw_stats(found)])
        if with_series:
            result["forming_candle"] = forming.model_dump(mode="json") if forming else None
            result["candles"], result["series"], result["oscillators"] = self._memoized(
                (inst.instrument_key, timeframe, "chart"), stamp, lambda: self._chart_payload(candles, ind))
        return result

    @staticmethod
    def _chart_payload(candles: list, ind) -> tuple[list[dict], dict, dict]:
        def points(name: str) -> list[dict]:
            return [{"time": t.isoformat(), "value": ta.clean(val)} for t, val in ind[name].items() if ta.clean(val) is not None]

        series = {name: points(name) for name in CHART_SERIES}
        series["supertrend"] = [
            {"time": t.isoformat(), "value": ta.clean(val), "dir": int(d)}
            for (t, val), d in zip(ind["st_line"].items(), ind["st_dir"])
            if ta.clean(val) is not None and d != 0
        ]
        oscillators = {name: points(name) for name in ("rsi14", "macd", "macd_signal", "macd_hist")}
        return [c.model_dump(mode="json") for c in candles], series, oscillators

    async def intraday_card(self, inst: Instrument, with_chart: bool) -> dict:
        """Intraday call for one instrument: 5-minute entry timing confirmed by the 15-minute trend."""
        a5 = await self.analyse(inst, ENTRY_TF, with_series=with_chart)
        a15 = await self.analyse(inst, TREND_TF, with_series=False)
        o5 = a5.get("outlook") if with_chart else await self.technical_outlook(inst, ENTRY_TF)
        o15 = await self.technical_outlook(inst, TREND_TF)
        sig5, sig15 = a5["signal"], a15["signal"]
        quote = self.cached_quote(inst.instrument_key)
        price = quote.last_price if quote else a5.get("last_close")

        l5, how5, up5 = _lean(o5, sig5["score"], ENTRY_BUY, ENTRY_SELL)
        l15, how15, up15 = _lean(o15, sig15["score"], TREND_BUY, TREND_SELL)
        issues = _data_issues(sig5) + _data_issues(sig15)
        word = {1: "up", -1: "down", 0: "neither way"}
        if issues:
            verdict, reason = "NO TRADE", "; ".join(dict.fromkeys(issues))
        elif l5 == 1 and l15 == 1:
            verdict, reason = "BUY", "5-min timing and 15-min trend both point up."
        elif l5 == -1 and l15 == -1:
            verdict, reason = "SELL", "5-min timing and 15-min trend both point down."
        elif l5 != 0 and l15 == -l5:
            verdict, reason = "WAIT", f"5-min points {word[l5]} but the 15-min trend points {word[l15]}: conflicting, stay out."
        elif l5 != 0:
            verdict, reason = "WAIT", f"5-min points {word[l5]}; waiting for the 15-min trend to confirm."
        elif l15 != 0:
            verdict, reason = "WAIT", f"15-min trend points {word[l15]}; waiting for a 5-min entry."
        else:
            verdict, reason = "WAIT", "No clear direction on either timeframe."

        last_candle = sig5.get("last_candle_time")
        prev = self._intraday_state.get(inst.symbol)
        if prev and prev[0] == verdict:
            since, inherited = prev[1], prev[2]
        else:
            # The first call after a restart can't know when the call really started.
            since, inherited = last_candle, prev is None
        self._intraday_state[inst.symbol] = (verdict, since, inherited)

        atr = a5["snapshot"].get("atr14")
        levels = None
        if verdict in ("BUY", "SELL") and price and atr:
            d = 1 if verdict == "BUY" else -1
            sc = sig5.get("scenario")
            if sc and (sc["direction"] == "long") == (d == 1):
                levels = {"entry_low": sc["entry_low"], "entry_high": sc["entry_high"], "target": sc["target"],
                          "stop": sc["stop_loss"], "risk_reward": sc["risk_reward"], "basis": sc["basis"]}
            else:
                levels = {"entry_low": round(price - 0.2 * atr, 2), "entry_high": round(price + 0.2 * atr, 2),
                          "target": round(price + d * 2.5 * atr, 2), "stop": round(price - d * 1.5 * atr, 2),
                          "risk_reward": round(2.5 / 1.5, 2), "basis": "ATR(14) on 5-minute candles: stop 1.5 ATR, target 2.5 ATR"}
        states = (a5.get("strategies") or {}).get("strategies") or []
        test5 = (o5 or {}).get("test") or {}
        card = {
            "instrument": inst.model_dump(),
            "quote": quote.model_dump(mode="json") if quote else None,
            "price": price,
            "verdict": verdict,
            "reason": reason,
            "since": since,
            "since_known": not inherited,
            "last_candle_time": last_candle,
            "entry": {"timeframe": ENTRY_TF, "lean": l5, "basis": how5, "up_pct": up5, "score": sig5["score"],
                      "signal": sig5["signal"], "horizon_label": (o5 or {}).get("horizon_label")},
            "trend": {"timeframe": TREND_TF, "lean": l15, "basis": how15, "up_pct": up15, "score": sig15["score"],
                      "signal": sig15["signal"], "horizon_label": (o15 or {}).get("horizon_label")},
            "levels": levels,
            "session_vwap": a5["snapshot"].get("vwap"),
            "supertrend": {"dir": a5["snapshot"].get("st_dir"), "line": a5["snapshot"].get("st_line")},
            "atr": atr,
            "support": sig5["levels"].get("support"),
            "resistance": sig5["levels"].get("resistance"),
            "strategies_long": [s["name"] for s in states if s["state"] == "long"],
            "strategies_short": [s["name"] for s in states if s["state"] == "short"],
            "reliability": {"buy_hit_pct": test5.get("buy_hit_pct"), "buy_calls": test5.get("buy_calls"),
                            "sell_hit_pct": test5.get("sell_hit_pct"), "sell_calls": test5.get("sell_calls"),
                            "skill_pct": test5.get("skill_pct"), "base_up_pct": test5.get("base_up_pct")},
            "risks": sig5.get("risks", []),
        }
        if with_chart:
            days = sorted({c["time"][:10] for c in a5["candles"]})[-CHART_SESSIONS:]
            first = f"{days[0]}T00:00" if days else ""

            def keep(rows: list[dict]) -> list[dict]:
                return [r for r in rows if r["time"] >= first]

            card["chart"] = {
                "candles": keep(a5["candles"]),
                "forming_candle": a5.get("forming_candle"),
                "series": {k: keep(a5["series"].get(k, [])) for k in ("vwap", "vwap_u1", "vwap_l1", "supertrend", "ema20")},
                "markers": keep((a5.get("history") or {}).get("markers") or []),
            }
        return card

    async def intraday(self, include_stocks: bool = True) -> dict:
        """Intraday desk: NIFTY 50 / BANK NIFTY / SENSEX first, then stocks with a live BUY/SELL."""
        idx, missing_idx = await self.indices()
        cards = []
        for inst in idx:
            try:
                cards.append(await self.intraday_card(inst, with_chart=True))
            except ProviderError as exc:
                if exc.http_status == 401:
                    raise
                cards.append({"instrument": inst.model_dump(), "error": exc.message})
        out = {
            "generated_at": now_ist().isoformat(),
            "source": self.provider.name,
            "session": session_state(),
            "entry_timeframe": ENTRY_TF,
            "trend_timeframe": TREND_TF,
            "indices": cards,
            "unavailable_indices": missing_idx,
            "method": f"BUY when the 5-minute outlook is at least {ENTRY_BUY:.0f}% up and the 15-minute outlook at least "
                      f"{TREND_BUY:.0f}% up (SELL: at most {ENTRY_SELL:.0f}% and {TREND_SELL:.0f}%). If a model has not beaten "
                      f"guessing on unseen data, its rule score (+/-{RULE_LEVEL}) is used instead. Otherwise WAIT. "
                      "Levels from the 5-minute ATR. Before brokerage, taxes and slippage.",
        }
        if include_stocks:
            instruments, _ = await self.universe()
            sem = asyncio.Semaphore(4)

            async def one(inst: Instrument) -> dict | None:
                try:
                    async with sem:
                        return await self.intraday_card(inst, with_chart=False)
                except ProviderError as exc:
                    if exc.http_status == 401:
                        raise
                    return None

            rows = [r for r in await asyncio.gather(*(one(i) for i in instruments)) if r]

            def strength(r: dict) -> float:
                return abs((r["entry"]["up_pct"] or 50) - 50) + abs(r["entry"]["score"] or 0) / 10

            out["stocks"] = {
                "buy": sorted((r for r in rows if r["verdict"] == "BUY"), key=strength, reverse=True),
                "sell": sorted((r for r in rows if r["verdict"] == "SELL"), key=strength, reverse=True),
                "waiting": len([r for r in rows if r["verdict"] not in ("BUY", "SELL")]),
                "scanned": len(rows),
            }
        return out

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
