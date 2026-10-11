from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.analysis import candles as cs
from app.analysis import indicators as ta
from app.analysis import patterns as pt
from app.analysis import probability as pb
from app.analysis import strategies as sx
from app.analysis import volume_profile as vp
from app.market_session import IST
from app.models import Candle
from tests.test_patterns import found, frame, path


def bars(rows, intraday=False, start=datetime(2024, 1, 1, tzinfo=IST)):
    """rows = [(open, high, low, close)] or [(open, high, low, close, time)] -> (df, ind)."""
    candles = []
    for i, r in enumerate(rows):
        t = r[4] if len(r) > 4 else start + timedelta(days=i)
        candles.append(Candle(time=t, open=r[0], high=r[1], low=r[2], close=r[3], volume=1000.0))
    df = ta.to_frame(candles)
    return df, ta.compute(df, intraday=intraday)


def ohlc(closes):
    """Plain candles from closes: open = previous close, 1-point wicks."""
    out, prev = [], closes[0]
    for c in closes:
        out.append((prev, max(prev, c) + 1.0, min(prev, c) - 1.0, c))
        prev = c
    return out


def session(day: datetime, rows):
    """15-minute candles from 09:15 for (open, high, low, close) rows."""
    t0 = day.replace(hour=9, minute=15)
    return [(*r, t0 + timedelta(minutes=15 * i)) for i, r in enumerate(rows)]


# ---- indicators --------------------------------------------------------------
def test_supertrend_flips_on_reversal():
    df, ind = frame(path(100, (160, 60), (100, 60)))
    d = ind["st_dir"]
    assert d.iloc[55] == 1 and d.iloc[-1] == -1
    flips = ((d == -1) & (d.shift() == 1)).sum()
    assert flips >= 1
    assert (ind["st_line"].iloc[-1] > df["close"].iloc[-1])  # line sits above price in a down-trend


def test_vwap_bands_bracket_vwap():
    rng = np.random.default_rng(3)
    rows, p = [], 100.0
    for day in (datetime(2024, 1, 2, tzinfo=IST), datetime(2024, 1, 3, tzinfo=IST)):
        day_rows = []
        for _ in range(25):
            c = p + rng.normal(0, 0.5)
            day_rows.append((p, max(p, c) + 0.3, min(p, c) - 0.3, c))
            p = c
        rows += session(day, day_rows)
    df, ind = bars(rows, intraday=True)
    ok = ind["vwap"].notna()
    assert ok.all()
    assert (ind["vwap_l2"] <= ind["vwap_l1"] + 1e-9).all() and (ind["vwap_l1"] <= ind["vwap"] + 1e-9).all()
    assert (ind["vwap_u1"] >= ind["vwap"] - 1e-9).all() and (ind["vwap_u2"] >= ind["vwap_u1"] - 1e-9).all()
    # First bar of each session: no spread yet.
    first = df.index.normalize() != pd.Series(df.index.normalize(), index=df.index).shift()
    assert np.allclose(ind["vwap_u1"][first], ind["vwap"][first])


def test_confirmed_pivots_are_causal():
    rng = np.random.default_rng(7)
    df, ind = frame(list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))))
    full = ta.confirmed_pivots(df, ind["atr14"])
    part = ta.confirmed_pivots(df.iloc[:200], ind["atr14"].iloc[:200])
    assert [s for s in full if s.confirm < 200] == part
    assert all(s.confirm == s.idx + 3 for s in full)


def test_anchored_vwap_starts_at_anchor_price():
    df, _ = frame(path(100, (120, 30)))
    anchor = np.full(len(df), -1)
    anchor[10:] = 10
    av = ta.anchored_vwap(df, anchor)
    assert av.iloc[:10].isna().all()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    assert abs(av.iloc[10] - typical.iloc[10]) < 1e-9


