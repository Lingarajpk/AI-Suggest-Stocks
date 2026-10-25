from datetime import datetime

import pytest

from app import paper
from app.market_session import IST

T0 = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
KEY = "NSE_EQ|X"


def _order(acc, **kw):
    base = {"side": "BUY", "type": "market", "product": "intraday", "qty": 10, "note": "breakout with volume"}
    o = paper.validate_order(acc, {**base, **kw})
    return paper.place(acc, o, "X", KEY, T0)


def test_validation_rules():
    acc = paper.new_account(100_000)
    with pytest.raises(paper.PaperError, match="why"):
        paper.validate_order(acc, {"side": "BUY", "qty": 1, "note": ""})
    with pytest.raises(paper.PaperError, match="intraday only"):
        paper.validate_order(acc, {"side": "SELL", "qty": 1, "product": "delivery", "note": "short it"})
    with pytest.raises(paper.PaperError, match="stop must be below"):
        paper.validate_order(acc, {"side": "BUY", "qty": 1, "note": "test", "ref_price": 100, "stop": 101})
    with pytest.raises(paper.PaperError, match="limit price"):
        paper.validate_order(acc, {"side": "BUY", "qty": 1, "note": "test", "type": "limit"})


def test_bracket_target_hit_cancels_stop_and_books_net():
    acc = paper.new_account(100_000)
    _order(acc, stop=95, target=110)
    paper.process(acc, {KEY: 100.0}, T0, True)
    assert len(acc["positions"]) == 1
    pos = acc["positions"][0]
    assert pos["entry"] == pytest.approx(100.03)  # market buy pays intraday slippage (0.03%)
    legs = [o for o in acc["orders"] if o.get("position") == pos["id"]]
    assert {o["role"] for o in legs} == {"stop", "target"}
    paper.process(acc, {KEY: 104.0}, T0, True)  # nothing yet
    assert acc["positions"]
    events = paper.process(acc, {KEY: 110.5}, T0, True)
    assert not acc["positions"] and "Target hit" in events[0]
    t = acc["journal"][-1]
    assert t["reason"] == "target" and t["exit"] == pytest.approx(110.4669, abs=1e-3)  # limit: never worse than 110
    assert t["charges"] > 0 and t["net"] == pytest.approx(t["gross"] - t["charges"], abs=0.01)
    assert t["mfe_pct"] > 9 and t["mae_pct"] == 0.0
    assert all(o["status"] != "open" for o in legs)  # the stop leg was cancelled (OCO)
    assert acc["cash"] == pytest.approx(100_000 + t["net"], abs=0.01)


def test_trailing_stop_follows_price_up():
    acc = paper.new_account(100_000)
    _order(acc, trail_pct=2)
    paper.process(acc, {KEY: 100.0}, T0, True)
    stop = next(o for o in acc["orders"] if o["role"] == "stop")
    assert stop["trigger"] == pytest.approx(100.03 * 0.98, abs=1e-3)
    paper.process(acc, {KEY: 120.0}, T0, True)
    assert stop["trigger"] == pytest.approx(117.6)
    paper.process(acc, {KEY: 117.0}, T0, True)
    assert acc["journal"][-1]["reason"] == "stop" and acc["journal"][-1]["net"] > 0


def test_limit_entry_waits_and_short_square_off():
    acc = paper.new_account(100_000)
    _order(acc, side="SELL", type="limit", price=105)
    paper.process(acc, {KEY: 100.0}, T0, True)
    assert not acc["positions"]  # a sell limit at 105 waits
    paper.process(acc, {KEY: 106.0}, T0, True)
    assert acc["positions"][0]["side"] == "short" and acc["positions"][0]["entry"] >= 105
    late = T0.replace(hour=15, minute=16)
    events = paper.process(acc, {KEY: 101.0}, late, True)
    assert not acc["positions"] and "Squared off" in events[0]
    assert acc["journal"][-1]["reason"] == "square_off" and acc["journal"][-1]["gross"] > 0


def test_no_fills_while_closed_and_cash_check():
    acc = paper.new_account(10_000)
    _order(acc, qty=1000)  # needs 1,00,000
    paper.process(acc, {KEY: 100.0}, T0, False)
    assert acc["orders"][0]["status"] == "open"  # market closed: waits
    paper.process(acc, {KEY: 100.0}, T0, True)
    assert acc["orders"][0]["status"] == "rejected" and "cash" in acc["orders"][0]["reason"]


def test_mark_and_stats():
    acc = paper.new_account(100_000)
    _order(acc)
    paper.process(acc, {KEY: 100.0}, T0, True)
    paper.process(acc, {KEY: 102.0}, T0, False)  # marks to market even when closed
    m = paper.mark(acc)
    assert m["unrealized"] == pytest.approx(19.7, abs=0.01) and m["equity"] == pytest.approx(100_019.7, abs=0.01)
    paper.close_position(acc, acc["positions"][0]["id"], 102.0, T0, "manual")
    st = paper.journal_stats(acc["journal"])
    assert st["trades"] == 1 and st["win_rate"] == 100.0 and st["evidence"]["grade"] == "insufficient"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("NEWS_ENABLED", "false")
    monkeypatch.setenv("WARM_UP", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    from fastapi.testclient import TestClient

    import app.main

    importlib.reload(app.main)
    monkeypatch.setattr(app.main, "is_session_open", lambda *a, **k: False)  # the market is closed for this test
    with TestClient(app.main.app) as c:
        yield c
    get_settings.cache_clear()


DEV = "test-device-0123456789"


def test_paper_api_flow(client):
    r = client.get(f"/api/paper/{DEV}").json()
    assert r["account"]["equity"] == paper.DEFAULT_CAPITAL and r["positions"] == []
    assert client.get("/api/paper/bad").status_code == 400
    bad = client.post(f"/api/paper/{DEV}/orders", json={"symbol": "RELIANCE", "side": "BUY", "qty": 1, "note": ""})
    assert bad.status_code == 400 and "why" in bad.json()["detail"]
    ok = client.post(f"/api/paper/{DEV}/orders", json={"symbol": "RELIANCE", "side": "BUY", "qty": 5, "note": "test entry",
                                                     "type": "limit", "price": 1.0}).json()
    assert ok["order"]["status"] == "open" and ok["orders"][0]["id"] == ok["order"]["id"]
    assert client.post(f"/api/paper/{DEV}/orders", json={"symbol": "NOPE123", "side": "BUY", "qty": 1, "note": "x y z"}).status_code == 404
    after = client.delete(f"/api/paper/{DEV}/orders/{ok['order']['id']}").json()
    assert after["orders"][0]["status"] == "cancelled"
    reset = client.post(f"/api/paper/{DEV}/reset", json={"capital": 50000}).json()
    assert reset["account"]["capital"] == 50000 and reset["orders"] == []
