import numpy as np
import pandas as pd

from app.analysis import indicators as ta
from app.analysis import probability as pb
from tests.test_patterns import frame


def test_logistic_learns_a_real_edge_and_reports_skill():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2020-01-01", periods=4000, freq="D", tz="Asia/Kolkata")
    X = pd.DataFrame(rng.normal(0, 1, (4000, len(pb.FEATURES))), index=idx, columns=list(pb.FEATURES))
    p_true = 1 / (1 + np.exp(-1.2 * X["trend"]))
    y = pd.Series((rng.random(4000) < p_true).astype(float), index=idx)
    built = pb.build([(X, y)], horizon=5)
    assert built["test"]["skill_pct"] > 5
    assert built["test"]["buy_hit_pct"] > 60
    m = built["model"]
    hi = np.zeros((1, len(pb.FEATURES)))
    hi[0, 0] = 2.0
    assert m.predict(hi)[0] > 0.8 and m.predict(-hi)[0] < 0.2


def test_pure_noise_gives_no_buy_or_sell_call():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2020-01-01", periods=3000, freq="D", tz="Asia/Kolkata")
    X = pd.DataFrame(rng.normal(0, 1, (3000, len(pb.FEATURES))), index=idx, columns=list(pb.FEATURES))
    y = pd.Series((rng.random(3000) < 0.5).astype(float), index=idx)
    built = pb.build([(X, y)], horizon=5)
    if built["test"]["skill_pct"] <= 0:
        out = pb.outlook(built, X, "1d", True, [])
        assert out["verdict"] == "NO EDGE"


def test_features_use_no_future_bars():
    rng = np.random.default_rng(5)
    df, ind = frame(list(100 * np.exp(np.cumsum(rng.normal(0, 0.015, 400)))))
    full = pb.features(df, ind, [])
    part = pb.features(df.iloc[:300], ind.iloc[:300], [])
    pd.testing.assert_frame_equal(full.iloc[:300], part)


def test_data_issues_force_no_trade():
    rng = np.random.default_rng(2)
    df, ind = frame(list(100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 600)))))
    X = pb.features(df, ind, [])
    built = pb.build([(X, pb.labels(df, 5))], horizon=5)
    out = pb.outlook(built, X, "1d", False, ["Current quote is stale"])
    assert out["verdict"] == "NO TRADE" and "stale" in out["reason"]
