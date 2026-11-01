"""Early signals: the intraday call evaluated on the still-forming 5-minute candle, before it closes.

The confirmed call (5-minute timing + 15-minute trend on completed candles) is unchanged. An early signal fires
when the forming candle, scored at the live price, already makes the 5-minute side agree with the 15-minute
trend, and that has held for HOLD_SECONDS. When the candle closes it is marked confirmed (the confirmed call
turned the same way) or cancelled. Every resolved early signal is kept, so the app can show how often early
signals were confirmed and how much earlier / better their entry was.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

HOLD_SECONDS = 20  # the forming candle must keep pointing the same way this long before an early signal fires
MIN_BAR_SECONDS = 15  # nothing in the first seconds of a candle: too little of it has formed
BAR_MINUTES = 5
SHOW_RESOLVED = timedelta(minutes=10)  # a confirmed / cancelled early signal stays visible this long
SIDE = {1: "BUY", -1: "SELL"}


class EarlyTracker:
    def __init__(self) -> None:
        self.current: dict[str, dict] = {}  # symbol -> early signal on the forming candle (watching or live)
        self.awaiting: dict[str, dict] = {}  # symbol -> live early signal whose candle has closed, not yet resolved
        self.resolved: dict[str, dict] = {}  # symbol -> latest resolved early signal
        self.records: deque[dict] = deque(maxlen=1000)

    def update(self, symbol: str, side: int, bar: datetime, now: datetime, price: float | None,
               levels: dict | None = None, info: dict | None = None) -> dict | None:
        """Feed the forming candle's early side (+1 / -1 / 0). Returns the signal when it goes live."""
        st = self.current.get(symbol)
        if st and st["bar"] != bar.isoformat():  # the candle closed
            if st["status"] == "live":
                self.awaiting[symbol] = st
            self.current.pop(symbol)
            st = None
        if st and st["status"] == "live":
            st["active"] = side == st["side_n"]  # still pointing that way now? (resolved at the close either way)
            return None
        if side == 0 or price is None or (st and st["side_n"] != side):
            self.current.pop(symbol, None)
            if side == 0 or price is None:
                return None
            st = None
        if st is None:
            self.current[symbol] = {"side_n": side, "side": SIDE[side], "bar": bar.isoformat(), "first_seen": now.isoformat(),
                                    "status": "watching"}
            return None
        held = (now - datetime.fromisoformat(st["first_seen"])).total_seconds()
        if held >= HOLD_SECONDS and (now - bar).total_seconds() >= MIN_BAR_SECONDS:
            st.update(status="live", active=True, since=now.isoformat(), price=round(price, 4), levels=levels,
                      confirms_at=(bar + timedelta(minutes=BAR_MINUTES)).isoformat(), **(info or {}))
            return st
        return None

    def resolve(self, symbol: str, last_completed: str | None, confirmed_side: int, price: float | None,
                now: datetime) -> dict | None:
        """Once the early signal's candle is the latest completed one, compare with the confirmed call."""
        st = self.awaiting.get(symbol)
        if not st or not last_completed or datetime.fromisoformat(last_completed) < datetime.fromisoformat(st["bar"]):
            return None
        self.awaiting.pop(symbol)
        ok = confirmed_side == st["side_n"]
        edge = None
        if price and st.get("price"):
            edge = round((price / st["price"] - 1) * 100 * st["side_n"], 3)
        rec = {**st, "status": "confirmed" if ok else "cancelled", "resolved_at": now.isoformat(),
               "resolve_price": round(price, 4) if price else None,
               # confirmed: how much better the early entry was than entering at the close;
               # cancelled: how far price had moved for (+) or against (-) the early entry by then
               "edge_pct": edge, "symbol": symbol}
        self.resolved[symbol] = rec
        self.records.append(rec)
        return rec

    def view(self, symbol: str, now: datetime) -> dict | None:
        """What the app shows for this symbol: a live early signal, one waiting for its close, or a recent result."""
        st = self.current.get(symbol)
        if st and st["status"] == "live":
            return _public(st, "live")
        if symbol in self.awaiting:
            return _public(self.awaiting[symbol], "confirming")
        rec = self.resolved.get(symbol)
        if rec and now - datetime.fromisoformat(rec["resolved_at"]) <= SHOW_RESOLVED:
            return _public(rec, rec["status"])
        return None

    def summary(self, day: str) -> dict:
        """Today's early signals: how many fired, how many the close confirmed, and the entry advantage."""
        today = [r for r in self.records if r["since"][:10] == day]
        conf = [r for r in today if r["status"] == "confirmed"]
        canc = [r for r in today if r["status"] == "cancelled"]
        mins = [(datetime.fromisoformat(r["confirms_at"]) - datetime.fromisoformat(r["since"])).total_seconds() / 60 for r in conf]

        def avg(xs):
            xs = [x for x in xs if x is not None]
            return round(sum(xs) / len(xs), 3) if xs else None

        open_now = [st for st in [*self.current.values(), *self.awaiting.values()]
                    if st["status"] == "live" and st["since"][:10] == day]
        return {"fired": len(today) + len(open_now), "confirmed": len(conf), "cancelled": len(canc), "open": len(open_now),
                "confirmed_pct": round(100 * len(conf) / len(today), 1) if today else None,
                "avg_minutes_earlier": round(sum(mins) / len(mins), 1) if mins else None,
                "avg_entry_edge_pct": avg([r["edge_pct"] for r in conf]),
                "avg_cancelled_move_pct": avg([r["edge_pct"] for r in canc]),
                "recent": [_public(r, r["status"]) for r in list(today)[-20:]][::-1]}


