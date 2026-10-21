"""Day-trader tools: Time Machine, exchange holiday calendar and the strategies taken from the day-trader skill pack."""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis import indicators as ta
from app.analysis import strategies as sx
from app.analysis import timemachine as tm
from app.market_session import CURRENCY, EQUITY, IST, holiday_name, is_session_open, session_state, set_calendar
from app.providers.base import parse_holidays


def sessions(n: int, hours=EQUITY, drift=0.0, noise=0.001, seed=1) -> pd.DataFrame:
    """n weekday sessions of 5-minute candles."""
    rng = np.random.default_rng(seed)
    rows, price, d = [], 100.0, date(2026, 6, 1)
    while len({r[0].date() for r in rows}) < n:
        if d.weekday() < 5:
            t = datetime.combine(d, hours.open, tzinfo=IST)
            for _ in range(tm.slot_count(hours)):
                o = price
                c = o * (1 + drift + rng.normal(0, noise))
                rows.append((t, o, max(o, c) * (1 + noise / 2), min(o, c) * (1 - noise / 2), c, 1000.0))
                price, t = c, t + timedelta(minutes=5)
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"]).set_index("time")


AFTER = datetime(2026, 9, 1, 8, 0, tzinfo=IST)  # before the open of a later day: full-session replays


def test_verdict_uses_break_even_for_the_r_multiple():
    assert tm.verdict(30, 30, 1.0, 40, 0.0)[0].startswith("Close to a coin flip")
    # 2R needs 33% of the decided replays: 24 vs 50 is 32% -> close to break-even, not a loser.
    assert tm.verdict(24.1, 50.5, 2.0, 25.4, 0.0)[0].startswith("Close to break-even")
    assert tm.verdict(60, 20, 1.0, 20, 0.4)[0].startswith("Strongly toward the target")
    assert tm.verdict(40, 50, 1.0, 10, -0.1)[0].startswith("Leans toward the stop")


def test_replays_are_seeded_and_add_up():
    df = sessions(30)
    kw = dict(side="BUY", entry=100.0, stop=99.0, target=101.0, now=AFTER, until=time(15, 15), replays=2000)
    a = tm.run(df, EQUITY, seed=19, **kw)
    b = tm.run(df, EQUITY, seed=19, **kw)
    assert a["p_target"] == b["p_target"] and a["history"] == b["history"]
    assert a["p_target"] + a["p_stop"] + a["p_neither"] == pytest.approx(100, abs=0.2)
    assert a["sessions"] >= 20 and a["until"] == "15:15" and a["start_label"].startswith("a full session")
    # The first session in the data has no previous close for its first candle, so it may be skipped.
    assert a["sessions"] - 1 <= a["history"]["sessions"] <= a["sessions"]


def test_a_drifting_market_favours_its_direction():
    df = sessions(30, drift=0.0004)
    up = tm.run(df, EQUITY, side="BUY", entry=100.0, stop=99.0, target=101.0, now=AFTER, until=time(15, 15), replays=3000)
    dn = tm.run(df, EQUITY, side="SELL", entry=100.0, stop=101.0, target=99.0, now=AFTER, until=time(15, 15), replays=3000)
    assert up["p_target"] > up["p_stop"] and dn["p_stop"] > dn["p_target"]


def test_refusals():
    df = sessions(30)
    common = dict(now=AFTER, until=time(15, 15))
    with pytest.raises(tm.TimeMachineError, match="below the entry"):
        tm.run(df, EQUITY, side="BUY", entry=100, stop=101, target=102, **common)
    with pytest.raises(tm.TimeMachineError, match="noise"):
        tm.run(df, EQUITY, side="BUY", entry=100, stop=99.99, target=101, **common)
    with pytest.raises(tm.TimeMachineError, match="at least 20"):
        tm.run(sessions(10), EQUITY, side="BUY", entry=100, stop=99, target=101, **common)


def test_currency_sessions_use_their_own_hours():
    df = sessions(25, hours=CURRENCY)
    r = tm.run(df, CURRENCY, side="BUY", entry=100.0, stop=99.5, target=100.5, now=AFTER, until=time(16, 45), replays=1000)
    assert r["sessions"] >= 20 and r["until"] == "16:45"


