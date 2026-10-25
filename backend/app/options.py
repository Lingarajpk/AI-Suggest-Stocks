"""Index option chains: parse Upstox's chain, summarise open interest and pick an option to buy.

The pick follows the intraday call for the index (5-minute timing confirmed by the 15-minute trend):
BUY -> buy a call (CE), SELL -> buy a put (PE), anything else -> wait. Premium targets and stops are
estimates: the underlying's target/stop move translated through the option's delta and gamma.
"""

import math
from datetime import date

# Indices with weekly/monthly options on Upstox, keyed by the dashboard label.
OPTION_INDICES = ("NIFTY 50", "BANK NIFTY", "SENSEX")
WINDOW = 15  # strikes each side of ATM sent to the browser
TARGET_DELTA = 0.55  # ATM to slightly in the money: moves with the index without paying for deep ITM
TICK = 0.05


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _tick(v: float, tick: float = TICK) -> float:
    return round(round(v / tick) * tick, 4)


BUILDUP = {
    "long_buildup": "Long build-up",  # price up, OI up: fresh buying
    "short_buildup": "Short build-up",  # price down, OI up: fresh writing/selling
    "short_covering": "Short covering",  # price up, OI down: sellers exiting
    "long_unwinding": "Long unwinding",  # price down, OI down: buyers exiting
}
# What each build-up says about the underlying, by option side (+1 bullish, -1 bearish).
FLOW_SIGN = {
    ("call", "long_buildup"): 1, ("call", "short_covering"): 1, ("call", "short_buildup"): -1, ("call", "long_unwinding"): -1,
    ("put", "short_buildup"): 1, ("put", "long_unwinding"): 1, ("put", "long_buildup"): -1, ("put", "short_covering"): -1,
}
FLOW_STRIKES = 6  # strikes each side of ATM in the flow reading


def classify(price_change: float | None, oi_change: float | None) -> str | None:
    """Long build-up / short build-up / short covering / long unwinding from today's price and OI change."""
    if not price_change or not oi_change:
        return None
    if oi_change > 0:
        return "long_buildup" if price_change > 0 else "short_buildup"
    return "short_covering" if price_change > 0 else "long_unwinding"


def _side(opt: dict | None) -> dict | None:
    if not opt:
        return None
    md = opt.get("market_data") or {}
    gk = opt.get("option_greeks") or {}
    ltp, close = _num(md.get("ltp")), _num(md.get("close_price"))
    oi, prev_oi = _num(md.get("oi")), _num(md.get("prev_oi"))
    return {
        "instrument_key": opt.get("instrument_key"),
        "ltp": ltp,
        "change": round(ltp - close, 4) if ltp is not None and close else None,
        "oi": oi,
        "oi_change": oi - prev_oi if oi is not None and prev_oi is not None else None,
        "volume": _num(md.get("volume")),
        "bid": _num(md.get("bid_price")),
        "ask": _num(md.get("ask_price")),
        "iv": _num(gk.get("iv")),
        "delta": _num(gk.get("delta")),
        "gamma": _num(gk.get("gamma")),
        "theta": _num(gk.get("theta")),
        "vega": _num(gk.get("vega")),
        "pop": _num(gk.get("pop")),
        "buildup": classify(round(ltp - close, 4) if ltp is not None and close else None,
                            oi - prev_oi if oi is not None and prev_oi is not None else None),
    }


def parse_chain(data: list[dict]) -> tuple[list[dict], float | None]:
    """Upstox /v2/option/chain rows -> [{strike, pcr, call, put}] sorted by strike, plus the spot price."""
    rows, spot = [], None
    for r in data or []:
        strike = _num(r.get("strike_price"))
        if strike is None:
            continue
        spot = spot or _num(r.get("underlying_spot_price"))
        rows.append({"strike": strike, "pcr": _num(r.get("pcr")), "call": _side(r.get("call_options")), "put": _side(r.get("put_options"))})
    rows.sort(key=lambda x: x["strike"])
    return rows, spot


