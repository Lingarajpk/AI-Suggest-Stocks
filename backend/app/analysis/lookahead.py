"""No-peeking check, in the spirit of freqtrade's lookahead analysis.

Everything the app shows for past candles (indicators, the signal score, strategy and candlestick
signals, volume-profile levels, outlook features, chart patterns) is computed once on the full
history and again on the history cut off at several earlier candles. Up to each cut, both runs must
agree: a value that changes once later candles exist was using the future, so its backtest would
look better than live trading could ever be.

Patterns are compared by (pattern, time it became known):
- found with full data but not when the data ended at the cut -> it used later candles (lookahead);
- shown when the data ended at the cut but gone with full data, after it had already broken out or
  failed -> it repaints (changes after the fact). A pattern that was still forming and later grew
  into a bigger shape of the same kind is normal and only noted, not counted as a problem.
"""

import numpy as np
import pandas as pd

from app.analysis import history
from app.analysis import indicators as ta
from app.analysis import patterns as pt
from app.analysis import probability as pb
from app.analysis import strategies as sx
from app.analysis import volume_profile as vp
from app.models import Timeframe, is_intraday

MAX_PER_CHECK = 5  # mismatches reported per table and cut, so one leak doesn't flood the report


def _numeric(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.apply(lambda c: pd.to_numeric(c.astype(float) if c.dtype == bool else c, errors="coerce"))


def _tables(df: pd.DataFrame, timeframe: Timeframe) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    """Every per-candle output of the analysis, as numeric tables indexed by candle time."""
    intraday = is_intraday(timeframe)
    ind = ta.compute(df, intraday)
    res = sx.compute(df, ind, timeframe)
    found = pt.detect(df, ind)
    signals = {}
    for key, (buy, sell) in res["signals"].items():
        signals[f"{key} BUY"] = buy
        signals[f"{key} SELL"] = sell
    for key, s in res["candles"].items():
        signals[f"candle {key}"] = s
    tables = {
        "indicator": ind,
        "signal score": history.score_series(df, ind).to_frame("score"),
        "strategy signal": pd.DataFrame(signals, index=df.index),
        "strategy feature": res["features"],
        "volume profile": vp.levels_series(df, intraday),
        "outlook feature": pb.features(df, ind, found, res["features"]),
    }
    return {k: _numeric(v) for k, v in tables.items()}, found


def _compare(name: str, full: pd.DataFrame, cut: pd.DataFrame, upto: pd.Timestamp) -> list[str]:
    f, c = full.loc[:upto], cut.loc[:upto]
    issues = []
    for col in f.columns:
        if col not in c.columns:
            issues.append(f"{name} '{col}': missing when the data ends at {upto}")
            continue
        a, b = f[col].to_numpy(float), c[col].reindex(f.index).to_numpy(float)
        bad = ~np.isclose(a, b, rtol=1e-6, atol=1e-9, equal_nan=True)
        if bad.any():
            i = int(np.argmax(bad))
            issues.append(f"{name} '{col}' at {f.index[i]}: {a[i]:.6g} with later data, {b[i]:.6g} when the data ends at {upto} "
                          f"({int(bad.sum())} candles differ)")
        if len(issues) >= MAX_PER_CHECK:
            break
    return issues


def _known(items: list[dict], upto: str) -> dict[tuple[str, str], dict]:
    return {(it["key"], it["detected_time"]): it for it in items if it.get("detected_time") and it["detected_time"] <= upto}


def check(df: pd.DataFrame, timeframe: Timeframe, cuts: int = 6, min_bars: int = 300, notes: list[str] | None = None) -> list[str]:
    """Readable descriptions of every value that changes once later candles exist (empty = no peeking).
    Forming patterns that later grew into a bigger shape go to `notes` instead."""
    if len(df) < min_bars + 10:
        return []
    full, full_patterns = _tables(df, timeframe)
    issues: list[str] = []
    for frac in np.linspace(0.55, 0.95, cuts):
        n = max(min_bars, int(len(df) * frac))
        part = df.iloc[:n]
        upto = part.index[-1]
        cut, cut_patterns = _tables(part, timeframe)
        for name, table in full.items():
            issues += _compare(name, table, cut[name], upto)
        stamp = upto.isoformat()
        seen_full, seen_cut = _known(full_patterns, stamp), _known(cut_patterns, stamp)
        for key, at in sorted(seen_full.keys() - seen_cut.keys())[:MAX_PER_CHECK]:
            issues.append(f"pattern '{key}' known at {at} with later data, but not when the data ends at {upto} (uses later candles)")
        for key, at in sorted(seen_cut.keys() - seen_full.keys())[:MAX_PER_CHECK]:
            if seen_cut[(key, at)]["status"] == "forming":
                if notes is not None:
                    notes.append(f"pattern '{key}' forming at {at} (data ending {upto}) later grew into a bigger {key}")
            else:
                issues.append(f"pattern '{key}' shown at {at} as '{seen_cut[(key, at)]['status']}' when the data ends at {upto}, "
                              "but gone with later data (repaints)")
    return issues
