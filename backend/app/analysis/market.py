"""Market-wide context from daily closes: India VIX, breadth, sector rotation and a risk-on/defensive vote.

Inputs are pandas Series of daily closes indexed by date (completed sessions only). Pure functions, so the
service can feed them from Upstox or demo data alike.

  VIX        level, 1- and 5-day change, percentile of the level within its last year
  breadth    of the universe: share above their 20- and 50-day averages, advancers vs decliners on the
             latest session, new 20-day highs and lows
  rotation   per sector index (after OpenAlice's sector-rotation idea): returns over 1D/1W/1M/3M, the
             same minus NIFTY 50 (relative strength), and acceleration = 1-week daily pace minus 3-month
             daily pace. Score = mean z-score across sectors of RS 1W, RS 1M, RS 3M and acceleration.
             Quadrant like a relative-rotation graph: leading / weakening / lagging / improving.
  votes      NIFTY trend, NIFTY momentum, VIX, breadth, gold vs NIFTY, rupee: each risk-on (+1),
             defensive (-1) or neutral (0). Verdict when one side leads by 2 or more.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

HORIZONS = {"1d": 1, "1w": 5, "1m": 21, "3m": 63}


def ret(closes: pd.Series, n: int) -> float | None:
    c = closes.dropna()
    if len(c) <= n or c.iloc[-1 - n] <= 0:
        return None
    return round(100 * (float(c.iloc[-1]) / float(c.iloc[-1 - n]) - 1), 2)


def vix_summary(closes: pd.Series) -> dict | None:
    c = closes.dropna()
    if len(c) < 30:
        return None
    level = float(c.iloc[-1])
    year = c.iloc[-252:]
    pct = round(100 * float((year.iloc[:-1] < level).mean()))
    state = "very calm" if pct < 10 else "calm" if pct < 35 else "normal" if pct < 65 else "elevated" if pct < 90 else "fearful"
    return {
        "level": round(level, 2),
        "change_1d_pct": ret(c, 1),
        "change_5d_pct": ret(c, 5),
        "percentile_1y": pct,
        "state": state,
        "text": f"India VIX {level:.1f} is higher than {pct}% of the last year's readings ({state}). "
                + ("Expect wider swings: smaller size, wider stops." if pct >= 65 else
                   "Option premiums are cheap relative to the year." if pct < 35 else "Normal volatility."),
        "history": [[d.isoformat() if hasattr(d, "isoformat") else str(d), round(float(v), 2)] for d, v in c.iloc[-120:].items()],
    }


def breadth(closes: dict[str, pd.Series]) -> dict | None:
    rows = []
    for sym, c in closes.items():
        c = c.dropna()
        if len(c) < 51:
            continue
        last, prev = float(c.iloc[-1]), float(c.iloc[-2])
        rows.append({
            "symbol": sym,
            "above20": last > float(c.iloc[-20:].mean()),
            "above50": last > float(c.iloc[-50:].mean()),
            "change": last / prev - 1,
            "high20": last >= float(c.iloc[-20:].max()),
            "low20": last <= float(c.iloc[-20:].min()),
        })
    n = len(rows)
    if not n:
        return None
    adv = sum(r["change"] > 0 for r in rows)
    dec = sum(r["change"] < 0 for r in rows)
    above50 = round(100 * sum(r["above50"] for r in rows) / n)
    return {
        "stocks": n,
        "above_20dma_pct": round(100 * sum(r["above20"] for r in rows) / n),
        "above_50dma_pct": above50,
        "advancers": adv,
        "decliners": dec,
        "unchanged": n - adv - dec,
        "new_20d_highs": sum(r["high20"] for r in rows),
        "new_20d_lows": sum(r["low20"] for r in rows),
        "text": ("Broad strength: most stocks are above their 50-day average." if above50 >= 60 else
                 "Broad weakness: most stocks are below their 50-day average." if above50 <= 40 else
                 "Mixed participation: roughly half the stocks are above their 50-day average."),
    }


def _z(values: list[float | None]) -> list[float]:
    xs = [v for v in values if v is not None]
    if len(xs) < 2:
        return [0.0 for _ in values]
    m, sd = float(np.mean(xs)), float(np.std(xs, ddof=1)) or 1.0
    return [0.0 if v is None else (v - m) / sd for v in values]


def sector_rotation(sectors: dict[str, pd.Series], bench: pd.Series) -> list[dict]:
    rows = []
    b = {k: ret(bench, n) for k, n in HORIZONS.items()}
    for name, c in sectors.items():
        r = {k: ret(c, n) for k, n in HORIZONS.items()}
        if r["1m"] is None:
            continue
        rs = {k: (None if r[k] is None or b[k] is None else round(r[k] - b[k], 2)) for k in HORIZONS}
        accel = None
        if r["1w"] is not None and r["3m"] is not None:
            accel = round(r["1w"] / 5 - r["3m"] / 63, 3)  # % per day: recent pace vs the quarter's
        rows.append({"sector": name, "returns": r, "relative": rs, "acceleration": accel})
    if not rows:
        return []
    cols = [_z([row["relative"]["1w"] for row in rows]), _z([row["relative"]["1m"] for row in rows]),
            _z([row["relative"]["3m"] for row in rows]), _z([row["acceleration"] for row in rows])]
    for i, row in enumerate(rows):
        row["score"] = round(sum(c[i] for c in cols) / len(cols), 2)
        long_rs = row["relative"]["3m"] if row["relative"]["3m"] is not None else row["relative"]["1m"]
        acc = row["acceleration"] or 0.0
        row["quadrant"] = ("leading" if long_rs >= 0 and acc >= 0 else "weakening" if long_rs >= 0 else
                           "improving" if acc >= 0 else "lagging")
    rows.sort(key=lambda x: -x["score"])
    return rows


def macro_votes(nifty: pd.Series, vix: dict | None, brd: dict | None, gold: pd.Series | None, usdinr: pd.Series | None) -> dict:
    votes = []

    def add(name: str, vote: int, detail: str) -> None:
        votes.append({"name": name, "vote": vote, "detail": detail})

    c = nifty.dropna()
    if len(c) >= 50:
        ma50 = float(c.iloc[-50:].mean())
        above = float(c.iloc[-1]) > ma50
        add("NIFTY trend", 1 if above else -1, f"NIFTY 50 is {'above' if above else 'below'} its 50-day average ({ma50:,.0f}).")
    r20 = ret(c, 20)
    if r20 is not None:
        add("NIFTY momentum", 1 if r20 > 1 else -1 if r20 < -1 else 0, f"NIFTY 50 {r20:+.1f}% over 20 sessions.")
    if vix:
        p = vix["percentile_1y"]
        add("India VIX", 1 if p < 50 else -1 if p >= 75 else 0, f"VIX {vix['level']} ({p}th percentile of the year).")
    if brd:
        a = brd["above_50dma_pct"]
        add("Breadth", 1 if a >= 60 else -1 if a <= 40 else 0, f"{a}% of stocks above their 50-day average.")
    if gold is not None:
        g, n = ret(gold, 21), ret(c, 21)
        if g is not None and n is not None:
            d = g - n
            add("Gold vs NIFTY", -1 if d > 2 else 1 if d < -2 else 0, f"Gold {g:+.1f}% vs NIFTY {n:+.1f}% over a month.")
    if usdinr is not None:
        u = ret(usdinr, 21)
        if u is not None:
            add("Rupee", -1 if u > 1 else 1 if u < -0.5 else 0,
                f"USDINR {u:+.1f}% over a month ({'rupee weaker' if u > 0 else 'rupee stronger'}).")
    on, off = sum(v["vote"] > 0 for v in votes), sum(v["vote"] < 0 for v in votes)
    if on - off >= 2:
        verdict, text = "risk_on", f"Risk-on: {on} of {len(votes)} signals favour buying strength."
    elif off - on >= 2:
        verdict, text = "defensive", f"Defensive: {off} of {len(votes)} signals point to caution."
    else:
        verdict, text = "mixed", f"Mixed: {on} risk-on vs {off} defensive signals."
    return {"verdict": verdict, "text": text, "votes": votes}


def finite(x) -> float | None:
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else x
