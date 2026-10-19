"""Forex desk and pair analysis: the stock engine (indicators, signals, patterns, strategies, outlook model)
run on NSE currency futures, with their own session (09:00-17:00), contracts, models and caches."""

import asyncio
import contextlib
import logging
import time

from app import options as opt
from app.analysis.costs import CURRENCY_COST_NOTE, charges, round_trip_pct
from app.forex import PAIR_INFO, PAIRS
from app.forex.greeks import add_greeks
from app.market_session import CURRENCY, now_ist, session_state
from app.models import Instrument, Timeframe
from app.providers.base import ProviderError, drop_incomplete
from app.service import ENTRY_BUY, ENTRY_SELL, ENTRY_TF, RULE_LEVEL, TREND_BUY, TREND_SELL, TREND_TF, MarketService, _norm

log = logging.getLogger(__name__)

FX_TICK = 0.0025
CHAIN_TTL = 5.0  # seconds an option chain is reused


def lot_costs(price: float, lot_units: float) -> dict:
    """What a round trip costs per lot, at 1 lot and at 10 lots (brokerage is per order, so it shrinks per lot),
    and how many 0.0025 ticks the price must move to cover it."""
    lot_value = price * lot_units
    one = charges("currency", lot_value, lot_value)
    ten = charges("currency", 10 * lot_value, 10 * lot_value) / 10
    tick_value = FX_TICK * lot_units
    return {
        "lot_value": round(lot_value, 2),
        "tick_value": round(tick_value, 2),
        "per_lot_1": round(one, 2),
        "per_lot_10": round(ten, 2),
        "breakeven_ticks_1": round(one / tick_value, 1),
        "breakeven_ticks_10": round(ten / tick_value, 1),
    }


