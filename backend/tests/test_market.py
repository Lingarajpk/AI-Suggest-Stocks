import numpy as np
import pandas as pd

from app import nse
from app.analysis import indicators as ta
from app.analysis import market as mk
from app.analysis import regime as rg


def _closes(values, start="2025-01-01"):
    idx = pd.bdate_range(start, periods=len(values)).date
    return pd.Series(values, index=idx, dtype=float)


def test_vix_percentile_and_state():
    v = _closes(list(np.linspace(10, 20, 250)) + [12.0])
    s = mk.vix_summary(v)
    assert s["level"] == 12.0 and 15 <= s["percentile_1y"] <= 25 and s["state"] == "calm"
    assert mk.vix_summary(_closes([14.0] * 10)) is None


def test_breadth_counts():
    up = _closes(np.linspace(100, 150, 60))
    down = _closes(np.linspace(150, 100, 60))
    b = mk.breadth({"A": up, "B": up, "C": down})
    assert b["stocks"] == 3 and b["advancers"] == 2 and b["decliners"] == 1
    assert b["above_50dma_pct"] == 67 and b["new_20d_highs"] == 2 and b["new_20d_lows"] == 1


def test_sector_rotation_ranks_leader_first():
    n = 80
    bench = _closes(np.linspace(100, 110, n))
    strong = _closes(np.concatenate([np.linspace(100, 105, n - 5), np.linspace(106, 125, 5)]))
    weak = _closes(np.linspace(100, 90, n))
    rows = mk.sector_rotation({"STRONG": strong, "WEAK": weak}, bench)
    assert rows[0]["sector"] == "STRONG" and rows[0]["quadrant"] == "leading"
    assert rows[-1]["sector"] == "WEAK" and rows[-1]["quadrant"] == "lagging"


def test_macro_votes_verdict():
    nifty = _closes(np.linspace(100, 130, 80))
    vix = {"level": 11.0, "percentile_1y": 20}
    brd = {"above_50dma_pct": 70}
    v = mk.macro_votes(nifty, vix, brd, None, None)
    assert v["verdict"] == "risk_on" and len(v["votes"]) == 4
    falling = _closes(np.linspace(130, 100, 80))
    v = mk.macro_votes(falling, {"level": 25.0, "percentile_1y": 95}, {"above_50dma_pct": 20}, None, None)
    assert v["verdict"] == "defensive"


def _ohlc(close: np.ndarray, start="2025-01-01", freq="D"):
    idx = pd.date_range(start, periods=len(close), freq=freq, tz="Asia/Kolkata")
    return pd.DataFrame({"open": close, "high": close * 1.004, "low": close * 0.996, "close": close, "volume": 1e5}, index=idx)


def test_regime_labels_trend_and_range():
    rng = np.random.default_rng(4)
    trend = _ohlc(100 * np.exp(np.cumsum(0.01 + rng.normal(0, 0.004, 300))))
    reg = rg.series(trend, ta.compute(trend, intraday=False))
    assert reg["label"].iloc[-1] == "trend_up"
    chop = _ohlc(100 + np.sin(np.arange(300) / 2.0) * 2 + rng.normal(0, 0.2, 300))
    reg = rg.series(chop, ta.compute(chop, intraday=False))
    assert reg["label"].iloc[-1] in ("range", "mixed", "volatile")
    assert rg.bucket("trend_up", "buy") == "with" and rg.bucket("trend_up", "sell") == "against"
    assert rg.bucket(None, "buy") == "mixed"


def test_higher_trend_uses_completed_weeks_only():
    close = np.linspace(100, 200, 200)
    df = _ohlc(close, freq="B")
    full = rg.higher_trend(df, intraday=False)
    cut = rg.higher_trend(df.iloc[:150], intraday=False)
    assert np.allclose(full.iloc[:150].to_numpy(), cut.to_numpy())
    assert full.iloc[-1] == 1.0  # a steady climb: price above a rising weekly EMA


def test_parse_ban_list():
    text = "Securities in Ban For Trade Date 24-OCT-2026:\n1,IDEA\n2,RBLBANK\n"
    assert nse.parse_ban(text) == {"trade_date": "24-OCT-2026", "symbols": ["IDEA", "RBLBANK"]}
    assert nse.parse_ban("Securities in Ban For Trade Date 24-OCT-2026: NIL")["symbols"] == []
