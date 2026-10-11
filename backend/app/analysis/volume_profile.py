"""Order-flow proxies built from candles (NSE has no public tick-by-tick history).

Volume profile: each candle's volume is spread evenly over its high-low range and summed into
price bins. POC = the busiest price; the value area is the band around it holding 70% of volume
(VAH/VAL at its edges). Estimated delta treats a candle's volume as buying in proportion to where
it closed in its range: volume x (close - open) / (high - low). CVD is the running sum. These are
approximations of real bid/ask order flow, not the real thing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.indicators import Swing, divergence

VALUE_AREA = 0.70
BINS = 24
DAILY_WINDOW = 60


def profile(high: np.ndarray, low: np.ndarray, volume: np.ndarray, bins: int = BINS) -> dict | None:
    """Volume at price; with no volume at all (an index) every candle weighs the same (time at price)."""
    lo, hi = float(np.min(low)), float(np.max(high))
    if not np.isfinite(lo) or hi <= lo:
        return None
    if volume.sum() <= 0:
        volume = np.ones(len(high))
    edges = np.linspace(lo, hi, bins + 1)
    # Overlap of every candle's [low, high] with every bin, as a share of the candle's range.
    top = np.minimum(high[:, None], edges[None, 1:])
    bot = np.maximum(low[:, None], edges[None, :-1])
    span = np.maximum(high - low, 1e-12)[:, None]
    share = np.clip(top - bot, 0, None) / span
    flat_bar = (high - low) <= 1e-12  # zero-range candle: all volume in its bin
    if flat_bar.any():
        which = np.clip(np.searchsorted(edges, high[flat_bar], side="right") - 1, 0, bins - 1)
        share[flat_bar] = 0
        share[np.where(flat_bar)[0], which] = 1
    vol = (share * volume[:, None]).sum(axis=0)
    poc = int(np.argmax(vol))
    inside, lo_i, hi_i = vol[poc], poc, poc
    target = VALUE_AREA * vol.sum()
    while inside < target and (lo_i > 0 or hi_i < bins - 1):
        below = vol[lo_i - 1] if lo_i > 0 else -1
        above = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if above >= below:
            hi_i += 1
            inside += above
        else:
            lo_i -= 1
            inside += below
    mid = (edges[:-1] + edges[1:]) / 2
    return {"poc": float(mid[poc]), "vah": float(edges[hi_i + 1]), "val": float(edges[lo_i])}


def estimated_delta(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (df["volume"] * (df["close"] - df["open"]) / rng).fillna(0.0)


def levels_series(df: pd.DataFrame, intraday: bool) -> pd.DataFrame:
    """POC/VAH/VAL known at each bar: the previous session's profile (intraday) or the profile of
    the DAILY_WINDOW bars before this one (daily)."""
    out = pd.DataFrame(index=df.index, columns=["poc", "vah", "val"], dtype=float)
    h, l, v = df["high"].to_numpy(float), df["low"].to_numpy(float), df["volume"].to_numpy(float)
    if intraday:
        days = pd.Series(df.index.date, index=df.index)
        prev = None
        for day, idx in days.groupby(days).indices.items():
            if prev is not None:
                out.iloc[idx] = [prev["poc"], prev["vah"], prev["val"]]
            prev = profile(h[idx], l[idx], v[idx]) or prev
    else:
        for i in range(DAILY_WINDOW, len(df)):
            p = profile(h[i - DAILY_WINDOW : i], l[i - DAILY_WINDOW : i], v[i - DAILY_WINDOW : i])
            if p:
                out.iloc[i] = [p["poc"], p["vah"], p["val"]]
    return out


def value_area_position(close: pd.Series, levels: pd.DataFrame, atr: pd.Series) -> pd.Series:
    """Where price sits versus value: +1 accepted above VAH, -1 below VAL, scaled around POC inside."""
    width = np.maximum((levels["vah"] - levels["val"]).astype(float), 0.5 * atr)
    return ((close - levels["poc"].astype(float)) / width).clip(-1, 1).fillna(0.0)


def current(df: pd.DataFrame, intraday: bool, swings: list[Swing]) -> dict:
    """Today's (intraday) or the last DAILY_WINDOW bars' profile, plus delta/CVD reads."""
    if intraday:
        today = df.index[-1].date()
        part = df[df.index.date == today]
        label = "today's session"
    else:
        part = df.tail(DAILY_WINDOW)
        label = f"last {len(part)} days"
    p = profile(part["high"].to_numpy(float), part["low"].to_numpy(float), part["volume"].to_numpy(float))
    delta = estimated_delta(df)
    cvd = delta.cumsum()
    div = divergence(swings, cvd.to_numpy(float), len(df))
    nz = np.nonzero(div)[0]
    last_div = None
    if len(nz):
        i = int(nz[-1])
        last_div = {"time": df.index[i].isoformat(), "direction": "up" if div[i] > 0 else "down", "bars_ago": len(df) - 1 - i}
    recent_delta = float(delta.tail(20).sum())
    vol20 = float(df["volume"].tail(20).sum()) or 1.0
    close = float(df["close"].iloc[-1])
    where = None
    if p:
        where = "above value (VAH)" if close > p["vah"] else "below value (VAL)" if close < p["val"] else "inside the value area"
    return {
        "window": label,
        "poc": round(p["poc"], 2) if p else None,
        "vah": round(p["vah"], 2) if p else None,
        "val": round(p["val"], 2) if p else None,
        "price_vs_value": where,
        "delta_20_pct": round(100 * recent_delta / vol20, 1),
        "cvd_divergence": last_div,
        "note": ("Index has no volume: profile shows time spent at each price; delta/CVD not available."
                 if float(df["volume"].sum()) <= 0 else
                 "Estimated from candles (no tick data): volume profile by price, delta from where each candle closed."),
    }


def cvd_divergence_series(df: pd.DataFrame, swings: list[Swing]) -> np.ndarray:
    return divergence(swings, estimated_delta(df).cumsum().to_numpy(float), len(df))
