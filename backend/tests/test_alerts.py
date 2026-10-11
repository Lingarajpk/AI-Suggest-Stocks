from datetime import datetime, timedelta

import pytest

from app.alerts import AlertEngine
from app.config import Settings
from app.market_session import IST
from app.models import Quote
from app.providers.demo import DemoProvider
from app.service import MarketService

T0 = datetime(2026, 10, 6, 10, 0, tzinfo=IST)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def quote(sym: str, price: float, change_pct: float, at: datetime) -> Quote:
    return Quote(
        instrument_key=f"DEMO|{sym}", symbol=sym, last_price=price, change=None, change_pct=change_pct,
        open=None, high=None, low=None, prev_close=None, volume=None, last_trade_time=at, fetched_at=at,
        freshness="polled", source="demo",
    )


@pytest.fixture
def engine():
    settings = Settings(_env_file=None, universe="TCS,INFY", alert_move_levels="2,4")
    clock = Clock()
    eng = AlertEngine(MarketService(DemoProvider(), settings), settings, clock=clock, session_open=lambda _: True)

    async def no_desk(include_stocks=True):
        return {"indices": [], "entry_timeframe": "5m"}

    eng.service.intraday = no_desk
    received = []

    async def listener(a):
        received.append(a)

    eng.listeners.append(listener)
    return eng, clock, received


async def test_level_alerts_fire_once_per_day(engine):
    eng, clock, received = engine
    for pct in (1.5, 2.3, 2.6, 4.1, 4.4):
        clock.now += timedelta(seconds=5)
        await eng.check_quotes({"DEMO|TCS": quote("TCS", 100, pct, clock.now)})
    titles = [a["title"] for a in received]
    assert len(titles) == 2 and "up +2.30%" in titles[0] and "up +4.10%" in titles[1]
    clock.now += timedelta(seconds=5)
    await eng.check_quotes({"DEMO|INFY": quote("INFY", 100, -2.2, clock.now)})
    assert received[-1]["direction"] == "down" and received[-1]["symbol"] == "INFY"


async def test_fast_move_alert_with_cooldown(engine):
    eng, clock, received = engine
    price = 100.0
    for _ in range(int(15 * 60 / 5) + 1):  # 15 minutes of 5-second quotes, rising 1.5% overall
        await eng.check_quotes({"DEMO|TCS": quote("TCS", price, 0.5, clock.now)})
        clock.now += timedelta(seconds=5)
        price += 1.5 / 180
    fast = [a for a in received if a["kind"] == "fast"]
    assert len(fast) == 1 and fast[0]["direction"] == "up" and "fast" in fast[0]["title"]
    # Still rising, but inside the cooldown: no repeat alert.
    for _ in range(60):
        await eng.check_quotes({"DEMO|TCS": quote("TCS", price, 0.5, clock.now)})
        clock.now += timedelta(seconds=5)
        price += 0.05
    assert len([a for a in received if a["kind"] == "fast"]) == 1
    assert eng.fast_movers()[0]["symbol"] == "TCS"


async def test_signal_alert_only_for_new_candle(engine, monkeypatch):
    eng, clock, received = engine
    candle = (clock.now - timedelta(minutes=15)).isoformat()
    row = {
        "instrument": {"symbol": "TCS", "instrument_key": "DEMO|TCS"},
        "analysis": {
            "signal": {"last_candle_time": candle, "status": "ok", "no_trade_reasons": [], "scenario": None},
            "history": {"latest_marker": {"time": candle, "side": "buy", "price": 100.0, "score": 35.0},
                        "buy": {"count": 30, "win_rate": 55.0}, "base_rate_up_pct": 50.0},
        },
    }
    old = {**row, "instrument": {"symbol": "INFY", "instrument_key": "DEMO|INFY"},
           "analysis": {**row["analysis"], "signal": {**row["analysis"]["signal"], "last_candle_time": clock.now.isoformat()}}}

    async def fake_scan(tf):
        return {"rows": [row, old]}

    monkeypatch.setattr(eng.service, "scan", fake_scan)
    await eng.check_signals()
    await eng.check_signals()  # same candle again: no duplicate
    sig = [a for a in received if a["kind"] == "signal"]
    assert len(sig) == 1 and sig[0]["symbol"] == "TCS" and "BUY" in sig[0]["title"]
    assert "right 55.0% of 30 (random 50.0%)" in sig[0]["message"]


async def test_no_fast_move_alert_across_overnight_gap(engine):
    eng, clock, received = engine
    await eng.check_quotes({"DEMO|TCS": quote("TCS", 100.0, 0.0, clock.now)})
    clock.now = clock.now.replace(hour=15, minute=29)
    await eng.check_quotes({"DEMO|TCS": quote("TCS", 100.0, 0.0, clock.now)})
    clock.now = (clock.now + timedelta(days=1)).replace(hour=9, minute=15)
    await eng.check_quotes({"DEMO|TCS": quote("TCS", 102.0, 1.0, clock.now)})  # gap open: not a "fast move"
    assert not [a for a in received if a["kind"] == "fast"]
    assert eng.fast_movers() == []


