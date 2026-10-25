import importlib

import pytest

from app import auth_users
from app.auth_users import COOKIE, Auth, hash_password, parse_users, verify_password

PW = "Test-pass-9x"


def test_hash_and_verify():
    h = hash_password(PW, iterations=1000)
    assert h.startswith("pbkdf2_sha256.1000.") and PW not in h
    assert verify_password(PW, h) and not verify_password("test-pass-9x", h)
    assert not verify_password(PW, "garbage")


def test_parse_users_keeps_display_name():
    users = parse_users(f"Akshay:{hash_password(PW, 1000)}, Lingaraj:{hash_password(PW, 1000)},bad")
    assert set(users) == {"akshay", "lingaraj"} and users["akshay"][0] == "Akshay"


def test_tokens_sign_expire_and_kind():
    a = Auth(f"Akshay:{hash_password(PW, 1000)}", "secret")
    tok = a.issue("Akshay", now=1000, seconds=60)
    assert a.verify(tok, now=1030) == "Akshay"
    assert a.verify(tok, now=1100) is None  # expired
    assert a.verify(tok, kind="ws", now=1030) is None  # a session cookie is not a websocket token
    body, sig = tok.rsplit(".", 1)
    assert a.verify(body + ".x" + sig[2:], now=1030) is None  # tampered signature
    assert Auth(f"Akshay:{hash_password(PW, 1000)}", "other").verify(tok, now=1030) is None  # different secret
    assert Auth(f"Lingaraj:{hash_password(PW, 1000)}", "secret").verify(tok, now=1030) is None  # user removed


def test_login_check_and_lockout():
    a = Auth(f"Akshay:{hash_password(PW, 1000)}", "secret")
    assert a.check("akshay", PW, "1.1.1.1", now=0) == "Akshay"  # username is case-insensitive
    for _ in range(auth_users.MAX_FAILURES):
        assert a.check("Akshay", "nope", "2.2.2.2", now=10) is None
    assert a.locked("Akshay", "3.3.3.3", now=20)  # locked by user
    assert a.locked("someone", "2.2.2.2", now=20)  # and by IP
    assert not a.locked("Akshay", "3.3.3.3", now=10 + auth_users.FAILURE_WINDOW + 1)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("NEWS_ENABLED", "false")
    monkeypatch.setenv("WARM_UP", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_USERS", f"Akshay:{hash_password(PW, 1000)},Lingaraj:{hash_password(PW, 1000)}")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    import app.main

    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c
    get_settings.cache_clear()


def test_api_requires_login(client):
    assert client.get("/api/health").json()["login_required"] is True  # open: keep-alive pings
    r = client.get("/api/quotes")
    assert r.status_code == 401 and r.json()["error"] == "login_required"
    assert client.get("/api/forex/pairs").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/upstox/notifier", json={"message_type": "other"}).json() == {"status": "ignored"}

    bad = client.post("/api/auth/login", json={"username": "Akshay", "password": "wrong"})
    assert bad.status_code == 401 and COOKIE not in client.cookies

    ok = client.post("/api/auth/login", json={"username": "lingaraj", "password": PW})
    assert ok.status_code == 200 and ok.json()["user"] == "Lingaraj"
    assert "httponly" in ok.headers["set-cookie"].lower()
    assert client.get("/api/quotes").status_code == 200
    assert client.get("/api/auth/me").json()["user"] == "Lingaraj"

    tok = client.get("/api/auth/ws-token").json()["token"]
    with client.websocket_connect(f"/ws/quotes?token={tok}") as ws:
        assert ws.receive_json()["type"] in ("quotes", "error")
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/quotes") as ws:
        ws.receive_json()

    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/quotes").status_code == 401