def _public(st: dict, status: str) -> dict:
    keys = ("symbol", "side", "bar", "since", "confirms_at", "price", "levels", "active", "score", "up_pct", "basis",
            "resolved_at", "resolve_price", "edge_pct")
    return {"status": status, **{k: st.get(k) for k in keys if k in st}}


# ---- replay on history: how early signals would have done ------------------------------------------------
RULE_LEVEL = 20  # same rule-score level the intraday call uses when the model isn't trusted


def _lean(score: float | None) -> int:
    return 0 if score is None else 1 if score >= RULE_LEVEL else -1 if score <= -RULE_LEVEL else 0


def replay(c5, c1, c15, cost_pct: float = 0.0, window: int = 400) -> dict:
    """Rebuild every 5-minute candle covered by the 1-minute data minute by minute, as it formed, and find where an
    early signal would have fired (forming candle's 5-minute side agrees with the completed 15-minute trend and
    differs from the call already confirmed). Each is then marked confirmed or cancelled by that candle's close.
    Uses the rule score (the combined model isn't replayed). Frames are indexed by candle start time, IST.

    Returns per-signal rows and a summary: how much earlier, how often confirmed, the entry edge versus waiting
    for the close, and the net effect of trading every early signal (cancelled ones exited at the close, paying
    cost_pct for the extra round trip)."""
    import pandas as pd

    from app.analysis import indicators as ta
    from app.analysis.signals import evaluate

    def score_at(frame) -> float | None:
        f = frame.iloc[-window:]
        if len(f) < 50:
            return None
        return evaluate(f, ta.compute(f, intraday=True), "5m", f.index[-1].to_pydatetime(), "polled")["score"]

    trend_cache: dict[int, int] = {}

    def trend_at(t) -> int:  # 15-minute lean from candles completed by time t
        done = c15[c15.index + pd.Timedelta(minutes=15) <= t]
        n = len(done)
        if n not in trend_cache:
            trend_cache[n] = _lean(score_at(done)) if n else 0
        return trend_cache[n]

    def confirmed_at_close(i: int) -> int:  # the confirmed call once 5-minute candle i has closed
        bar = c5.index[i]
        l5, l15 = _lean(score_at(c5.iloc[: i + 1])), trend_at(bar + pd.Timedelta(minutes=5))
        return l5 if l5 and l5 == l15 else 0

    rows = []
    start = c1.index[0]
    bars = [i for i, b in enumerate(c5.index) if b >= start and i > 0]
    prev_confirmed = confirmed_at_close(bars[0] - 1) if bars else 0
    for i in bars:
        bar = c5.index[i]
        minutes = c1[(c1.index >= bar) & (c1.index < bar + pd.Timedelta(minutes=5))]
        fired = None
        for m in range(1, 5):  # after 1..4 minutes of the 5 (minute 5 is the close itself)
            part = minutes[minutes.index < bar + pd.Timedelta(minutes=m)]
            if part.empty:
                continue
            forming = pd.DataFrame({"open": [part["open"].iloc[0]], "high": [part["high"].max()], "low": [part["low"].min()],
                                    "close": [part["close"].iloc[-1]], "volume": [part["volume"].sum()]}, index=[bar])
            frame = pd.concat([c5.iloc[:i][["open", "high", "low", "close", "volume"]], forming])
            now = bar + pd.Timedelta(minutes=m)
            l5, l15 = _lean(score_at(frame)), trend_at(now)
            if l5 and l5 == l15 and l5 != prev_confirmed:
                fired = (m, l5, float(part["close"].iloc[-1]))
                break
        confirmed = confirmed_at_close(i)
        if fired:
            m, side, price = fired
            close = float(c5["close"].iloc[i])
            edge = (close / price - 1) * 100 * side
            ok = confirmed == side
            rows.append({"bar": bar.isoformat(), "side": "BUY" if side == 1 else "SELL", "minute": m, "minutes_earlier": 5 - m,
                         "price": price, "close": close, "status": "confirmed" if ok else "cancelled", "edge_pct": round(edge, 4),
                         # trading it early: confirmed → the entry edge; cancelled → exit at the close, paying an extra round trip
                         "net_vs_waiting_pct": round(edge - (0 if ok else cost_pct), 4)})
        prev_confirmed = confirmed
    conf = [r for r in rows if r["status"] == "confirmed"]
    canc = [r for r in rows if r["status"] == "cancelled"]

    def avg(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    return {"rows": rows, "summary": {
        "bars": len(bars), "fired": len(rows), "confirmed": len(conf), "cancelled": len(canc),
        "confirmed_pct": round(100 * len(conf) / len(rows), 1) if rows else None,
        "avg_minutes_earlier": avg([r["minutes_earlier"] for r in conf]),
        "avg_entry_edge_confirmed_pct": avg([r["edge_pct"] for r in conf]),
        "avg_move_cancelled_pct": avg([r["edge_pct"] for r in canc]),
        "avg_net_vs_waiting_pct": avg([r["net_vs_waiting_pct"] for r in rows]),
        "cost_pct": cost_pct,
    }}