class ForexService(MarketService):
    hours = CURRENCY
    price_dp = 4
    cost_note = CURRENCY_COST_NOTE

    async def universe(self) -> tuple[list[Instrument], list[str]]:
        """The front-month future of each pair, re-resolved daily (so it rolls to the next month on its own)."""
        today = now_ist().date()
        if not self._universe or self._universe[0] != today:
            resolved, missing = await self.provider.resolve_currencies(list(PAIRS))
            self._universe = (today, resolved, missing)
        return self._universe[1], self._universe[2]

    async def indices(self) -> tuple[list[Instrument], list[str]]:
        return [], []

    async def instrument(self, symbol: str) -> Instrument | None:
        """USDINR, usd-inr, USD/INR ... -> the pair's front-month future."""
        want = _norm(symbol)
        pairs, _ = await self.universe()
        return next((i for i in pairs if i.symbol == want), None)

    async def search(self, query: str, limit: int = 12) -> list[dict]:
        q = _norm(query)
        pairs, _ = await self.universe()
        return [{"symbol": i.symbol, "name": PAIR_INFO[i.symbol]["currency"], "exchange": "NSE", "kind": "currency"}
                for i in pairs if q and (q in i.symbol or q in _norm(PAIR_INFO[i.symbol]["currency"]))][:limit]

    def cached_card(self, symbol: str) -> dict | None:
        for include in (True, False):
            hit = self._desk.get(include)
            if hit and time.monotonic() - hit[0] <= 60 and hit[1] == self._entry_bar():
                card = next((c for c in hit[2]["pairs"] if c.get("instrument", {}).get("symbol") == symbol and "error" not in c), None)
                if card:
                    return card
        return None

    async def intraday_card(self, inst: Instrument, with_chart: bool) -> dict:
        card = await super().intraday_card(inst, with_chart)
        price = card.get("price")
        card["pair"] = PAIR_INFO.get(inst.symbol)
        card["costs"] = lot_costs(price, inst.lot_units or 1000) if price else None
        return card

    async def _build_intraday(self, include_stocks: bool = False) -> dict:
        pairs, missing = await self.universe()
        cards = []
        for inst in pairs:
            try:
                cards.append(await self.intraday_card(inst, with_chart=True))
            except ProviderError as exc:
                if exc.http_status == 401:
                    raise
                cards.append({"instrument": inst.model_dump(), "error": exc.message})
        return {
            "generated_at": now_ist().isoformat(),
            "source": self.provider.name,
            "session": session_state(hours=self.hours),
            "market_hours": "09:00–17:00 IST",
            "entry_timeframe": ENTRY_TF,
            "trend_timeframe": TREND_TF,
            "pairs": cards,
            "unavailable": missing,
            "backtest_cost_pct": round_trip_pct("currency"),
            "method": f"Same rules as the stock desk, on the front-month NSE currency future: BUY when the 5-minute outlook "
                      f"is at least {ENTRY_BUY:.0f}% up and the 15-minute outlook at least {TREND_BUY:.0f}% up (SELL: at most "
                      f"{ENTRY_SELL:.0f}% and {TREND_SELL:.0f}%). The model is trained on the four currency pairs only; if it "
                      f"has not beaten guessing on unseen data, the rule score (+/-{RULE_LEVEL}) is used instead. Otherwise WAIT. "
                      "Levels from the 5-minute ATR. The contract switches to the next month two days before expiry.",
        }

    async def detail(self, symbol: str, timeframe: Timeframe) -> dict:
        inst = await self.instrument(symbol)
        if inst is None:
            raise ProviderError("not_found", f"{symbol.upper()} is not one of the forex pairs ({', '.join(PAIRS)})", 404)
        with contextlib.suppress(ProviderError):
            await self.refresh_quotes()
        q = self.cached_quote(inst.instrument_key)
        analysis = await self.analyse(inst, timeframe, with_series=True)
        outlook = analysis.pop("outlook", None) or {"status": "not_available", "message": "No candle data."}
        price = q.last_price if q else analysis.get("last_close")
        return {
            "instrument": inst.model_dump(),
            "pair": PAIR_INFO.get(inst.symbol),
            "timeframe": timeframe,
            "source": self.provider.name,
            "session": session_state(hours=self.hours),
            "quote": q.model_dump(mode="json") if q else None,
            "generated_at": now_ist().isoformat(),
            "costs": lot_costs(price, inst.lot_units or 1000) if price else None,
            "model_outlook": outlook,
            **analysis,
        }

    async def quotes_payload(self) -> dict:
        pairs, missing = await self.universe()
        q = await self.refresh_quotes()
        return {
            "source": self.provider.name,
            "session": session_state(hours=self.hours),
            "server_time": now_ist().isoformat(),
            "quotes": [q[i.instrument_key].model_dump(mode="json") for i in pairs if i.instrument_key in q],
            "contracts": {i.symbol: {"contract": i.contract, "expiry": i.expiry, "lot_units": i.lot_units} for i in pairs},
            "unavailable": missing,
        }

    async def _spot(self, inst: Instrument) -> float | None:
        with contextlib.suppress(ProviderError):
            await self.refresh_quotes()
        q = self.cached_quote(inst.instrument_key)
        if q:
            return q.last_price
        candles = drop_incomplete(await self.provider.candles(inst, "5m", include_partial=True), "5m", now_ist(), self.hours)
        return candles[-1].close if candles else None

    async def option_chain(self, symbol: str, expiry: str | None = None) -> dict:
        """Currency option chain around the front-month future, with greeks and which option to buy now."""
        inst = await self.instrument(symbol)
        if inst is None:
            raise ProviderError("no_options", f"{symbol} is not one of the forex pairs", 404)
        spot = await self._spot(inst)
        if spot is None:
            raise ProviderError("no_options", f"No {inst.symbol} price yet to centre the option chain on", 404)
        key = (inst.symbol, expiry or "")
        hit = self._opt_chains.get(key)
        if not hit or time.monotonic() - hit[0] > CHAIN_TTL:
            hit = (time.monotonic(), await self.provider.currency_option_chain(inst.symbol, expiry, spot))
            self._opt_chains[key] = hit
        expiries, raw = hit[1]
        if not raw:
            raise ProviderError("no_options", f"No {inst.symbol} options are listed right now", 404)
        raw = add_greeks([{**r, "call_options": _copy(r.get("call_options")), "put_options": _copy(r.get("put_options"))}
                          for r in raw], spot, now_ist())
        rows, _ = opt.parse_chain(raw)
        lot = int(inst.lot_units or 1000)
        try:
            card = self.cached_card(inst.symbol) or await self.intraday_card(inst, with_chart=False)
        except ProviderError:
            raise
        except Exception:  # the chain is still useful without the call
            log.exception("forex call failed for %s", inst.symbol)
            card = None
        summary = opt.summarize(rows, spot)
        chosen = raw[0].get("expiry")
        return {
            "symbol": inst.symbol,
            "instrument": inst.model_dump(),
            "source": self.provider.name,
            "generated_at": now_ist().isoformat(),
            "expiry": chosen,
            "expiries": expiries[:8],
            "lot_size": lot,
            "tick": FX_TICK,
            "spot": spot,
            "spot_label": f"{inst.contract} (future)",
            "summary": summary,
            "recommendation": opt.recommend(rows, spot, card, summary, lot, tick=FX_TICK),
            "index_call": {k: (card or {}).get(k) for k in ("verdict", "reason", "levels", "atr", "since")} if card else None,
            "greeks_note": "Implied volatility and greeks are estimated from the premium (Black-76 on the future); "
                           "Upstox does not publish them for currency options.",
            "rows": opt.window(rows, spot),
        }

    async def warm(self) -> None:
        """Train the forex models and stats up front, then keep the desk fresh after every 5-minute candle."""
        for tf in ("5m", "15m", "1d"):
            try:
                await self.outlook_model(tf)
                await self.pattern_stats(tf)
                await self.strategy_stats(tf)
            except Exception as exc:  # e.g. Upstox not logged in yet
                log.info("forex warm-up for %s skipped: %s", tf, exc)
            await asyncio.sleep(0)


def _copy(leg: dict | None) -> dict | None:
    return None if leg is None else {**leg, "option_greeks": dict(leg.get("option_greeks") or {})}

