from datetime import datetime, timedelta

import numpy as np

from app.analysis import indicators as ta
from app.analysis import patterns as pt
from app.market_session import IST
from app.models import Candle


def path(*legs):
    """Piecewise-linear closes: path(100, (150, 40), ...) goes from 100 to 150 over 40 bars."""
    out = [float(legs[0])]
    for target, bars in legs[1:]:
        out += list(np.linspace(out[-1], target, bars + 1)[1:])
    return out


def frame(closes, volumes=None, gaps=None):
    """Daily candles with a ~2-point range; `gaps` maps bar index -> open price override."""
    start = datetime(2024, 1, 1, tzinfo=IST)
    candles, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = (gaps or {}).get(i, prev)
        candles.append(Candle(time=start + timedelta(days=i), open=o, high=max(o, c) + 1.0, low=min(o, c) - 1.0,
                              close=c, volume=(volumes[i] if volumes else 1000.0)))
        prev = c
    df = ta.to_frame(candles)
    return df, ta.compute(df, intraday=False)


def found(items, key):
    return [i for i in items if i["key"] == key]


def test_double_bottom_breaks_out_and_hits_target():
    df, ind = frame(path(160, (100, 45), (118, 10), (100.5, 10), (150, 25)))
    hits = found(pt.detect(df, ind), "double_bottom")
    assert hits, "double bottom not detected"
    db = hits[-1]
    assert db["direction"] == "up"
    assert db["status"] == "target_hit"
    # Target = breakout level (the middle high) + pattern height.
    assert abs(db["target"] - (db["breakout_level"] + (db["breakout_level"] - db["stop"]))) < 0.5


def test_head_and_shoulders_top_breaks_down():
    df, ind = frame(path(90, (150, 40), (138, 8), (168, 10), (138.5, 10), (151, 8), (100, 25)))
    hs = found(pt.detect(df, ind), "hs_top")
    assert hs, "head & shoulders not detected"
    assert hs[-1]["direction"] == "down"
    assert hs[-1]["status"] in ("target_hit", "breakout", "expired")


def test_ascending_triangle_breakout_up():
    closes = path(100, (140, 30), (150, 5), (136, 6), (150, 6), (141, 5), (150, 5), (145, 4), (180, 20))
    tri = found(pt.detect(*frame(closes)), "triangle_ascending")
    assert tri and tri[-1]["direction"] == "up"


def test_breakaway_gap_and_island_reversal():
    flat_ = [100 + 0.3 * np.sin(i) for i in range(30)]
    closes = flat_ + list(np.linspace(108, 125, 15))
    vols = [1000.0] * 30 + [4000.0] + [1500.0] * 14
    items = pt.detect(*frame(closes, vols, gaps={30: 107.0}))
    assert found(items, "gap_breakaway"), "breakaway gap not detected"

    closes = [100 + 0.3 * np.sin(i) for i in range(30)] + [110, 111, 110.5, 111] + list(np.linspace(97, 90, 10))
    vols = [1000.0] * len(closes)
    isl = found(pt.detect(*frame(closes, vols, gaps={30: 109.0, 34: 97.5})), "island_reversal")
    assert isl and isl[0]["direction"] == "down"


def test_detection_never_uses_future_bars():
    rng = np.random.default_rng(7)
    closes = list(200 * np.exp(np.cumsum(rng.normal(0, 0.015, 600))))
    full_df, full_ind = frame(closes)
    full = pt.detect(full_df, full_ind)
    cut = 420
    part = pt.detect(full_df.iloc[:cut], full_ind.iloc[:cut])
    cut_time = full_df.index[cut - 1].isoformat()
    sig = lambda i: (i["key"], i["start_time"], i["detected_time"])  # noqa: E731
    part_keys = {sig(i) for i in part}
    # Anything the full run detected by the cut must already be visible with only data up to the cut.
    early = [i for i in full if i["detected_time"] <= cut_time]
    assert early
    missing = [sig(i) for i in early if sig(i) not in part_keys]
    assert not missing, missing
    # And a pattern that had already broken out by the cut keeps the same entry in the full run.
    full_by = {sig(i): i for i in full}
    for i in part:
        if i.get("breakout_time") and sig(i) in full_by:
            assert full_by[sig(i)]["breakout_time"] == i["breakout_time"]
            assert full_by[sig(i)]["entry_price"] == i["entry_price"]


def test_stats_aggregate():
    rng = np.random.default_rng(3)
    df, ind = frame(list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 500)))))
    items = pt.detect(df, ind)
    merged = pt.merge_stats([pt.raw_stats(items), pt.raw_stats(items)])
    for k, v in merged.items():
        assert v["wins"] + v["losses"] + v["expired"] == v["resolved"]
        if v["resolved"]:
            assert 0 <= v["success_rate"] <= 100
        assert k in pt.CATALOG
