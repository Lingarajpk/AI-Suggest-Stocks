"""Rule-based technical signal (Phase 4).

This is a transparent indicator score, NOT a trained or validated prediction model. It
deliberately returns no outcome probabilities; those come from the horizon-specific
ML models in a later phase, once they have been backtested out-of-sample.
"""

import math
from datetime import datetime, timedelta

import pandas as pd

from app.analysis.indicators import clean, swing_levels
from app.models import Timeframe

MIN_BARS = 60
MIN_RISK_REWARD = 1.0
MAX_HISTORY_AGE = {"1m": timedelta(days=4), "5m": timedelta(days=4), "15m": timedelta(days=4), "1h": timedelta(days=4), "1d": timedelta(days=6)}
WEIGHTS = {"trend": 30, "macd": 20, "rsi": 20, "momentum": 15, "vwap": 15}


def classify(score: float) -> str:
    if score >= 50:
        return "Strong Bullish"
    if score >= 20:
        return "Bullish"
    if score <= -50:
        return "Strong Bearish"
    if score <= -20:
        return "Bearish"
    return "Neutral"


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def evaluate(
    df: pd.DataFrame,
    ind: pd.DataFrame,
    timeframe: Timeframe,
    now: datetime,
    quote_freshness: str | None,
) -> dict:
    no_trade: list[str] = []
    if df.empty:
        return {"status": "no_trade", "signal": None, "score": None, "no_trade_reasons": ["No candle data"],
                "evidence": [], "risks": [], "scenario": None, "levels": {}, "bars": 0}

    bars = len(df)
    last_time = df.index[-1].to_pydatetime()
    if bars < MIN_BARS:
        no_trade.append(f"Only {bars} completed candles (need {MIN_BARS})")
    if now - last_time > MAX_HISTORY_AGE[timeframe]:
        no_trade.append(f"Latest completed candle is from {last_time:%d %b %Y %H:%M}; history looks stale")
    if quote_freshness in ("stale", "unavailable"):
        no_trade.append(f"Current quote is {quote_freshness}")

    row = ind.iloc[-1]
    close = float(df["close"].iloc[-1])
    v = {k: clean(row.get(k)) for k in ind.columns}
    atr = v.get("atr14")

    evidence = []

    def add(factor: str, weight: int, value: float | None, detail: str) -> None:
        if value is None:
            return
        evidence.append({"factor": factor, "weight": weight, "score": round(value, 3), "detail": detail})

    # Trend structure: price and EMA stack
    checks = []
    if v["ema20"] is not None:
        checks.append(_sign(close - v["ema20"]))
    if v["ema20"] is not None and v["ema50"] is not None:
        checks.append(_sign(v["ema20"] - v["ema50"]))
    if v["ema50"] is not None and v["ema200"] is not None:
        checks.append(_sign(v["ema50"] - v["ema200"]))
    if checks:
        parts = [f"close {close:.2f}"] + [f"EMA{n} {v[f'ema{n}']:.2f}" for n in (20, 50, 200) if v[f"ema{n}"] is not None]
        add("Trend (EMA stack)", WEIGHTS["trend"], sum(checks) / len(checks), ", ".join(parts))

    # MACD histogram sign and slope
    if v["macd_hist"] is not None and len(ind) > 1:
        prev = clean(ind["macd_hist"].iloc[-2])
        slope = 0 if prev is None else _sign(v["macd_hist"] - prev)
        add("MACD", WEIGHTS["macd"], 0.6 * _sign(v["macd_hist"]) + 0.4 * slope,
            f"histogram {v['macd_hist']:.3f} ({'rising' if slope > 0 else 'falling' if slope < 0 else 'flat'})")

    # RSI centred on 50, faded at extremes
    if v["rsi14"] is not None:
        r = v["rsi14"]
        s = _clip((r - 50) / 20)
        if r > 75 or r < 25:
            s *= 0.3
        add("RSI(14)", WEIGHTS["rsi"], s, f"RSI {r:.1f}" + (" (extreme, faded)" if r > 75 or r < 25 else ""))

    # Momentum normalised by volatility
    if v["roc10"] is not None and atr:
        atr_pct = atr / close * 100
        add("Momentum (10-bar ROC)", WEIGHTS["momentum"], _clip(v["roc10"] / (atr_pct * math.sqrt(10))),
            f"{v['roc10']:+.2f}% vs ATR {atr_pct:.2f}%")

    # Distance from session VWAP (intraday only)
    if v["vwap"] is not None and atr:
        add("Price vs VWAP", WEIGHTS["vwap"], _clip((close - v["vwap"]) / atr), f"VWAP {v['vwap']:.2f}")

    total_w = sum(e["weight"] for e in evidence)
    score = 100 * sum(e["weight"] * e["score"] for e in evidence) / total_w if total_w else 0.0
    adx_v = v.get("adx14")
    if adx_v is not None and adx_v < 20:
        score *= 0.6
        evidence.append({"factor": "ADX(14)", "weight": 0, "score": 0, "detail": f"ADX {adx_v:.1f} < 20: weak trend, score damped 40%"})
    elif adx_v is not None:
        evidence.append({"factor": "ADX(14)", "weight": 0, "score": 0, "detail": f"ADX {adx_v:.1f}: trend strength adequate"})
    score = round(score, 1)
    signal = classify(score)

    support, resistance = swing_levels(df, close)
    risks = []
    if atr and atr / close * 100 > (3 if timeframe == "1d" else 1.2):
        risks.append(f"High volatility: ATR is {atr / close * 100:.2f}% of price")
    if v["rsi14"] is not None and (v["rsi14"] > 70 or v["rsi14"] < 30):
        risks.append(f"RSI at {v['rsi14']:.1f} — stretched, mean-reversion risk")
    if adx_v is not None and adx_v < 20:
        risks.append("Weak trend (ADX < 20): signals are less reliable in ranging markets")
    rv = v.get("rel_volume")
    if rv is not None and rv < 0.6:
        risks.append(f"Low participation: volume {rv:.2f}x the 20-bar average")
    if atr and resistance and score > 0 and resistance - close < 0.5 * atr:
        risks.append(f"Price is within 0.5 ATR of resistance {resistance:.2f}")
    if atr and support and score < 0 and close - support < 0.5 * atr:
        risks.append(f"Price is within 0.5 ATR of support {support:.2f}")

    if not no_trade and signal == "Neutral":
        no_trade.append("Evidence is weak or mixed (|score| < 20)")

    scenario = None
    if not no_trade and atr:
        d = 1 if score > 0 else -1
        target = close + d * 2.5 * atr
        capped_by = None
        if d > 0 and resistance and close < resistance < target:
            target, capped_by = resistance, "resistance"
        if d < 0 and support and target < support < close:
            target, capped_by = support, "support"
        stop = close - d * 1.5 * atr
        rr = abs(target - close) / abs(close - stop)
        if rr < MIN_RISK_REWARD:
            no_trade.append(f"Risk/reward 1:{rr:.2f} is below 1:{MIN_RISK_REWARD} (target capped by nearby {capped_by or 'level'})")
    if not no_trade and atr:
        scenario = {
            "direction": "long" if d > 0 else "short",
            "entry_low": round(close - 0.25 * atr, 2),
            "entry_high": round(close + 0.25 * atr, 2),
            "target": round(target, 2),
            "stop_loss": round(stop, 2),
            "risk_reward": round(rr, 2),
            "target_capped_by": capped_by,
            "basis": "ATR(14) multiples (stop 1.5x, target 2.5x, capped at nearest swing level). A scenario, not a forecast.",
        }

    return {
        "status": "no_trade" if no_trade else "ok",
        "signal": signal,
        "score": score,
        "no_trade_reasons": no_trade,
        "evidence": evidence,
        "risks": risks,
        "scenario": scenario,
        "levels": {"support": clean(support), "resistance": clean(resistance), "atr": atr},
        "bars": bars,
        "last_candle_time": last_time.isoformat(),
        "method": "rule_based_technical_v1",
    }
