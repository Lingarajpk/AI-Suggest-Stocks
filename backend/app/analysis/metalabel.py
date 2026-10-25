"""Meta-labelling (Lopez de Prado, AFML ch. 3): a second model that decides whether to TAKE a strategy's signal.

The primary models are the strategies themselves; each says BUY or SELL. Every past signal is followed with the
usual trade rules (stop 1.5 ATR, target 2.5 ATR, or a trend-reversal / opposite-signal exit: three barriers),
and labelled 1 if the trade made money after costs. A logistic regression then learns, from the market at the
moment of entry (the same features as the outlook model, turned to the trade's side so "with the trade" is
positive) and which strategy fired, how likely that trade is to win.

Honesty rules:
  * strict time split: the model is fitted on trades that EXITED before the cut and tested on trades that
    ENTERED after it (no overlap, no look-ahead)
  * it is used only if, on those unseen trades, it beats the base win rate (Brier skill > 0) AND the trades
    it rates in the top half actually averaged more after costs than all trades
  * it never changes a signal; it adds "this setup, here, has won X% of the time" next to it
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis import probability as pb
from app.analysis.history import simulate_trades

UNSIGNED = {"vol_regime"}  # features that describe the market but not a direction
TRAIN_FRACTION = 0.7
MIN_TRADES = 300


def _signed(X: pd.DataFrame, side: int) -> np.ndarray:
    sign = np.array([1.0 if c in UNSIGNED else float(side) for c in X.columns])
    return X.to_numpy(float) * sign


def dataset(df: pd.DataFrame, ind: pd.DataFrame, X: pd.DataFrame, signals: dict[str, tuple[pd.Series, pd.Series]],
            keys: list[str], cost_pct: float) -> list[dict]:
    """One row per past trade of each strategy: entry/exit time, strategy, side, net result and features."""
    pos = {t.isoformat(): i for i, t in enumerate(df.index)}
    Xf = X.fillna(0.0)
    rows = []
    for key in keys:
        b, s = signals[key]
        trades, _ = simulate_trades(df, ind, b, s, cost_pct)
        for t in trades:
            i = pos.get(t["entry_time"])
            if i is None:
                continue
            side = 1 if t["side"] == "buy" else -1
            rows.append({"entry": t["entry_time"], "exit": t["exit_time"], "key": key, "side": side,
                         "pnl": float(t["pnl_pct"]), "y": 1.0 if t["pnl_pct"] > 0 else 0.0,
                         "x": _signed(Xf.iloc[[i]], side)[0]})
    return rows


def _design(rows: list[dict], keys: list[str]) -> np.ndarray:
    onehot = np.zeros((len(rows), len(keys)))
    for j, r in enumerate(rows):
        onehot[j, keys.index(r["key"])] = 1.0
    return np.column_stack([np.vstack([r["x"] for r in rows]), onehot])


def build(rows: list[dict], keys: list[str], feature_names: list[str]) -> dict | None:
    if len(rows) < MIN_TRADES:
        return None
    rows = sorted(rows, key=lambda r: r["entry"])
    cut = rows[int(len(rows) * TRAIN_FRACTION)]["entry"]
    train = [r for r in rows if r["exit"] < cut]
    test = [r for r in rows if r["entry"] >= cut]
    if len(train) < 200 or len(test) < 80:
        return None
    m = pb.train(_design(train, keys), np.array([r["y"] for r in train]))
    p = m.predict(_design(test, keys))
    y = np.array([r["y"] for r in test])
    pnl = np.array([r["pnl"] for r in test])
    base = float(np.mean([r["y"] for r in train]))
    brier, brier0 = float(np.mean((p - y) ** 2)), float(np.mean((base - y) ** 2))
    top = p >= np.median(p)
    test_stats = {
        "trades": len(test), "train_trades": len(train),
        "test_period": [test[0]["entry"], test[-1]["entry"]],
        "base_win_pct": round(100 * float(y.mean()), 1),
        "skill_pct": round(100 * (1 - brier / brier0), 2) if brier0 > 0 else 0.0,
        "top_half_win_pct": round(100 * float(y[top].mean()), 1),
        "top_half_avg_pct": round(float(pnl[top].mean()), 3),
        "all_avg_pct": round(float(pnl.mean()), 3),
        "bottom_half_avg_pct": round(float(pnl[~top].mean()), 3) if (~top).any() else None,
    }
    useful = test_stats["skill_pct"] > 0 and test_stats["top_half_avg_pct"] > test_stats["all_avg_pct"]
    final = pb.train(_design(rows, keys), np.array([r["y"] for r in rows]))
    return {"model": final, "keys": keys, "features": feature_names, "test": test_stats, "useful": useful}


def score_current(built: dict | None, X: pd.DataFrame, states: list[dict]) -> dict[str, dict]:
    """P(win) for each strategy whose latest signal is fresh (3 candles or less), when the model is useful."""
    if not built or not built["useful"]:
        return {}
    x_now = X[built["features"]].iloc[[-1]].fillna(0.0)
    out = {}
    for s in states:
        ls = s.get("last_signal")
        if s["key"] not in built["keys"] or not ls or ls["bars_ago"] > 3:
            continue
        side = 1 if ls["side"] == "buy" else -1
        row = {"x": _signed(x_now, side)[0], "key": s["key"]}
        p = float(built["model"].predict(_design([row], built["keys"]))[0])
        out[s["key"]] = {"win_pct": round(100 * p, 1), "base_win_pct": built["test"]["base_win_pct"], "side": ls["side"]}
    return out
