"""What kind of market a chart is in, bar by bar (every value known at the bar's close).

  efficiency ratio  Kaufman: net move / total path over 20 bars. 1 = straight line, 0 = pure chop.
  VHF               Vertical Horizontal Filter (28): range of closes / sum of absolute changes.
  trend quality     R-squared of a 20-bar linear fit, signed by its slope.
  ATR percentile    Today's ATR (as % of price) against its own last 250 bars.

  label             trend   ADX >= 25 or efficiency >= 0.4, with R-squared >= 0.5 (direction from the slope)
                    range   ADX < 20 and efficiency < 0.25
                    volatile ATR in its top 20% and not trending
                    mixed   anything else

  higher timeframe  The trend of the next timeframe up (hourly bars for intraday charts, weekly for daily),
                    taken from the last COMPLETED higher-timeframe bar so it never peeks ahead.

Labels describe; they don't change any signal. The strategy stats are split by the label at entry, and the
numeric versions are offered to the outlook model, which keeps them only if they help on unseen data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ER_N, VHF_N, LR_N, ATR_RANK_N = 20, 28, 20, 250
LABELS = {"trend_up": "Trending up", "trend_down": "Trending down", "range": "Range-bound", "volatile": "Volatile", "mixed": "Mixed"}


def efficiency_ratio(close: pd.Series, n: int = ER_N) -> pd.Series:
    path = close.diff().abs().rolling(n).sum()
    return ((close - close.shift(n)).abs() / path.replace(0, np.nan)).clip(0, 1)


def vhf(close: pd.Series, n: int = VHF_N) -> pd.Series:
    path = close.diff().abs().rolling(n).sum()
    return (close.rolling(n + 1).max() - close.rolling(n + 1).min()) / path.replace(0, np.nan)


def trend_fit(close: pd.Series, n: int = LR_N) -> tuple[pd.Series, pd.Series]:
    """(R-squared, slope sign) of a rolling linear fit of the close on time."""
    x = pd.Series(np.arange(len(close), dtype=float), index=close.index)
    r = x.rolling(n).corr(close)
    return (r * r).clip(0, 1), np.sign(r).fillna(0.0)


def atr_rank(df: pd.DataFrame, atr: pd.Series, n: int = ATR_RANK_N) -> pd.Series:
    """Share of the previous `n` bars whose ATR% was below this bar's (0-1)."""
    a = (atr / df["close"]).to_numpy(float)
    out = np.full(len(a), np.nan)
    for i in range(50, len(a)):
        w = a[max(0, i - n):i]
        w = w[np.isfinite(w)]
        if len(w) >= 50 and np.isfinite(a[i]):
            out[i] = float((w < a[i]).mean())
    return pd.Series(out, index=df.index)


def series(df: pd.DataFrame, ind: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    out = pd.DataFrame(index=df.index)
    out["er"] = efficiency_ratio(close)
    out["vhf"] = vhf(close)
    out["r2"], out["slope"] = trend_fit(close)
    out["atr_rank"] = atr_rank(df, ind["atr14"])
    adx = ind["adx14"]
    trending = ((adx >= 25) | (out["er"] >= 0.4)) & (out["r2"] >= 0.5)
    ranging = (adx < 20) & (out["er"] < 0.25)
    volatile = out["atr_rank"] >= 0.8
    label = np.where(trending & (out["slope"] > 0), "trend_up",
                     np.where(trending & (out["slope"] < 0), "trend_down",
                              np.where(ranging, "range", np.where(volatile, "volatile", "mixed"))))
    warm = out[["er", "r2"]].notna().all(axis=1) & adx.notna()
    out["label"] = pd.Series(label, index=df.index).where(warm)
    return out


def higher_trend(df: pd.DataFrame, intraday: bool) -> pd.Series:
    """Trend of the next timeframe up in [-1, 1] (close vs its EMA20 and the EMA20's slope), from the last
    completed higher bar: hourly for intraday charts, weekly for daily and weekly ones."""
    close = df["close"]
    step = pd.Series(close.index).diff().median() if len(close) > 2 else pd.Timedelta(days=1)
    day = close.index.normalize()
    if intraday and step < pd.Timedelta(minutes=30):
        start = day + pd.Timedelta(minutes=15)  # NSE sessions start at :15
        bucket = start + ((close.index - start) // pd.Timedelta(hours=1)) * pd.Timedelta(hours=1)
    elif intraday:
        bucket = day
    elif step < pd.Timedelta(days=5):
        bucket = day - pd.to_timedelta(close.index.weekday, unit="D")
    else:
        bucket = day - pd.to_timedelta(close.index.day - 1, unit="D")  # weekly charts: monthly trend
    higher = close.groupby(bucket).last()
    ema = higher.ewm(span=20, adjust=False, min_periods=20).mean()
    trend = (0.5 * np.sign(higher - ema) + 0.5 * np.sign(ema.diff())).where(ema.notna() & ema.diff().notna())
    prev = trend.shift(1)  # the bar currently forming isn't finished yet
    return pd.Series(prev.reindex(bucket).to_numpy(), index=close.index).fillna(0.0)


def features(df: pd.DataFrame, ind: pd.DataFrame, reg: pd.DataFrame, intraday: bool) -> pd.DataFrame:
    """Numeric regime features for the outlook model."""
    X = pd.DataFrame(index=df.index)
    X["htf_trend"] = higher_trend(df, intraday)
    X["trend_quality"] = (reg["r2"] * reg["slope"]).fillna(0.0)
    X["efficiency"] = (reg["er"] * reg["slope"]).fillna(0.0)
    X["vol_regime"] = (reg["atr_rank"] - 0.5).fillna(0.0)
    return X


def current(df: pd.DataFrame, reg: pd.DataFrame, htf: pd.Series, intraday: bool) -> dict | None:
    if reg.empty or pd.isna(reg["label"].iloc[-1]):
        return None
    r = reg.iloc[-1]
    label = r["label"]
    h = float(htf.iloc[-1])
    htf_word = "up" if h > 0.25 else "down" if h < -0.25 else "flat"
    text = {
        "trend_up": "Steady move up: trend-following setups tend to work better than fading it.",
        "trend_down": "Steady move down: trend-following setups tend to work better than catching the bottom.",
        "range": "Sideways chop: breakouts often fail; mean-reversion setups suit it better.",
        "volatile": "Large swings without a clear direction: use wider stops or smaller size.",
        "mixed": "No clear character: neither trending nor cleanly range-bound.",
    }[label]
    return {
        "label": label,
        "name": LABELS[label],
        "text": text,
        "efficiency": round(float(r["er"]), 2),
        "vhf": None if pd.isna(r["vhf"]) else round(float(r["vhf"]), 2),
        "trend_r2": round(float(r["r2"]), 2),
        "atr_percentile": None if pd.isna(r["atr_rank"]) else round(100 * float(r["atr_rank"])),
        "higher_tf": "hourly" if intraday else "weekly",
        "higher_tf_trend": htf_word,
    }


def bucket(label: str | None, side: str) -> str:
    """Regime relative to a trade: 'with' (trend on the trade's side), 'against', 'range', 'volatile', 'mixed'."""
    if label in ("trend_up", "trend_down"):
        return "with" if (label == "trend_up") == (side == "buy") else "against"
    return label or "mixed"
