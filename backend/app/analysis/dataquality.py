"""Checks on the candles themselves, so a signal built on broken data says so.

  duplicates     the same candle time twice
  broken bars    high below the open/close, low above it, or a non-positive price
  missing bars   an intraday session (other than today's) with under 90% of its candles
  zero volume    over 20% of recent candles traded nothing (stocks and currency only; indices have no volume)
  jumps          a one-candle move far outside the stock's normal range: an unadjusted split or bonus,
                 or a bad print. Indicators across such a jump are distorted.

Only "error" level issues should block a call; "warn" is shown next to it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.market_session import MarketHours

RECENT = 200


def check(df: pd.DataFrame, bar_minutes: int, hours: MarketHours, has_volume: bool) -> dict:
    issues: list[dict] = []
    if df.empty:
        return {"ok": False, "issues": [{"level": "error", "text": "No candles."}], "checked_bars": 0, "as_of": None}
    tail = df.iloc[-RECENT:]

    dup = int(df.index.duplicated().sum())
    if dup:
        issues.append({"level": "warn", "text": f"{dup} duplicate candle time{'s' if dup > 1 else ''} in the data."})

    o, h, lo, c = (tail[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    broken = int(((h < np.maximum(o, c) - 1e-9) | (lo > np.minimum(o, c) + 1e-9) | (lo <= 0) | ~np.isfinite(c)).sum())
    if broken:
        issues.append({"level": "error" if broken > 3 else "warn",
                       "text": f"{broken} recent candle{'s have' if broken > 1 else ' has'} inconsistent high/low/open/close."})

    if bar_minutes:
        per_session = ((hours.close.hour * 60 + hours.close.minute) - hours.open_minutes) // bar_minutes
        days = pd.Series(df.index.date).value_counts().sort_index()
        past = days.iloc[:-1].iloc[-5:]  # the latest session may still be in progress
        short = [str(d) for d, n in past.items() if n < 0.9 * per_session]
        if short:
            issues.append({"level": "warn", "text": f"Missing candles on {', '.join(short)} (fewer than 90% of a full session)."})

    if has_volume:
        zero = float((tail["volume"] <= 0).mean())
        if zero > 0.2:
            issues.append({"level": "warn", "text": f"{zero:.0%} of recent candles show no volume: thin trading or a feed gap."})

    ret = np.abs(np.diff(np.log(np.maximum(c, 1e-9))))
    if len(ret) > 30:
        typical = float(np.median(ret)) or 1e-9
        limit = max(0.2, 15 * typical)  # 20% in one candle, or 15x the median move
        big = np.nonzero(ret > limit)[0]
        if len(big):
            when = tail.index[big[-1] + 1]
            issues.append({"level": "warn",
                           "text": f"Unusual {abs(np.expm1(ret[big[-1]])):.0%} one-candle move on {when:%d %b %Y %H:%M}: "
                                   "possibly an unadjusted split/bonus or a bad print, so indicators around it are distorted."})

    return {"ok": not any(i["level"] == "error" for i in issues), "issues": issues, "checked_bars": int(len(tail)),
            "as_of": df.index[-1].isoformat()}