def _oi(row: dict, side: str) -> float:
    return (row.get(side) or {}).get("oi") or 0.0


def max_pain(rows: list[dict]) -> float | None:
    """Strike where option buyers in total would lose the most (writers pay out the least) at expiry."""
    if not rows:
        return None
    best, best_pay = None, math.inf
    for k in rows:
        K = k["strike"]
        pay = sum(_oi(r, "call") * max(0.0, K - r["strike"]) + _oi(r, "put") * max(0.0, r["strike"] - K) for r in rows)
        if pay < best_pay:
            best, best_pay = K, pay
    return best


def atm_strike(rows: list[dict], spot: float | None) -> float | None:
    if not rows or spot is None:
        return None
    return min(rows, key=lambda r: abs(r["strike"] - spot))["strike"]


def summarize(rows: list[dict], spot: float | None) -> dict:
    """PCR, max pain, OI walls, ATM IV and straddle, and what the open interest leans towards."""
    call_oi = sum(_oi(r, "call") for r in rows)
    put_oi = sum(_oi(r, "put") for r in rows)
    call_chg = sum(((r.get("call") or {}).get("oi_change") or 0.0) for r in rows)
    put_chg = sum(((r.get("put") or {}).get("oi_change") or 0.0) for r in rows)
    pcr = round(put_oi / call_oi, 2) if call_oi else None
    atm = atm_strike(rows, spot)
    atm_row = next((r for r in rows if r["strike"] == atm), None)
    ivs = [x for x in ((atm_row or {}).get("call") or {}, (atm_row or {}).get("put") or {}) if x.get("iv")]
    atm_iv = round(sum(x["iv"] for x in ivs) / len(ivs), 2) if ivs else None
    straddle = None
    if atm_row and (atm_row.get("call") or {}).get("ltp") and (atm_row.get("put") or {}).get("ltp"):
        straddle = round(atm_row["call"]["ltp"] + atm_row["put"]["ltp"], 4)
    call_wall = max(rows, key=lambda r: _oi(r, "call"))["strike"] if call_oi else None
    put_wall = max(rows, key=lambda r: _oi(r, "put"))["strike"] if put_oi else None

    # Option writers hold most open interest: put writing builds support, call writing caps the upside.
    score, notes = 0, []
    if pcr is not None:
        if pcr >= 1.2:
            score, notes = score + 1, [*notes, f"PCR {pcr} - more puts written, supportive"]
        elif pcr <= 0.8:
            score, notes = score - 1, [*notes, f"PCR {pcr} - more calls written, capping"]
        else:
            notes.append(f"PCR {pcr} - balanced")
    if call_chg or put_chg:
        if put_chg > 1.2 * max(call_chg, 0):
            score, notes = score + 1, [*notes, "fresh put writing today"]
        elif call_chg > 1.2 * max(put_chg, 0):
            score, notes = score - 1, [*notes, "fresh call writing today"]
    if spot is not None and call_wall and put_wall:
        notes.append(f"range by OI: {put_wall:g} (put wall) to {call_wall:g} (call wall)")
    bias = "bullish" if score >= 1 else "bearish" if score <= -1 else "neutral"
    return {
        "pcr": pcr, "max_pain": max_pain(rows), "call_wall": call_wall, "put_wall": put_wall, "atm": atm,
        "atm_iv": atm_iv, "straddle": straddle, "call_oi": call_oi, "put_oi": put_oi,
        "call_oi_change": call_chg, "put_oi_change": put_chg, "bias": bias, "bias_score": score, "notes": notes,
    }


def _delta(side: dict, kind: str, strike: float, spot: float) -> float:
    """Absolute delta; without greeks, a rough guess from moneyness."""
    d = side.get("delta")
    if d is not None and d != 0:
        return abs(d)
    m = (spot - strike) / spot * (1 if kind == "CE" else -1)
    return min(0.95, max(0.05, 0.5 + m * 25))


