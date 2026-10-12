from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.alerts import AlertEngine
from app.config import Settings
from app.market_session import IST
from app.providers.demo import DemoProvider
from app.service import MarketService
from app.watchlists import WatchlistStore, clean_symbols

DEVICE = "dev-1234567890abcdef"
OTHER = "dev-fedcba0987654321"


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    for k, v in {"DATA_MODE": "demo", "NEWS_ENABLED": "false", "ALERTS_ENABLED": "false", "WARM_UP": "false",
                 "ADMIN_KEY": "owner-pass", "REDIS_URL": "", "DATA_DIR": str(tmp_path_factory.mktemp("data"))}.items():
        monkeypatch_module.setenv(k, v)
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main

    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c
    get_settings.cache_clear()


def test_search_finds_stocks_beyond_the_universe_and_indices(client):
    res = client.get("/api/instruments/search?q=tata").json()["results"]
    syms = [r["symbol"] for r in res]
    assert "TATAMOTORS" in syms and "TATASTEEL" in syms
    assert client.get("/api/instruments/search?q=nifty").json()["results"][0]["kind"] == "index"


def test_watchlist_roundtrip_with_a_stock_outside_the_universe(client):
    r = client.put(f"/api/watchlist/{DEVICE}", json={"symbols": ["tatamotors", "NIFTY 50", "NOTREAL", "TCS"]})
    assert r.json()["symbols"] == ["TATAMOTORS", "NIFTY 50", "TCS"]  # unknown symbols dropped
    assert client.get(f"/api/watchlist/{DEVICE}").json()["symbols"] == ["TATAMOTORS", "NIFTY 50", "TCS"]
    calls = client.get(f"/api/watchlist/{DEVICE}/calls").json()["rows"]
    assert [c["symbol"] for c in calls] == ["TATAMOTORS", "NIFTY 50", "TCS"]
    for c in calls:
        assert c["action"]["label"] and c["verdict"] in ("BUY", "SELL", "WAIT", "NO TRADE")
    # A stock outside the universe gets a detail page too.
    assert client.get("/api/stocks/TATAMOTORS?timeframe=5m").status_code == 200
    assert client.get("/api/watchlist/short").status_code == 400


def test_push_subscription_and_owner_flag(client):
    assert client.get("/api/push/key").json()["public_key"]
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}}
    assert client.put(f"/api/watchlist/{DEVICE}/push", json={"subscription": sub}).json()["push"] is True
    assert client.put(f"/api/watchlist/{DEVICE}/push", json={"subscription": {"endpoint": "http://bad"}}).status_code == 400
    assert client.put(f"/api/watchlist/{DEVICE}/owner?key=nope").status_code == 403
    assert client.put(f"/api/watchlist/{DEVICE}/owner?key=owner-pass").json()["owner"] is True
    assert client.get(f"/api/watchlist/{DEVICE}").json() == {"symbols": ["TATAMOTORS", "NIFTY 50", "TCS"], "push": True, "owner": True}


def test_clean_symbols_dedupes_and_caps():
    assert clean_symbols(["tcs", "TCS", " bank  nifty ", "bad;sym"]) == ["TCS", "BANK NIFTY"]
    assert len(clean_symbols([f"S{i}" for i in range(100)])) == 30


def test_position_follows_a_call_to_target_then_needs_a_reset():
    svc = MarketService(DemoProvider(), Settings(_env_file=None))
    lv = {"target": 110.0, "stop": 95.0}
    c1, c2, c3, c4 = (f"2026-10-05T10:{m}:00+05:30" for m in ("00", "05", "10", "15"))
    p = svc._follow("X", "BUY", lv, 100.0, c1)
    assert p["status"] == "open" and p["entry"] == 100.0
    assert svc._action("BUY", p)["label"] == "HOLD BUY"
    p = svc._follow("X", "BUY", lv, 111.0, c2)
    assert p["status"] == "target" and p["pnl_pct"] == 11.0
    assert svc._action("BUY", p)["label"] == "SELL NOW"
    # Still BUY on the next candle: don't re-enter until the signal resets.
    assert svc._follow("X", "BUY", lv, 111.5, c3)["status"] == "target"
    svc._follow("X", "WAIT", None, 111.0, c3)
    p = svc._follow("X", "BUY", {"target": 120.0, "stop": 105.0}, 112.0, c4)
    assert p["status"] == "open" and p["entry"] == 112.0 and p["previous"]["status"] == "target"


