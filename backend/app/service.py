"""Orchestrates provider data, indicators and signals for the API layer."""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta

from app import options as opt
from app.analysis import dataquality as dq
from app.analysis import indicators as ta
from app.analysis import market as mk
from app.analysis import metalabel as ml
from app.analysis import tearsheet as ts
from app.analysis import patterns as pt
from app.analysis import probability as pb
from app.analysis import strategies as sx
from app.analysis import timemachine as tm
from app.analysis.costs import COST_NOTE, cost_for
from app.analysis.history import HORIZON
from app.analysis.history import signal_history
from app.analysis.signals import evaluate
from app.config import Settings
from app.market_session import EQUITY, MarketHours, is_session_open, now_ist, session_state
from app.models import Instrument, Quote, Timeframe, is_intraday
from app.providers.base import SECTOR_INDICES, VIX_INDEX, MarketDataProvider, ProviderError, drop_incomplete, live_bar_start
from app.watchlists import SYMBOL_RE

log = logging.getLogger(__name__)

# Intraday: the 5-minute chart times the entry, the 15-minute chart must agree on direction.
ENTRY_TF: Timeframe = "5m"
TREND_TF: Timeframe = "15m"
ENTRY_BUY, ENTRY_SELL = 55.0, 45.0
TREND_BUY, TREND_SELL = 52.0, 48.0
RULE_LEVEL = 20
DESK_FRESH_SECONDS = 20  # the intraday desk is rebuilt in the background once older than this
CHART_SESSIONS = 2
TM_LOOKBACK_DAYS = 90  # ~60 sessions of 5-minute candles for the Time Machine's pool
CONTEXT_FRESH_SECONDS = 300  # market board
OPTION_SAMPLE_SECONDS = 300  # index option chains are sampled this often during the session (PCR / IV trend)
IV_HISTORY_DAYS = 400
GOLD_ETF = "GOLDBEES"
OPENING_RANGE_MINUTES = 15
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


def opening_range(df, timeframe: Timeframe) -> dict | None:
    """High and low of the latest session's first 15 minutes (completed candles), for 1/3/5/15-minute charts."""
    from app.providers.base import TIMEFRAMES

    minutes = TIMEFRAMES[timeframe].bar_minutes
    if df.empty or not minutes or minutes > OPENING_RANGE_MINUTES:
        return None
    day = df[df.index.date == df.index[-1].date()]
    first = day.index[0]
    rng = day[day.index < first + timedelta(minutes=OPENING_RANGE_MINUTES)]
    if rng.empty:
        return None
    return {"high": round(float(rng["high"].max()), 4), "low": round(float(rng["low"].min()), 4),
            "start": first.isoformat(), "minutes": OPENING_RANGE_MINUTES,
            "complete": len(rng) * minutes >= OPENING_RANGE_MINUTES}


def _data_issues(sig: dict) -> list[str]:
    return [r for r in sig["no_trade_reasons"] if r.startswith(("Only ", "Data check")) or "stale" in r or "quote is" in r]

# Bars sent to the chart (the analysis itself always uses the full history).
CHART_BARS = {"1m": 750, "3m": 750, "5m": 900, "15m": 800, "30m": 800, "1h": 700, "1d": 750, "1w": 400}
CHART_SERIES = ("ema20", "ema50", "ema200", "bb_upper", "bb_lower", "vwap", "vwap_u1", "vwap_l1", "vwap_u2", "vwap_l2")
SNAPSHOT_FIELDS = (
    "ema9", "ema20", "ema50", "ema200", "rsi14", "macd", "macd_signal", "macd_hist", "atr14",
    "bb_upper", "bb_mid", "bb_lower", "adx14", "plus_di", "minus_di", "vwap", "rel_volume", "roc10",
    "sma20", "st_line", "st_dir", "kc_upper", "kc_lower", "squeeze_on", "zscore20", "dc_upper", "dc_lower",
    "chandelier_long", "chandelier_short", "mfi14", "stoch_k", "stoch_d", "cpr_p", "cpr_bc", "cpr_tc", "cpr_r1", "cpr_s1",
)


