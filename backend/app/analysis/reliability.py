"""How much a backtest number can be trusted.

  wilson          95% Wilson score interval of a win rate (honest for small samples, unlike +/- 2 s.e.).
  evidence        Grade of a set of trades: the lower bound of the win rate against the break-even win rate
                  implied by the average win / average loss, plus a minimum sample size.
  deflated_sharpe Bailey & Lopez de Prado (2014): probability that the true Sharpe is above zero after
                  allowing for the number of strategies tried (the best of many looks good by luck alone).
  risk_stats      Sortino, Ulcer index, tail ratio, CVaR 95%, skew and kurtosis of per-trade results.
  decay           Are the most recent trades still as good as the whole history?

All inputs are per-trade results in % after costs. Nothing here changes a signal; it only labels how far
its track record can be relied on.
"""

from __future__ import annotations

import math

import numpy as np

MIN_TRADES = 30  # below this a win rate is anecdote, whatever it says
RECENT_TRADES = 30  # window of the decay check
EULER = 0.5772156649


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% interval of the true win rate, in %."""
    if n <= 0:
        return None
    p = wins / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)


def breakeven_win_rate(pnl: list[float]) -> float | None:
    """Win rate (%) at which these average wins and losses would net zero: |avg loss| / (avg win + |avg loss|)."""
    won = [p for p in pnl if p > 0]
    lost = [-p for p in pnl if p <= 0]
    if not won or not lost:
        return None
    aw, al = sum(won) / len(won), sum(lost) / len(lost)
    return round(100 * al / (aw + al), 1) if aw + al > 0 else None


def evidence(pnl: list[float]) -> dict:
    """Grade: 'proven' (even the low end of the win rate clears break-even), 'promising' (positive average,
    not yet proven), 'weak' (average after costs <= 0) or 'insufficient' (fewer than MIN_TRADES)."""
    n = len(pnl)
    wins = sum(p > 0 for p in pnl)
    ci = wilson(wins, n)
    be = breakeven_win_rate(pnl)
    avg = sum(pnl) / n if n else None
    if n < MIN_TRADES:
        grade, text = "insufficient", f"Only {n} trade{'s' if n != 1 else ''}: need {MIN_TRADES}+ before the win rate means much."
    elif avg is not None and avg <= 0:
        grade, text = "weak", "Loses money on average after costs."
    elif ci and be is not None and ci[0] > be:
        grade, text = "proven", f"Even the low end of the win rate ({ci[0]}%) is above break-even ({be}%)."
    else:
        low = f"{ci[0]}%" if ci else "?"
        grade, text = "promising", f"Profitable so far, but the win rate could be as low as {low}" + (
            f" vs {be}% needed to break even." if be is not None else ".")
    return {"grade": grade, "text": text, "win_rate_low": ci[0] if ci else None, "win_rate_high": ci[1] if ci else None,
            "breakeven_win_rate": be, "min_trades": MIN_TRADES}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation; |error| < 1.2e-9)."""
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > hi:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _moments(x: np.ndarray) -> tuple[float, float, float, float]:
    mean, sd = float(x.mean()), float(x.std(ddof=1))
    if sd <= 0:
        return mean, 0.0, 0.0, 3.0
    z = (x - mean) / sd
    return mean, sd, float((z ** 3).mean()), float((z ** 4).mean())


def deflated_sharpe(pnl: list[float], trials: int) -> float | None:
    """Probability (0-1) that the per-trade Sharpe is truly above zero once `trials` strategies were tried.

    The benchmark is the Sharpe the luckiest of `trials` skill-less strategies would show (Bailey & Lopez
    de Prado 2014, eq. 2), whose Sharpe estimates scatter with standard deviation 1/sqrt(n) around zero."""
    n = len(pnl)
    if n < MIN_TRADES:
        return None
    x = np.asarray(pnl, dtype=float)
    mean, sd, skew, kurt = _moments(x)
    if sd <= 0:
        return None
    sr = mean / sd
    sr0 = 0.0
    if trials > 1:
        spread = 1 / math.sqrt(n - 1)
        sr0 = spread * ((1 - EULER) * _norm_ppf(1 - 1 / trials) + EULER * _norm_ppf(1 - 1 / (trials * math.e)))
    den = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if den <= 0:
        return None
    return round(_norm_cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(den)), 3)


def per_trade_sharpe(pnl: list[float]) -> float | None:
    if len(pnl) < 2:
        return None
    x = np.asarray(pnl, dtype=float)
    sd = float(x.std(ddof=1))
    return float(x.mean()) / sd if sd > 0 else None


def risk_stats(pnl: list[float]) -> dict:
    """Downside-aware numbers for a list of per-trade % results (time order matters only for Ulcer)."""
    empty = {"sortino": None, "ulcer_index": None, "tail_ratio": None, "cvar95_pct": None, "skew": None, "kurtosis": None}
    if len(pnl) < 5:
        return empty
    x = np.asarray(pnl, dtype=float)
    mean, sd, skew, kurt = _moments(x)
    down = np.minimum(x, 0)
    dd = math.sqrt(float((down ** 2).mean()))
    eq = np.cumprod(1 + x / 100)
    peak = np.maximum.accumulate(eq)
    ulcer = math.sqrt(float(((((eq / peak) - 1) * 100) ** 2).mean()))
    p95, p5 = float(np.percentile(x, 95)), float(np.percentile(x, 5))
    tail = abs(p95 / p5) if p5 < 0 else None
    worst = x[x <= p5]
    return {
        "sortino": round(mean / dd, 2) if dd > 0 else None,  # per trade
        "ulcer_index": round(ulcer, 2),
        "tail_ratio": round(tail, 2) if tail is not None else None,
        "cvar95_pct": round(float(worst.mean()), 2) if len(worst) else None,  # average of the worst 5% of trades
        "skew": round(skew, 2),
        "kurtosis": round(kurt - 3, 2),  # excess
    }


def decay(pnl_in_time_order: list[float], window: int = RECENT_TRADES) -> dict | None:
    """Recent trades vs the whole record: 'holding' / 'fading' / 'improving'. Needs 2x the window."""
    n = len(pnl_in_time_order)
    if n < 2 * window:
        return None
    recent = pnl_in_time_order[-window:]
    older = pnl_in_time_order[:-window]
    r, o = sum(recent) / window, sum(older) / len(older)
    sd = float(np.std(older, ddof=1)) or 1e-9
    z = (r - o) / (sd / math.sqrt(window))  # how unusual the recent average is if nothing changed
    status = "fading" if z < -2 and r < o else "improving" if z > 2 and r > o else "holding"
    text = {
        "fading": f"Last {window} trades average {r:+.2f}% vs {o:+.2f}% before: this setup is working less well lately.",
        "improving": f"Last {window} trades average {r:+.2f}% vs {o:+.2f}% before: doing better lately.",
        "holding": f"Last {window} trades ({r:+.2f}% avg) are in line with the longer record ({o:+.2f}%).",
    }[status]
    return {"status": status, "recent_avg_pct": round(r, 2), "older_avg_pct": round(o, 2), "window": window, "text": text}
