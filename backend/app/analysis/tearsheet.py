"""Performance tearsheet for one strategy's pooled trades (layout after NautilusTrader's tearsheets).

Sizing assumption, stated on the page: capital is split equally across the N instruments tested and every trade
uses one share of it, so a trade's net % result moves the account by result / N. Daily P&L is the sum over trades
exiting that day; the equity curve is its running total (no compounding), in % of starting capital.

  stats        total and annualised return, Sharpe, Sortino, max drawdown and its length, Calmar, win rate,
               profit factor, expectancy, average win / loss, best / worst trade
  equity       running total and drawdown, thinned to at most 400 points
  monthly      net % per calendar month (heatmap), and per year
  rolling      63-day rolling Sharpe, weekly points
  distribution histogram of per-trade results
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.analysis.history import profit_factor

MAX_POINTS = 400
ROLL = 63


def _thin(s: pd.Series, n: int = MAX_POINTS) -> list[list]:
    if len(s) > n:
        s = s.iloc[np.linspace(0, len(s) - 1, n).round().astype(int)]
    return [[d.date().isoformat(), round(float(v), 3)] for d, v in s.items()]


def _day(iso: str) -> pd.Timestamp:
    t = pd.Timestamp(iso)
    return (t.tz_localize(None) if t.tzinfo else t).normalize()


def build(times: list[str], pnl: list[float], instruments: int) -> dict | None:
    if len(pnl) < 5 or len(times) != len(pnl):
        return None
    n = max(1, instruments)
    order = np.argsort(times)
    days = [_day(times[i]) for i in order]
    res = np.array([pnl[i] for i in order], dtype=float)
    idx = pd.bdate_range(days[0], days[-1])
    daily = pd.Series(0.0, index=idx)
    for d, r in zip(days, res):
        if d in daily.index:
            daily[d] += r / n
    equity = daily.cumsum()
    peak = equity.cummax()
    dd = equity - peak
    years = max(len(idx) / 252, 1 / 252)
    sd = float(daily.std(ddof=1)) if len(daily) > 1 else 0.0
    down = math.sqrt(float((np.minimum(daily, 0) ** 2).mean())) if len(daily) else 0.0
    max_dd = float(dd.min())
    # Longest stretch below a previous peak, in trading days.
    under, longest, run = (dd < -1e-12).to_numpy(), 0, 0
    for u in under:
        run = run + 1 if u else 0
        longest = max(longest, run)
    annual = float(equity.iloc[-1]) / years
    won, lost = res[res > 0], res[res <= 0]
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    yearly = daily.groupby(daily.index.year).sum()
    rolling = (daily.rolling(ROLL).mean() / daily.rolling(ROLL).std(ddof=1) * math.sqrt(252)).replace([np.inf, -np.inf], np.nan).dropna()
    hist, edges = np.histogram(res, bins=min(20, max(5, len(res) // 10)))
    return {
        "stats": {
            "trades": int(len(res)),
            "instruments": n,
            "period": [idx[0].date().isoformat(), idx[-1].date().isoformat()],
            "total_return_pct": round(float(equity.iloc[-1]), 2),
            "annual_return_pct": round(annual, 2),
            "sharpe": round(float(daily.mean()) / sd * math.sqrt(252), 2) if sd > 0 else None,
            "sortino": round(float(daily.mean()) / down * math.sqrt(252), 2) if down > 0 else None,
            "max_drawdown_pct": round(max_dd, 2),
            "longest_drawdown_days": int(longest),
            "calmar": round(annual / abs(max_dd), 2) if max_dd < 0 else None,
            "win_rate": round(100 * len(won) / len(res), 1),
            "profit_factor": profit_factor(list(res)),
            "expectancy_pct": round(float(res.mean()), 3),
            "avg_win_pct": round(float(won.mean()), 2) if len(won) else None,
            "avg_loss_pct": round(float(lost.mean()), 2) if len(lost) else None,
            "best_trade_pct": round(float(res.max()), 2),
            "worst_trade_pct": round(float(res.min()), 2),
        },
        "equity": _thin(equity),
        "drawdown": _thin(dd),
        "monthly": [{"month": str(p), "pct": round(float(v), 2)} for p, v in monthly.items()],
        "yearly": [{"year": int(y), "pct": round(float(v), 2)} for y, v in yearly.items()],
        "rolling_sharpe": _thin(rolling.iloc[::5], 200) if len(rolling) else [],
        "distribution": [{"from": round(float(edges[i]), 2), "to": round(float(edges[i + 1]), 2), "count": int(c)} for i, c in enumerate(hist)],
        "method": f"Capital split equally across {n} instruments; each trade uses one share, so a trade's net result moves the "
                  "account by result / instruments. Daily P&L is summed by exit day, no compounding. After estimated costs. In-sample.",
    }
