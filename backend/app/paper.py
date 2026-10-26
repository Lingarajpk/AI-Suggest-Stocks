"""Paper trading and trade journal, per device (no real orders, ever).

Design borrowed, not copied: OpenAlice's mock broker and "every order has a reason" journal, NautilusTrader's
order types and position accounting, AI-Trader's rule that the SERVER decides fill prices.

  orders     market, limit, stop (stop-loss market), and bracket entries that attach a stop and a target as a
             one-cancels-the-other pair; the stop can trail by a percentage. Products: intraday (squared off at
             15:15, shorts allowed) and delivery (no shorts).
  fills      only while the market is open, only from the server's latest quote, never from a client price:
               market            last price, plus slippage
               limit             when the last price reaches the limit; never worse than the limit
               stop / trailing   when the last price crosses the trigger; last price plus slippage
             Quotes are polled every few seconds, so a fast move can fill beyond the level (as in real life).
  positions  one per filled entry (hedging-style, so each has its own bracket); marked to market on every quote.
  journal    every closed position with gross, charges (Upstox rates from costs.py), net, the entry note ("why"
             is required), an exit note, and the best / worst move while it was open (MFE / MAE).
  cash       no leverage: a long uses its full value; an intraday short's result is settled when it closes.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, time
from pathlib import Path

from app.analysis import reliability as rl
from app.analysis.costs import SLIPPAGE_PER_SIDE, charges
from app.analysis.history import profit_factor
from app.cache import Cache

log = logging.getLogger(__name__)

DEFAULT_CAPITAL = 1_000_000.0
SQUARE_OFF = time(15, 15)
MAX_OPEN_ORDERS = 50
MAX_JOURNAL = 500
ORDER_TYPES = ("market", "limit", "stop")
PRODUCTS = ("intraday", "delivery")


class PaperError(ValueError):
    pass


def new_account(capital: float = DEFAULT_CAPITAL) -> dict:
    return {"capital": capital, "cash": capital, "orders": [], "positions": [], "journal": [], "equity": [], "version": 1}


def _id() -> str:
    return uuid.uuid4().hex[:10]


def _slip(product: str) -> float:
    return SLIPPAGE_PER_SIDE["intraday" if product == "intraday" else "delivery"]


def validate_order(acc: dict, o: dict) -> dict:
    """Normalise and check a new order request; raises PaperError with a plain reason."""
    side = str(o.get("side", "")).upper()
    typ = str(o.get("type", "market")).lower()
    product = str(o.get("product", "intraday")).lower()
    try:
        qty = int(o.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    note = " ".join(str(o.get("note") or "").split())[:500]
    if side not in ("BUY", "SELL"):
        raise PaperError("Side must be BUY or SELL.")
    if typ not in ORDER_TYPES:
        raise PaperError("Order type must be market, limit or stop.")
    if product not in PRODUCTS:
        raise PaperError("Product must be intraday or delivery.")
    if not 0 < qty <= 1_000_000:
        raise PaperError("Quantity must be a whole number above 0.")
    if len(note) < 3:
        raise PaperError("Write why you are taking this trade (a few words): the journal keeps it.")
    if side == "SELL" and product == "delivery":
        raise PaperError("Short selling is intraday only, as on Upstox.")
    price = _num(o.get("price"))
    trigger = _num(o.get("trigger"))
    if typ == "limit" and not price:
        raise PaperError("A limit order needs a limit price.")
    if typ == "stop" and not trigger:
        raise PaperError("A stop order needs a trigger price.")
    stop, target, trail = _num(o.get("stop")), _num(o.get("target")), _num(o.get("trail_pct"))
    ref = price or trigger or _num(o.get("ref_price"))
    if ref and stop and (stop >= ref if side == "BUY" else stop <= ref):
        raise PaperError(f"For a {side} the stop must be {'below' if side == 'BUY' else 'above'} the entry.")
    if ref and target and (target <= ref if side == "BUY" else target >= ref):
        raise PaperError(f"For a {side} the target must be {'above' if side == 'BUY' else 'below'} the entry.")
    if trail is not None and not 0.1 <= trail <= 20:
        raise PaperError("Trailing stop must be between 0.1% and 20%.")
    if sum(1 for x in acc["orders"] if x["status"] == "open") >= MAX_OPEN_ORDERS:
        raise PaperError(f"At most {MAX_OPEN_ORDERS} open orders.")
    return {"side": side, "type": typ, "product": product, "qty": qty, "note": note, "price": price, "trigger": trigger,
            "stop": stop, "target": target, "trail_pct": trail, "setup": str(o.get("setup") or "")[:80] or None}


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def place(acc: dict, order: dict, symbol: str, key: str, now: datetime) -> dict:
    o = {"id": _id(), "symbol": symbol, "instrument_key": key, "status": "open", "placed": now.isoformat(),
         "role": "entry", **order}
    acc["orders"].append(o)
    return o


def cancel(acc: dict, order_id: str) -> dict:
    o = next((x for x in acc["orders"] if x["id"] == order_id), None)
    if o is None:
        raise PaperError("No such order.")
    if o["status"] != "open":
        raise PaperError(f"The order is already {o['status']}.")
    o["status"] = "cancelled"
    return o


def _entry_fill(o: dict, ltp: float) -> float | None:
    """Fill price for an entry order at this quote, or None if it doesn't fill yet."""
    buy = o["side"] == "BUY"
    s = _slip(o["product"])
    if o["type"] == "market":
        return ltp * (1 + s) if buy else ltp * (1 - s)
    if o["type"] == "limit":
        if buy and ltp <= o["price"]:
            return min(o["price"], ltp * (1 + s))
        if not buy and ltp >= o["price"]:
            return max(o["price"], ltp * (1 - s))
        return None
    if buy and ltp >= o["trigger"]:
        return ltp * (1 + s)
    if not buy and ltp <= o["trigger"]:
        return ltp * (1 - s)
    return None


