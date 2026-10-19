"""Estimated round-trip costs for backtests: Upstox charges plus slippage, as a % of the position.

Backtests work in % returns, so every trade is costed as one round trip on a fixed position of
NOTIONAL rupees (brokerage is a flat Rs 20 cap, so the % cost depends on the position size).
Rates mirror frontend/src/lib/charges.ts - keep the two in sync. Check
https://upstox.com/brokerage-charges/ when rates change.
"""

from typing import Literal

from app.models import Timeframe, is_intraday

Product = Literal["intraday", "delivery", "futures"]

NOTIONAL = 100_000.0  # rupees per backtested trade

RATES: dict[str, dict[str, float]] = {
    # brokerage: pct of order value, capped at Rs 20 per order
    "intraday": {"brokerage_pct": 0.001, "brokerage_cap": 20, "stt_sell_pct": 0.00025, "stt_buy_pct": 0.0,
                 "stamp_buy_pct": 0.00003, "exchange_pct": 0.0000297, "dp": 0.0},
    "delivery": {"brokerage_pct": 0.025, "brokerage_cap": 20, "stt_sell_pct": 0.001, "stt_buy_pct": 0.001,
                 "stamp_buy_pct": 0.00015, "exchange_pct": 0.0000297, "dp": 18.5},
    # Index trades go through F&O: futures rates (NSE futures transaction charge, STT 0.02% on the sell side).
    "futures": {"brokerage_pct": 1.0, "brokerage_cap": 20, "stt_sell_pct": 0.0002, "stt_buy_pct": 0.0,
                "stamp_buy_pct": 0.00002, "exchange_pct": 0.0000173, "dp": 0.0},
}
SEBI_PCT = 0.000001  # Rs 10 per crore
GST_PCT = 0.18  # on brokerage + exchange + SEBI (+ DP)
# Price slippage per side (entry and exit each), as a fraction of the price.
SLIPPAGE_PER_SIDE = {"intraday": 0.0003, "delivery": 0.0005, "futures": 0.0001}


def product_for(timeframe: Timeframe, kind: str = "equity") -> Product:
    """How the backtested trade would be placed: index -> futures, equity -> intraday or delivery."""
    if kind == "index":
        return "futures"
    return "intraday" if is_intraday(timeframe) else "delivery"


def charges(product: Product, buy_value: float, sell_value: float) -> float:
    """Upstox charges in rupees for one round trip."""
    r = RATES[product]
    order = lambda v: min(r["brokerage_cap"], v * r["brokerage_pct"]) if v > 0 else 0.0  # noqa: E731
    brokerage = order(buy_value) + order(sell_value)
    turnover = buy_value + sell_value
    stt = sell_value * r["stt_sell_pct"] + buy_value * r["stt_buy_pct"]
    exchange = turnover * r["exchange_pct"]
    sebi = turnover * SEBI_PCT
    stamp = buy_value * r["stamp_buy_pct"]
    gst = (brokerage + exchange + sebi + r["dp"]) * GST_PCT
    return brokerage + stt + exchange + sebi + stamp + r["dp"] + gst


def round_trip_pct(product: Product, notional: float = NOTIONAL) -> float:
    """Charges plus slippage for one round trip, in % of the position (e.g. 0.14 = 0.14%)."""
    fees = charges(product, notional, notional) / notional
    return round(100 * (fees + 2 * SLIPPAGE_PER_SIDE[product]), 4)


def cost_for(timeframe: Timeframe, kind: str = "equity") -> float:
    return round_trip_pct(product_for(timeframe, kind))


COST_NOTE = "after estimated Upstox charges and slippage (₹1 lakh per trade)"
