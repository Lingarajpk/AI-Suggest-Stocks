from datetime import timedelta

import pytest

from app import service as service_mod
from app.alerts import AlertEngine
from app.config import get_settings
from app.providers.demo import DemoProvider
from app.service import MarketService


@pytest.fixture()
def demo(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(service_mod, "is_session_open", lambda *a, **k: True)  # score the forming candle now
    yield MarketService(DemoProvider(), get_settings())
    get_settings.cache_clear()


async def test_early_side_scores_the_forming_candle(demo):
    await demo.refresh_quotes()
    desk = await demo.intraday(include_stocks=False, fresh=True)
    card = next(c for c in desk["indices"] if "error" not in c)
    inst = await demo.instrument(card["instrument"]["symbol"])
    side, info = await demo.early_side(inst, card)
    assert side in (-1, 0, 1) and info["score"] is not None and info["basis"] in ("rules", "model")
    assert info["price"] == demo.cached_quote(inst.instrument_key).last_price
    if side:  # an early side never repeats the confirmed call
        assert {"BUY": 1, "SELL": -1}.get(card["verdict"]) != side and side == card["trend"]["lean"]
        assert (info["levels"]["target"] - info["price"]) * side > 0


async def test_check_early_runs_and_fires_after_hold(demo, monkeypatch):
    engine = AlertEngine(demo, get_settings(), session_open=lambda now: True)
    await demo.refresh_quotes()
    await demo.intraday(include_stocks=False, fresh=True)

    async def always_buy(inst, card):  # force a live early BUY to exercise the alert path
        from app.market_session import now_ist
        from app.providers.base import live_bar_start
        return 1, {"score": 30.0, "up_pct": None, "basis": "rules", "bar": live_bar_start(now_ist(), 5, demo.hours) - timedelta(minutes=5),
                   "price": 100.0, "levels": {"entry": 100.0, "target": 102.5, "stop": 98.5}}

    monkeypatch.setattr(demo, "early_side", always_buy)
    await engine.check_early()
    for st in demo.early.current.values():  # pretend the hold time has passed
        st["first_seen"] = "2000-01-01T00:00:00+05:30"
    await engine.check_early()
    titles = [a["title"] for a in engine.recent()]
    assert any(t.startswith("⚡ EARLY BUY") for t in titles), titles
