"""Automatic alerts during market hours.

Runs on the backend independently of any open browser. Three kinds of alert:
  * signal  - a new BUY/SELL signal point on a just-completed candle (default 15m)
  * level   - the day's change crosses a threshold (default +/-2% and +/-4%), once per day
  * fast    - price moves >= FAST_MOVE_PCT within FAST_WINDOW minutes (with a cooldown)

Every alert is kept in memory (recent history), pushed to browsers over the WebSocket,
and optionally sent to Telegram.
"""

import asyncio
import itertools
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Awaitable, Callable

import httpx

from app.config import Settings
from app.market_session import is_session_open, now_ist
from app.models import Quote
from app.providers.base import ProviderError
from app.service import MarketService

log = logging.getLogger(__name__)
Listener = Callable[[dict], Awaitable[None]]


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, http: httpx.AsyncClient) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._http = http

    async def send(self, alert: dict) -> None:
        text = f"{alert['title']}\n{alert['message']}\n\nAI Stock alert · not investment advice"
        try:
            r = await self._http.post(self._url, json={"chat_id": self._chat_id, "text": text}, timeout=15)
            if r.status_code != 200:
                log.warning("Telegram send failed: HTTP %s %s", r.status_code, r.text[:200])
        except httpx.HTTPError as exc:
            log.warning("Telegram send failed: %s", exc.__class__.__name__)


