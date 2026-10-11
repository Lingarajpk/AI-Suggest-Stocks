"""Technical indicators computed from completed candles (pandas, Wilder smoothing where standard)."""

import math
from dataclasses import dataclass

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


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


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


def session_weights(df: pd.DataFrame) -> pd.Series:
    """Volume per bar; for a session with no volume at all (an index) every bar counts equally,
    so 'VWAP' becomes the session's average price."""
    vol = df["volume"].astype(float)
    no_volume = vol.groupby(df.index.date).transform("sum") <= 0
    return vol.where(~no_volume, 1.0)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP reset each trading day. Only meaningful for intraday timeframes."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    w = session_weights(df)
    pv = (typical * w).groupby(day).cumsum()
    vol = w.groupby(day).cumsum()
    return (pv / vol).where(vol > 0)


def vwap_bands(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Session VWAP and its volume-weighted standard deviation (for the +/-1 and +/-2 sigma bands)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    w = session_weights(df)
    vol = w.groupby(day).cumsum()
    pv = (typical * w).groupby(day).cumsum()
    pv2 = (typical * typical * w).groupby(day).cumsum()
    vwap = (pv / vol).where(vol > 0)
    var = (pv2 / vol - vwap * vwap).clip(lower=0)
    return vwap, np.sqrt(var).where(vol > 0)


def anchored_vwap(df: pd.DataFrame, anchor: np.ndarray) -> pd.Series:
    """VWAP measured from a per-bar anchor bar index (-1 = no anchor yet)."""
    typical = ((df["high"] + df["low"] + df["close"]) / 3).to_numpy(float)
    v = df["volume"].to_numpy(float)
    if v.sum() <= 0:  # index: no volume, every bar weighs the same
        v = np.ones(len(df))
    cpv = np.concatenate([[0.0], np.cumsum(typical * v)])
    cv = np.concatenate([[0.0], np.cumsum(v)])
    idx = np.arange(len(df))
    a = np.asarray(anchor, dtype=int)
    ok = a >= 0
    a0 = np.where(ok, a, 0)
    num = cpv[idx + 1] - cpv[a0]
    den = cv[idx + 1] - cv[a0]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(ok & (den > 0), num / den, np.nan)
    return pd.Series(out, index=df.index)


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> tuple[pd.Series, pd.Series]:
    """Supertrend line and direction (+1 up / -1 down). A flip is where the direction changes."""
    a = atr(df, n).to_numpy(float)
    h, l, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    hl2 = (h + l) / 2
    size = len(df)
    line = np.full(size, np.nan)
    direction = np.zeros(size)
    fu = fl = np.nan
    d = 0
    for i in range(size):
        if not np.isfinite(a[i]):
            continue
        ub, lb = hl2[i] + mult * a[i], hl2[i] - mult * a[i]
        if d == 0:
            fu, fl, d = ub, lb, 1 if c[i] >= hl2[i] else -1
        else:
            fu = ub if (ub < fu or c[i - 1] > fu) else fu
            fl = lb if (lb > fl or c[i - 1] < fl) else fl
            if d == 1 and c[i] < fl:
                d = -1
            elif d == -1 and c[i] > fu:
                d = 1
        direction[i] = d
        line[i] = fl if d == 1 else fu
    return pd.Series(line, index=df.index), pd.Series(direction, index=df.index)


@dataclass(frozen=True)
class Swing:
    idx: int
    price: float
    kind: str  # "H" or "L"
    confirm: int  # first bar whose close knows this swing (idx + right)


def confirmed_pivots(df: pd.DataFrame, atr_s: pd.Series, left: int = 3, right: int = 3, atr_mult: float = 0.5) -> list[Swing]:
    """Swing highs/lows that are the extreme of `left` bars before and `right` bars after, and stand
    out by at least atr_mult x ATR. A swing is only usable from bar idx + right on (no look-ahead)."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    a = atr_s.to_numpy(float)
    out: list[Swing] = []
    for i in range(left, len(df) - right):
        lo, hi = i - left, i + right + 1
        th = atr_mult * a[i] if np.isfinite(a[i]) else 0.0
        if h[i] >= h[lo:i].max() and h[i] > h[i + 1 : hi].max() and h[i] - l[lo:hi].min() >= th:
            out.append(Swing(i, float(h[i]), "H", i + right))
        if l[i] <= l[lo:i].min() and l[i] < l[i + 1 : hi].min() and h[lo:hi].max() - l[i] >= th:
            out.append(Swing(i, float(l[i]), "L", i + right))
    return out


def divergence(price_swings: list[Swing], osc: np.ndarray, size: int, max_gap: int = 60) -> np.ndarray:
    """Regular divergence between consecutive confirmed swings, marked on the confirming bar:
    +1 = lower price low with a higher oscillator low, -1 = higher price high with a lower oscillator high."""
    out = np.zeros(size)
    for kind, sign in (("L", 1), ("H", -1)):
        sw = [s for s in price_swings if s.kind == kind]
        for a, b in zip(sw, sw[1:]):
            if b.idx - a.idx > max_gap or b.confirm >= size:
                continue
            oa, ob = osc[a.idx], osc[b.idx]
            if not (np.isfinite(oa) and np.isfinite(ob)):
                continue
            if sign == 1 and b.price < a.price and ob > oa:
                out[b.confirm] = 1
            elif sign == -1 and b.price > a.price and ob < oa:
                out[b.confirm] = -1
    return out


def macd_events(ind: pd.DataFrame, swings: list[Swing]) -> pd.DataFrame:
    """MACD signal-line crosses, zero-line crosses and divergence on confirmed swings (+1 bullish / -1 bearish)."""
    m, sgl = ind["macd"], ind["macd_signal"]
    diff = m - sgl
    cross = np.sign(diff).diff().fillna(0) / 2
    zero = np.sign(m).diff().fillna(0) / 2
    out = pd.DataFrame(index=ind.index)
    out["macd_cross"] = cross.where(diff.notna() & diff.shift().notna(), 0.0)
    out["macd_zero"] = zero.where(m.notna() & m.shift().notna(), 0.0)
    out["macd_div"] = divergence(swings, m.to_numpy(float), len(ind))
    return out


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
    out["sma20"] = sma(close, 20)
    out["st_line"], out["st_dir"] = supertrend(df)
    if intraday:
        vw, sd = vwap_bands(df)
        out["vwap"] = vw
        for k in (1, 2):
            out[f"vwap_u{k}"], out[f"vwap_l{k}"] = vw + k * sd, vw - k * sd
    else:
        out["vwap"] = np.nan
        for k in (1, 2):
            out[f"vwap_u{k}"] = out[f"vwap_l{k}"] = np.nan
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
