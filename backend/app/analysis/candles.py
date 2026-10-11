"""Candlestick patterns, each judged at the candle's close (no look-ahead).

Every detector returns a series of +1 (bullish read), -1 (bearish read) or 0. Sizes are measured
against ATR so a "long body" means the same thing on a ₹100 and a ₹3,000 stock. Reversal candles
(hammer, shooting star, engulfing, stars, doji) only count after a move in the opposite direction,
as the classic definitions require; an inside bar is read in the direction of the trend.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# key -> (name, bias, description)
CATALOG: dict[str, tuple[str, str, str]] = {
    "doji": ("Doji", "either", "Open ≈ close after a move: indecision, a possible turn against the prior move."),
    "hammer": ("Hammer", "bullish", "Long lower wick (≥ 2× body), small upper wick, after a decline."),
    "shooting_star": ("Shooting Star", "bearish", "Long upper wick (≥ 2× body), small lower wick, after a rise."),
    "engulfing_bull": ("Bullish Engulfing", "bullish", "Green body fully covers the prior red body, after a decline."),
    "engulfing_bear": ("Bearish Engulfing", "bearish", "Red body fully covers the prior green body, after a rise."),
    "morning_star": ("Morning Star", "bullish", "Long red, small-body pause, then a green close above the first body's middle."),
    "evening_star": ("Evening Star", "bearish", "Long green, small-body pause, then a red close below the first body's middle."),
    "inside_bar": ("Inside Bar", "either", "Range inside the prior candle's range: a pause, read in the trend's direction."),
}


def detect_series(df: pd.DataFrame, ind: pd.DataFrame) -> dict[str, pd.Series]:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    a = ind["atr14"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    green, red = c > o, c < o
    ema = ind["ema20"]
    # Prior move, measured on the bar before the pattern starts.
    def down_before(k: int) -> pd.Series:
        return (c.shift(k) < ema.shift(k)) & (c.shift(k) < c.shift(k + 5))

    def up_before(k: int) -> pd.Series:
        return (c.shift(k) > ema.shift(k)) & (c.shift(k) > c.shift(k + 5))

    sized = rng >= 0.5 * a
    z = pd.Series(0.0, index=df.index)
    out: dict[str, pd.Series] = {}

    # A tiny-bodied hammer (dragonfly) or shooting star (gravestone) counts as those, not as a doji.
    hammer = sized & (lower >= 2 * body) & (lower >= 0.6 * rng) & (upper <= 0.15 * rng)
    out["hammer"] = z.mask(hammer & down_before(1), 1.0)
    star = sized & (upper >= 2 * body) & (upper >= 0.6 * rng) & (lower <= 0.15 * rng)
    out["shooting_star"] = z.mask(star & up_before(1), -1.0)

    doji = (body <= 0.1 * rng) & sized & ~hammer & ~star
    out["doji"] = z.mask(doji & up_before(1), -1.0).mask(doji & down_before(1), 1.0)

    pb = body.shift()
    eng_bull = green & red.shift(fill_value=False) & (o <= c.shift()) & (c >= o.shift()) & (body > pb) & (body >= 0.3 * a)
    eng_bear = red & green.shift(fill_value=False) & (o >= c.shift()) & (c <= o.shift()) & (body > pb) & (body >= 0.3 * a)
    out["engulfing_bull"] = z.mask(eng_bull & down_before(2), 1.0)
    out["engulfing_bear"] = z.mask(eng_bear & up_before(2), -1.0)

    b2, o2, c2 = body.shift(2), o.shift(2), c.shift(2)
    long2 = b2 >= 0.6 * a.shift(2)
    small1 = body.shift(1) <= 0.35 * b2
    mid2 = (o2 + c2) / 2
    morning = long2 & red.shift(2, fill_value=False) & small1 & green & (c > mid2)
    evening = long2 & green.shift(2, fill_value=False) & small1 & red & (c < mid2)
    out["morning_star"] = z.mask(morning & down_before(3), 1.0)
    out["evening_star"] = z.mask(evening & up_before(3), -1.0)

    inside = (h < h.shift()) & (l > l.shift())
    trend = np.sign(ema - ema.shift(5)).fillna(0.0)
    out["inside_bar"] = (inside.astype(float) * trend).fillna(0.0)
    return {k: v.fillna(0.0) for k, v in out.items()}


def net_signal(series: dict[str, pd.Series], decay_bars: int = 5) -> pd.Series:
    """All candlestick reads combined into one value in [-1, 1], fading over decay_bars."""
    total = sum(series.values())
    return decayed(total.clip(-1, 1), decay_bars)


def decayed(sig: pd.Series, span: int) -> pd.Series:
    """Hold each non-zero signal and fade it linearly to zero over `span` bars."""
    v = sig.to_numpy(float)
    out = np.zeros(len(v))
    last_val, age = 0.0, span
    for i, x in enumerate(v):
        if x != 0:
            last_val, age = x, 0
        else:
            age += 1
        out[i] = last_val * max(0.0, 1 - age / span) if age < span else 0.0
    return pd.Series(out, index=sig.index)


def recent(df: pd.DataFrame, series: dict[str, pd.Series], bars: int = 60) -> list[dict]:
    """Candlestick hits in the last `bars` candles, for chart markers and the panel."""
    start = max(0, len(df) - bars)
    out = []
    for k, s in series.items():
        hits = s.iloc[start:]
        for t, v in hits[hits != 0].items():
            name, bias, _ = CATALOG[k]
            out.append({"key": k, "name": name, "time": t.isoformat(), "direction": "up" if v > 0 else "down",
                        "price": round(float(df.at[t, "close"]), 2)})
    out.sort(key=lambda x: x["time"])
    return out
