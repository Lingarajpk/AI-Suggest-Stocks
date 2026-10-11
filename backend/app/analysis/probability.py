"""Combined outlook: one probability that price is higher after the horizon, from all strategies.

Every strategy the app already computes becomes a feature (the five rule-score factors, trend
strength, volume and active chart-pattern breakouts). A logistic regression is trained on the
pooled history of every stock in the universe, with a strict time split:

  train  = the older 70% of bars        test = the newer 30% (never seen while fitting)

The test set tells us whether the probabilities are worth anything (skill vs. always guessing the
base rate, and how often "≥55% up" calls actually went up). Only then is the model refit on all
data and applied to the latest candle. If the out-of-sample skill is not positive, the outlook
says so and makes no BUY/SELL call. All features are known at the candle's close (no look-ahead).

The extra strategy features (Supertrend, Trend+Momentum, liquidity sweeps, FVGs, AMD, breakout
probability, candlesticks, CVD divergence, value area, MACD divergence) must earn their place:
inside the training period, the newest 20% is held out for validation and features are added one
at a time (greedy forward selection) only while each improves the validation Brier score. The
30% test period is never used for this choice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis import strategies as sx
from app.analysis.history import HORIZON, score_components

BUY_AT = 0.55
SELL_AT = 0.45
TRAIN_FRACTION = 0.7
VALID_FRACTION = 0.2  # newest share of the training period used to choose the extra features
MIN_GAIN = 0.0005  # validation Brier improvement an extra feature must bring
RIDGE = 1.0

FEATURES: dict[str, str] = {
    "trend": "Trend (EMA stack)",
    "macd": "MACD",
    "rsi": "RSI(14)",
    "momentum": "Momentum (ROC vs ATR)",
    "vwap": "Price vs VWAP",
    "adx": "Trend strength (ADX)",
    "volume": "Relative volume",
    "pattern": "Chart-pattern breakouts",
    "range_pos": "Position in 20-bar range",
}


EXTRA = sx.FEATURES
LABELS = {**FEATURES, **EXTRA}


def features(df: pd.DataFrame, ind: pd.DataFrame, patterns: list[dict], extra: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per bar (base features + strategy features); every value is known at that bar's close."""
    if extra is None:
        timeframe = "1d" if ind["vwap"].isna().all() else "15m"
        extra = sx.compute(df, ind, timeframe)["features"]
    return pd.concat([_base_features(df, ind, patterns), extra[list(EXTRA)]], axis=1)


def _base_features(df: pd.DataFrame, ind: pd.DataFrame, patterns: list[dict]) -> pd.DataFrame:
    comps = score_components(df, ind)
    X = pd.DataFrame(index=df.index)
    for k in ("trend", "macd", "rsi", "momentum"):
        X[k] = comps[k]
    X["vwap"] = comps["vwap"].fillna(0.0)  # daily candles have no session VWAP
    # Trend strength signed by the trend direction: strong up-trend > 0, strong down-trend < 0.
    di = np.sign(ind["plus_di"] - ind["minus_di"])
    X["adx"] = (ind["adx14"].clip(0, 50) / 50) * di
    rv = ind["rel_volume"].replace([np.inf, -np.inf], np.nan)
    X["volume"] = np.log(rv.clip(0.2, 5)).fillna(0.0) * np.sign(df["close"].diff()).fillna(0.0)
    hi, lo = df["high"].rolling(20).max(), df["low"].rolling(20).min()
    X["range_pos"] = ((df["close"] - lo) / (hi - lo).replace(0, np.nan) * 2 - 1).clip(-1, 1)

    # +1 while a bullish pattern breakout is running, -1 for a bearish one (from its breakout close
    # until its exit), averaged when several overlap.
    pat = np.zeros(len(df))
    cnt = np.zeros(len(df))
    pos = {t.isoformat(): i for i, t in enumerate(df.index)}
    for p in patterns:
        if not p.get("breakout_time") or p.get("direction") not in ("up", "down"):
            continue
        a = pos.get(p["breakout_time"])
        if a is None:
            continue
        b = pos.get(p.get("exit_time") or "", len(df))
        b = min(b, a + 20, len(df))
        d = 1.0 if p["direction"] == "up" else -1.0
        pat[a:b] += d
        cnt[a:b] += 1
    X["pattern"] = np.where(cnt > 0, pat / np.maximum(cnt, 1), 0.0)
    return X[list(FEATURES)]


def labels(df: pd.DataFrame, horizon: int) -> pd.Series:
    fwd = df["close"].shift(-horizon) / df["close"] - 1
    return (fwd > 0).astype(float).where(fwd.notna())


