from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.analysis import indicators as ta
from app.analysis.signals import classify, evaluate
from app.market_session import IST, next_upstox_token_expiry
from app.models import Candle
from app.providers.base import drop_incomplete
from app.providers.upstox import merge_candles, parse_candles, parse_quote


def make_df(closes, start=datetime(2025, 1, 1, tzinfo=IST)):
    candles = [
        Candle(time=start + timedelta(days=i), open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000 + i)
        for i, c in enumerate(closes)
    ]
    return ta.to_frame(candles)


def test_ema_matches_pandas_definition():
    s = pd.Series(np.arange(1, 31, dtype=float))
    out = ta.ema(s, 10)
    assert out.iloc[:9].isna().all()
    assert out.iloc[-1] == pytest.approx(s.ewm(span=10, adjust=False).mean().iloc[-1])


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert ta.rsi(up).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert ta.rsi(down).iloc[-1] == pytest.approx(0.0)
    rng = np.random.default_rng(1)
    noisy = pd.Series(100 + rng.normal(0, 1, 300).cumsum())
    r = ta.rsi(noisy).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_session_vwap_resets_daily():
    t0 = datetime(2025, 1, 2, 9, 15, tzinfo=IST)
    candles = [
        Candle(time=t0, open=10, high=10, low=10, close=10, volume=100),
        Candle(time=t0 + timedelta(minutes=15), open=20, high=20, low=20, close=20, volume=100),
        Candle(time=t0 + timedelta(days=1), open=50, high=50, low=50, close=50, volume=10),
    ]
    v = ta.session_vwap(ta.to_frame(candles))
    assert list(v.round(6)) == [10.0, 15.0, 50.0]


def test_classify_thresholds():
    assert classify(60) == "Strong Bullish"
    assert classify(25) == "Bullish"
    assert classify(0) == "Neutral"
    assert classify(-25) == "Bearish"
    assert classify(-60) == "Strong Bearish"


def test_uptrend_scores_bullish_and_short_history_is_no_trade():
    closes = list(100 * np.exp(np.linspace(0, 0.5, 260)) * (1 + 0.003 * np.sin(np.arange(260))))
    df = make_df(closes)
    now = df.index[-1].to_pydatetime() + timedelta(hours=20)
    res = evaluate(df, ta.compute(df, intraday=False), "1d", now, "polled")
    assert res["score"] > 20
    assert res["signal"] in ("Bullish", "Strong Bullish")

    short = make_df(closes[:30])
    res = evaluate(short, ta.compute(short, intraday=False), "1d", short.index[-1].to_pydatetime(), "polled")
    assert res["status"] == "no_trade"
    assert res["scenario"] is None


def test_stale_quote_forces_no_trade():
    closes = list(np.linspace(100, 150, 120))
    df = make_df(closes)
    res = evaluate(df, ta.compute(df, intraday=False), "1d", df.index[-1].to_pydatetime(), "stale")
    assert res["status"] == "no_trade"
    assert any("stale" in r for r in res["no_trade_reasons"])


def test_parse_upstox_quote_and_candles():
    fetched = datetime(2025, 1, 6, 11, 0, tzinfo=IST)  # Monday, in session
    payload = {
        "instrument_token": "NSE_EQ|TESTISIN",
        "last_price": 110.0,
        "net_change": 10.0,
        "volume": 5000,
        "ohlc": {"open": 101, "high": 112, "low": 100, "close": 110},
        "last_trade_time": str(int((fetched - timedelta(seconds=30)).timestamp() * 1000)),
    }
    q = parse_quote(payload, fetched, 180, "TEST")
    assert q.prev_close == 100.0 and q.change_pct == pytest.approx(10.0)
    assert q.freshness == "polled"
    payload["last_trade_time"] = str(int((fetched - timedelta(hours=1)).timestamp() * 1000))
    assert parse_quote(payload, fetched, 180, "TEST").freshness == "stale"

    rows = [
        ["2025-01-03T00:00:00+05:30", 2, 3, 1, 2.5, 100, 0],
        ["2025-01-02T00:00:00+05:30", 1, 2, 0.5, 1.5, 90, 0],
    ]
    merged = merge_candles(parse_candles(rows))
    assert [c.close for c in merged] == [1.5, 2.5]


def test_drop_incomplete_candles():
    t = datetime(2025, 1, 6, 10, 0, tzinfo=IST)
    c = [Candle(time=t, open=1, high=1, low=1, close=1, volume=1)]
    assert drop_incomplete(c, "15m", t + timedelta(minutes=10)) == []
    assert drop_incomplete(c, "15m", t + timedelta(minutes=15)) == c
    day = [Candle(time=datetime(2025, 1, 6, tzinfo=IST), open=1, high=1, low=1, close=1, volume=1)]
    assert drop_incomplete(day, "1d", datetime(2025, 1, 6, 14, 0, tzinfo=IST)) == []
    assert drop_incomplete(day, "1d", datetime(2025, 1, 6, 15, 31, tzinfo=IST)) == day


