import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("DATA_MODE", "demo")
    monkeypatch_module.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main

    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_health_reports_demo(client):
    body = client.get("/api/health").json()
    assert body["data_mode"] == "demo" and body["provider"] == "demo"
    assert "upstox_api_secret" not in str(body).lower()


def test_scanner_and_detail(client):
    scan = client.get("/api/scanner?timeframe=1d").json()
    assert scan["source"] == "demo"
    assert scan["scanned"] == len(scan["rows"]) >= 10
    assert all(r["quote"]["source"] == "demo" for r in scan["rows"])

    detail = client.get("/api/stocks/RELIANCE?timeframe=15m").json()
    assert detail["candles"] and detail["signal"]["method"] == "rule_based_technical_v1"
    assert detail["model_outlook"]["status"] == "not_available"
    assert client.get("/api/stocks/NOTREAL").status_code == 404


def test_upstox_login_rejected_in_demo(client):
    assert client.get("/api/auth/upstox/login", follow_redirects=False).status_code == 400


def test_websocket_streams_quotes(client):
    with client.websocket_connect("/ws/quotes") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "quotes" and msg["source"] == "demo" and len(msg["quotes"]) >= 10


def test_today_outlook(client):
    body = client.get("/api/today").json()
    assert body["source"] == "demo" and body["considered"] >= 10
    for item in body["upside"]:
        assert item["daily_score"] > 0 and (item["intraday_score"] is None or item["intraday_score"] > 0)
        assert "win_rate" in item["track_record"]
    for item in body["downside"]:
        assert item["daily_score"] < 0
    detail = client.get("/api/stocks/TCS?timeframe=1d").json()
    assert "markers" in detail["history"]


def test_alerts_and_movers_endpoints(client):
    assert isinstance(client.get("/api/alerts").json()["alerts"], list)  # may be non-empty during market hours
    m = client.get("/api/movers").json()
    assert set(m) >= {"gainers", "losers", "fast"}
    assert all(r["change_pct"] > 0 for r in m["gainers"]) and all(r["change_pct"] < 0 for r in m["losers"])
    assert client.get("/api/health").json()["alerts"]["enabled"] is True


def test_one_minute_chart_and_forming_candle(client):
    d = client.get("/api/stocks/INFY?timeframe=1m").json()
    assert d["timeframe"] == "1m" and len(d["candles"]) > 100
    assert "forming_candle" in d
    if d["forming_candle"]:
        assert d["forming_candle"]["time"] > d["candles"][-1]["time"]