class AlertEngine:
    def __init__(
        self,
        service: MarketService,
        settings: Settings,
        clock: Callable[[], datetime] = now_ist,
        session_open: Callable[[datetime], bool] = is_session_open,
    ) -> None:
        self.service = service
        self.settings = settings
        self.clock = clock
        self.session_open = session_open
        self.alerts: deque[dict] = deque(maxlen=200)
        self.listeners: list[Listener] = []
        self.telegram: TelegramNotifier | None = None
        self._ids = itertools.count(1)
        self._fired: dict[str, datetime] = {}
        self._prices: dict[str, deque[tuple[datetime, float]]] = {}
        self._last_signal_check: datetime | None = None
        self._task: asyncio.Task | None = None
        self.levels = sorted({abs(float(x)) for x in settings.alert_move_levels.split(",") if x.strip()})

    # ---- lifecycle ---------------------------------------------------
    def start(self) -> None:
        if self.settings.alerts_enabled and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            now = self.clock()
            if self.session_open(now):
                try:
                    await self.check_quotes(await self.service.refresh_quotes())
                    if not self._last_signal_check or now - self._last_signal_check >= timedelta(seconds=60):
                        self._last_signal_check = now
                        await self.check_signals()
                except ProviderError as exc:
                    if exc.http_status == 401:
                        await self._emit_once("auth", "system", "Upstox login needed", "Alerts are paused until you log in with Upstox again.", None, None)
                        await asyncio.sleep(60)
                        continue
                    log.warning("alert check failed: %s", exc.message)
                except Exception:
                    log.exception("alert check failed")
                await asyncio.sleep(self.settings.quote_poll_seconds)
            else:
                await asyncio.sleep(30)

    # ---- checks ------------------------------------------------------
    async def check_quotes(self, quotes: dict[str, Quote]) -> None:
        now = self.clock()
        equities, _ = await self.service.universe()
        for inst in equities:
            q = quotes.get(inst.instrument_key)
            if q is None or q.freshness in ("stale", "unavailable"):
                continue
            sym = inst.symbol

            # Day-change levels, each fired once per day per direction.
            if q.change_pct is not None:
                for lvl in self.levels:
                    if q.change_pct >= lvl:
                        await self._emit_once(f"level:{sym}:+{lvl}:{now.date()}", "up", f"🟢 {sym} up {q.change_pct:+.2f}% today",
                                              f"{sym} crossed +{lvl:g}% for the day at ₹{q.last_price:,.2f}.", sym, q)
                    elif q.change_pct <= -lvl:
                        await self._emit_once(f"level:{sym}:-{lvl}:{now.date()}", "down", f"🔴 {sym} down {q.change_pct:+.2f}% today",
                                              f"{sym} crossed -{lvl:g}% for the day at ₹{q.last_price:,.2f}.", sym, q)

            # Fast move versus the price FAST_WINDOW minutes ago.
            hist = self._prices.setdefault(sym, deque())
            hist.append((now, q.last_price))
            window = timedelta(minutes=self.settings.alert_fast_window_minutes)
            while len(hist) > 1 and now - hist[1][0] >= window:
                hist.popleft()
            then, old = hist[0]
            if now - then >= window * 0.9 and old:
                move = (q.last_price / old - 1) * 100
                if abs(move) >= self.settings.alert_fast_move_pct:
                    d = "up" if move > 0 else "down"
                    mins = round((now - then).total_seconds() / 60)
                    await self._emit_cooldown(
                        f"fast:{sym}:{d}", d,
                        f"{'⚡🟢' if d == 'up' else '⚡🔴'} {sym} moving {'up' if d == 'up' else 'down'} fast: {move:+.2f}% in {mins} min",
                        f"{sym} went from ₹{old:,.2f} to ₹{q.last_price:,.2f} in {mins} minutes (day {q.change_pct:+.2f}%).", sym, q,
                    )

    async def check_signals(self) -> None:
        tf = self.settings.alert_signal_timeframe
        scan = await self.service.scan(tf)
        now = self.clock()
        for row in scan["rows"]:
            a = row.get("analysis")
            if not a:
                continue
            sig, hist = a["signal"], a.get("history") or {}
            await self._check_trade(row, sig, hist, tf, now)
            m = hist.get("latest_marker")
            # Only a marker on the newest completed candle is "new"; older ones were already shown on the chart.
            if not m or m["time"] != sig.get("last_candle_time"):
                continue
            if now - datetime.fromisoformat(m["time"]) > timedelta(hours=2):
                continue
            sym = row["instrument"]["symbol"]
            side = m["side"]
            stats = hist.get(side) or {}
            base_up = hist.get("base_rate_up_pct")
            base = base_up if side == "buy" or base_up is None else round(100 - base_up, 1)
            record = (
                f"Past {side.upper()} signals on {sym}: right {stats['win_rate']}% of {stats['count']} (random {base}%)."
                if stats.get("count") else "No past signals to compare."
            )
            q = self.service.cached_quote(row["instrument"]["instrument_key"])
            sc = sig.get("scenario")
            levels = f" Zone ₹{sc['entry_low']:,.2f}–{sc['entry_high']:,.2f}, target ₹{sc['target']:,.2f}, stop ₹{sc['stop_loss']:,.2f}." if sc else ""
            status = " Marked NO TRADE: " + "; ".join(sig["no_trade_reasons"]) + "." if sig["status"] == "no_trade" else ""
            await self._emit_once(
                f"signal:{sym}:{tf}:{m['time']}", "up" if side == "buy" else "down",
                f"{'▲ BUY' if side == 'buy' else '▼ SELL'} signal: {sym} ({tf})",
                f"{sym} at ₹{m['price']:,.2f}, score {m['score']:+.0f}.{levels}{status} {record}", sym, q,
            )

    async def _check_trade(self, row: dict, sig: dict, hist: dict, tf: str, now: datetime) -> None:
        """Exit and early-warning alerts for the trade opened by a BUY/SELL signal."""
        sym = row["instrument"]["symbol"]
        last_candle = sig.get("last_candle_time")
        if not last_candle or now - datetime.fromisoformat(last_candle) > timedelta(hours=2):
            return
        q = self.service.cached_quote(row["instrument"]["instrument_key"])
        ex = hist.get("latest_exit")
        if ex and ex["exit_time"] == last_candle:
            was_buy = ex["side"] == "buy"
            await self._emit_once(
                f"exit:{sym}:{tf}:{ex['entry_time']}", "down" if was_buy else "up",
                f"{'⚠ EXIT BUY' if was_buy else '⚠ EXIT SELL'}: {sym} ({tf}) — {ex['reason_text']}",
                f"{'Sell your buy' if was_buy else 'Buy back your sell'} from ₹{ex['entry_price']:,.2f}: {sym} is now "
                f"₹{ex['exit_price']:,.2f}, {ex['reason_text']}. Result {ex['pnl_pct']:+.2f}% over {ex['bars_held']} candles.",
                sym, q,
            )
        ot = hist.get("open_trade")
        if ot and ot["status"] == "weakening":
            was_buy = ot["side"] == "buy"
            await self._emit_once(
                f"weak:{sym}:{tf}:{ot['entry_time']}", "down" if was_buy else "up",
                f"⚠ {sym} {'turning down after BUY' if was_buy else 'turning up after SELL'} ({tf})",
                f"Open {ot['side'].upper()} from ₹{ot['entry_price']:,.2f} is {ot['pnl_pct']:+.2f}% now: "
                f"{', '.join(ot['warnings'])}. Watch the stop at ₹{ot['stop']:,.2f}.",
                sym, q,
            )

    # ---- emit --------------------------------------------------------
    async def _emit_once(self, key: str, direction: str, title: str, message: str, symbol: str | None, q: Quote | None) -> None:
        if key in self._fired:
            return
        await self._emit(key, direction, title, message, symbol, q)

    async def _emit_cooldown(self, key: str, direction: str, title: str, message: str, symbol: str | None, q: Quote | None) -> None:
        last = self._fired.get(key)
        if last and self.clock() - last < timedelta(minutes=self.settings.alert_cooldown_minutes):
            return
        await self._emit(key, direction, title, message, symbol, q)

    async def _emit(self, key: str, direction: str, title: str, message: str, symbol: str | None, q: Quote | None) -> None:
        now = self.clock()
        self._fired[key] = now
        if len(self._fired) > 5000:  # forget yesterday's keys
            cutoff = now - timedelta(days=1)
            self._fired = {k: v for k, v in self._fired.items() if v >= cutoff}
        alert = {
            "id": next(self._ids),
            "time": now.isoformat(),
            "kind": key.split(":")[0],
            "direction": direction,
            "symbol": symbol,
            "title": title,
            "message": message,
            "price": q.last_price if q else None,
            "change_pct": q.change_pct if q else None,
            "source": self.service.provider.name,
        }
        self.alerts.appendleft(alert)
        log.info("ALERT %s", title)
        for listener in list(self.listeners):
            try:
                await listener(alert)
            except Exception:
                log.exception("alert listener failed")
        if self.telegram:
            await self.telegram.send(alert)

    async def send_test(self) -> dict:
        """Clearly-labelled test alert to check pop-ups, sound and Telegram."""
        await self._emit(
            f"test:{self.clock().isoformat()}", "up", "🔔 Test alert from AI Stock",
            "If you can see (and hear) this, automatic alerts are working. This is a test, not a market signal.", None, None,
        )
        return self.alerts[0]

    # ---- read API ----------------------------------------------------
    def recent(self, limit: int = 50) -> list[dict]:
        return list(itertools.islice(self.alerts, limit))

    def fast_movers(self) -> list[dict]:
        """Price change over the tracked window for each stock, largest absolute move first."""
        out = []
        for sym, hist in self._prices.items():
            if len(hist) < 2 or not hist[0][1]:
                continue
            (t0, p0), (t1, p1) = hist[0], hist[-1]
            out.append({"symbol": sym, "change_pct": round((p1 / p0 - 1) * 100, 2), "minutes": round((t1 - t0).total_seconds() / 60), "price": p1})
        return sorted(out, key=lambda x: -abs(x["change_pct"]))