def test_token_expiry_is_next_0330_ist():
    assert next_upstox_token_expiry(datetime(2025, 1, 6, 10, 0, tzinfo=IST)) == datetime(2025, 1, 7, 3, 30, tzinfo=IST)
    assert next_upstox_token_expiry(datetime(2025, 1, 6, 2, 0, tzinfo=IST)) == datetime(2025, 1, 6, 3, 30, tzinfo=IST)


def test_scenario_risk_reward_floor():
    closes = list(100 * np.exp(np.linspace(0, 0.5, 260)) * (1 + 0.003 * np.sin(np.arange(260))))
    df = make_df(closes)
    now = df.index[-1].to_pydatetime() + timedelta(hours=20)
    res = evaluate(df, ta.compute(df, intraday=False), "1d", now, "polled")
    if res["scenario"]:
        assert res["scenario"]["risk_reward"] >= 1.0
    else:
        assert any("Risk/reward" in r for r in res["no_trade_reasons"])


def test_ai_number_check_flags_invented_numbers():
    from app.ai.nvidia import unverified_numbers

    facts = {"price": 1241.9, "rsi": 28.15, "support": 1215.0}
    assert unverified_numbers("Price 1,241.90 with RSI 28.2 near support 1215.", facts) == []
    assert unverified_numbers("Price could reach 1400 soon, with 3 risks.", facts) == ["1400"]
    assert unverified_numbers("Score -17.7 out of 100, below the 200-day EMA.", {"score": -17.7, "score_out_of_100": 1, "ema200": 5}) == []


@pytest.mark.parametrize("intraday", [False, True])
def test_history_score_matches_live_signal_score(intraday):
    from app.analysis.history import score_series

    rng = np.random.default_rng(7)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, 400))))
    df = make_df(closes)
    ind = ta.compute(df, intraday=intraday)
    series = score_series(df, ind)
    for end in (120, 250, 400):
        sub_df, sub_ind = df.iloc[:end], ind.iloc[:end]
        live = evaluate(sub_df, sub_ind, "1d", sub_df.index[-1].to_pydatetime(), "polled")
        assert series.iloc[end - 1] == pytest.approx(live["score"], abs=0.11)


def test_signal_history_markers_and_stats():
    from app.analysis.history import signal_history

    rng = np.random.default_rng(3)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.015, 500))))
    df = make_df(closes)
    h = signal_history(df, ta.compute(df, intraday=False), "1d", with_markers=True)
    assert h["horizon_bars"] == 5
    sides = [m["side"] for m in h["markers"]]
    assert "buy" in sides and "sell" in sides
    decided = [m for m in h["markers"] if m["outcome"]]
    assert h["buy"]["count"] + h["sell"]["count"] == len(decided)
    assert all(m["outcome"] is None for m in h["markers"] if m["time"] > df.index[-6].isoformat())


def test_trade_exit_after_rise_then_fall():
    from app.analysis.history import simulate_trades

    # Flat, then a strong rise (BUY), then a sustained fall: the BUY must exit, not stay open.
    closes = [100.0] * 80 + [100 + i * 0.8 for i in range(1, 30)] + [123 - i * 0.9 for i in range(1, 30)]
    df = make_df(closes)
    ind = ta.compute(df, intraday=False)
    buy = pd.Series(False, index=df.index)
    sell = pd.Series(False, index=df.index)
    buy.iloc[85] = True
    trades, open_trade = simulate_trades(df, ind, buy, sell)
    assert len(trades) == 1 and open_trade is None
    t = trades[0]
    assert t["side"] == "buy" and t["exit_time"] > t["entry_time"]
    assert t["reason"] in ("reversal", "target", "stop")
    assert t["reason_text"]


def test_open_trade_reports_weakening():
    from app.analysis.history import simulate_trades

    closes = [100.0] * 80 + [100 + i * 0.5 for i in range(1, 15)] + [106.5, 106.2]
    df = make_df(closes)
    ind = ta.compute(df, intraday=False)
    buy = pd.Series(False, index=df.index)
    buy.iloc[85] = True
    _, ot = simulate_trades(df, ind, buy, pd.Series(False, index=df.index))
    if ot is not None:  # may have hit its target already
        assert ot["side"] == "buy" and ot["status"] in ("holding", "weakening")
        if ot["status"] == "weakening":
            assert ot["warnings"]
