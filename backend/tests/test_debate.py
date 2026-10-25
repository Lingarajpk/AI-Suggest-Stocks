import json

import pytest
from fastapi.testclient import TestClient

from app.ai import debate as db


def test_rating_and_json_parsing():
    assert db.rating_of("strong buy") == "STRONG BUY" and db.rating_of("Hold") == "HOLD"
    assert db.rating_of("overweight") == "REVIEW" and db.rating_of(None) == "REVIEW"
    assert db._json('Sure! ```json\n{"rating": "BUY", "entry": 100}\n```') == {"rating": "BUY", "entry": 100}
    assert db._json("no json here") is None


def test_level_checks():
    ok = {"rating": "BUY", "entry": 100.0, "stop": 98.0, "target": 104.0}
    assert db.check_levels(ok, price=100.2, atr=2.0) == []
    wrong_side = {**ok, "stop": 102.0}
    assert "wrong sides" in " ".join(db.check_levels(wrong_side, 100.2, 2.0))
    far = {**ok, "entry": 110.0, "stop": 108.0, "target": 115.0}
    assert any("1 ATR" in x for x in db.check_levels(far, 100.2, 2.0))
    tight = {**ok, "stop": 99.9}
    assert any("ATR away" in x for x in db.check_levels(tight, 100.2, 2.0))
    assert db.check_levels({"rating": "HOLD"}, 100, 2) == []
    assert db.check_levels({"rating": "SELL", "entry": None, "stop": None, "target": None}, 100, 2) == ["levels missing"]


def test_settle_past_calls():
    hist = [{"time": "t1", "rating": "BUY", "price": 100.0}, {"time": "t2", "rating": "SELL", "price": 110.0},
            {"time": "t3", "rating": "HOLD", "price": 105.0}, {"time": "t4", "rating": "REVIEW", "price": 105.0}]
    out = db.settle(hist, 105.0)
    assert [o["worked"] for o in out] == [True, True, None]
    assert out[0]["move_since_pct"] == 5.0


REPLIES = {
    "BULL": '{"points": ["Trend is up"], "strongest": "Trend"}',
    "BEAR": '{"points": ["RSI stretched"], "rebuttal": "Trend is late", "strongest": "Risk"}',
    "JUDGE": '{"rating": "Hold", "confidence": "low", "thesis": "Mixed evidence.", "entry": null, "stop": null, "target": null,'
             ' "horizon": "5 days", "change_my_mind": "A close above resistance"}',
    "RISK": '{"verdict": "approve", "notes": ["Nothing to trade"], "size_hint": "none"}',
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("NEWS_ENABLED", "false")
    monkeypatch.setenv("WARM_UP", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main

    importlib.reload(app.main)
    calls = []

    async def fake_chat(self, system, user, max_tokens=600, temperature=0.2):
        role = next(k for k in REPLIES if f"Your role: {k}" in system)
        calls.append(role)
        return REPLIES[role]

    monkeypatch.setattr("app.ai.nvidia.NvidiaExplainer.chat", fake_chat)
    with TestClient(app.main.app) as c:
        c.calls = calls
        yield c
    get_settings.cache_clear()


def _events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_debate_streams_all_steps_and_caches(client):
    evs = _events(client.post("/api/stocks/RELIANCE/debate?timeframe=1d"))
    assert [e["step"] for e in evs] == ["facts", "bull", "bear", "judge", "risk", "done"]
    assert evs[3]["rating"] == "HOLD" and evs[0]["facts"]["data_source"] == "demo"
    assert "outlook_model" in evs[0]["facts"] and "past_calls_on_this_chart" in evs[0]["facts"]
    assert client.calls == ["BULL", "BEAR", "JUDGE", "RISK"]
    again = _events(client.post("/api/stocks/RELIANCE/debate?timeframe=1d"))
    assert all(e.get("cached") for e in again) and len(client.calls) == 4  # served from cache
