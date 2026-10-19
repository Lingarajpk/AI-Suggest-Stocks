import asyncio

import pandas as pd
import pytest

from app.analysis import history, lookahead
from app.providers.demo import DemoProvider


def demo_frame(symbol: str, timeframe: str, bars: int = 900) -> pd.DataFrame:
    async def load():
        p = DemoProvider()
        if symbol == "NIFTY 50":
            inst = (await p.resolve_indices())[0][0]
        else:
            inst = (await p.resolve_equities([symbol]))[0][0]
        return await p.candles(inst, timeframe)

    candles = asyncio.run(load())
    return pd.DataFrame([c.model_dump() for c in candles]).set_index("time").iloc[-bars:]


@pytest.mark.parametrize("symbol,timeframe", [("RELIANCE", "15m"), ("RELIANCE", "1d"), ("NIFTY 50", "5m")])
def test_no_signal_indicator_or_pattern_uses_later_candles(symbol, timeframe):
    issues = lookahead.check(demo_frame(symbol, timeframe), timeframe, cuts=4)
    assert issues == [], "\n".join(issues)


def test_checker_catches_a_leak(monkeypatch):
    real = history.score_series
    monkeypatch.setattr(history, "score_series", lambda df, ind: real(df, ind).shift(-2))  # peeks 2 candles ahead
    issues = lookahead.check(demo_frame("RELIANCE", "15m"), "15m", cuts=2)
    assert issues and all("signal score" in i for i in issues)