def fit_logistic(X: np.ndarray, y: np.ndarray, ridge: float = RIDGE, iters: int = 30) -> np.ndarray:
    """L2-regularised logistic regression by Newton/IRLS. X already includes the intercept column."""
    w = np.zeros(X.shape[1])
    reg = np.eye(X.shape[1]) * ridge
    reg[0, 0] = 0.0  # don't shrink the intercept
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (p - y) + reg @ w
        H = (X * (p * (1 - p))[:, None]).T @ X + reg
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-7:
            break
    return w


class Model:
    def __init__(self, w: np.ndarray, mean: np.ndarray, std: np.ndarray) -> None:
        self.w, self.mean, self.std = w, mean, std
        self.cols: list[str] = []

    def design(self, X: np.ndarray) -> np.ndarray:
        Z = (X - self.mean) / self.std
        return np.column_stack([np.ones(len(Z)), Z])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-np.clip(self.design(X) @ self.w, -30, 30)))

    def contributions(self, x: np.ndarray) -> np.ndarray:
        """Each feature's push on the log-odds relative to an average bar."""
        return self.w[1:] * (x - self.mean) / self.std


def train(X: np.ndarray, y: np.ndarray) -> Model:
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-9] = 1.0
    m = Model(np.zeros(X.shape[1] + 1), mean, std)
    m.w = fit_logistic(m.design(X), y)
    return m


def evaluate(p: np.ndarray, y: np.ndarray, base: float) -> dict:
    brier = float(np.mean((p - y) ** 2))
    brier_base = float(np.mean((base - y) ** 2))
    bins = []
    for lo, hi, label in ((0, 0.40, "< 40%"), (0.40, 0.45, "40–45%"), (0.45, 0.55, "45–55%"), (0.55, 0.60, "55–60%"), (0.60, 1.01, "≥ 60%")):
        m = (p >= lo) & (p < hi)
        if m.sum():
            bins.append({"range": label, "n": int(m.sum()), "predicted_up_pct": round(100 * float(p[m].mean()), 1),
                         "actual_up_pct": round(100 * float(y[m].mean()), 1)})
    buy, sell = p >= BUY_AT, p <= SELL_AT
    return {
        "samples": int(len(y)),
        "base_up_pct": round(100 * float(y.mean()), 1),
        "brier": round(brier, 4),
        "brier_baseline": round(brier_base, 4),
        "skill_pct": round(100 * (1 - brier / brier_base), 2) if brier_base > 0 else 0.0,
        "accuracy_pct": round(100 * float(((p >= 0.5) == (y == 1)).mean()), 1),
        "buy_calls": int(buy.sum()),
        "buy_hit_pct": round(100 * float(y[buy].mean()), 1) if buy.sum() else None,
        "sell_calls": int(sell.sum()),
        "sell_hit_pct": round(100 * float(1 - y[sell].mean()), 1) if sell.sum() else None,
        "calibration": bins,
    }


def build(datasets: list[tuple[pd.DataFrame, pd.Series]], horizon: int) -> dict | None:
    """Train + out-of-sample test on pooled (features, labels) frames from many stocks."""
    frames = []
    for X, y in datasets:
        d = X.assign(_y=y).dropna()
        if len(d):
            frames.append(d)
    if not frames:
        return None
    data = pd.concat(frames).sort_index(kind="stable")
    if len(data) < 200:
        return None
    times = data.index
    cut = times[int(len(times) * TRAIN_FRACTION)]
    train_d = data[times < cut]
    # Purge: a training label looks `horizon` bars ahead, so leave a gap before the test period.
    test_d = data[times >= cut]
    test_times = test_d.index.unique().sort_values()
    if len(test_times) > horizon:
        test_d = test_d[test_d.index >= test_times[horizon]]
    if len(train_d) < 100 or len(test_d) < 50:
        return None
    candidates = [c for c in EXTRA if c in data.columns]
    cols, selection = select_features(train_d, candidates, horizon)
    m_oos = train(train_d[cols].to_numpy(float), train_d["_y"].to_numpy(float))
    p_test = m_oos.predict(test_d[cols].to_numpy(float))
    oos = evaluate(p_test, test_d["_y"].to_numpy(float), float(train_d["_y"].mean()))
    oos["train_samples"] = int(len(train_d))
    oos["train_period"] = [train_d.index.min().isoformat(), train_d.index.max().isoformat()]
    oos["test_period"] = [test_d.index.min().isoformat(), test_d.index.max().isoformat()]
    oos["features_used"] = [LABELS[c] for c in cols]
    oos["features_added"] = selection
    oos["features_dropped"] = [LABELS[c] for c in candidates if c not in cols]
    final = train(data[cols].to_numpy(float), data["_y"].to_numpy(float))
    final.cols = cols
    return {"model": final, "test": oos, "features": cols}


