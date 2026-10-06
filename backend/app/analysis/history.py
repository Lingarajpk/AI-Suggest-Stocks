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
HORIZON = {"1m": (15, "15 minutes"), "1d": (5, "5 trading days"), "1h": (7, "about 1 trading day"), "15m": (8, "2 hours")}


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


EXIT_TEXT = {
    "reversal": {"buy": "price turned down (closed below EMA20, MACD negative)", "sell": "price turned up (closed above EMA20, MACD positive)"},
    "target": {"buy": "target reached", "sell": "target reached"},
    "stop": {"buy": "stop-loss hit", "sell": "stop-loss hit"},
    "opposite": {"buy": "opposite SELL signal", "sell": "opposite BUY signal"},
}


def simulate_trades(df: pd.DataFrame, ind: pd.DataFrame, buy: pd.Series, sell: pd.Series) -> tuple[list[dict], dict | None]:
    """Follow each BUY/SELL signal until it exits; return (closed trades, open trade or None).

    Entry at the signal candle's close. From the next candle on, the first rule that applies exits:
      stop    - price touches entry -/+ 1.5 x ATR (filled at the stop)
      target  - price touches entry +/- 2.5 x ATR (filled at the target)
      reversal- close back through EMA20 with the MACD histogram on the other side of zero
      opposite- the opposite signal fires (exit, then the new trade opens on the same close)
    """
    o_close, o_high, o_low = df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    ema20, hist, atr = ind["ema20"].to_numpy(), ind["macd_hist"].to_numpy(), ind["atr14"].to_numpy()
    b, s_ = buy.to_numpy(), sell.to_numpy()
    idx = df.index
    trades: list[dict] = []
    pos: dict | None = None

    def close_pos(i: int, price: float, reason: str) -> None:
        d = 1 if pos["side"] == "buy" else -1
        trades.append({
            **{k: v for k, v in pos.items() if k != "i"},
            "exit_time": idx[i].isoformat(),
            "exit_price": round(float(price), 2),
            "reason": reason,
            "reason_text": EXIT_TEXT[reason][pos["side"]],
            "pnl_pct": round(float(d * (price / pos["entry_price"] - 1) * 100), 2),
            "bars_held": i - pos["i"],
        })

    for i in range(len(df)):
        if pos is not None and i > pos["i"]:
            long = pos["side"] == "buy"
            if long and o_low[i] <= pos["stop"]:
                close_pos(i, pos["stop"], "stop"); pos = None
            elif not long and o_high[i] >= pos["stop"]:
                close_pos(i, pos["stop"], "stop"); pos = None
            elif long and o_high[i] >= pos["target"]:
                close_pos(i, pos["target"], "target"); pos = None
            elif not long and o_low[i] <= pos["target"]:
                close_pos(i, pos["target"], "target"); pos = None
            elif long and o_close[i] < ema20[i] and hist[i] < 0:
                close_pos(i, o_close[i], "reversal"); pos = None
            elif not long and o_close[i] > ema20[i] and hist[i] > 0:
                close_pos(i, o_close[i], "reversal"); pos = None
            elif (long and s_[i]) or (not long and b[i]):
                close_pos(i, o_close[i], "opposite"); pos = None
        if pos is None and (b[i] or s_[i]) and not math.isnan(atr[i]) and atr[i] > 0:
            d = 1 if b[i] else -1
            pos = {
                "i": i,
                "side": "buy" if b[i] else "sell",
                "entry_time": idx[i].isoformat(),
                "entry_price": round(float(o_close[i]), 2),
                "stop": round(float(o_close[i] - d * 1.5 * atr[i]), 2),
                "target": round(float(o_close[i] + d * 2.5 * atr[i]), 2),
            }

    open_trade = None
    if pos is not None:
        last = len(df) - 1
        long = pos["side"] == "buy"
        d = 1 if long else -1
        # "weakening": still open, but momentum is fading against the trade or price slipped past EMA20.
        fading = (hist[last] < hist[last - 1]) if long else (hist[last] > hist[last - 1])
        crossed = (o_close[last] < ema20[last]) if long else (o_close[last] > ema20[last])
        warnings = []
        if crossed:
            warnings.append("price closed " + ("below" if long else "above") + " EMA20")
        if fading:
            warnings.append("MACD momentum " + ("falling" if long else "rising"))
        open_trade = {
            **{k: v for k, v in pos.items() if k != "i"},
            "bars_held": last - pos["i"],
            "last_close": round(float(o_close[last]), 2),
            "pnl_pct": round(float(d * (o_close[last] / pos["entry_price"] - 1) * 100), 2),
            "status": "weakening" if warnings else "holding",
            "warnings": warnings,
        }
    return trades, open_trade


def trade_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"count": 0, "wins": 0, "win_rate": None, "avg_pnl_pct": None, "total_pnl_pct": None}
    pnl = [float(t["pnl_pct"]) for t in trades]
    wins = int(sum(p > 0 for p in pnl))
    return {
        "count": len(trades),
        "wins": wins,
        "win_rate": round(100 * wins / len(trades), 1),
        "avg_pnl_pct": round(sum(pnl) / len(pnl), 2),
        "total_pnl_pct": round(sum(pnl), 2),
    }


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
    trades, open_trade = simulate_trades(df, ind, buy, sell)
    result["trades"] = {
        "buy": trade_stats([t for t in trades if t["side"] == "buy"]),
        "sell": trade_stats([t for t in trades if t["side"] == "sell"]),
        "rules": "Entry at the signal candle close; exit on stop (1.5 ATR), target (2.5 ATR), trend reversal "
                 "(close through EMA20 with MACD flipped) or opposite signal. Before costs.",
    }
    result["open_trade"] = open_trade
    result["latest_exit"] = trades[-1] if trades else None
    if with_markers:
        result["exits"] = trades
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