# ---- candlesticks -------------------------------------------------------------
def _after_decline(last_rows):
    closes = path(150, (100, 40))
    return bars(ohlc(closes) + last_rows)


def test_hammer_after_decline():
    df, ind = _after_decline([(100, 100.6, 94, 100.4)])
    s = cs.detect_series(df, ind)
    assert s["hammer"].iloc[-1] == 1
    assert s["shooting_star"].iloc[-1] == 0


def test_bullish_engulfing_and_morning_star():
    df, ind = _after_decline([(100, 100.5, 97, 97.5), (97, 101.5, 96.5, 101)])
    assert cs.detect_series(df, ind)["engulfing_bull"].iloc[-1] == 1
    df, ind = _after_decline([(100, 100.3, 94.7, 95), (95, 95.5, 94.3, 94.8), (94.9, 99.5, 94.8, 99)])
    assert cs.detect_series(df, ind)["morning_star"].iloc[-1] == 1


def test_inside_bar_follows_trend():
    closes = path(100, (150, 40))
    df, ind = bars(ohlc(closes) + [(150, 158, 146, 155), (155, 157, 150, 156)])
    assert cs.detect_series(df, ind)["inside_bar"].iloc[-1] == 1


def test_no_reversal_candle_without_prior_move():
    df, ind = bars(ohlc([100.0] * 40) + [(100, 100.6, 94, 100.4)])
    assert cs.detect_series(df, ind)["hammer"].iloc[-1] == 0


# ---- strategies ---------------------------------------------------------------
def test_liquidity_sweep_of_swing_high():
    closes = path(100, (130, 40), (115, 10), (125, 8))
    rows = ohlc(closes) + [(125, 133, 124, 126)]  # wick above the 131 swing high, close back below
    df, ind = bars(rows)
    buy, sell, label = sx.liquidity_sweep(df, ind, ta.confirmed_pivots(df, ind["atr14"]), intraday=False)
    assert sell[-1] and not buy[-1]
    assert "swing high" in label[-1]


def test_fvg_retest_and_inversion():
    base = ohlc(path(100, (100.5, 40)))
    # Bullish gap: bar i-2 high 102, bar i low 104.
    gap = [(100.5, 102, 100, 101.5), (101.5, 106, 101.4, 105.5), (105.5, 108, 104, 107.5)]
    retest = [(107.5, 107.6, 103.5, 104.5), (104.5, 106.5, 103.8, 106)]  # dips into 102-104, green close
    df, ind = bars(base + gap + retest)
    sig, _ = sx.fair_value_gaps(df, ind)
    assert sig["fvg"][0][-1]

    through = [(107.5, 107.6, 99, 100), (100, 101, 98.5, 99)]  # closes below the gap: it becomes an IFVG
    red_retest = [(101, 102.8, 99.2, 100)]  # rallies into the old 102-104 gap, closes red below it -> SELL
    df, ind = bars(base + gap + through + red_retest)
    sig, _ = sx.fair_value_gaps(df, ind)
    assert sig["ifvg"][1][-1] and not sig["ifvg"][0].any()


def test_amd_sweep_and_reclaim():
    day = datetime(2024, 1, 2, tzinfo=IST)
    rows = [(100, 102, 99.5, 101), (101, 101.8, 100, 100.5), (100.5, 101.5, 100, 101), (101, 101.6, 100.2, 100.8),  # 09:15-10:00
            (100.8, 101.5, 100.5, 101.2), (101.2, 101.8, 101, 101.6),  # 10:15, 10:30 inside
            (101.6, 103.0, 101.5, 102.6),  # 10:45 sweeps the 102 high, closes above
            (102.6, 102.7, 101.0, 101.3)]  # 11:00 closes back inside -> SELL
    df, _ = bars(session(day, rows), intraday=True)
    buy, sell, label = sx.amd(df)
    assert sell[-1] and not buy.any()
    assert "opening-hour high" in label[-1]


