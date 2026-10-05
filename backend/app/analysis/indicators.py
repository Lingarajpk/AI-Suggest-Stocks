"""Technical indicators computed from completed candles (pandas, Wilder smoothing where standard)."""

import math

import numpy as np
import pandas as pd

from app.market_session import IST
from app.models import Candle


def to_frame(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame([c.model_dump() for c in candles])
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(IST)
    return df.set_index("time").sort_index()


def ema(s: pd.Series, n: int) -> pd.Series:
    out = s.ewm(span=n, adjust=False, min_periods=n).mean()
    return out


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_n = atr(df, n)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr_n
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr_n
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), plus_di, minus_di


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP reset each trading day. Only meaningful for intraday timeframes."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    pv = (typical * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum()
    return (pv / vol).where(vol > 0)


def swing_levels(df: pd.DataFrame, last_close: float, window: int = 120, k: int = 3) -> tuple[float | None, float | None]:
    """Nearest swing-low support below and swing-high resistance above the last close."""
    recent = df.tail(window)
    highs, lows = recent["high"].to_numpy(), recent["low"].to_numpy()
    pivots_hi, pivots_lo = [], []
    for i in range(k, len(recent) - k):
        if highs[i] == highs[i - k : i + k + 1].max():
            pivots_hi.append(highs[i])
        if lows[i] == lows[i - k : i + k + 1].min():
            pivots_lo.append(lows[i])
    support = max((p for p in pivots_lo if p < last_close), default=None)
    resistance = min((p for p in pivots_hi if p > last_close), default=None)
    return support, resistance


def compute(df: pd.DataFrame, intraday: bool) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    close = df["close"]
    for n in (9, 20, 50, 200):
        out[f"ema{n}"] = ema(close, n)
    out["rsi14"] = rsi(close)
    macd_line = ema(close, 12) - ema(close, 26)
    out["macd"] = macd_line
    out["macd_signal"] = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    out["atr14"] = atr(df)
    mid = close.rolling(20).mean()
    sd = close.rolling(20).std(ddof=0)
    out["bb_mid"], out["bb_upper"], out["bb_lower"] = mid, mid + 2 * sd, mid - 2 * sd
    out["adx14"], out["plus_di"], out["minus_di"] = adx(df)
    out["vwap"] = session_vwap(df) if intraday else np.nan
    out["rel_volume"] = df["volume"] / df["volume"].shift().rolling(20).mean()
    out["roc10"] = close.pct_change(10) * 100
    return out


def clean(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 4)
