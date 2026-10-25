import numpy as np
import pandas as pd

from app.analysis import indicators as ta
from app.analysis import strategies as sx


def _df(close, freq="D", start="2025-01-01 09:15", spread=0.5, volume=1000.0):
    close = np.asarray(close, dtype=float)
    idx = pd.date_range(start, periods=len(close), freq=freq, tz="Asia/Kolkata")
    return pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": close + spread, "low": close - spread,
                         "close": close, "volume": volume}, index=idx)


def test_cpr_uses_previous_session():
    rows = []
    for d, (h, l, c) in enumerate([(110, 90, 100), (130, 100, 120)]):
        for k in range(3):
            rows.append((pd.Timestamp(f"2025-01-0{d + 6} 09:15", tz="Asia/Kolkata") + pd.Timedelta(minutes=5 * k), h, l, c))
    df = pd.DataFrame([{"open": c, "high": h, "low": l, "close": c, "volume": 1.0} for _, h, l, c in rows],
                      index=[t for t, *_ in rows])
    piv = ta.session_pivots(df)
    assert piv["cpr_p"].iloc[:3].isna().all()  # no previous session yet
    assert piv["cpr_p"].iloc[3] == 100.0 and piv["cpr_bc"].iloc[3] == 100.0 and piv["cpr_tc"].iloc[3] == 100.0
    assert piv["cpr_r1"].iloc[3] == 110.0 and piv["cpr_s1"].iloc[3] == 90.0


def test_oscillator_bounds():
    rng = np.random.default_rng(3)
    df = _df(100 + np.cumsum(rng.normal(0, 1, 300)))
    df["volume"] = rng.uniform(500, 1500, 300)
    ind = ta.compute(df, intraday=False)
    for col in ("mfi14", "stoch_k", "stoch_d"):
        v = ind[col].dropna()
        assert len(v) > 200 and v.between(0, 100).all(), col
    assert set(ind["squeeze_on"].dropna().unique()) <= {0.0, 1.0}
    assert (ind["dc_upper"].dropna() >= ind["dc_lower"].dropna()).all()


def test_squeeze_release_fires_on_expansion():
    quiet = 100 + 0.05 * np.sin(np.arange(60))  # tight coil: bands shrink inside Keltner
    burst = 100 + np.cumsum(np.full(6, 1.5))  # strong move up out of it
    df = _df(np.r_[np.linspace(90, 100, 40), quiet, burst], spread=0.3)
    ind = ta.compute(df, intraday=False)
    sig = sx.extra_setups(df, ind, intraday=False)
    buy, sell = sig["squeeze"]
    assert buy.iloc[-6:].any() and not sell.iloc[-6:].any()


def test_zscore_reversion_and_nr7():
    rng = np.random.default_rng(1)
    base = 100 + rng.normal(0, 0.3, 80)
    drop = np.r_[base, 96.0, 97.0]  # a 2+ sigma flush, then a green candle back up
    df = _df(drop, spread=0.2)
    df.iloc[-1, df.columns.get_loc("open")] = 96.2
    ind = ta.compute(df, intraday=False)
    buy, _ = sx.extra_setups(df, ind, intraday=False)["zscore_reversion"]
    assert buy.iloc[-1]

    c = np.r_[np.linspace(100, 110, 30), 110.0, 111.5]
    df = _df(c, spread=1.0)
    df.iloc[-2, df.columns.get_loc("high")] = 110.1  # an NR7 candle
    df.iloc[-2, df.columns.get_loc("low")] = 109.9
    ind = ta.compute(df, intraday=False)
    buy, _ = sx.extra_setups(df, ind, intraday=False)["nr7_breakout"]
    assert buy.iloc[-1]


def test_new_strategies_in_catalog_and_features():
    for k in ("squeeze", "zscore_reversion", "nr7_breakout", "cpr_breakout"):
        assert k in sx.CATALOG
    for k in ("squeeze", "zscore", "stoch", "mfi", "obv_trend", "cpr_pos"):
        assert k in sx.FEATURES