def _open_position(acc: dict, o: dict, fill: float, now: datetime) -> dict:
    fill = round(fill, 4)
    long = o["side"] == "BUY"
    if long and fill * o["qty"] > acc["cash"] + 1e-6:
        o["status"], o["reason"] = "rejected", "Not enough cash (paper trading uses no leverage)."
        return {}
    o.update(status="filled", fill_price=round(fill, 4), filled_at=now.isoformat())
    pos = {"id": _id(), "symbol": o["symbol"], "instrument_key": o["instrument_key"], "side": "long" if long else "short",
           "qty": o["qty"], "entry": round(fill, 4), "product": o["product"], "opened": now.isoformat(), "note": o["note"],
           "setup": o.get("setup"), "best": fill, "worst": fill, "last": fill, "entry_order": o["id"]}
    if long:
        acc["cash"] -= fill * o["qty"]
    group = _id()
    exit_side = "SELL" if long else "BUY"
    for role, kind, level in (("stop", "stop", o.get("stop")), ("target", "limit", o.get("target"))):
        if level or (role == "stop" and o.get("trail_pct")):
            trig = level
            if role == "stop" and o.get("trail_pct") and not trig:
                trig = fill * (1 - o["trail_pct"] / 100) if long else fill * (1 + o["trail_pct"] / 100)
            acc["orders"].append({
                "id": _id(), "symbol": o["symbol"], "instrument_key": o["instrument_key"], "status": "open",
                "placed": now.isoformat(), "role": role, "side": exit_side, "qty": o["qty"], "type": kind,
                "price": level if kind == "limit" else None, "trigger": round(trig, 4) if kind == "stop" else None,
                "trail_pct": o.get("trail_pct") if role == "stop" else None, "product": o["product"],
                "position": pos["id"], "oco": group, "note": f"{role} for {o['symbol']}"})
    acc["positions"].append(pos)
    return pos