def test_candle_age_measured_from_candle_close():
    close_day = datetime(2026, 10, 6, 15, 35, tzinfo=IST)
    daily = datetime(2026, 10, 6, 0, 0, tzinfo=IST).isoformat()
    assert not AlertEngine.candle_too_old(daily, "1d", close_day)  # today's daily candle just completed
    assert AlertEngine.candle_too_old(daily, "1d", close_day + timedelta(days=1))
    last_15m = datetime(2026, 10, 6, 15, 15, tzinfo=IST).isoformat()
    assert not AlertEngine.candle_too_old(last_15m, "15m", close_day)


async def test_pattern_breakout_alert(engine, monkeypatch):
    eng, clock, received = engine
    candle = (clock.now - timedelta(minutes=15)).isoformat()
    pattern = {"key": "double_bottom", "name": "Double Bottom", "status": "breakout", "direction": "up", "stars": 2,
               "breakout_time": candle, "breakout_level": 100.0, "target": 110.0, "stop": 95.0, "volume_confirmed": True}
    row = {"instrument": {"symbol": "TCS", "instrument_key": "DEMO|TCS"},
           "analysis": {"signal": {"last_candle_time": candle, "status": "ok", "no_trade_reasons": [], "scenario": None},
                        "history": {}, "patterns": {"current": [pattern]}}}

    async def fake_scan(tf):
        return {"rows": [row]}

    async def fake_stats(tf):
        return {"patterns": []}

    monkeypatch.setattr(eng.service, "scan", fake_scan)
    monkeypatch.setattr(eng.service, "pattern_stats", fake_stats)
    monkeypatch.setattr(eng.service, "strategy_stats", fake_stats)
    await eng.check_signals()
    await eng.check_signals()
    pat = [a for a in received if a["kind"] == "pattern"]
    assert len(pat) == 1 and "Double Bottom breakout" in pat[0]["title"] and "Volume confirmed" in pat[0]["message"]


async def test_strategy_alert_once_and_not_for_breakout_probability(engine, monkeypatch):
    eng, clock, received = engine
    candle = (clock.now - timedelta(minutes=15)).isoformat()
    fired = {"time": candle, "side": "sell", "price": 101.3, "bars_ago": 0, "detail": "swept previous day high 103.00"}
    states = [
        {"key": "liquidity_sweep", "name": "Liquidity Sweep", "state": "short", "last_signal": fired},
        {"key": "breakout_prob", "name": "Breakout Probability", "state": "short", "last_signal": fired},
        {"key": "fvg", "name": "Fair Value Gap retest", "state": "none",
         "last_signal": {**fired, "time": (clock.now - timedelta(hours=3)).isoformat()}},
    ]
    row = {"instrument": {"symbol": "TCS", "instrument_key": "DEMO|TCS"},
           "analysis": {"signal": {"last_candle_time": candle, "status": "ok", "no_trade_reasons": [], "scenario": None},
                        "history": {}, "patterns": {"current": []}, "strategies": {"strategies": states}}}

    async def fake_scan(tf):
        return {"rows": [row]}

    async def fake_stats(tf):
        return {}

    monkeypatch.setattr(eng.service, "scan", fake_scan)
    monkeypatch.setattr(eng.service, "pattern_stats", fake_stats)
    monkeypatch.setattr(eng.service, "strategy_stats", fake_stats)
    await eng.check_signals()
    await eng.check_signals()
    got = [a for a in received if a["kind"] == "strategy"]
    assert len(got) == 1 and "Liquidity Sweep" in got[0]["title"] and got[0]["direction"] == "down"
    assert "previous day high" in got[0]["message"]


def test_alert_ids_increase_across_restarts():
    settings = Settings(_env_file=None, universe="TCS")
    a = AlertEngine(MarketService(DemoProvider(), settings), settings)
    first = next(a._ids)
    b = AlertEngine(MarketService(DemoProvider(), settings), settings)
    assert next(b._ids) >= first


async def test_intraday_index_alert_only_on_a_fresh_change(engine):
    eng, clock, received = engine
    candle = (clock.now - timedelta(minutes=5)).isoformat()
    card = {"instrument": {"symbol": "NIFTY 50", "instrument_key": "DEMO|NIFTY 50"}, "verdict": "BUY", "since": candle,
            "since_known": True, "last_candle_time": candle, "price": 25000.0, "reason": "both point up.",
            "entry": {"up_pct": 58.0}, "levels": {"entry_low": 24990.0, "entry_high": 25010.0, "target": 25100.0, "stop": 24940.0}}
    desks = [
        {"indices": [{**card, "since_known": False}], "entry_timeframe": "5m"},  # first look after a restart
        {"indices": [card], "entry_timeframe": "5m"},
        {"indices": [card], "entry_timeframe": "5m"},  # same candle again: no duplicate
    ]

    async def desk(include_stocks=True):
        return desks.pop(0)

    eng.service.intraday = desk
    for _ in range(3):
        await eng.check_intraday()
    got = [a for a in received if a["kind"] == "intraday"]
    assert len(got) == 1 and "INTRADAY BUY" in got[0]["title"] and "target 25,100.00" in got[0]["message"]