def pick_option(rows: list[dict], spot: float, kind: str, target: float, stop: float, lot_size: int | None,
                tick: float = TICK) -> dict | None:
    """The near-ATM option of one side with delta closest to ~0.55, a tight spread and real open interest."""
    side_key = "call" if kind == "CE" else "put"
    atm = atm_strike(rows, spot)
    if atm is None:
        return None
    i = next(n for n, r in enumerate(rows) if r["strike"] == atm)
    near = [r for r in rows[max(0, i - 4): i + 5] if (r.get(side_key) or {}).get("ltp")]
    if not near:
        return None
    top_oi = max(_oi(r, side_key) for r in near) or 1.0

    def cost(r: dict) -> float:
        s = r[side_key]
        spread = (s["ask"] - s["bid"]) / s["ltp"] if s.get("ask") and s.get("bid") and s["ask"] > s["bid"] else 0.02
        thin = 0.3 if _oi(r, side_key) < 0.1 * top_oi else 0.0
        return abs(_delta(s, kind, r["strike"], spot) - TARGET_DELTA) + 2 * min(spread, 0.2) + thin

    best = min(near, key=cost)
    s = best[side_key]
    delta = _delta(s, kind, best["strike"], spot)
    gamma = s.get("gamma") or 0.0
    entry = s["ask"] if s.get("ask") and s["ask"] > 0 else s["ltp"]
    move_t, move_s = abs(target - spot), abs(spot - stop)
    # Second-order (delta + gamma) estimate of the premium when the index reaches its target or stop.
    prem_t = _tick(entry + delta * move_t + 0.5 * gamma * move_t**2, tick)
    prem_s = _tick(max(tick, entry - delta * move_s + 0.5 * gamma * move_s**2), tick)
    lot = int(lot_size) if lot_size else None
    return {
        "kind": kind,
        "strike": best["strike"],
        "instrument_key": s.get("instrument_key"),
        "moneyness": "ATM" if best["strike"] == atm else ("ITM" if (best["strike"] < spot) == (kind == "CE") else "OTM"),
        "ltp": s["ltp"],
        "entry": _tick(entry, tick),
        "target": prem_t,
        "stop": prem_s,
        "delta": round(delta, 2),
        "iv": s.get("iv"),
        "theta": s.get("theta"),
        "oi": s.get("oi"),
        "lot_size": lot,
        "lot_cost": round(entry * lot, 2) if lot else None,
        "profit_per_lot": round((prem_t - entry) * lot, 2) if lot else None,
        "loss_per_lot": round((entry - prem_s) * lot, 2) if lot else None,
        "theta_per_lot": round(abs(s["theta"]) * lot, 2) if lot and s.get("theta") else None,
        "underlying_target": round(target, 4),
        "underlying_stop": round(stop, 4),
    }


def recommend(rows: list[dict], spot: float | None, card: dict | None, summary: dict, lot_size: int | None,
              tick: float = TICK) -> dict:
    """BUY CE / BUY PE / WAIT from the index's intraday call, with the chain's OI as a cross-check."""
    if not rows or spot is None:
        return {"action": "WAIT", "reason": "No option prices available.", "pick": None, "candidates": {}}
    verdict = (card or {}).get("verdict") or "WAIT"
    levels = (card or {}).get("levels")
    atr = (card or {}).get("atr") or spot * 0.002
    # Each side's candidate, using the call's levels when it has them, else the usual 2.5/1.5 ATR.
    cands = {}
    for kind, d in (("CE", 1), ("PE", -1)):
        if levels and verdict == ("BUY" if d == 1 else "SELL"):
            t, s = levels["target"], levels["stop"]
        else:
            t, s = spot + d * 2.5 * atr, spot - d * 1.5 * atr
        cands[kind] = pick_option(rows, spot, kind, t, s, lot_size, tick)

    bias = summary.get("bias")
    if verdict in ("BUY", "SELL"):
        kind = "CE" if verdict == "BUY" else "PE"
        word = "up" if verdict == "BUY" else "down"
        what = {"index": "Index", "currency": "Currency pair"}.get(((card or {}).get("instrument") or {}).get("kind"), "Stock")
        reason = f"{what} intraday call is {verdict}: {(card or {}).get('reason') or ''}".strip()
        against = (verdict == "BUY" and bias == "bearish") or (verdict == "SELL" and bias == "bullish")
        if against:
            reason += f" Caution: open interest leans {bias}, against the move {word}; use a smaller size."
        elif bias != "neutral":
            reason += f" Open interest also leans {bias}."
        return {"action": f"BUY {kind}", "reason": reason, "pick": cands.get(kind), "candidates": cands, "against_oi": against}
    why = (card or {}).get("reason") or "No confirmed intraday call yet."
    return {"action": "WAIT", "reason": f"No option buy yet: {why}", "pick": None, "candidates": cands, "against_oi": False}