def close_position(acc: dict, pos_id: str, price: float, now: datetime, reason: str, exit_note: str = "") -> dict:
    price = round(price, 4)
    pos = next((p for p in acc["positions"] if p["id"] == pos_id), None)
    if pos is None:
        raise PaperError("No such open position.")
    long = pos["side"] == "long"
    qty, entry = pos["qty"], pos["entry"]
    buy_v, sell_v = (entry * qty, price * qty) if long else (price * qty, entry * qty)
    fees = charges(pos["product"], buy_v, sell_v)
    gross = (price - entry) * qty if long else (entry - price) * qty
    acc["cash"] += (price * qty - fees) if long else (gross - fees)
    acc["positions"] = [p for p in acc["positions"] if p["id"] != pos_id]
    for o in acc["orders"]:  # its remaining bracket legs
        if o.get("position") == pos_id and o["status"] == "open":
            o["status"] = "cancelled"
    d = 1 if long else -1
    entry_val = entry * qty
    trade = {
        "id": pos["id"], "symbol": pos["symbol"], "side": pos["side"], "qty": qty, "product": pos["product"],
        "entry": entry, "exit": round(price, 4), "opened": pos["opened"], "closed": now.isoformat(), "reason": reason,
        "gross": round(gross, 2), "charges": round(fees, 2), "net": round(gross - fees, 2),
        "net_pct": round(100 * (gross - fees) / entry_val, 3) if entry_val else 0.0,
        "mfe_pct": round(100 * d * ((pos["best"] if long else pos["worst"]) / entry - 1), 2),
        "mae_pct": round(100 * d * ((pos["worst"] if long else pos["best"]) / entry - 1), 2),
        "note": pos["note"], "setup": pos.get("setup"), "exit_note": exit_note[:500], "lesson": "",
    }
    acc["journal"] = (acc["journal"] + [trade])[-MAX_JOURNAL:]
    return trade


def process(acc: dict, quotes: dict[str, float], now: datetime, session_open: bool) -> list[str]:
    """Advance the account one quote tick. Returns human-readable events (fills, exits, square-offs)."""
    events: list[str] = []
    # Mark open positions and trail stops.
    for p in acc["positions"]:
        ltp = quotes.get(p["instrument_key"])
        if not ltp:
            continue
        p["last"] = round(ltp, 4)
        p["best"], p["worst"] = max(p["best"], ltp), min(p["worst"], ltp)
        for o in acc["orders"]:
            if o.get("position") == p["id"] and o["status"] == "open" and o.get("trail_pct"):
                t = o["trail_pct"] / 100
                if p["side"] == "long":
                    o["trigger"] = round(max(o["trigger"], p["best"] * (1 - t)), 4)
                else:
                    o["trigger"] = round(min(o["trigger"], p["worst"] * (1 + t)), 4)
    if session_open:
        # Intraday square-off.
        if now.time() >= SQUARE_OFF:
            for o in acc["orders"]:
                if o["status"] == "open" and o["product"] == "intraday" and o["role"] == "entry":
                    o["status"], o["reason"] = "cancelled", "Intraday entries expire at 15:15."
            for p in [p for p in acc["positions"] if p["product"] == "intraday"]:
                ltp = quotes.get(p["instrument_key"]) or p["last"]
                s = _slip("intraday")
                t = close_position(acc, p["id"], ltp * (1 - s) if p["side"] == "long" else ltp * (1 + s), now, "square_off")
                events.append(f"Squared off {t['symbol']} at {t['exit']} (intraday, 15:15)")
        for o in list(acc["orders"]):
            if o["status"] != "open":
                continue
            ltp = quotes.get(o["instrument_key"])
            if not ltp:
                continue
            if o["role"] == "entry":
                fill = _entry_fill(o, ltp)
                if fill is not None:
                    pos = _open_position(acc, o, fill, now)
                    events.append(f"Filled {o['side']} {o['qty']} {o['symbol']} at {o.get('fill_price')}" if pos else
                                  f"Rejected {o['symbol']}: {o.get('reason')}")
            else:
                pos = next((p for p in acc["positions"] if p["id"] == o.get("position")), None)
                if pos is None:
                    o["status"] = "cancelled"
                    continue
                fill = _entry_fill(o, ltp)  # same rules: stop = stop order, target = limit order
                if fill is not None:
                    o.update(status="filled", fill_price=round(fill, 4), filled_at=now.isoformat())
                    t = close_position(acc, pos["id"], fill, now, o["role"])
                    events.append(f"{o['role'].title()} hit on {t['symbol']} at {t['exit']}: {t['net']:+,.0f} after charges")
    return events


def mark(acc: dict) -> dict:
    """Account value now: cash plus open longs at market plus open shorts' running result."""
    longs = sum(p["last"] * p["qty"] for p in acc["positions"] if p["side"] == "long")
    shorts = sum((p["entry"] - p["last"]) * p["qty"] for p in acc["positions"] if p["side"] == "short")
    unreal = sum(((p["last"] - p["entry"]) if p["side"] == "long" else (p["entry"] - p["last"])) * p["qty"] for p in acc["positions"])
    equity = acc["cash"] + longs + shorts
    return {"equity": round(equity, 2), "cash": round(acc["cash"], 2), "unrealized": round(unreal, 2),
            "return_pct": round(100 * (equity / acc["capital"] - 1), 2) if acc["capital"] else 0.0}