def _split(d: pd.DataFrame, fraction: float, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Older `fraction` / newer rest by time, with a `horizon`-bar purge gap before the newer part."""
    times = d.index
    cut = times[int(len(times) * fraction)]
    old, new = d[times < cut], d[times >= cut]
    new_times = new.index.unique().sort_values()
    if len(new_times) > horizon:
        new = new[new.index >= new_times[horizon]]
    return old, new


def select_features(train_d: pd.DataFrame, candidates: list[str], horizon: int) -> tuple[list[str], list[dict]]:
    """Base features always; extra ones added greedily while they improve the validation Brier score.
    Uses only the training period (its newest VALID_FRACTION as validation)."""
    cols = list(FEATURES)
    if not candidates:
        return cols, []
    fit_d, val_d = _split(train_d, 1 - VALID_FRACTION, horizon)
    if len(fit_d) < 100 or len(val_d) < 50:
        return cols, []
    y_fit, y_val = fit_d["_y"].to_numpy(float), val_d["_y"].to_numpy(float)

    def brier(cs: list[str]) -> float:
        m = train(fit_d[cs].to_numpy(float), y_fit)
        return float(np.mean((m.predict(val_d[cs].to_numpy(float)) - y_val) ** 2))

    best = brier(cols)
    remaining = list(candidates)
    added = []
    while remaining:
        scores = {c: brier(cols + [c]) for c in remaining}
        c = min(scores, key=scores.get)
        gain = best - scores[c]
        if gain <= MIN_GAIN:
            break
        cols.append(c)
        remaining.remove(c)
        best = scores[c]
        added.append({"feature": LABELS[c], "brier_gain": round(gain, 4)})
    return cols, added


def outlook(built: dict | None, X: pd.DataFrame, timeframe: str, data_ok: bool, data_issues: list[str]) -> dict:
    h, h_label = HORIZON[timeframe]
    if built is None:
        return {"status": "not_available", "message": "Not enough history yet to train and test the combined model.",
                "horizon_bars": h, "horizon_label": h_label}
    cols = built.get("features") or list(FEATURES)
    row = X[cols].iloc[[-1]]
    if row.isna().any(axis=1).iloc[0]:
        return {"status": "not_available", "message": "Indicators are still warming up on this stock's history.",
                "horizon_bars": h, "horizon_label": h_label}
    m: Model = built["model"]
    test = built["test"]
    x = row.to_numpy(float)
    p_up = float(m.predict(x)[0])
    contrib = m.contributions(x[0])
    drivers = sorted(
        ({"feature": k, "label": LABELS[k], "value": round(float(x[0][i]), 3), "push": round(float(contrib[i]), 3)}
         for i, k in enumerate(cols)),
        key=lambda d: -abs(d["push"]),
    )
    skilled = test["skill_pct"] > 0
    if not data_ok:
        verdict, reason = "NO TRADE", "; ".join(data_issues) or "Data quality checks failed"
    elif not skilled:
        verdict, reason = "NO EDGE", "On unseen data this model did not beat simply guessing the usual up-rate, so no call is made."
    elif p_up >= BUY_AT:
        verdict, reason = "BUY", f"Up probability {p_up:.0%} is at or above the {BUY_AT:.0%} buy threshold."
    elif p_up <= SELL_AT:
        verdict, reason = "SELL", f"Up probability {p_up:.0%} is at or below the {SELL_AT:.0%} sell threshold."
    else:
        verdict, reason = "HOLD", f"Up probability {p_up:.0%} is between {SELL_AT:.0%} and {BUY_AT:.0%}: no clear edge either way."
    return {
        "status": "ok",
        "verdict": verdict,
        "reason": reason,
        "up_pct": round(100 * p_up, 1),
        "down_pct": round(100 * (1 - p_up), 1),
        "horizon_bars": h,
        "horizon_label": h_label,
        "drivers": drivers,
        "thresholds": {"buy_at_pct": BUY_AT * 100, "sell_at_pct": SELL_AT * 100},
        "test": test,
        "method": "Logistic regression on all strategy signals, trained on the older 70% of pooled history of every "
                  "stock and tested on the newest 30%; extra strategy features are kept only if they improved a "
                  "validation slice of the training period. Refit on all data for today's estimate. Before costs.",
        "message": f"{100 * p_up:.0f}% chance the close is higher in {h_label} ({verdict}).",
    }