def test_holiday_calendar():
    rows = [
        {"date": "2026-11-09", "description": "Diwali Balipratipada", "holiday_type": "TRADING_HOLIDAY",
         "closed_exchanges": ["NSE", "BSE", "NFO", "CDS"], "open_exchanges": []},
        {"date": "2026-11-08", "description": "Muhurat Trading", "holiday_type": "SPECIAL_TIMING", "closed_exchanges": [],
         "open_exchanges": [{"exchange": "NSE", "start_time": int(datetime(2026, 11, 8, 18, 0, tzinfo=IST).timestamp() * 1000),
                             "end_time": int(datetime(2026, 11, 8, 19, 0, tzinfo=IST).timestamp() * 1000)}]},
        {"date": "2026-11-10", "description": "Settlement only", "holiday_type": "SETTLEMENT_HOLIDAY",
         "closed_exchanges": ["NSE"], "open_exchanges": []},
    ]
    cal = parse_holidays(rows)
    try:
        for ex, (closed, special) in cal.items():
            set_calendar(ex, closed, special)
        assert holiday_name(date(2026, 11, 9)) == "Diwali Balipratipada"
        assert session_state(datetime(2026, 11, 9, 11, 0, tzinfo=IST)) == "closed"
        assert session_state(datetime(2026, 11, 9, 11, 0, tzinfo=IST), CURRENCY) == "closed"
        # A Sunday Muhurat session is open for its hour only.
        assert is_session_open(datetime(2026, 11, 8, 18, 30, tzinfo=IST))
        assert not is_session_open(datetime(2026, 11, 8, 11, 0, tzinfo=IST))
        # Settlement holidays do not stop trading.
        assert is_session_open(datetime(2026, 11, 10, 11, 0, tzinfo=IST))
    finally:
        set_calendar("NSE", {}, {})
        set_calendar("CDS", {}, {})


def test_opening_range_breakout_and_classic_setups():
    df = sessions(3, noise=0.0)  # flat: nothing leaves the range until the candle we push
    last = df.index[-1].date()
    day = df.index.date == last
    pos = np.nonzero(day)[0]
    # Push the 10th candle of the last session well above its first-15-minute range.
    k = pos[9]
    df.iloc[k, df.columns.get_loc("close")] = df["high"].iloc[pos[:3]].max() * 1.01
    df.iloc[k, df.columns.get_loc("high")] = df.iloc[k]["close"] * 1.001
    ind = ta.compute(df, intraday=True)
    buy, sell, labels = sx.opening_range_breakout(df)
    first = np.nonzero(buy & day)[0]
    assert len(first) == 1 and first[0] <= k and "opening range" in labels[first[0]]

    c = pd.Series(np.r_[np.full(30, 100.0), 101.0], index=pd.date_range("2026-01-01", periods=31, freq="D", tz=IST))
    frame = pd.DataFrame({"open": c, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1.0})
    setups = sx.classic_setups(frame, ta.compute(frame, intraday=False))
    assert bool(setups["breakout_20"][0].iloc[-1]) and not setups["breakout_20"][0].iloc[:-1].any()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DATA_MODE", "demo")
    mp.setenv("NEWS_ENABLED", "false")
    mp.setenv("WARM_UP", "false")
    mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("dtdata")))
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main

    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c
    mp.undo()
    get_settings.cache_clear()


def test_time_machine_api(client):
    q = client.get("/api/quotes").json()
    price = next(x["last_price"] for x in q["indices"] if x["symbol"] == "NIFTY 50")
    r = client.get(f"/api/timemachine/NIFTY50?side=BUY&entry={price}&stop={price * 0.996:.2f}&target={price * 1.008:.2f}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replays"] == 10_000 and body["seed"] == 19 and body["verdict"] and len(body["bands"]) == body["bars"]
    assert body["avg_r_after_costs"] < body["avg_r"]
    bad = client.get(f"/api/timemachine/NIFTY50?side=BUY&entry={price}&stop={price * 1.01}&target={price * 1.02}")
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_plan"
    fx = client.get("/api/forex/quotes").json()["quotes"][0]
    p = fx["last_price"]
    r = client.get(f"/api/forex/timemachine/{fx['symbol']}?side=SELL&entry={p}&stop={p + 0.05:.4f}&target={p - 0.1:.4f}")
    assert r.status_code == 200, r.text
    assert r.json()["until"] == "16:45"


def test_strategies_include_the_new_setups(client):
    d = client.get("/api/stocks/RELIANCE?timeframe=5m").json()
    keys = {s["key"] for s in d["strategies"]["strategies"]}
    assert {"orb", "breakout_20", "flag", "reclaim_ma20", "oversold_bounce"} <= keys
    assert d["opening_range"]["complete"] in (True, False)
    stats = client.get("/api/strategies/stats?timeframe=1d").json()
    assert any(r["key"] == "flag" for r in stats["rows"])