def test_breakout_probability_is_causal_and_bounded():
    rng = np.random.default_rng(11)
    df, ind = frame(list(100 * np.exp(np.cumsum(rng.normal(0, 0.015, 300)))))
    probs, _ = sx.breakout_probability(df, ind)
    up, dn = probs[0.5]
    vals = pd.concat([up, dn]).dropna()
    assert ((vals >= 0) & (vals <= 1)).all() and len(vals) > 100
    probs_part, _ = sx.breakout_probability(df.iloc[:200], ind.iloc[:200])
    pd.testing.assert_series_equal(probs[1.0][0].iloc[:200], probs_part[1.0][0])


def _random_intraday(n_days=6, seed=4):
    rng = np.random.default_rng(seed)
    rows, p = [], 100.0
    for d in range(n_days):
        day = datetime(2024, 1, 2, tzinfo=IST) + timedelta(days=d)
        dr = []
        for _ in range(25):
            c = p * np.exp(rng.normal(0, 0.004))
            dr.append((p, max(p, c) * (1 + abs(rng.normal(0, 0.002))), min(p, c) * (1 - abs(rng.normal(0, 0.002))), c))
            p = c
        rows += session(day, dr)
    return bars(rows, intraday=True)


def test_strategies_use_no_future_bars():
    rng = np.random.default_rng(9)
    daily = frame(list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 320)))))
    for (df, ind), tf, k in ((daily, "1d", 240), (_random_intraday(), "15m", 110)):
        full = sx.compute(df, ind, tf)
        part = sx.compute(df.iloc[:k], ind.iloc[:k], tf)
        pd.testing.assert_frame_equal(full["features"].iloc[:k], part["features"])
        for key, (b, s) in part["signals"].items():
            fb, fs = full["signals"][key]
            assert (fb.iloc[:k].to_numpy() == b.to_numpy()).all(), key
            assert (fs.iloc[:k].to_numpy() == s.to_numpy()).all(), key


def test_block_and_stats_shape():
    df, ind = _random_intraday()
    res = sx.compute(df, ind, "15m")
    blk = sx.block(df, ind, res, with_series=True)
    assert {s["key"] for s in blk["strategies"]} == set(sx.CATALOG)
    assert len(blk["breakout"]["levels"]) == 3
    assert blk["volume_profile"]["poc"] is not None
    rows = sx.rows(sx.merge_stats([sx.raw_stats(df, ind, res, 8)]))
    assert len(rows) == len(sx.CATALOG) + len(cs.CATALOG)
    for r in rows:
        if r["signals"]:
            assert 0 <= r["hit_rate"] <= 100 and r["base_rate"] is not None


# ---- volume profile -------------------------------------------------------------
def test_volume_profile_poc_and_value_area():
    high = np.array([101.0] * 10 + [110.0, 95.0])
    low = np.array([99.0] * 10 + [108.0, 93.0])
    vol = np.array([1000.0] * 10 + [50.0, 50.0])
    p = vp.profile(high, low, vol)
    assert 99 <= p["poc"] <= 101
    assert p["val"] <= p["poc"] <= p["vah"]
    assert p["vah"] < 108 and p["val"] > 95


def test_estimated_delta_sign():
    df, _ = bars([(100, 103, 99, 103), (103, 104, 99, 99)])
    d = vp.estimated_delta(df)
    assert d.iloc[0] > 0 > d.iloc[1]


# ---- new chart patterns ---------------------------------------------------------
def test_ascending_channel_detected():
    legs = [100]
    for k in range(5):
        legs += [(112 + 6 * k, 8), (104 + 6 * k, 8)]
    closes = path(*legs) + path(130, (160, 20))[1:]
    hits = found(pt.detect(*frame(closes)), "channel_up")
    assert hits, "channel not detected"
    assert hits[-1]["direction"] in ("up", "down", None)


