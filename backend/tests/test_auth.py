import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.market_session import now_ist
from app.token_store import DURABLE_KEY, TokenStore

API_KEY = "test-client-id"


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    for k, v in {
        "DATA_MODE": "upstox", "UPSTOX_API_KEY": API_KEY, "UPSTOX_API_SECRET": "s3cret", "ADMIN_KEY": "owner-pass",
        "UPSTOX_OWNER_USER_ID": "OWNER1", "UPSTOX_ACCESS_TOKEN": "", "NEWS_ENABLED": "false", "ALERTS_ENABLED": "false",
        "WARM_UP": "false", "REDIS_URL": "", "DATA_DIR": str(tmp_path_factory.mktemp("data")),
    }.items():
        monkeypatch_module.setenv(k, v)
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main

    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_visitors_cannot_start_an_upstox_login(client):
    assert client.get("/api/auth/upstox/login", follow_redirects=False).status_code == 403
    assert client.get("/api/auth/upstox/login?key=wrong", follow_redirects=False).status_code == 403
    r = client.get("/api/auth/upstox/login?key=owner-pass", follow_redirects=False)
    assert r.status_code == 307 and API_KEY in r.headers["location"]
    assert client.post("/api/auth/upstox/request?key=nope").status_code == 403
    h = client.get("/api/health").json()["upstox"]
    assert h["owner_protected"] and not h["authenticated"]


def test_unauthenticated_data_calls_say_owner_must_reconnect(client):
    body = client.get("/api/quotes").json()
    assert body["error"] == "provider_not_authenticated" and "site owner" in body["message"]


def _payload(**kw):
    return {"client_id": API_KEY, "user_id": "OWNER1", "access_token": "tok-123", "token_type": "Bearer",
            "expires_at": str(int((time.time() + 3600) * 1000)), "issued_at": str(int(time.time() * 1000)),
            "message_type": "access_token", **kw}


def test_notifier_only_accepts_the_owners_token_for_a_pending_request(client):
    app = client.app
    profiles = {"tok-123": {"user_id": "OWNER1", "user_name": "LINGARAJ P K"}, "tok-friend": {"user_id": "FRIEND9", "user_name": "X"}}

    async def profile(token):
        return profiles[token]

    async def request_token():
        return {"authorization_expiry": str(int((time.time() + 600) * 1000)), "notifier_url": "https://x/api/auth/upstox/notifier"}

    app.state.auth.profile = profile
    app.state.auth.request_token = request_token

    # No request outstanding: a token pushed out of the blue is refused.
    assert client.post("/api/auth/upstox/notifier", json=_payload()).status_code == 409
    r = client.post("/api/auth/upstox/request?key=owner-pass")
    assert r.status_code == 200 and r.json()["status"] == "requested"
    assert client.get("/api/health").json()["upstox"]["approval_pending"]
    assert client.post("/api/auth/upstox/notifier", json=_payload(client_id="other-app")).status_code == 403
    assert client.post("/api/auth/upstox/notifier", json=_payload(access_token="tok-friend")).status_code == 403
    assert client.post("/api/auth/upstox/notifier", json=_payload()).json()["status"] == "stored"
    h = client.get("/api/health").json()["upstox"]
    assert h["authenticated"] and h["source"] == "approval" and h["connected_as"] == "Lingaraj"
    assert not h["approval_pending"]


class FakeRedis:
    backend = "redis"

    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ttl_seconds):
        self.data[key] = value


async def test_token_survives_a_restart_through_redis(tmp_path):
    redis = FakeRedis()
    first = TokenStore(tmp_path / "a" / "t.json", durable=redis)
    await first.set("tok-abc", expires_at=now_ist() + timedelta(hours=5), user_id="OWNER1", user_name="Lingaraj")
    assert DURABLE_KEY in redis.data
    # New instance on a wiped disk (what a free-hosting restart looks like).
    second = TokenStore(tmp_path / "b" / "t.json", durable=redis)
    assert second.get() is None
    await second.restore()
    assert second.get() == "tok-abc" and second.user_id == "OWNER1"


async def test_expired_token_is_not_restored(tmp_path):
    redis = FakeRedis()
    first = TokenStore(tmp_path / "a" / "t.json", durable=redis)
    await first.set("old", expires_at=now_ist() + timedelta(seconds=1))
    redis.data[DURABLE_KEY]["expires_at"] = (now_ist() - timedelta(minutes=1)).isoformat()
    second = TokenStore(tmp_path / "b" / "t.json", durable=redis)
    await second.restore()
    assert second.get() is None