def test_position_exit_when_the_signal_ends_and_stop():
    svc = MarketService(DemoProvider(), Settings(_env_file=None))
    lv = {"target": 90.0, "stop": 105.0}
    svc._follow("Y", "SELL", lv, 100.0, "2026-10-05T10:00:00+05:30")
    p = svc._follow("Y", "WAIT", None, 99.0, "2026-10-05T10:05:00+05:30")
    assert p["status"] == "signal_ended" and p["pnl_pct"] == 1.0
    assert svc._action("WAIT", p)["label"] == "BUY BACK NOW"
    svc2 = MarketService(DemoProvider(), Settings(_env_file=None))
    svc2._follow("Z", "BUY", {"target": 110.0, "stop": 95.0}, 100.0, "2026-10-05T10:00:00+05:30")
    assert svc2._follow("Z", "BUY", None, 94.0, "2026-10-05T10:05:00+05:30")["status"] == "stop"


class FakePush:
    def __init__(self):
        self.sent = []

    async def send(self, sub, payload):
        self.sent.append((sub["endpoint"], payload))
        return sub["endpoint"] != "https://gone"


async def test_exit_alert_goes_to_watchers_by_push_and_owner_telegram(tmp_path):
    settings = Settings(_env_file=None, universe="TCS")
    clock_now = datetime(2026, 10, 6, 11, 0, tzinfo=IST)
    eng = AlertEngine(MarketService(DemoProvider(), settings), settings, clock=lambda: clock_now, session_open=lambda _: True)
    store = WatchlistStore(tmp_path / "w.json", None)
    await store.set_symbols(DEVICE, ["TATAMOTORS"])
    await store.set_push(DEVICE, {"endpoint": "https://watcher"})
    await store.set_symbols(OTHER, ["TCS"])
    await store.set_push(OTHER, {"endpoint": "https://gone"})
    await store.set_owner(OTHER, True)
    push = FakePush()
    telegram = []

    class Tg:
        async def send(self, alert):
            telegram.append(alert)

    eng.watchlists, eng.push, eng.telegram = store, push, Tg()
    closed_at = (clock_now - timedelta(minutes=5)).isoformat()
    card = {"instrument": {"symbol": "TATAMOTORS", "instrument_key": "DEMO|TATAMOTORS"}, "verdict": "WAIT",
            "position": {"side": "BUY", "entry": 100.0, "target": 103.0, "stop": 98.0, "opened": "2026-10-06T10:00:00+05:30",
                         "status": "target", "exit_price": 103.2, "closed": closed_at, "pnl_pct": 3.2}}
    await eng._exit_alert(card, "5m", clock_now)
    await eng._exit_alert(card, "5m", clock_now)  # once only
    trade = [a for a in eng.alerts if a["kind"] == "trade"]
    assert len(trade) == 1 and "SELL NOW" in trade[0]["title"] and "target reached" in trade[0]["title"]
    assert [e for e, _ in push.sent] == ["https://watcher"]  # only the device watching TATAMOTORS
    assert telegram == []  # the owner watches TCS, not TATAMOTORS

    card2 = {**card, "instrument": {"symbol": "TCS", "instrument_key": "DEMO|TCS"}}
    await eng._exit_alert(card2, "5m", clock_now)
    assert len(telegram) == 1 and telegram[0]["symbol"] == "TCS"
    assert store.get(OTHER)["push"] is False  # expired subscription removed
