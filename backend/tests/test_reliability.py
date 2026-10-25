import numpy as np
import pandas as pd

from app.analysis import dataquality as dq
from app.analysis import probability as pb
from app.analysis import reliability as rl
from app.market_session import EQUITY


def test_wilson_matches_known_values():
    lo, hi = rl.wilson(6, 10)
    assert (lo, hi) == (31.3, 83.2)  # textbook 95% Wilson interval for 6/10
    assert rl.wilson(0, 0) is None
    lo, hi = rl.wilson(0, 20)
    assert lo == 0.0 and 0 < hi < 20


def test_evidence_grades():
    assert rl.evidence([1.0] * 10)["grade"] == "insufficient"
    assert rl.evidence([-1.0, 0.5] * 20)["grade"] == "weak"
    # 70% winners of +2 vs -1: break-even is 33%, Wilson low of 28/40 is ~55% -> proven
    proven = rl.evidence([2.0] * 28 + [-1.0] * 12)
    assert proven["grade"] == "proven" and proven["breakeven_win_rate"] == 33.3
    # 45% winners of +1.3 vs -1: profitable on average but could easily be below 43.5% break-even
    assert rl.evidence([1.3] * 18 + [-1.0] * 22)["grade"] == "promising"


def test_deflated_sharpe_penalises_many_trials():
    rng = np.random.default_rng(3)
    pnl = list(rng.normal(0.15, 1.0, 200))
    one = rl.deflated_sharpe(pnl, trials=1)
    many = rl.deflated_sharpe(pnl, trials=50)
    assert one is not None and many is not None and many < one
    assert rl.deflated_sharpe(pnl[:10], trials=1) is None  # too few trades


def test_risk_stats_and_decay():
    r = rl.risk_stats([1, -1, 2, -0.5, 1.5, -2, 0.5, 1, -1, 3])
    assert r["sortino"] > 0 and r["cvar95_pct"] == -2.0 and r["ulcer_index"] >= 0
    assert rl.decay([1.0] * 60)["status"] == "holding"
    fading = rl.decay([1.0 + 0.1 * (i % 3) for i in range(60)] + [-1.0 - 0.1 * (i % 3) for i in range(30)])
    assert fading["status"] == "fading"
    assert rl.decay([1.0] * 20) is None


def _frame(n=300, step="5min"):
    idx = pd.date_range("2026-01-05 09:15", periods=n, freq=step, tz="Asia/Kolkata")
    c = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.1, n))
    return pd.DataFrame({"open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1000.0}, index=idx)


def test_data_quality_flags_broken_and_jumps():
    df = _frame()
    assert dq.check(df, 0, EQUITY, True)["ok"]
    bad = df.copy()
    bad.iloc[-10:-5, bad.columns.get_loc("high")] = bad["low"].iloc[-10:-5] - 1  # high below low
    res = dq.check(bad, 0, EQUITY, True)
    assert not res["ok"] and any("inconsistent" in i["text"] for i in res["issues"])
    jump = df.copy()
    jump.iloc[-20:] = jump.iloc[-20:] * 0.5  # an unadjusted 1:1 bonus
    assert any("Unusual" in i["text"] for i in dq.check(jump, 0, EQUITY, True)["issues"])
    quiet = df.copy()
    quiet["volume"] = 0.0
    assert any("no volume" in i["text"] for i in dq.check(quiet, 0, EQUITY, True)["issues"])
    assert not any("no volume" in i["text"] for i in dq.check(quiet, 0, EQUITY, False)["issues"])  # index


def test_platt_scaling_recovers_overconfident_model():
    rng = np.random.default_rng(5)
    true_p = rng.uniform(0.3, 0.7, 4000)
    y = (rng.uniform(size=4000) < true_p).astype(float)
    # An overconfident model: right direction, log-odds three times too large.
    z = 3 * np.log(true_p / (1 - true_p))
    p = 1 / (1 + np.exp(-z))
    a, b = pb.fit_platt(p, y)
    assert 0.2 < a < 0.5 and abs(b) < 0.2
    p_cal = 1 / (1 + np.exp(-(a * z + b)))
    assert np.mean((p_cal - y) ** 2) < np.mean((p - y) ** 2)


def test_walk_forward_and_grade():
    rng = np.random.default_rng(2)
    n = 3000
    idx = pd.date_range("2025-01-01", periods=n, freq="h")
    X = pd.DataFrame(rng.normal(size=(n, len(pb.FEATURES))), index=idx, columns=list(pb.FEATURES))
    y = (X["trend"] + rng.normal(0, 1, n) > 0).astype(float)
    folds = pb.walk_forward(X.assign(_y=y), list(pb.FEATURES), horizon=5)
    assert len(folds) == 4 and all(f["skill_pct"] > 0 for f in folds)
    assert pb.grade(5.0, folds)["grade"] == "A"
    assert pb.grade(-1.0, folds)["grade"] == "none"