def test_rounding_bottom_detected():
    x = np.linspace(-1, 1, 61)
    bowl = list(100 + 50 * x ** 2)
    closes = path(200, (150, 30))[:-1] + bowl + path(150, (140, 6))[1:] + path(140, (200, 30))[1:]
    hits = found(pt.detect(*frame(closes)), "rounding_bottom")
    assert hits, "rounding bottom not detected"
    assert hits[-1]["direction"] == "up"


def test_diamond_detected():
    closes = path(80, (120, 60), (110, 6), (126, 8), (100, 10), (122, 10), (108, 8), (116, 6), (90, 25))
    hits = found(pt.detect(*frame(closes)), "diamond_top")
    assert hits, "diamond not detected"


# ---- feature selection ---------------------------------------------------------
def test_selection_keeps_useful_and_drops_useless_extra_features():
    rng = np.random.default_rng(0)
    n = 4000
    idx = pd.date_range("2015-01-01", periods=n, freq="D", tz="Asia/Kolkata")
    cols = list(pb.FEATURES) + list(pb.EXTRA)
    X = pd.DataFrame(rng.normal(0, 1, (n, len(cols))), index=idx, columns=cols)
    p_true = 1 / (1 + np.exp(-(0.4 * X["trend"] + 1.0 * X["sweep"])))
    y = pd.Series((rng.random(n) < p_true).astype(float), index=idx)
    built = pb.build([(X, y)], horizon=5)
    assert "sweep" in built["features"]
    assert "candles" not in built["features"]
    assert pb.LABELS["sweep"] not in built["test"]["features_dropped"]


def test_selection_never_sees_the_test_period():
    rng = np.random.default_rng(1)
    n = 4000
    idx = pd.date_range("2015-01-01", periods=n, freq="D", tz="Asia/Kolkata")
    cols = list(pb.FEATURES) + list(pb.EXTRA)
    X = pd.DataFrame(rng.normal(0, 1, (n, len(cols))), index=idx, columns=cols)
    # "fvg" only predicts in the final 30% (the test period): selection must not pick it.
    late = np.arange(n) >= int(0.7 * n)
    p_true = 1 / (1 + np.exp(-np.where(late, 2.0 * X["fvg"], 0.0)))
    y = pd.Series((rng.random(n) < p_true).astype(float), index=idx)
    built = pb.build([(X, y)], horizon=5)
    assert "fvg" not in built["features"]


def test_index_without_volume_gets_session_average_vwap():
    day = datetime(2024, 1, 2, tzinfo=IST)
    rows = session(day, [(100, 101, 99, 100.5), (100.5, 102, 100, 101.5), (101.5, 103, 101, 102.5)])
    candles = [Candle(time=r[4], open=r[0], high=r[1], low=r[2], close=r[3], volume=0.0) for r in rows]
    df = ta.to_frame(candles)
    ind = ta.compute(df, intraday=True)
    typical = (df["high"] + df["low"] + df["close"]) / 3
    assert abs(ind["vwap"].iloc[-1] - typical.mean()) < 1e-9
    assert vp.profile(df["high"].to_numpy(), df["low"].to_numpy(), df["volume"].to_numpy()) is not None


async def test_demo_five_minute_bars_line_up_with_fifteen_minute_bars():
    from app.providers.demo import DemoProvider

    p = DemoProvider()
    inst = (await p.resolve_indices())[0][0]
    five = await p.candles(inst, "5m")
    fifteen = await p.candles(inst, "15m")
    assert all(c.time.minute % 5 == 0 for c in five)
    last15 = fifteen[-1]
    parts = [c for c in five if last15.time <= c.time < last15.time + timedelta(minutes=15)]
    assert len(parts) == 3
    assert abs(parts[0].open - last15.open) < 1e-6 and abs(parts[-1].close - last15.close) < 1e-6
    assert max(c.high for c in parts) <= last15.high + 1e-6 and min(c.low for c in parts) >= last15.low - 1e-6
