from datetime import datetime, timedelta

from app.analysis.early import HOLD_SECONDS, EarlyTracker
from app.market_session import IST

BAR = datetime(2026, 11, 2, 10, 0, tzinfo=IST)
NEXT = BAR + timedelta(minutes=5)


def at(seconds: float) -> datetime:
    return BAR + timedelta(seconds=seconds)


def test_fires_only_after_holding():
    t = EarlyTracker()
    assert t.update("NIFTY 50", 1, BAR, at(30), 100.0) is None  # first seen
    assert t.update("NIFTY 50", 1, BAR, at(30 + HOLD_SECONDS - 1), 100.5) is None  # not held long enough
    fired = t.update("NIFTY 50", 1, BAR, at(30 + HOLD_SECONDS), 101.0, {"target": 105, "stop": 98})
    assert fired and fired["side"] == "BUY" and fired["price"] == 101.0 and fired["confirms_at"] == NEXT.isoformat()
    s = t.summary(BAR.date().isoformat())
    assert s["fired"] == 1 and s["open"] == 1 and s["confirmed_pct"] is None  # live, not resolved yet
    assert t.view("NIFTY 50", at(60))["status"] == "live"
    assert t.update("NIFTY 50", 1, BAR, at(90), 101.2) is None  # fires once per candle


def test_too_early_in_the_candle_and_flip_resets():
    t = EarlyTracker()
    t.update("X", 1, BAR, at(0), 100.0)
    assert t.update("X", 1, BAR, at(HOLD_SECONDS), 100.0)["side"] == "BUY"  # held 20 s, candle 20 s old (>= 15 s)
    t2 = EarlyTracker()
    t2.update("Y", -1, BAR, at(20), 100.0)
    t2.update("Y", 1, BAR, at(30), 100.0)  # flipped: the hold starts again
    assert t2.update("Y", 1, BAR, at(30 + HOLD_SECONDS - 1), 100.0) is None
    assert t2.update("Y", 1, BAR, at(30 + HOLD_SECONDS), 100.0)["side"] == "BUY"
    t3 = EarlyTracker()
    t3.update("Z", 1, BAR, at(1), 100.0)
    assert t3.update("Z", 1, BAR, at(1 + HOLD_SECONDS - 10), 100.0) is None  # bar < 15 s old and not held


def test_confirmed_at_close():
    t = EarlyTracker()
    t.update("A", 1, BAR, at(30), 100.0)
    t.update("A", 1, BAR, at(60), 100.0)
    t.update("A", 0, NEXT, NEXT + timedelta(seconds=2), 101.0)  # the candle closed: waiting for the confirmed call
    assert t.view("A", NEXT + timedelta(seconds=2))["status"] == "confirming"
    assert t.resolve("A", (BAR - timedelta(minutes=5)).isoformat(), 0, 101.0, NEXT) is None  # desk not rebuilt yet
    rec = t.resolve("A", BAR.isoformat(), 1, 100.5, NEXT + timedelta(seconds=5))
    assert rec["status"] == "confirmed" and rec["edge_pct"] == 0.5  # entered 0.5% better than at the close
    assert t.view("A", NEXT + timedelta(minutes=1))["status"] == "confirmed"
    assert t.view("A", NEXT + timedelta(minutes=20)) is None
    s = t.summary(BAR.date().isoformat())
    assert s["fired"] == 1 and s["confirmed"] == 1 and s["confirmed_pct"] == 100.0 and s["avg_entry_edge_pct"] == 0.5


def test_cancelled_at_close():
    t = EarlyTracker()
    t.update("B", -1, BAR, at(30), 100.0)
    t.update("B", -1, BAR, at(60), 100.0)
    t.update("B", 0, BAR, at(200), 100.4)  # faded inside the candle: still live until the close, shown as inactive
    assert t.view("B", at(200))["active"] is False
    t.update("B", 0, NEXT, NEXT, 100.4)
    rec = t.resolve("B", BAR.isoformat(), 0, 100.4, NEXT + timedelta(seconds=5))
    assert rec["status"] == "cancelled" and rec["edge_pct"] == -0.4  # price moved against the early SELL
    s = t.summary(BAR.date().isoformat())
    assert s["cancelled"] == 1 and s["confirmed_pct"] == 0.0 and s["avg_cancelled_move_pct"] == -0.4


def test_never_fired_leaves_nothing():
    t = EarlyTracker()
    t.update("C", 1, BAR, at(30), 100.0)  # only watching
    t.update("C", 1, NEXT, NEXT + timedelta(seconds=1), 100.0)
    assert t.resolve("C", BAR.isoformat(), 1, 100.0, NEXT) is None and t.summary(BAR.date().isoformat())["fired"] == 0


async def test_replay_on_demo_candles():
    import pandas as pd

    from app.analysis.early import replay
    from app.providers.demo import DemoProvider

    p = DemoProvider()
    inst = (await p.resolve_equities(["RELIANCE"]))[0][0]

    async def frame(tf):
        cs = await p.candles(inst, tf)
        df = pd.DataFrame([c.model_dump() for c in cs]).set_index("time")
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    c1 = (await frame("1m")).iloc[-300:]  # ~1 session of minutes keeps the test quick
    res = replay(await frame("5m"), c1, await frame("15m"), cost_pct=0.1)
    s = res["summary"]
    assert s["bars"] > 0 and s["fired"] == len(res["rows"]) == s["confirmed"] + s["cancelled"]
    for r in res["rows"]:
        assert 1 <= r["minutes_earlier"] <= 4 and r["status"] in ("confirmed", "cancelled")
        if r["status"] == "cancelled":
            assert r["net_vs_waiting_pct"] == round(r["edge_pct"] - 0.1, 4)