class MarketService:
    # Session the instruments trade in, and decimals for prices in plain-text instructions.
    hours: MarketHours = EQUITY
    price_dp = 2
    cost_note = COST_NOTE

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
        # timeframe -> (per-instrument raw strategy accumulators, instruments tested): the trade lists behind the stats
        self._strategy_raw: dict[str, tuple[list[dict], int]] = {}
        # timeframe -> (computed at, meta-labelling model or None)
        self._meta: dict[str, tuple[datetime, dict | None]] = {}
        self._meta_lock = asyncio.Lock()
        self._meta_tasks: dict[str, asyncio.Task] = {}
        self._strategy_lock = asyncio.Lock()
        # Stocks resolved on demand (watchlists can hold any NSE stock): symbol -> instrument
        self._extra: dict[str, Instrument] = {}
        # Intraday calls being followed until they hit target / stop or the signal ends: symbol -> position
        self._positions: dict[str, dict] = {}
        # symbol -> (intraday verdict, candle it was first seen on, seen since startup only = not a fresh change)
        self._intraday_state: dict[str, tuple[str, str, bool]] = {}
        self._training: dict[str, asyncio.Task] = {}
        # Work that only changes when a candle completes: (instrument_key, timeframe, part) -> (stamp, value)
        self._memo: dict[tuple, tuple[tuple, object]] = {}
        # Option contracts (expiries, lot size) per index and short-lived chains: key -> (monotonic time, value)
        self._opt_contracts: dict[str, tuple[float, list[dict]]] = {}
        self._opt_chains: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        # Intraday desk, with and without stocks: include_stocks -> (monotonic time, 5-min bar, desk)
        self._desk: dict[bool, tuple[float, str, dict]] = {}
        self._desk_locks: dict[bool, asyncio.Lock] = {}
        self._desk_tasks: dict[bool, asyncio.Task] = {}
        self.cache = None  # set by the app: keeps each symbol's daily ATM IV for the IV percentile
        # (symbol, expiry) -> today's chain snapshots [{time, pcr, atm_iv, straddle, spot}]
        self._opt_trend: dict[tuple[str, str], list[dict]] = {}
        self._iv_saved: dict[str, float] = {}

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
            return df, ta.compute(df, intraday=is_intraday(timeframe))

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
        found = next((i for i in idx if _norm(i.symbol) == want), None)
        if found:
            return found
        sym = " ".join(symbol.upper().split())
        if sym in self._extra:
            return self._extra[sym]
        if not SYMBOL_RE.match(sym) or " " in sym:
            return None
        if self.provider.name == "demo" and not any(h["symbol"] == sym for h in await self.provider.search(sym)):
            return None  # demo mode makes up prices for anything: only accept its list of real names
        resolved, _ = await self.provider.resolve_equities([sym])
        if resolved:
            self._extra[sym] = resolved[0]
            return resolved[0]
        return None

    async def search(self, query: str, limit: int = 12) -> list[dict]:
        """Indices and NSE stocks matching a symbol or company name."""
        q = query.strip()
        if not q:
            return []
        idx, _ = await self.indices()
        nq = _norm(q)
        hits = [{"symbol": i.symbol, "name": i.name, "exchange": i.exchange, "kind": "index"}
                for i in idx if nq in _norm(i.symbol) or _norm(i.symbol) in INDEX_ALIASES.get(nq, "")]
        stocks = await self.provider.search(q, limit)
        return (hits + [{**s, "kind": "equity"} for s in stocks])[:limit]

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
            known = {i.instrument_key for i in equities + idx}
            extra = [i for i in self._extra.values() if i.instrument_key not in known]
            self._last_quotes = await self.provider.quotes(equities + idx + extra)
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
                raw = [sx.raw_stats(df, ind, res, h, cost_for(timeframe, inst.kind)) for inst, df, ind, _, res in frames]
                return raw, sx.merge_stats(raw)

            raw, merged = await asyncio.to_thread(work)
            self._strategy_raw[timeframe] = (raw, len(frames))
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
                          f"Pooled over the universe, in-sample. Hit rates are direction only; trade results are {self.cost_note}.",
            }
            self._strategy_stats[timeframe] = (now_ist(), payload)
            return payload

    def _trades_of(self, timeframe: str, key: str) -> tuple[list[str], list[float]]:
        raw, _ = self._strategy_raw.get(timeframe, ([], 0))
        times, pnl = [], []
        for one in raw:
            acc = one.get(key)
            if acc and len(acc.get("times") or []) == len(acc["pnl"]):
                times += acc["times"]
                pnl += acc["pnl"]
        return times, pnl

    async def strategy_lab(self, timeframe: Timeframe) -> dict:
        """Leaderboard of every strategy on this timeframe: pooled, after costs, ranked by how far the record
        can be trusted (evidence grade), then by Sharpe."""
        stats = await self.strategy_stats(timeframe)
        _, n = self._strategy_raw.get(timeframe, ([], 0))
        labs = self.__dict__.setdefault("_lab", {})
        hit = labs.get(timeframe)
        if hit and hit[0] == stats["generated_at"]:
            rows = hit[1]
        else:
            rows = None
        by_key = {r["key"]: r for r in stats["rows"]}
        rank = {"proven": 0, "promising": 1, "insufficient": 2, "weak": 3}

        def work():
            rows = []
            for key, (name, kind, desc) in sx.CATALOG.items():
                times, pnl = self._trades_of(timeframe, key)
                sheet = ts.build(times, pnl, n)
                r = by_key.get(key, {})
                rows.append({"key": key, "name": name, "kind": kind, "description": desc,
                             "evidence": r.get("evidence"), "deflated_sharpe": r.get("deflated_sharpe"), "decay": r.get("decay"),
                             "hit_rate": r.get("hit_rate"), "edge_pts": r.get("edge_pts"),
                             "stats": sheet["stats"] if sheet else None,
                             "spark": [p[1] for p in sheet["equity"][:: max(1, len(sheet["equity"]) // 60)]] if sheet else []})
            rows.sort(key=lambda x: (rank.get((x["evidence"] or {}).get("grade"), 4),
                                     -((x["stats"] or {}).get("sharpe") or -99)))
            return rows

        if rows is None:
            rows = await asyncio.to_thread(work)
            labs[timeframe] = (stats["generated_at"], rows)
        ready, meta = self.meta_model_nowait(timeframe)
        return {
            "timeframe": timeframe, "source": self.provider.name, "generated_at": now_ist().isoformat(),
            "instruments": n, "horizon_label": stats["horizon_label"], "rows": rows,
            "meta": {"status": "training" if not ready else "ok" if meta else "not_available",
                     "useful": bool(meta and meta["useful"]), "test": meta["test"] if meta else None},
            "method": "Each strategy's signals on every instrument, traded with the same rules (stop 1.5 ATR, target 2.5 ATR or "
                      f"a trend-reversal exit), {self.cost_note}. Ranked by evidence grade, then Sharpe. In-sample.",
        }

    async def strategy_tearsheet(self, timeframe: Timeframe, key: str) -> dict:
        if key not in sx.CATALOG:
            raise ProviderError("unknown_strategy", f"No strategy called {key}", 404)
        await self.strategy_stats(timeframe)
        _, n = self._strategy_raw.get(timeframe, ([], 0))
        times, pnl = self._trades_of(timeframe, key)
        sheet = await asyncio.to_thread(ts.build, times, pnl, n)
        name, kind, desc = sx.CATALOG[key]
        return {"key": key, "name": name, "kind": kind, "description": desc, "timeframe": timeframe,
                "source": self.provider.name, "generated_at": now_ist().isoformat(), "tearsheet": sheet}

    def meta_model_nowait(self, timeframe: Timeframe) -> tuple[bool, dict | None]:
        hit = self._meta.get(timeframe)
        stale = not hit or now_ist() - hit[0] >= timedelta(minutes=30)
        task = self._meta_tasks.get(timeframe)
        if stale and (task is None or task.done()):
            self._meta_tasks[timeframe] = asyncio.create_task(self._meta_quietly(timeframe))
        return (True, hit[1]) if hit else (False, None)

    async def _meta_quietly(self, timeframe: Timeframe) -> None:
        try:
            await self.meta_model(timeframe)
        except Exception as exc:
            log.info("meta-label training for %s failed: %s", timeframe, exc)

    async def meta_model(self, timeframe: Timeframe) -> dict | None:
        """Meta-labelling model: which strategy signals tend to win after costs, given the market at entry."""
        async with self._meta_lock:
            hit = self._meta.get(timeframe)
            if hit and now_ist() - hit[0] < timedelta(minutes=30):
                return hit[1]
            frames, _ = await self._universe_frames(timeframe)
            keys = list(sx.CATALOG)

            def work():
                rows, names = [], None
                for inst, df, ind, items, res in frames:
                    X = pb.features(df, ind, items, res["features"])
                    names = list(X.columns)
                    rows += ml.dataset(df, ind, X, res["signals"], keys, cost_for(timeframe, inst.kind))
                return ml.build(rows, keys, names or []) if names else None

            built = await asyncio.to_thread(work)
            self._meta[timeframe] = (now_ist(), built)
            return built

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
            merged = pt.merge_stats([pt.raw_stats(items, cost_for(timeframe, inst.kind)) for inst, _, _, items, _ in frames])
            rows = []
            for k, (name, bias, kind, stars) in pt.CATALOG.items():
                rows.append({"key": k, "name": name, "bias": bias, "kind": kind, "stars": stars, "rule": pt.RULES[k],
                             **(merged.get(k) or pt.finalize(pt._empty_stats()))})
            payload = {
                "timeframe": timeframe,
                "source": self.provider.name,
                "generated_at": now_ist().isoformat(),
                "instruments": used,
                "unavailable": errors,
                "patterns": rows,
                "method": "Each pattern's breakout is entered at the breakout candle's close; success = the book's measured-move "
                          f"target is reached before the pattern's stop. In-sample; P&L {self.cost_note}.",
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
                candles = drop_incomplete(await self.provider.candles(inst, timeframe, include_partial=True), timeframe, now_ist(), self.hours)
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

    def outlook_model_nowait(self, timeframe: Timeframe) -> tuple[bool, dict | None]:
        """(ready, model) without waiting: if the model isn't trained yet, training starts in the
        background so the chart can be shown immediately and the outlook fills in on the next refresh."""
        hit = self._models.get(timeframe)
        if hit:
            if now_ist() - hit[0] >= timedelta(minutes=30):
                self._train_in_background(timeframe)  # refresh, but keep serving the current model
            return True, hit[1]
        self._train_in_background(timeframe)
        return False, None

    def _train_in_background(self, timeframe: Timeframe) -> None:
        task = self._training.get(timeframe)
        if task is None or task.done():
            self._training[timeframe] = asyncio.create_task(self._train_quietly(timeframe))

    async def _train_quietly(self, timeframe: Timeframe) -> None:
        try:
            await self.outlook_model(timeframe)
        except Exception as exc:  # e.g. not logged in: retried on the next request
            log.info("background model training for %s failed: %s", timeframe, exc)

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
        candles = drop_incomplete(await self.provider.candles(inst, timeframe, include_partial=True), timeframe, now_ist(), self.hours)
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
        candles = drop_incomplete(all_candles, timeframe, now_ist(), self.hours)
        forming = all_candles[-1] if len(all_candles) > len(candles) else None
        df, ind, stamp = self._frame(inst, timeframe, candles)
        quote = self.cached_quote(inst.instrument_key)
        freshness = quote.freshness if quote else None
        if df.empty:
            sig = evaluate(df, df, timeframe, now_ist(), freshness)
            return {"signal": sig, "snapshot": {}, "candles": [], "series": {}, "patterns": {"current": []}, "strategies": None}
        sig = evaluate(df, ind, timeframe, now_ist(), freshness)
        from app.providers.base import TIMEFRAMES

        quality = self._memoized((inst.instrument_key, timeframe, "quality"), stamp,
                                 lambda: dq.check(df, TIMEFRAMES[timeframe].bar_minutes, self.hours, inst.kind != "index"))
        if not quality["ok"]:  # broken candles: no call, whatever the indicators say
            sig = {**sig, "status": "no_trade", "scenario": None,
                   "no_trade_reasons": sig["no_trade_reasons"] + ["Data check failed: " + "; ".join(
                       i["text"] for i in quality["issues"] if i["level"] == "error")]}
        last = ind.iloc[-1]
        found = await self.detect_patterns(inst, timeframe, df, ind)
        strat = await self.run_strategies(inst, timeframe, df, ind)
        result = {
            "signal": sig,
            "snapshot": {k: ta.clean(last.get(k)) for k in SNAPSHOT_FIELDS},
            "last_close": float(df["close"].iloc[-1]),
            "history": self._memoized((inst.instrument_key, timeframe, "history", with_series), stamp,
                                      lambda: signal_history(df, ind, timeframe, with_markers=with_series,
                                                             cost_pct=cost_for(timeframe, inst.kind),
                                                             cost_note=self.cost_note)),
            "patterns": {"current": pt.current(found)},
            "data_quality": quality,
            "opening_range": opening_range(df, timeframe),
            "strategies": self._memoized((inst.instrument_key, timeframe, "strategies", with_series), stamp,
                                         lambda: sx.block(df, ind, strat, with_series)),
        }
        if with_series:
            # Data-quality NO TRADE reasons (not "weak evidence") also block the combined call.
            issues = _data_issues(sig)
            ready, built = self.outlook_model_nowait(timeframe)
            X_now = pb.features(df, ind, found, strat["features"])
            meta_ready, meta = self.meta_model_nowait(timeframe)
            if result["strategies"] is not None and meta_ready and meta:
                result["strategies"] = {**result["strategies"],
                                        "meta": ml.score_current(meta, X_now, result["strategies"]["strategies"]),
                                        "meta_test": {**meta["test"], "useful": meta["useful"]}}
            if ready:
                result["outlook"] = pb.outlook(built, X_now, timeframe, not issues, issues)
            else:
                h, h_label = HORIZON[timeframe]
                result["outlook"] = {"status": "training", "horizon_bars": h, "horizon_label": h_label,
                                     "message": "Training the combined model on every stock's history — this fills in within a minute."}
            done = [p for p in found if p["status"] in pt.RESOLVED]
            result["patterns"]["recent"] = done[-30:]
            result["patterns"]["stock_stats"] = pt.merge_stats([pt.raw_stats(found, cost_for(timeframe, inst.kind))])
        if with_series:
            result["forming_candle"] = forming.model_dump(mode="json") if forming else None
            result["candles"], result["series"], result["oscillators"] = self._memoized(
                (inst.instrument_key, timeframe, "chart"), stamp, lambda: self._chart_payload(candles, ind, CHART_BARS.get(timeframe, 800)))
        return result

    @staticmethod
    def _chart_payload(candles: list, ind, max_bars: int) -> tuple[list[dict], dict, dict]:
        """The latest `max_bars` candles, and every series as a plain array aligned to them (null where
        undefined). Far smaller than {time, value} objects; the browser re-attaches the times."""
        start = max(0, len(candles) - max_bars)

        def values(name: str) -> list[float | None]:
            return [ta.clean(v) for v in ind[name].iloc[start:]]

        series = {name: values(name) for name in CHART_SERIES}
        dirs = [int(d) for d in ind["st_dir"].iloc[start:]]
        series["supertrend"] = [v if d else None for v, d in zip(values("st_line"), dirs)]
        series["supertrend_dir"] = dirs
        oscillators = {name: values(name) for name in ("rsi14", "macd", "macd_signal", "macd_hist")}
        return [c.model_dump(mode="json") for c in candles[start:]], series, oscillators

    async def _contracts(self, inst: Instrument) -> list[dict]:
        """Option contracts (expiry, lot size) of an index or stock, cached for 6 hours."""
        key = inst.instrument_key
        hit = self._opt_contracts.get(key)
        if not hit or time.monotonic() - hit[0] > 6 * 3600:
            try:
                found = await self.provider.option_contracts(inst)
            except ProviderError as exc:
                if exc.http_status == 401:
                    raise
                found = []  # Upstox answers non-F&O stocks with an error: same as no contracts
            hit = (time.monotonic(), found)
            self._opt_contracts[key] = hit
        return hit[1]

    async def _daily_closes(self, inst: Instrument):
        import pandas as pd

        candles = drop_incomplete(await self.provider.candles(inst, "1d", include_partial=True), "1d", now_ist(), self.hours)
        return pd.Series([c.close for c in candles], index=[c.time.date() for c in candles], dtype=float)

    async def market_context(self, usdinr: Instrument | None = None) -> dict:
        """Market board: India VIX, breadth of the universe, sector rotation vs NIFTY 50, a risk-on/defensive
        vote, upcoming index expiries and the F&O ban list. Cached for 5 minutes."""
        from app import nse

        hit = self.__dict__.get("_context")
        if hit and time.monotonic() - hit[0] < CONTEXT_FRESH_SECONDS:
            return hit[1]
        sem = asyncio.Semaphore(4)

        async def closes(inst: Instrument | None):
            if inst is None:
                return None
            try:
                async with sem:
                    return await self._daily_closes(inst)
            except ProviderError as exc:
                if exc.http_status == 401:
                    raise
                return None

        idx, _ = await self.indices()
        nifty_inst = next((i for i in idx if i.symbol == "NIFTY 50"), None)
        vix_inst = await self.provider.resolve_index(VIX_INDEX)
        sector_insts = [await self.provider.resolve_index(s) for s in SECTOR_INDICES]
        stocks, _ = await self.universe()
        gold, _ = await self.provider.resolve_equities([GOLD_ETF])
        jobs = [closes(nifty_inst), closes(vix_inst), closes(gold[0] if gold else None), closes(usdinr)]
        jobs += [closes(i) for i in sector_insts] + [closes(i) for i in stocks]
        res = await asyncio.gather(*jobs)
        nifty, vix_c, gold_c, fx_c = res[:4]
        sec_c = res[4:4 + len(sector_insts)]
        stock_c = res[4 + len(sector_insts):]

        vix = mk.vix_summary(vix_c) if vix_c is not None else None
        brd = mk.breadth({s.symbol: c for s, c in zip(stocks, stock_c) if c is not None})
        rotation = mk.sector_rotation({s: c for s, c in zip(SECTOR_INDICES, sec_c) if c is not None}, nifty) if nifty is not None else []
        votes = mk.macro_votes(nifty, vix, brd, gold_c, fx_c) if nifty is not None else None

        expiries = []
        today = now_ist().date()
        for inst in idx:
            if inst.symbol not in opt.OPTION_INDICES:
                continue
            up = opt.upcoming_expiries(await self._contracts(inst), today)
            if up:
                expiries.append({"index": inst.symbol, "next": up[0], "is_today": up[0] == today.isoformat(),
                                 "days": (date.fromisoformat(up[0]) - today).days})
        if self.provider.name == "demo":
            ban = {"status": "demo", "trade_date": None, "symbols": [], "message": "Not available in demo mode."}
        else:
            ban = await nse.fo_ban_list()
        out = {
            "generated_at": now_ist().isoformat(),
            "source": self.provider.name,
            "session": session_state(hours=self.hours),
            "nifty": {"level": None if nifty is None or nifty.empty else round(float(nifty.iloc[-1]), 2),
                      "returns": {k: mk.ret(nifty, n) for k, n in mk.HORIZONS.items()} if nifty is not None else {}},
            "vix": vix,
            "breadth": brd,
            "sectors": rotation,
            "regime": votes,
            "expiries": expiries,
            "fo_ban": ban,
            "method": "Daily closes of completed sessions. Sector score = average z-score across sectors of relative strength "
                      "vs NIFTY 50 over 1 week, 1 month, 3 months and the acceleration (1-week daily pace minus 3-month). "
                      "Breadth is over this app's stock universe, not the whole exchange. Context, not a trade signal.",
        }
        self.__dict__["_context"] = (time.monotonic(), out)
        return out

    async def option_chain(self, symbol: str, expiry: str | None = None) -> dict:
        """Option chain of an index (NIFTY 50 / BANK NIFTY / SENSEX) or F&O stock, with which option to buy now."""
        inst = await self.instrument(symbol)
        if not inst or (inst.kind == "index" and inst.symbol not in opt.OPTION_INDICES):
            raise ProviderError("no_options", f"{symbol} has no option chain", 404)
        key = inst.instrument_key
        contracts = await self._contracts(inst)
        expiries = opt.upcoming_expiries(contracts, now_ist().date())
        if not expiries:
            raise ProviderError("no_options", f"{inst.symbol} has no F&O options (only F&O stocks and the main indices do)", 404)
        expiry = expiry if expiry in expiries else expiries[0]
        ttl = 5 if self.settings.quote_poll_seconds <= 5 else self.settings.quote_poll_seconds
        chain = self._opt_chains.get((key, expiry))
        if not chain or time.monotonic() - chain[0] > ttl:
            chain = (time.monotonic(), await self.provider.option_chain(inst, expiry))
            self._opt_chains[(key, expiry)] = chain
        rows, spot = opt.parse_chain(chain[1])
        quote = self.cached_quote(key)
        spot = spot or (quote.last_price if quote else None)
        lot = opt.lot_for(contracts, expiry)
        try:
            card = self.cached_card(inst.symbol) or await self.intraday_card(inst, with_chart=False)
        except ProviderError:
            raise
        except Exception:  # the chain is still useful without the call
            log.exception("intraday call failed for %s", inst.symbol)
            card = None
        summary = opt.summarize(rows, spot)
        trend = self._record_chain(inst.symbol, expiry, summary, spot)
        return {
            "flow": opt.oi_flow(rows, spot),
            "trend": trend,
            "iv_rank": await self._iv_rank(inst, summary.get("atm_iv")),
            "symbol": inst.symbol,
            "instrument": inst.model_dump(),
            "source": self.provider.name,
            "generated_at": now_ist().isoformat(),
            "expiry": expiry,
            "expiries": expiries[:8],
            "lot_size": lot,
            "spot": spot,
            "summary": summary,
            "recommendation": opt.recommend(rows, spot, card, summary, lot),
            "index_call": {k: (card or {}).get(k) for k in ("verdict", "reason", "levels", "atr", "since")} if card else None,
            "rows": opt.window(rows, spot),
        }

    def _record_chain(self, symbol: str, expiry: str, summary: dict, spot: float | None) -> list[dict]:
        """Today's PCR / ATM IV / straddle snapshots for this chain (at most one a minute)."""
        now = now_ist()
        key = (symbol, expiry)
        snaps = [s for s in self._opt_trend.get(key, []) if s["time"][:10] == now.date().isoformat()]
        if summary.get("pcr") is not None and is_session_open(now, self.hours) and (
                not snaps or (now - datetime.fromisoformat(snaps[-1]["time"])).total_seconds() >= 60):
            snaps.append({"time": now.isoformat(), "pcr": summary["pcr"], "atm_iv": summary.get("atm_iv"),
                          "straddle": summary.get("straddle"), "spot": spot})
        self._opt_trend[key] = snaps[-200:]
        return snaps

    async def _iv_rank(self, inst: Instrument, atm_iv: float | None) -> dict | None:
        """Where today's ATM IV sits in this symbol's own daily history (kept in the cache). Until 20 days are
        collected, indices fall back to India VIX's percentile, which is NIFTY's 30-day implied volatility."""
        hist: dict[str, float] = {}
        if self.cache is not None:
            hist = await self.cache.get(f"ivhist:{inst.symbol}") or {}
            now = now_ist()
            if atm_iv and is_session_open(now, self.hours) and time.monotonic() - self._iv_saved.get(inst.symbol, 0) > 600:
                hist[now.date().isoformat()] = atm_iv
                cutoff = (now.date() - timedelta(days=IV_HISTORY_DAYS)).isoformat()
                hist = {d: v for d, v in hist.items() if d >= cutoff}
                await self.cache.set(f"ivhist:{inst.symbol}", hist, IV_HISTORY_DAYS * 86400)
                self._iv_saved[inst.symbol] = time.monotonic()
        own = opt.iv_percentile(hist, atm_iv)
        if own:
            return own
        if inst.kind == "index":
            vix_inst = await self.provider.resolve_index(VIX_INDEX)
            if vix_inst is not None:
                try:
                    vix = mk.vix_summary(await self._daily_closes(vix_inst))
                except ProviderError:
                    vix = None
                if vix:
                    return {"rank": None, "percentile": vix["percentile_1y"], "days": len(hist), "source": "vix",
                            "vix": vix["level"]}
        return {"rank": None, "percentile": None, "days": len(hist), "source": "collecting"} if atm_iv else None

    async def options_sampler(self) -> None:
        """During the session, read each index's nearest option chain every few minutes so the PCR / IV trend
        and the daily IV history build up even when nobody has the options tab open."""
        while True:
            if is_session_open(now_ist(), self.hours):
                for sym in opt.OPTION_INDICES:
                    try:
                        await self.option_chain(sym)
                    except Exception as exc:
                        log.info("option sample for %s skipped: %s", sym, exc)
                await asyncio.sleep(OPTION_SAMPLE_SECONDS)
            else:
                await asyncio.sleep(60)

    async def time_machine(self, inst: Instrument, side: str, entry: float, stop: float, target: float,
                           until: str | None = None, seed: int = 19, replays: int = 10_000) -> dict:
        """10,000 replays of the rest of the session for one plan, plus the same trade on each past session."""
        from datetime import time as dtime

        try:
            end = dtime.fromisoformat(until) if until else tm.default_until(self.hours)
        except ValueError:
            raise ProviderError("invalid_plan", "Square-off time must look like 15:15", 400) from None
        key = (inst.instrument_key, side, entry, stop, target, end, seed, replays, self._entry_bar())
        cache = self.__dict__.setdefault("_tm_cache", {})
        if key in cache:
            return cache[key]
        candles = drop_incomplete(await self.provider.candles(inst, "5m", include_partial=True, lookback_days=TM_LOOKBACK_DAYS),
                                  "5m", now_ist(), self.hours)
        df = ta.to_frame(candles)
        try:
            res = await asyncio.to_thread(tm.run, df, self.hours, side=side, entry=entry, stop=stop, target=target, now=now_ist(),
                                          until=end, cost_pct=cost_for("5m", inst.kind), replays=replays, seed=seed)
        except tm.TimeMachineError as exc:
            raise ProviderError("invalid_plan", str(exc), 400) from None
        out = {
            "symbol": inst.symbol, "instrument": inst.model_dump(), "source": self.provider.name, "generated_at": now_ist().isoformat(),
            **res,
            "method": "Each replay rebuilds the rest of the session from this instrument's own 5-minute candles at the same time "
                      f"of day over its last {res['sessions']} full sessions, scaled to the current price; the stop is checked "
                      "before the target. The history line is the same trade (same % stop and target, same entry time) on each "
                      "of those real sessions. A simulation, not a prediction: results, news and halts can do things those "
                      f"sessions never did. Average R after costs subtracts the round-trip charges and slippage ({self.cost_note}).",
        }
        cache[key] = out
        while len(cache) > 200:  # a few hundred recent plans at most
            cache.pop(next(iter(cache)))
        return out

    async def daily_trend(self, inst: Instrument) -> dict | None:
        """The daily chart's rule score as context for an intraday call (cached per daily candle)."""
        try:
            candles = drop_incomplete(await self.provider.candles(inst, "1d", include_partial=True), "1d", now_ist(), self.hours)
        except ProviderError as exc:
            if exc.http_status == 401:
                raise
            return None
        df, ind, stamp = self._frame(inst, "1d", candles)
        if df.empty:
            return None

        def work():
            score = evaluate(df, ind, "1d", now_ist(), None)["score"]
            if score is None:
                return None
            return {"score": score, "lean": 1 if score >= RULE_LEVEL else -1 if score <= -RULE_LEVEL else 0}

        return self._memoized((inst.instrument_key, "1d", "daily_trend"), stamp, work)

    async def intraday_card(self, inst: Instrument, with_chart: bool) -> dict:
        """Intraday call for one instrument: 5-minute entry timing confirmed by the 15-minute trend."""
        a5 = await self.analyse(inst, ENTRY_TF, with_series=with_chart)
        a15 = await self.analyse(inst, TREND_TF, with_series=False)
        o5 = await self.technical_outlook(inst, ENTRY_TF)
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
                levels = {"entry_low": round(price - 0.2 * atr, 4), "entry_high": round(price + 0.2 * atr, 4),
                          "target": round(price + d * 2.5 * atr, 4), "stop": round(price - d * 1.5 * atr, 4),
                          "risk_reward": round(2.5 / 1.5, 2), "basis": "ATR(14) on 5-minute candles: stop 1.5 ATR, target 2.5 ATR"}
        states = (a5.get("strategies") or {}).get("strategies") or []
        test5 = (o5 or {}).get("test") or {}
        position = self._follow(inst.symbol, verdict, levels, price, last_candle)
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
            "opening_range": a5.get("opening_range"),
            "regime": (a5.get("strategies") or {}).get("regime"),
            "daily": await self._daily_context(inst, verdict),
            "position": position,
            "action": self._action(verdict, position),
        }
        if with_chart:
            days = sorted({c["time"][:10] for c in a5["candles"]})[-CHART_SESSIONS:]
            first = f"{days[0]}T00:00" if days else ""
            cut = next((i for i, c in enumerate(a5["candles"]) if c["time"] >= first), 0)
            card["chart"] = {
                "candles": a5["candles"][cut:],
                "forming_candle": a5.get("forming_candle"),
                "series": {k: a5["series"].get(k, [])[cut:]
                           for k in ("vwap", "vwap_u1", "vwap_l1", "supertrend", "supertrend_dir", "ema20")},
                "markers": [m for m in (a5.get("history") or {}).get("markers") or [] if m["time"] >= first],
            }
        return card

    async def _daily_context(self, inst: Instrument, verdict: str) -> dict | None:
        d = await self.daily_trend(inst)
        if d is None:
            return None
        side = 1 if verdict == "BUY" else -1 if verdict == "SELL" else 0
        return {**d, "agrees": None if side == 0 or d["lean"] == 0 else d["lean"] == side}

    def _follow(self, symbol: str, verdict: str, levels: dict | None, price: float | None, candle: str | None) -> dict | None:
        """Track the latest BUY/SELL call like a trade so the app can say when to get out: target
        reached, stop-loss hit, or the signal ended (the two timeframes no longer agree)."""
        pos = self._positions.get(symbol)
        if pos and pos["opened"][:10] != (candle or "")[:10]:
            pos = None  # intraday calls don't carry over to the next session
        # 1. Manage the open call.
        if pos and pos["status"] == "open" and price:
            long = pos["side"] == "BUY"
            if price >= pos["target"] if long else price <= pos["target"]:
                reason = "target"
            elif price <= pos["stop"] if long else price >= pos["stop"]:
                reason = "stop"
            elif verdict != pos["side"]:
                reason = "signal_ended"
            else:
                reason = None
            if reason:
                pos = {**pos, "status": reason, "exit_price": round(price, 4), "closed": candle,
                       "pnl_pct": round((price / pos["entry"] - 1) * 100 * (1 if long else -1), 2)}
        # 2. After an exit, a new call on the same side needs the signal to reset first.
        if pos and pos["status"] != "open" and verdict != pos["side"]:
            pos = {**pos, "rearmed": True}
        # 3. Open a new call.
        if verdict in ("BUY", "SELL") and levels and price and (
                pos is None or (pos["status"] != "open" and (pos["side"] != verdict or pos.get("rearmed")))):
            prev = None if pos is None else {k: v for k, v in pos.items() if k != "previous"}
            pos = {"side": verdict, "entry": round(price, 4), "target": levels["target"], "stop": levels["stop"],
                   "opened": candle or now_ist().isoformat(), "status": "open", "exit_price": None, "closed": None,
                   "pnl_pct": None, "previous": prev}
        if pos is None:
            self._positions.pop(symbol, None)
        else:
            self._positions[symbol] = pos
        return pos

    def _action(self, verdict: str, pos: dict | None) -> dict:
        """One plain instruction: BUY / SELL / HOLD the open call / SELL NOW to exit / WAIT."""
        f = lambda v: f"{v:,.{self.price_dp}f}"  # noqa: E731
        if pos and pos["status"] == "open":
            if pos["side"] == "BUY":
                return {"label": "HOLD BUY", "tone": "bull", "text": f"Bought at {f(pos['entry'])}: sell at target "
                        f"{f(pos['target'])}, or at the stop {f(pos['stop'])}."}
            return {"label": "HOLD SELL", "tone": "bear", "text": f"Sold at {f(pos['entry'])}: buy back at target "
                    f"{f(pos['target'])}, or at the stop {f(pos['stop'])}."}
        if pos and pos["status"] in ("target", "stop", "signal_ended"):
            what = {"target": "target reached", "stop": "stop-loss hit", "signal_ended": "the signal ended"}[pos["status"]]
            word = "SELL NOW" if pos["side"] == "BUY" else "BUY BACK NOW"
            return {"label": word, "tone": "warn", "text": f"Exit the {pos['side']} from {f(pos['entry'])}: {what} at "
                    f"{f(pos['exit_price'])} ({pos['pnl_pct']:+.2f}% before costs)."}
        if verdict == "BUY":
            return {"label": "BUY", "tone": "bull", "text": "Both timeframes point up."}
        if verdict == "SELL":
            return {"label": "SELL", "tone": "bear", "text": "Both timeframes point down."}
        return {"label": verdict, "tone": "muted", "text": "No trade right now."}

    async def watchlist_calls(self, symbols: list[str]) -> dict:
        """Intraday call (and position) for every symbol on a watchlist."""
        sem = asyncio.Semaphore(4)

        async def one(sym: str) -> dict:
            inst = await self.instrument(sym)
            if inst is None:
                return {"symbol": sym, "error": "Not found on NSE"}
            try:
                async with sem:
                    return {"symbol": sym, **await self.intraday_card(inst, with_chart=False)}
            except ProviderError as exc:
                if exc.http_status == 401:
                    raise
                return {"symbol": sym, "instrument": inst.model_dump(), "error": exc.message}

        rows = await asyncio.gather(*(one(s) for s in symbols))
        return {"generated_at": now_ist().isoformat(), "source": self.provider.name, "session": session_state(hours=self.hours), "rows": rows}

    # ---- intraday desk cache --------------------------------------------------
    def _entry_bar(self) -> str:
        now = now_ist()
        return live_bar_start(now, 5, self.hours).isoformat() if is_session_open(now, self.hours) else now.date().isoformat()

    def _desk_stale(self, hit: tuple[float, str, dict]) -> bool:
        return time.monotonic() - hit[0] > DESK_FRESH_SECONDS or hit[1] != self._entry_bar()

    async def intraday(self, include_stocks: bool = True, fresh: bool = False) -> dict:
        """Intraday desk, answered at once from cache. A desk older than 20 s, or from before the current
        5-minute bar, is still returned but rebuilt in the background (fresh=True waits for the rebuild)."""
        hit = self._desk.get(include_stocks)
        if hit and not self._desk_stale(hit):
            return hit[2]
        if hit and not fresh:
            task = self._desk_tasks.get(include_stocks)
            if not task or task.done():
                self._desk_tasks[include_stocks] = asyncio.create_task(self._refresh_desk_quietly(include_stocks))
            return hit[2]
        return await self._refresh_desk(include_stocks)

    async def _refresh_desk(self, include_stocks: bool) -> dict:
        async with self._desk_locks.setdefault(include_stocks, asyncio.Lock()):
            hit = self._desk.get(include_stocks)
            if hit and not self._desk_stale(hit):
                return hit[2]  # rebuilt by another caller while this one waited
            started, bar = time.monotonic(), self._entry_bar()
            desk = await self._build_intraday(include_stocks)
            self._desk[include_stocks] = (started, bar, desk)
            if include_stocks:  # the index-only desk is a subset: share it
                self._desk[False] = (started, bar, {k: v for k, v in desk.items() if k != "stocks"})
            return desk

    async def _refresh_desk_quietly(self, include_stocks: bool) -> None:
        try:
            await self._refresh_desk(include_stocks)
        except Exception as exc:
            log.warning("intraday desk refresh failed: %s", exc)

    def cached_card(self, symbol: str) -> dict | None:
        """The index's card from a desk that is still current (used by the option chain)."""
        for include_stocks in (True, False):
            hit = self._desk.get(include_stocks)
            if hit and time.monotonic() - hit[0] <= 60 and hit[1] == self._entry_bar():
                card = next((c for c in hit[2]["indices"] if c.get("instrument", {}).get("symbol") == symbol and "error" not in c), None)
                if card:
                    return card
        return None

    async def desk_warmer(self) -> None:
        """During the session, rebuild the desk a few seconds after every 5-minute candle closes,
        so the first visitor after a new candle doesn't wait for the calculation."""
        while True:
            now = now_ist()
            if is_session_open(now, self.hours):
                try:
                    await self.intraday(include_stocks=True, fresh=True)
                except Exception as exc:
                    log.info("desk warm-up skipped: %s", exc)
                now = now_ist()
                nxt = live_bar_start(now, 5, self.hours) + timedelta(minutes=5, seconds=4)
                await asyncio.sleep(max(5.0, (nxt - now).total_seconds()))
            else:
                await asyncio.sleep(60)

    async def _build_intraday(self, include_stocks: bool = True) -> dict:
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
            "session": session_state(hours=self.hours),
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
