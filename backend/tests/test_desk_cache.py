import asyncio
from datetime import datetime

from app.market_session import IST
from app.providers.base import live_bar_start
from app.service import MarketService


def at(h, m, s=0):
    return datetime(2026, 10, 19, h, m, s, tzinfo=IST)


def test_live_bar_start_aligns_to_the_open():
    assert live_bar_start(at(10, 44, 59), 5) == at(10, 40)
    assert live_bar_start(at(10, 45, 0), 5) == at(10, 45)
    assert live_bar_start(at(10, 44), 15) == at(10, 30)
    assert live_bar_start(at(9, 15), 60) == at(9, 15)
    assert live_bar_start(at(10, 20), 60) == at(10, 15)
    assert live_bar_start(at(9, 0), 5) == at(9, 15)


class CountingService(MarketService):
    """Desk caching around a fake, countable build."""

    def __init__(self):  # noqa: D107 - no provider needed
        self.builds = 0
        self._desk, self._desk_locks, self._desk_tasks = {}, {}, {}

    async def _build_intraday(self, include_stocks=True):
        self.builds += 1
        await asyncio.sleep(0.01)
        desk = {"indices": [{"instrument": {"symbol": "NIFTY 50"}, "verdict": "BUY"}], "n": self.builds}
        if include_stocks:
            desk["stocks"] = {"buy": [], "sell": []}
        return desk


def test_desk_is_served_from_cache_and_rebuilt_in_background(monkeypatch):
    async def run():
        svc = CountingService()
        first = await svc.intraday()
        assert svc.builds == 1 and first["n"] == 1
        # Within 20 s and the same bar: cached, no rebuild; the index-only desk is shared.
        assert (await svc.intraday())["n"] == 1
        assert "stocks" not in await svc.intraday(include_stocks=False) and svc.builds == 1
        assert svc.cached_card("NIFTY 50")["verdict"] == "BUY"

        # Stale: the old desk comes back at once while a rebuild runs in the background.
        t, bar, desk = svc._desk[True]
        svc._desk[True] = (t - 60, bar, desk)
        assert (await svc.intraday())["n"] == 1
        await svc._desk_tasks[True]
        assert svc.builds == 2 and (await svc.intraday())["n"] == 2

        # fresh=True waits for a rebuild when stale; concurrent callers share one build.
        t, bar, desk = svc._desk[True]
        svc._desk[True] = (t - 60, bar, desk)
        a, b = await asyncio.gather(svc.intraday(fresh=True), svc.intraday(fresh=True))
        assert a["n"] == b["n"] == 3 and svc.builds == 3

    asyncio.run(run())
