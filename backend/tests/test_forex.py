from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient

from app.analysis.costs import round_trip_pct
from app.forex.greeks import black, implied_vol
from app.forex.service import lot_costs
from app.market_session import CURRENCY, EQUITY, IST, is_session_open, session_state
from app.models import Candle
from app.providers.base import drop_incomplete, front_month, live_bar_start


def at(h: int, m: int) -> datetime:
    return datetime(2026, 10, 19, h, m, tzinfo=IST)  # a Monday


def test_currency_session_is_longer_than_equities():
    assert is_session_open(at(9, 5), CURRENCY) and not is_session_open(at(9, 5), EQUITY)
    assert is_session_open(at(16, 30), CURRENCY) and not is_session_open(at(16, 30))
    assert session_state(at(17, 0), CURRENCY) == "closed" and session_state(at(8, 59), CURRENCY) == "pre_open"


def test_currency_bars_align_to_nine():
    assert live_bar_start(at(10, 7), 60, CURRENCY) == at(10, 0)
    assert live_bar_start(at(10, 7), 60, EQUITY) == at(9, 15)
    # A 16:00 bar is still forming at 16:30 in the currency session (it would count as done after 15:30 for stocks).
    bar = [Candle(time=at(16, 0), open=1, high=1, low=1, close=1, volume=1)]
    assert drop_incomplete(bar, "1h", at(16, 30), CURRENCY) == []
    assert drop_incomplete(bar, "1h", at(17, 0), CURRENCY) == bar


def test_front_month_rolls_two_days_before_expiry():
    futs = [{"expiry": "2026-10-23", "weekly": True}, {"expiry": "2026-10-28", "weekly": False},
            {"expiry": "2026-11-26", "weekly": False}]
    assert front_month(futs, date(2026, 10, 19))["expiry"] == "2026-10-28"  # weekly contracts are skipped
    assert front_month(futs, date(2026, 10, 25))["expiry"] == "2026-10-28"
    assert front_month(futs, date(2026, 10, 26))["expiry"] == "2026-11-26"  # two days left: next month


def test_currency_costs():
    # No STT and a flat Rs 20 brokerage: costs are small per rupee traded but large against a 1-lot move.
    assert 0 < round_trip_pct("currency") < round_trip_pct("futures")
    c = lot_costs(95.0, 1000)
    assert c["tick_value"] == 2.5 and c["per_lot_1"] > c["per_lot_10"]
    assert c["breakeven_ticks_1"] > 15 > c["breakeven_ticks_10"]


def test_implied_vol_round_trip():
    f, k, t = 95.0, 95.25, 7 / 365
    for sign in (1, -1):
        price = black(f, k, t, 0.06, sign)[0]
        assert implied_vol(price, f, k, t, sign) == pytest.approx(0.06, abs=1e-4)
    assert implied_vol(0.1, 95.0, 94.0, t, 1) is None  # below intrinsic value


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DATA_MODE", "demo")
    mp.setenv("NEWS_ENABLED", "false")
    mp.setenv("WARM_UP", "false")
    mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("fxdata")))
    from app.config import get_settings

    get_settings.cache_clear()
    import importlib

    import app.main

    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c
    mp.undo()
    get_settings.cache_clear()


def test_forex_quotes_and_desk(client):
    q = client.get("/api/forex/quotes").json()
    assert {x["symbol"] for x in q["quotes"]} == {"USDINR", "EURINR", "GBPINR", "JPYINR"}
    assert q["contracts"]["USDINR"]["lot_units"] == 1000
    desk = client.get("/api/forex/desk").json()
    assert [c["instrument"]["symbol"] for c in desk["pairs"]] == ["USDINR", "EURINR", "GBPINR", "JPYINR"]
    card = desk["pairs"][0]
    assert card["instrument"]["kind"] == "currency" and card["costs"]["tick_value"] == 2.5
    assert card["verdict"] in ("BUY", "SELL", "WAIT", "NO TRADE") and card["chart"]["candles"]
    # Fully separate from stocks: the stock desk has no currency pairs.
    stock_desk = client.get("/api/intraday?stocks=false").json()
    assert all(c["instrument"]["kind"] != "currency" for c in stock_desk["indices"])


def test_forex_pair_detail(client):
    r = client.get("/api/forex/pairs/usd-inr?timeframe=1h")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["instrument"]["symbol"] == "USDINR" and d["timeframe"] == "1h"
    assert {c["time"][11:16] for c in d["candles"]} <= {f"{h:02d}:00" for h in range(9, 17)}  # hourly bars from 09:00
    assert "10 lots" in d["history"]["notes"]
    assert client.get("/api/forex/pairs/XAUUSD").status_code == 404
    assert client.get("/api/stocks/USDINR").status_code == 404  # not a stock


def test_forex_option_chain(client):
    r = client.get("/api/forex/options/USDINR")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lot_size"] == 1000 and body["tick"] == 0.0025 and body["expiry"] in body["expiries"]
    atm = next(x for x in body["rows"] if x["strike"] == body["summary"]["atm"])
    assert atm["call"]["iv"] is not None and 0 < atm["call"]["delta"] < 1 and -1 < atm["put"]["delta"] < 0
    rec = body["recommendation"]
    assert rec["action"] in ("BUY CE", "BUY PE", "WAIT")
    if rec["pick"]:
        assert rec["pick"]["target"] > rec["pick"]["entry"] > rec["pick"]["stop"]
        assert round(rec["pick"]["entry"] / 0.0025, 6) == round(rec["pick"]["entry"] / 0.0025)  # on the 0.0025 tick
