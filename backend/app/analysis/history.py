"""Historical buy/sell signal points and their track record.

The same rule-based score used for the live signal is computed for every past bar.
A BUY point is where the score crosses up into bullish (>= +20); a SELL point is where
it crosses down into bearish (<= -20). Each point is then checked against what the
price actually did over the next few bars, and compared with the base rate (how often
price rose over the same horizon on any bar). These are in-sample statistics on the
displayed history, before brokerage, taxes and slippage.
"""

import math

import numpy as np
import pandas as pd

from app.analysis.signals import MIN_BARS, WEIGHTS
from app.models import Timeframe

THRESHOLD = 20
# Forward horizon in bars used to judge each signal point.
HORIZON = {"1d": (5, "5 trading days"), "1h": (7, "about 1 trading day"), "15m": (8, "2 hours")}


def score_series(df: pd.DataFrame, ind: pd.DataFrame) -> pd.Series:
    """Vectorised version of signals.evaluate's score for every bar."""
    close = df["close"]
    trend = pd.concat(
        [np.sign(close - ind["ema20"]), np.sign(ind["ema20"] - ind["ema50"]), np.sign(ind["ema50"] - ind["ema200"])],
        axis=1,
    ).mean(axis=1, skipna=True)
    hist = ind["macd_hist"]
    macd = 0.6 * np.sign(hist) + 0.4 * np.sign(hist.diff()).fillna(0)
    macd = macd.where(hist.notna())
    rsi = ind["rsi14"]
    rsi_c = ((rsi - 50) / 20).clip(-1, 1)
    rsi_c = rsi_c.where(~((rsi > 75) | (rsi < 25)), rsi_c * 0.3)
    atr = ind["atr14"]
    atr_pct = atr / close * 100
    mom = (ind["roc10"] / (atr_pct * math.sqrt(10))).clip(-1, 1).where(atr > 0)
    vwap = ((close - ind["vwap"]) / atr).clip(-1, 1).where(atr > 0)

    comps = {"trend": trend, "macd": macd, "rsi": rsi_c, "momentum": mom, "vwap": vwap}
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    for name, s in comps.items():
        ok = s.notna()
        num += s.fillna(0) * WEIGHTS[name]
        den += ok * WEIGHTS[name]
    score = (100 * num / den).where(den > 0)
    score = score.where(~(ind["adx14"] < 20), score * 0.6)
    return score.round(1)


def signal_history(df: pd.DataFrame, ind: pd.DataFrame, timeframe: Timeframe, with_markers: bool) -> dict:
    score = score_series(df, ind)
    valid = pd.Series(np.arange(len(df)) >= MIN_BARS, index=df.index) & score.notna()
    prev = score.shift()
    buy = valid & (score >= THRESHOLD) & (prev < THRESHOLD)
    sell = valid & (score <= -THRESHOLD) & (prev > -THRESHOLD)

    h, h_label = HORIZON[timeframe]
    close = df["close"]
    fwd = (close.shift(-h) / close - 1) * 100  # NaN for the last h bars: outcome not known yet

    def stats(mask: pd.Series, direction: int) -> dict:
        r = fwd[mask].dropna()
        if r.empty:
            return {"count": 0, "wins": 0, "win_rate": None, "avg_return_pct": None}
        wins = int((r * direction > 0).sum())
        return {
            "count": int(len(r)),
            "wins": wins,
            "win_rate": round(100 * wins / len(r), 1),
            "avg_return_pct": round(float(r.mean()), 2),
        }

    base = fwd[valid].dropna()
    result = {
        "horizon_bars": h,
        "horizon_label": h_label,
        "buy": stats(buy, 1),
        "sell": stats(sell, -1),
        "base_rate_up_pct": round(100 * float((base > 0).mean()), 1) if len(base) else None,
        "period_start": df.index[MIN_BARS].isoformat() if len(df) > MIN_BARS else None,
        "period_end": df.index[-1].isoformat(),
        "notes": "In-sample, before brokerage, taxes and slippage. Past hit rates do not guarantee future results.",
    }
    events = df.index[buy | sell]
    last = events[-1] if len(events) else None
    result["latest_marker"] = (
        {"time": last.isoformat(), "side": "buy" if buy[last] else "sell", "price": round(float(close[last]), 2), "score": float(score[last])}
        if last is not None
        else None
    )
    if with_markers:
        markers = []
        for t in events:
            side = "buy" if buy[t] else "sell"
            outcome = None
            if not math.isnan(fwd[t]):
                outcome = "win" if (fwd[t] > 0) == (side == "buy") else "loss"
            markers.append({
                "time": t.isoformat(),
                "side": side,
                "price": round(float(close[t]), 2),
                "score": float(score[t]),
                "forward_return_pct": None if math.isnan(fwd[t]) else round(float(fwd[t]), 2),
                "outcome": outcome,
            })
        result["markers"] = markers
    return result