def snapshot_equity(acc: dict, now: datetime) -> None:
    day = now.date().isoformat()
    eq = mark(acc)["equity"]
    if acc["equity"] and acc["equity"][-1][0] == day:
        acc["equity"][-1][1] = eq
    else:
        acc["equity"] = (acc["equity"] + [[day, eq]])[-750:]


def journal_stats(journal: list[dict]) -> dict:
    pnl = [t["net_pct"] for t in journal]
    rupees = [t["net"] for t in journal]
    if not pnl:
        return {"trades": 0}
    won = [t for t in journal if t["net"] > 0]
    by_setup: dict[str, list[float]] = {}
    for t in journal:
        by_setup.setdefault(t.get("setup") or "manual", []).append(t["net_pct"])
    return {
        "trades": len(pnl), "win_rate": round(100 * len(won) / len(pnl), 1), "net_total": round(sum(rupees), 2),
        "avg_pct": round(sum(pnl) / len(pnl), 3), "profit_factor": profit_factor(pnl),
        "avg_mfe_pct": round(sum(t["mfe_pct"] for t in journal) / len(journal), 2),
        "avg_mae_pct": round(sum(t["mae_pct"] for t in journal) / len(journal), 2),
        "evidence": rl.evidence(pnl),
        "by_setup": [{"setup": k, "trades": len(v), "avg_pct": round(sum(v) / len(v), 3),
                      "win_rate": round(100 * sum(x > 0 for x in v) / len(v), 1)} for k, v in by_setup.items()],
        "exits": {r: sum(1 for t in journal if t["reason"] == r) for r in {t["reason"] for t in journal}},
    }


class PaperStore:
    """One JSON document per device: Redis when configured (survives restarts), else a local file."""

    DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

    def __init__(self, folder: Path, durable: Cache | None) -> None:
        self.folder = folder
        self.durable = durable
        self.accounts: dict[str, dict] = {}
        self._indexed: set[str] | None = None  # devices known to be in the store's index
        self._saved: dict[str, str] = {}  # last saved JSON per device: unchanged accounts are not rewritten

    def _path(self, device: str) -> Path:
        return self.folder / f"paper_{device}.json"

    async def get(self, device: str) -> dict:
        if not self.DEVICE_RE.match(device or ""):
            raise PaperError("Unknown device.")
        if device in self.accounts:
            return self.accounts[device]
        doc = await self.durable.get(f"paper:v1:{device}") if self.durable is not None else None
        if doc is None and self._path(device).exists():
            try:
                doc = json.loads(self._path(device).read_text())
            except ValueError:
                doc = None
        self.accounts[device] = doc or new_account()
        return self.accounts[device]

    async def load_all(self) -> None:
        """At start-up: every device with an account, so pending orders keep working after a restart."""
        devices: list[str] = []
        if self.durable is not None:
            devices = await self.durable.get("paper:v1:devices") or []
            self._indexed = set(devices)
        elif self.folder.exists():
            devices = [f.stem.removeprefix("paper_") for f in self.folder.glob("paper_*.json")]
        for d in devices:
            try:
                await self.get(d)
            except PaperError:
                pass

    async def save(self, device: str) -> None:
        acc = self.accounts.get(device)
        if acc is None:
            return
        doc = json.dumps(acc, default=str)
        if self._saved.get(device) == doc:
            return
        self._saved[device] = doc
        if self.durable is not None:
            await self.durable.set(f"paper:v1:{device}", acc, 400 * 86400)
            if self._indexed is None:
                self._indexed = set(await self.durable.get("paper:v1:devices") or [])
            if device not in self._indexed:
                self._indexed.add(device)
                await self.durable.set("paper:v1:devices", sorted(self._indexed)[-500:], 400 * 86400)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._path(device).write_text(json.dumps(acc))
        except OSError:
            pass

    def active(self) -> list[tuple[str, dict]]:
        return [(d, a) for d, a in self.accounts.items()
                if a["positions"] or any(o["status"] == "open" for o in a["orders"])]
