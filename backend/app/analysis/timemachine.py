"""Time Machine: replay the rest of the session thousands of times before taking a trade.

Adapted from the day-trader skill pack (github.com/Jakeschincariol/day-trader-skill, MIT) for NSE stocks,
indices and currency futures:

1. The pool: every FULL past session (at least 90% of its 5-minute candles present). Each candle becomes
   four ratios (open, high, low, close) to the close of the candle before it.
2. One replay: start at the entry price; for each remaining 5-minute slot up to the square-off time, draw a
   past candle from the same time of day (+/- `window` slots) and scale it to the current price. The stop
   is checked before the target, so a candle that touches both counts as the stop (a gap through the stop
   counts as -1R; a real fill can be worse).
3. The history: the same trade (same % stop and target, entered at the same time of day) on each real past
   session in the pool. What really happened, not a simulation.
4. The verdict compares how often the target came first, among replays that hit either side, with the
   break-even rate 1 / (1 + R) for a target R times the risk.

It is a simulation built from the last ~60 sessions, not a prediction: results, news and halts can do
things those sessions never did. Seeded, so the same inputs give the same numbers.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from app.market_session import IST, MarketHours, is_session_open

BAR = 5  # minutes
MIN_SESSIONS = 20
FULL_SHARE = 0.9  # a session counts as full with at least 90% of its candles
MIN_RISK_PCT = 0.05  # a stop closer than 0.05% of the price is inside normal noise


class TimeMachineError(ValueError):
    """A plan the Time Machine refuses, with the reason in plain words."""


def slot_count(hours: MarketHours) -> int:
    return (hours.close.hour * 60 + hours.close.minute - hours.open_minutes) // BAR


def slot_of(t: time, hours: MarketHours) -> int:
    return (t.hour * 60 + t.minute - hours.open_minutes) // BAR


def slot_time(slot: int, hours: MarketHours) -> str:
    m = hours.open_minutes + slot * BAR
    return f"{m // 60:02d}:{m % 60:02d}"


def build_pool(df: pd.DataFrame, hours: MarketHours, before: date) -> tuple[np.ndarray, np.ndarray, list[date], np.ndarray]:
    """(ratios [sessions, slots, 4], present [sessions, slots], session dates, closes [sessions, slots]) for full
    sessions before `before`. Ratios are to the previous candle's close (the first candle's includes the gap)."""
    n_slots = slot_count(hours)
    prev_close = df["close"].shift(1)
    ok = prev_close.notna()
    d = df[ok]
    pc = prev_close[ok].to_numpy(float)
    ratios = np.stack([d["open"].to_numpy(float) / pc, d["high"].to_numpy(float) / pc,
                       d["low"].to_numpy(float) / pc, d["close"].to_numpy(float) / pc], axis=1)
    times = d.index.tz_convert(IST) if d.index.tz is not None else d.index
    days = np.array([t.date() for t in times])
    slots = np.array([slot_of(t.time(), hours) for t in times])
    keep_days = sorted({x for x in days if x < before})
    out_r = np.ones((len(keep_days), n_slots, 4))
    out_p = np.zeros((len(keep_days), n_slots), bool)
    out_c = np.full((len(keep_days), n_slots), np.nan)
    closes = d["close"].to_numpy(float)
    index = {x: i for i, x in enumerate(keep_days)}
    for k in range(len(d)):
        i = index.get(days[k])
        s = slots[k]
        if i is None or not 0 <= s < n_slots:
            continue
        out_r[i, s] = ratios[k]
        out_p[i, s] = True
        out_c[i, s] = closes[k]
    full = out_p.sum(axis=1) >= FULL_SHARE * n_slots
    return out_r[full], out_p[full], [x for x, f in zip(keep_days, full) if f], out_c[full]


def verdict(p_target: float, p_stop: float, r_mult: float, p_neither: float, avg_r: float) -> tuple[str, list[str]]:
    """One plain line from the odds, plus notes when most replays hit neither side or the average R disagrees."""
    decided = p_target + p_stop
    notes = []
    if decided < 5:
        line = f"Mostly undecided: only {decided:.1f}% of replays reached the target or the stop."
    else:
        share = 100 * p_target / decided
        need = 100 / (1 + r_mult)
        gap = share - need
        even = "Close to a coin flip" if abs(r_mult - 1) < 0.05 else "Close to break-even"
        if abs(gap) < 5:
            line = f"{even}: target first {p_target:.1f}% vs stop first {p_stop:.1f}%."
        else:
            side = "the target" if gap > 0 else "the stop"
            strength = "Strongly toward" if abs(gap) >= 15 else "Leans toward"
            line = f"{strength} {side}: target first {p_target:.1f}% vs stop first {p_stop:.1f}%."
        notes.append(f"At {r_mult:.1f}R the target has to come first in {need:.0f}% of the replays that hit a side to break "
                     f"even; it did in {share:.1f}%.")
        if (gap >= 5 and avg_r < 0) or (gap <= -5 and avg_r > 0):
            notes.append(f"But the average R is {avg_r:+.3f}: the replays that hit neither side pull the other way.")
    if p_neither > 40:
        notes.append(f"{p_neither:.1f}% of the replays hit neither by the square-off time; the price then decides those.")
    return line, notes


def run(df: pd.DataFrame, hours: MarketHours, *, side: str, entry: float, stop: float, target: float, now: datetime,
        until: time, cost_pct: float = 0.0, replays: int = 10_000, seed: int = 19, window: int = 2) -> dict:
    """Replays plus history for one plan. `df` is 5-minute candles (completed only), oldest first."""
    long = side == "BUY"
    risk = (entry - stop) if long else (stop - entry)
    reward = (target - entry) if long else (entry - target)
    if entry <= 0 or risk <= 0:
        raise TimeMachineError(f"For a {side} the stop must be {'below' if long else 'above'} the entry.")
    if reward <= 0:
        raise TimeMachineError(f"For a {side} the target must be {'above' if long else 'below'} the entry.")
    if 100 * risk / entry < MIN_RISK_PCT:
        raise TimeMachineError(f"The stop is only {100 * risk / entry:.3f}% from the entry: normal price noise would hit it. "
                               "Use a wider stop.")
    r_mult = reward / risk
    n_slots = slot_count(hours)
    end = min(n_slots, max(0, -(-(until.hour * 60 + until.minute - hours.open_minutes) // BAR)))

    now = now.astimezone(IST)
    live = is_session_open(now, hours)
    start = max(0, slot_of(now.time(), hours)) if live else 0
    start_label = f"from {slot_time(start, hours)} today" if live else "a full session from the open"
    if start >= end:
        raise TimeMachineError(f"It is past the square-off time ({until:%H:%M}): no time left for a new day trade.")

    ratios, present, days, closes = build_pool(df, hours, now.date())
    if len(days) < MIN_SESSIONS:
        raise TimeMachineError(f"Only {len(days)} full past sessions in the data; the Time Machine needs at least {MIN_SESSIONS}.")

    rng = np.random.default_rng(seed)
    flat_r = ratios.reshape(-1, 4)
    flat_p = present.reshape(-1)
    s_idx = np.arange(present.size) % n_slots
    price = np.full(replays, float(entry))
    status = np.zeros(replays, np.int8)  # 1 target, -1 stop, 0 still open
    bands = []
    for s in range(start, end):
        lo, hi = max(0, s - window), min(n_slots - 1, s + window)
        cand = np.nonzero(flat_p & (s_idx >= lo) & (s_idx <= hi))[0]
        r = flat_r[rng.choice(cand, size=replays)]
        o, h, low, c = (price * r[:, k] for k in range(4))
        active = status == 0
        if long:
            hit_stop = active & ((low <= stop) | (o <= stop))
            hit_tgt = active & ~hit_stop & (h >= target)
        else:
            hit_stop = active & ((h >= stop) | (o >= stop))
            hit_tgt = active & ~hit_stop & (low <= target)
        status[hit_stop] = -1
        status[hit_tgt] = 1
        price = c
        q = np.percentile(price, [5, 25, 50, 75, 95])
        bands.append({"time": slot_time(s + 1, hours), **{f"p{p}": round(float(v), 4) for p, v in zip((5, 25, 50, 75, 95), q)}})

    d = 1 if long else -1
    r_open = d * (price - entry) / risk
    r_each = np.where(status == 1, r_mult, np.where(status == -1, -1.0, r_open))
    pct = lambda m: round(100 * float(np.mean(m)), 1)  # noqa: E731
    p_target, p_stop = pct(status == 1), pct(status == -1)
    p_neither = round(100 - p_target - p_stop, 1)
    p_green = pct((status == 1) | ((status == 0) & (d * (price - entry) > 0)))
    avg_r = round(float(np.mean(r_each)), 3)
    cost_r = round(cost_pct / 100 * entry / risk, 3)
    line, notes = verdict(p_target, p_stop, r_mult, p_neither, avg_r)

    hist = history(closes, ratios, present, start, end, long, entry, stop, target, risk, r_mult)
    return {
        "side": side, "entry": entry, "stop": stop, "target": target, "r_multiple": round(r_mult, 2),
        "replays": replays, "seed": seed, "window": window,
        "start": slot_time(start, hours), "until": slot_time(end, hours), "start_label": start_label,
        "bars": end - start, "sessions": len(days), "pool_from": days[0].isoformat(), "pool_to": days[-1].isoformat(),
        "p_target": p_target, "p_stop": p_stop, "p_neither": p_neither, "p_green": p_green,
        "close": {k: round(float(np.percentile(price, p)), 4) for k, p in (("p5", 5), ("median", 50), ("p95", 95))},
        "avg_r": avg_r, "cost_r": cost_r, "avg_r_after_costs": round(avg_r - cost_r, 3),
        "verdict": line, "notes": notes, "history": hist, "bands": bands,
    }


def history(closes, ratios, present, start, end, long, entry, stop, target, risk, r_mult) -> dict:
    """The same trade, in % terms, entered at the same slot on each real past session."""
    stop_pct, target_pct = stop / entry - 1, target / entry - 1
    counts = {"target": 0, "stop": 0, "neither": 0}
    rs = []
    for k in range(len(closes)):
        if start == 0:
            if not present[k, 0]:
                continue
            e = closes[k, 0] / ratios[k, 0, 3] * ratios[k, 0, 0]  # the first candle's open
        else:
            if not present[k, start - 1]:
                continue
            e = closes[k, start - 1]
        st, tg = e * (1 + stop_pct), e * (1 + target_pct)
        out, last = None, e
        for s in range(start, end):
            if not present[k, s]:
                continue
            pc = closes[k, s] / ratios[k, s, 3]
            o, h, low = pc * ratios[k, s, 0], pc * ratios[k, s, 1], pc * ratios[k, s, 2]
            last = closes[k, s]
            if (long and (low <= st or o <= st)) or (not long and (h >= st or o >= st)):
                out = "stop"
                break
            if (long and h >= tg) or (not long and low <= tg):
                out = "target"
                break
        out = out or "neither"
        counts[out] += 1
        rs.append(r_mult if out == "target" else -1.0 if out == "stop" else (1 if long else -1) * (last - e) / (e * risk / entry))
    n = sum(counts.values())
    return {"sessions": n, **counts, "avg_r": round(float(np.mean(rs)), 3) if rs else None}


def default_until(hours: MarketHours) -> time:
    """Square-off time: Upstox closes intraday positions about 10-15 minutes before the end of the session;
    plan to be out 15 minutes before the close."""
    end = datetime.combine(date.today(), hours.close) - timedelta(minutes=15)
    return end.time()