def oi_flow(rows: list[dict], spot: float | None, n: int = FLOW_STRIKES) -> dict | None:
    """Today's OI change near the money, read through each strike's build-up: call buying and short covering
    or put writing lean bullish; call writing or put buying lean bearish. Score in [-1, 1] by OI weight."""
    near = window(rows, spot, n)
    bull = bear = 0.0
    totals = {(side, k): 0.0 for side in ("call", "put") for k in BUILDUP}
    movers = []
    for r in near:
        for side in ("call", "put"):
            leg = r.get(side) or {}
            b, chg = leg.get("buildup"), leg.get("oi_change")
            if not b or not chg:
                continue
            w = abs(chg)
            totals[(side, b)] += w
            if FLOW_SIGN[(side, b)] > 0:
                bull += w
            else:
                bear += w
            movers.append({"strike": r["strike"], "side": "CE" if side == "call" else "PE", "buildup": b,
                           "label": BUILDUP[b], "oi_change": chg, "lean": "bullish" if FLOW_SIGN[(side, b)] > 0 else "bearish"})
    if bull + bear <= 0:
        return None
    score = (bull - bear) / (bull + bear)
    lean = "bullish" if score >= 0.2 else "bearish" if score <= -0.2 else "neutral"
    movers.sort(key=lambda m: -abs(m["oi_change"]))
    text = {
        "bullish": "Near-the-money OI today leans bullish: put writing / call buying outweigh call writing / put buying.",
        "bearish": "Near-the-money OI today leans bearish: call writing / put buying outweigh put writing / call buying.",
        "neutral": "Near-the-money OI today is balanced between bullish and bearish build-ups.",
    }[lean]
    return {
        "score": round(score, 2), "lean": lean, "text": text, "strikes": len(near),
        "breakdown": [{"side": "CE" if side == "call" else "PE", "buildup": k, "label": BUILDUP[k], "oi_change": v}
                      for (side, k), v in totals.items() if v],
        "top": movers[:5],
    }


def iv_percentile(history: dict[str, float], current: float | None) -> dict | None:
    """IV rank (where today sits between the period's low and high) and percentile (share of days below)
    from a {date: ATM IV} history. Needs 20 days."""
    vals = [v for _, v in sorted(history.items()) if v]
    if current is None or len(vals) < 20:
        return None
    lo, hi = min(vals), max(vals)
    return {"rank": round(100 * (current - lo) / (hi - lo)) if hi > lo else 50,
            "percentile": round(100 * sum(v < current for v in vals) / len(vals)),
            "days": len(vals), "low": round(lo, 2), "high": round(hi, 2), "source": "own"}


def window(rows: list[dict], spot: float | None, n: int = WINDOW) -> list[dict]:
    atm = atm_strike(rows, spot)
    if atm is None:
        return rows
    i = next(k for k, r in enumerate(rows) if r["strike"] == atm)
    return rows[max(0, i - n): i + n + 1]


def upcoming_expiries(contracts: list[dict], today: date) -> list[str]:
    return sorted({c["expiry"] for c in contracts if c.get("expiry") and c["expiry"] >= today.isoformat()})


def lot_for(contracts: list[dict], expiry: str) -> int | None:
    return next((int(c["lot_size"]) for c in contracts if c.get("expiry") == expiry and c.get("lot_size")), None)
