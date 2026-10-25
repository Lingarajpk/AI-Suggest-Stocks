import pytest
from fastapi.testclient import TestClient

from app import telegram_bot as tb


def test_parse_commands():
    assert tb.parse("/call@MyBot reliance") == ("/call", "RELIANCE")
    assert tb.parse("/DESK") == ("/desk", "")
    assert tb.parse("/quote  nifty   50 ") == ("/quote", "NIFTY 50")
    assert tb.parse("hello") == ("", "")


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("NEWS_ENABLED", "false")
    monkeypatch.setenv("WARM_UP", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main

    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c, c.app.state
    get_settings.cache_clear()


def test_bot_answers_from_app_data(state):
    client, st = state
    bot = tb.TelegramBot("x", "1", None, st)

    def ask(cmd, arg=""):
        return client.portal.call(bot.answer, cmd, arg)

    text, buttons = ask("/call", "RELIANCE")
    assert text.startswith("RELIANCE") and "Call:" in text and buttons[0][0] == ("Refresh", "call:RELIANCE")
    assert "not found" in ask("/call", "NOPE999")[0]
    assert "Usage" in ask("/quote")[0]
    assert ask("/desk")[0].startswith("Intraday desk")
    assert ask("/market")[0].startswith("Market board")
    assert "owner" in ask("/paper")[0]
    assert "Commands" in ask("/help")[0] and "Unknown command" in ask("/nope")[0]
