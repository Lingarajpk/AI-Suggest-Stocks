import pytest
from fastapi.testclient import TestClient

from app.analysis import probability as pb


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("DATA_MODE", "demo")
    monkeypatch_module.setenv("NEWS_ENABLED", "false")  # no network in tests
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
    o = detail["model_outlook"]
    assert o["status"] == "ok" and o["verdict"] in ("BUY", "SELL", "HOLD", "NO EDGE", "NO TRADE")
    assert abs(o["up_pct"] + o["down_pct"] - 100) < 0.2
    assert o["test"]["samples"] > 1000 and o["test"]["test_period"][0] > o["test"]["train_period"][1]
    assert {d["feature"] for d in o["drivers"]} >= {"trend", "pattern"}
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


def test_patterns_in_detail_and_stats(client):
    d = client.get("/api/stocks/RELIANCE?timeframe=1d").json()
    pats = d["patterns"]
    assert set(pats) >= {"current", "recent", "stock_stats"}
    for p in pats["recent"]:
        assert p["status"] in ("target_hit", "stopped", "expired") and p["breakout_time"] >= p["detected_time"] >= p["start_time"]
    stats = client.get("/api/patterns/stats?timeframe=1d").json()
    assert stats["instruments"] >= 10 and len(stats["patterns"]) >= 25
    assert any(p["resolved"] for p in stats["patterns"])
    scan = client.get("/api/scanner?timeframe=1d").json()
    assert all("patterns" in r["analysis"] for r in scan["rows"] if r["analysis"])


def test_strategies_in_detail_and_stats(client):
    d = client.get("/api/stocks/RELIANCE?timeframe=15m").json()
    st = d["strategies"]
    assert {s["key"] for s in st["strategies"]} >= {"trend_momentum", "liquidity_sweep", "fvg", "ifvg", "amd", "breakout_prob"}
    assert set(st) >= {"breakout", "candles", "volume_profile", "macd", "fvg_zones", "markers"}
    assert d["series"]["supertrend"] and d["series"]["vwap_u1"]
    test = d["model_outlook"]["test"]
    assert test["features_used"][:9] == list(pb.FEATURES.values())
    assert set(test["features_dropped"]).isdisjoint(test["features_used"])
    daily = client.get("/api/stocks/RELIANCE?timeframe=1d").json()
    amd = next(s for s in daily["strategies"]["strategies"] if s["key"] == "amd")
    assert amd["state"] == "n/a"
    stats = client.get("/api/strategies/stats?timeframe=15m").json()
    assert stats["instruments"] >= 10
    keys = {r["key"] for r in stats["rows"]}
    assert "cdl_engulfing_bull" in keys and "trend_momentum" in keys
    assert any(r["signals"] and r["hit_rate"] is not None for r in stats["rows"])


def test_explain_facts_cover_every_timeframe():
    from app.ai.nvidia import build_facts

    for tf in ("1m", "15m", "1h", "1d"):
        detail = {"instrument": {"symbol": "X", "name": "X", "sector": None}, "timeframe": tf, "source": "demo",
                  "quote": None, "last_close": 10.0, "snapshot": {"ema20": 9.0},
                  "signal": {"status": "ok", "signal": "Bullish", "score": 30, "no_trade_reasons": [], "evidence": [],
                             "risks": [], "scenario": None, "levels": {}, "last_candle_time": None, "bars": 100}}
        assert build_facts(detail)["bar_unit"]


def test_news_endpoints_without_network(client):
    feed = client.get("/api/news").json()
    assert feed["enabled"] is False and isinstance(feed["items"], list)
    body = client.get("/api/news/INFY").json()
    assert body["symbol"] == "INFY" and body["summary"]["label"] == "No recent news"
    assert body["adjusted"]["shift_pts"] == 0 and body["learning"]["status"] == "demo"
    assert client.get("/api/news/NOTREAL").status_code == 404


def test_intraday_desk_and_index_pages(client):
    d = client.get("/api/intraday").json()
    assert d["entry_timeframe"] == "5m" and d["trend_timeframe"] == "15m"
    names = [c["instrument"]["symbol"] for c in d["indices"]]
    assert names == ["NIFTY 50", "BANK NIFTY", "SENSEX"]
    for c in d["indices"]:
        assert c["verdict"] in ("BUY", "SELL", "WAIT", "NO TRADE") and c["reason"]
        assert c["chart"]["candles"] and c["chart"]["series"]["vwap"]  # index VWAP = session average price
        if c["verdict"] in ("BUY", "SELL"):
            lv = c["levels"]
            assert (lv["target"] > c["price"] > lv["stop"]) if c["verdict"] == "BUY" else (lv["target"] < c["price"] < lv["stop"])
    s = d["stocks"]
    assert s["scanned"] >= 10 and len(s["buy"]) + len(s["sell"]) + s["waiting"] == s["scanned"]
    assert all(r["entry"]["lean"] == 1 and r["trend"]["lean"] == 1 for r in s["buy"])
    for sym in ("NIFTY50", "BANKNIFTY", "SENSEX"):
        r = client.get(f"/api/stocks/{sym}?timeframe=5m")
        assert r.status_code == 200 and r.json()["instrument"]["kind"] == "index"
    assert client.get("/api/stocks/NOTREAL?timeframe=5m").status_code == 404
