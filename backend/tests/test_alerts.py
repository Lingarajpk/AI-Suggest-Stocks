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
