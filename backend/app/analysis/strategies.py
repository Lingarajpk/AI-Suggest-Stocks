"""Entry strategies, each producing BUY/SELL points known at the candle's close (no look-ahead).

  trend_momentum   Supertrend up + close above VWAP + MACD histogram > 0 (mirror for short). On daily
                   candles the VWAP leg is anchored at the latest confirmed swing.
  liquidity_sweep  A wick runs a known level (confirmed swing high/low, equal highs/lows, previous
                   day's high/low) and the candle closes back on the other side: a stop-hunt reversal.
  fvg / ifvg       Fair value gap = 3-candle gap (low[i] > high[i-2]). A retest that closes in the gap's
                   direction is a signal. A gap closed straight through flips into an inversion gap
                   (IFVG) that is traded the opposite way on its retest.
  amd              Power of 3 (intraday): 09:15-10:15 range = accumulation, a sweep of it before 13:00 =
                   manipulation, a close back inside before 14:30 = distribution, traded against the sweep.
  breakout_prob    How often the next candle reached prior close +/- 0.5/1/1.5 ATR in this stock's own
                   history, split by whether the current candle is green or red (causal expanding rate).
  supertrend_flip  Supertrend (10, 3) changes direction.
  macd_divergence  Price makes a lower low (higher high) between confirmed swings while MACD does not.

Every strategy's points are backtested with the same trade rules as the main signal
(history.simulate_trades) and against the base rate, pooled across the universe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis import candles as cs
from app.analysis import indicators as ta
from app.analysis import volume_profile as vp
from app.analysis.history import simulate_trades
from app.analysis.signals import MIN_BARS

# key -> (name, kind, description)
CATALOG: dict[str, tuple[str, str, str]] = {
    "trend_momentum": ("Trend + Momentum", "trend",
                       "Supertrend, VWAP and MACD all agree. Signal on the first candle they line up."),
    "liquidity_sweep": ("Liquidity Sweep", "reversal",
                        "Wick runs a swing high/low, equal highs/lows or previous day's high/low, then closes back inside."),
    "fvg": ("Fair Value Gap retest", "continuation",
            "Price returns into an open 3-candle gap and closes in the gap's direction."),
    "ifvg": ("Inverted FVG retest", "reversal",
             "A gap that price closed through flips sides; its retest is traded the other way."),
    "amd": ("AMD / Power of 3", "reversal",
            "Intraday only: opening-hour range swept before 13:00, then a close back inside it."),
    "breakout_prob": ("Breakout Probability", "statistical",
                      "Next-candle odds of reaching ±0.5 ATR from this stock's own history turn clearly one-sided."),
    "supertrend_flip": ("Supertrend flip", "trend", "Supertrend (10, 3) changes direction."),
    "macd_divergence": ("MACD divergence", "reversal",
                        "Lower low in price with a higher MACD low (or the mirror) between confirmed swings."),
}

# Extra features offered to the combined outlook model (kept only if they help on unseen data).
FEATURES: dict[str, str] = {
    "supertrend": "Supertrend direction",
    "trend_momentum": "Trend + Momentum alignment",
    "sweep": "Liquidity sweep",
    "fvg": "Fair value gaps",
    "amd": "AMD (Power of 3)",
    "breakout_prob": "Breakout probability",
    "candles": "Candlestick patterns",
    "cvd_div": "CVD divergence (est.)",
    "value_area": "Price vs value area",
    "macd_div": "MACD divergence",
}

LEVELS = (0.5, 1.0, 1.5)
MIN_SAMPLES = 20
DECAY = 10  # bars a one-off signal keeps influencing the model features
SWEEP_EQ = 0.1  # equal highs/lows tolerance (x ATR)
FVG_MIN = 0.25  # minimum gap size (x ATR)
ZONE_LIFE = 100


def _known_swings(swings: list[ta.Swing], n: int) -> list[list[ta.Swing]]:
    """For each bar, the swings confirmed at or before the previous bar's close."""
    by_bar: list[list[ta.Swing]] = [[] for _ in range(n)]
    for s in swings:
        if s.confirm + 1 < n:
            by_bar[s.confirm + 1].append(s)
    return by_bar


def _anchor(swings: list[ta.Swing], n: int) -> np.ndarray:
    a = np.full(n, -1)
    for s in sorted(swings, key=lambda s: s.confirm):
        if s.confirm < n:
            a[s.confirm:] = s.idx
    return a


def trend_momentum(df: pd.DataFrame, ind: pd.DataFrame, swings: list[ta.Swing], intraday: bool) -> tuple[pd.Series, pd.Series, pd.Series]:
    c = df["close"]
    ref = ind["vwap"] if intraday else ta.anchored_vwap(df, _anchor(swings, len(df)))
    st, hist = ind["st_dir"], ind["macd_hist"]
    long = (st == 1) & (c > ref) & (hist > 0)
    short = (st == -1) & (c < ref) & (hist < 0)
    state = long.astype(float) - short.astype(float)
    return long & ~long.shift(fill_value=False), short & ~short.shift(fill_value=False), state


def liquidity_sweep(df: pd.DataFrame, ind: pd.DataFrame, swings: list[ta.Swing], intraday: bool) -> tuple[np.ndarray, np.ndarray, list[str]]:
    h, l, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    a = ind["atr14"].to_numpy(float)
    n = len(df)
    buy, sell = np.zeros(n, bool), np.zeros(n, bool)
    label = [""] * n
    known = _known_swings(swings, n)
    levels: list[dict] = []  # {price, kind, label, born}
    days = df.index.date
    for i in range(n):
        tol = SWEEP_EQ * a[i] if np.isfinite(a[i]) else 0.0
        for s in known[i]:
            eq = any(lv["kind"] == s.kind and lv["label"] != "PDH/PDL" and abs(lv["price"] - s.price) <= tol for lv in levels)
            name = ("equal highs" if s.kind == "H" else "equal lows") if eq else ("swing high" if s.kind == "H" else "swing low")
            levels.append({"price": s.price, "kind": s.kind, "label": name, "born": i})
        if intraday and i > 0 and days[i] != days[i - 1]:
            levels = [lv for lv in levels if not lv["label"].startswith("previous day")]
            prev = np.where(days == days[i - 1])[0]
            levels.append({"price": float(h[prev].max()), "kind": "H", "label": "previous day high", "born": i})
            levels.append({"price": float(l[prev].min()), "kind": "L", "label": "previous day low", "born": i})
        levels = [lv for lv in levels if i - lv["born"] <= 150][-24:]
        up_hit = dn_hit = None
        alive = []
        for lv in levels:
            if lv["kind"] == "H" and h[i] > lv["price"]:
                if c[i] < lv["price"] and (up_hit is None or lv["price"] > up_hit["price"]):
                    up_hit = lv
                continue  # swept or broken: the liquidity there is used up
            if lv["kind"] == "L" and l[i] < lv["price"]:
                if c[i] > lv["price"] and (dn_hit is None or lv["price"] < dn_hit["price"]):
                    dn_hit = lv
                continue
            alive.append(lv)
        levels = alive
        if i < MIN_BARS // 2 or (up_hit and dn_hit):
            continue
        if up_hit:
            sell[i], label[i] = True, f"swept {up_hit['label']} {up_hit['price']:.2f}"
        elif dn_hit:
            buy[i], label[i] = True, f"swept {dn_hit['label']} {dn_hit['price']:.2f}"
    return buy, sell, label


def fair_value_gaps(df: pd.DataFrame, ind: pd.DataFrame) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], list[dict]]:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = ind["atr14"].to_numpy(float)
    n = len(df)
    sig = {k: (np.zeros(n, bool), np.zeros(n, bool)) for k in ("fvg", "ifvg")}
    zones: list[dict] = []  # {lo, hi, dir, kind, born}
    for i in range(n):
        keep = []
        for z in zones:
            if i - z["born"] > ZONE_LIFE:
                continue
            lo, hi, d = z["lo"], z["hi"], z["dir"]
            through = c[i] < lo if d == 1 else c[i] > hi
            if through:
                if z["kind"] == "fvg":  # closed straight through: flips into an inversion gap
                    keep.append({**z, "dir": -d, "kind": "ifvg", "born": i})
                continue
            touched = (l[i] <= hi) if d == 1 else (h[i] >= lo)
            confirms = (c[i] > o[i] and c[i] >= lo) if d == 1 else (c[i] < o[i] and c[i] <= hi)
            if touched and confirms and i > z["born"]:
                b, s = sig[z["kind"]]
                (b if d == 1 else s)[i] = True
                continue  # one trade per zone
            keep.append(z)
        zones = keep[-30:]
        if i >= 2 and np.isfinite(a[i]):
            if l[i] > h[i - 2] and l[i] - h[i - 2] >= FVG_MIN * a[i]:
                zones.append({"lo": float(h[i - 2]), "hi": float(l[i]), "dir": 1, "kind": "fvg", "born": i, "start": i - 2})
            elif h[i] < l[i - 2] and l[i - 2] - h[i] >= FVG_MIN * a[i]:
                zones.append({"lo": float(h[i]), "hi": float(l[i - 2]), "dir": -1, "kind": "fvg", "born": i, "start": i - 2})
    idx = df.index
    open_zones = [{"kind": z["kind"], "direction": "up" if z["dir"] == 1 else "down", "low": round(z["lo"], 2), "high": round(z["hi"], 2),
                   "start_time": idx[z.get("start", z["born"])].isoformat(), "since_time": idx[z["born"]].isoformat()}
                  for z in zones[-8:]]
    for i in range(min(n, MIN_BARS // 2)):
        for b, s in sig.values():
            b[i] = s[i] = False
    return sig, open_zones


def amd(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[str]]:
    h, l, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    n = len(df)
    buy, sell = np.zeros(n, bool), np.zeros(n, bool)
    label = [""] * n
    tod = df.index.hour * 60 + df.index.minute
    days = pd.Series(df.index.date)
    for _, idx in days.groupby(days).indices.items():
        acc = [i for i in idx if tod[i] < 10 * 60 + 15]
        if not acc:
            continue
        a_hi, a_lo = h[acc].max(), l[acc].min()
        swept = 0
        for i in idx:
            t = tod[i]
            if t < 10 * 60 + 15:
                continue
            if swept == 0:
                if t >= 13 * 60:
                    break
                up, dn = h[i] > a_hi, l[i] < a_lo
                if up == dn:
                    continue
                swept = 1 if up else -1
            if t >= 14 * 60 + 30:
                break
            if swept == 1 and c[i] < a_hi:
                sell[i], label[i] = True, f"swept opening-hour high {a_hi:.2f}, closed back inside"
                break
            if swept == -1 and c[i] > a_lo:
                buy[i], label[i] = True, f"swept opening-hour low {a_lo:.2f}, closed back inside"
                break
    return buy, sell, label


def breakout_probability(df: pd.DataFrame, ind: pd.DataFrame) -> tuple[dict[float, tuple[pd.Series, pd.Series]], pd.Series]:
    """Per level k: (P(next high >= close + k ATR), P(next low <= close - k ATR)) known at each bar,
    from this stock's earlier candles of the same colour. Also returns the colour series."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    a = ind["atr14"]
    green = c >= o
    out = {}
    for k in LEVELS:
        hit_up = (h.shift(-1) >= c + k * a).astype(float).where(a.notna() & h.shift(-1).notna())
        hit_dn = (l.shift(-1) <= c - k * a).astype(float).where(a.notna() & l.shift(-1).notna())
        res = []
        for hit in (hit_up, hit_dn):
            p = pd.Series(np.nan, index=df.index)
            for col in (True, False):
                m = (green == col) & hit.notna()
                # Outcome of bar j is known at the close of j+1, so bar i may use j <= i-1.
                s = (hit.where(m, 0.0).fillna(0.0)).cumsum().shift(1)
                cnt = m.astype(float).cumsum().shift(1)
                rate = (s / cnt).where(cnt >= MIN_SAMPLES)
                p = p.where(green != col, rate)
            res.append(p)
        out[k] = (res[0], res[1])
    return out, green


def compute(df: pd.DataFrame, ind: pd.DataFrame, timeframe: str) -> dict:
    """All strategies, candlesticks and order-flow proxies for one instrument's completed candles."""
    intraday = timeframe != "1d"
    n = len(df)
    swings = ta.confirmed_pivots(df, ind["atr14"])
    idx = df.index
    signals: dict[str, tuple[pd.Series, pd.Series]] = {}
    labels: dict[str, list[str]] = {}

    def as_series(b, s):
        return pd.Series(np.asarray(b, bool), index=idx), pd.Series(np.asarray(s, bool), index=idx)

    tm_b, tm_s, tm_state = trend_momentum(df, ind, swings, intraday)
    signals["trend_momentum"] = (tm_b.fillna(False), tm_s.fillna(False))
    b, s, labels["liquidity_sweep"] = liquidity_sweep(df, ind, swings, intraday)
    signals["liquidity_sweep"] = as_series(b, s)
    fvg_sig, zones = fair_value_gaps(df, ind)
    for k, (b, s) in fvg_sig.items():
        signals[k] = as_series(b, s)
    if intraday:
        b, s, labels["amd"] = amd(df)
    else:
        b, s, labels["amd"] = np.zeros(n, bool), np.zeros(n, bool), [""] * n
    signals["amd"] = as_series(b, s)
    probs, green = breakout_probability(df, ind)
    bias = (probs[0.5][0] - probs[0.5][1])
    bias_prev = bias.shift()
    signals["breakout_prob"] = ((bias >= 0.1) & ~(bias_prev >= 0.1), (bias <= -0.1) & ~(bias_prev <= -0.1))
    st = ind["st_dir"]
    signals["supertrend_flip"] = ((st == 1) & (st.shift() == -1), (st == -1) & (st.shift() == 1))
    macd_ev = ta.macd_events(ind, swings)
    signals["macd_divergence"] = (macd_ev["macd_div"] > 0, macd_ev["macd_div"] < 0)

    cdl = cs.detect_series(df, ind)
    levels = vp.levels_series(df, intraday)
    cvd_div = vp.cvd_divergence_series(df, swings)

    def signed(key: str) -> pd.Series:
        bb, ss = signals[key]
        return bb.astype(float) - ss.astype(float)

    X = pd.DataFrame(index=idx)
    X["supertrend"] = st.fillna(0.0)
    X["trend_momentum"] = tm_state.fillna(0.0)
    X["sweep"] = cs.decayed(signed("liquidity_sweep"), DECAY)
    X["fvg"] = cs.decayed((signed("fvg") + signed("ifvg")).clip(-1, 1), DECAY)
    X["amd"] = cs.decayed(signed("amd"), DECAY)
    X["breakout_prob"] = (probs[1.0][0] - probs[1.0][1]).fillna(0.0)
    X["candles"] = cs.net_signal(cdl)
    X["cvd_div"] = cs.decayed(pd.Series(cvd_div, index=idx), DECAY)
    X["value_area"] = vp.value_area_position(df["close"], levels, ind["atr14"])
    X["macd_div"] = cs.decayed(macd_ev["macd_div"], DECAY)

    return {
        "intraday": intraday,
        "swings": swings,
        "signals": signals,
        "labels": labels,
        "tm_state": tm_state,
        "zones": zones,
        "probs": probs,
        "green": green,
        "candles": cdl,
        "macd_events": macd_ev,
        "features": X[list(FEATURES)],
    }


def _last_signal(df: pd.DataFrame, b: pd.Series, s: pd.Series, label: list[str] | None) -> dict | None:
    hits = np.nonzero((b | s).to_numpy())[0]
    if not len(hits):
        return None
    i = int(hits[-1])
    return {"time": df.index[i].isoformat(), "side": "buy" if b.iloc[i] else "sell", "price": round(float(df["close"].iloc[i]), 2),
            "bars_ago": len(df) - 1 - i, "detail": (label[i] if label else "") or None}


def block(df: pd.DataFrame, ind: pd.DataFrame, res: dict, with_series: bool) -> dict:
    """What the stock page shows: each strategy's state now, breakout odds, candlesticks, volume profile."""
    last = len(df) - 1
    c = float(df["close"].iloc[-1])
    v = ind.iloc[-1]
    states = []
    for key, (name, kind, desc) in CATALOG.items():
        b, s = res["signals"][key]
        ls = _last_signal(df, b, s, res["labels"].get(key))
        state, detail = "none", ""
        if key == "trend_momentum":
            st = res["tm_state"].iloc[-1]
            state = "long" if st > 0 else "short" if st < 0 else "none"
            parts = [f"Supertrend {'up' if v['st_dir'] == 1 else 'down'}",
                     f"MACD histogram {'above' if (v['macd_hist'] or 0) > 0 else 'below'} 0"]
            if res["intraday"] and ta.clean(v["vwap"]) is not None:
                parts.insert(1, f"price {'above' if c > v['vwap'] else 'below'} VWAP")
            detail = ("Aligned " + state + ": " if state != "none" else "Not aligned: ") + ", ".join(parts)
        elif key == "supertrend_flip":
            state = "long" if v["st_dir"] == 1 else "short" if v["st_dir"] == -1 else "none"
            detail = f"Supertrend {'up' if state == 'long' else 'down'} at {ta.clean(v['st_line'])}"
        elif key == "amd" and not res["intraday"]:
            state, detail = "n/a", "Session-based: only applies to intraday candles."
        elif key == "fvg":
            open_fvg = [z for z in res["zones"] if z["kind"] == "fvg"]
            detail = f"{len(open_fvg)} open gap(s) nearby" if open_fvg else "No open gaps"
        elif key == "ifvg":
            open_ifvg = [z for z in res["zones"] if z["kind"] == "ifvg"]
            detail = f"{len(open_ifvg)} inversion gap(s) waiting for a retest" if open_ifvg else "No inversion gaps"
        elif key == "breakout_prob":
            p_up, p_dn = res["probs"][0.5]
            u, d = p_up.iloc[-1], p_dn.iloc[-1]
            if pd.notna(u) and pd.notna(d):
                state = "long" if u - d >= 0.1 else "short" if d - u >= 0.1 else "none"
                detail = f"Next candle reaches +0.5 ATR {u:.0%} vs −0.5 ATR {d:.0%} of the time"
            else:
                detail = "Not enough history yet"
        if ls and ls["bars_ago"] <= 3 and state in ("none",):
            state = "long" if ls["side"] == "buy" else "short"
        states.append({"key": key, "name": name, "kind": kind, "description": desc, "state": state,
                       "detail": detail or None, "last_signal": ls})

    a = ta.clean(v["atr14"])
    green = bool(res["green"].iloc[-1])
    table = []
    for k in LEVELS:
        p_up, p_dn = res["probs"][k]
        table.append({
            "atr_mult": k,
            "up_level": round(c + k * a, 2) if a else None,
            "down_level": round(c - k * a, 2) if a else None,
            "p_up_pct": None if pd.isna(p_up.iloc[-1]) else round(100 * float(p_up.iloc[-1]), 1),
            "p_down_pct": None if pd.isna(p_dn.iloc[-1]) else round(100 * float(p_dn.iloc[-1]), 1),
        })
    out = {
        "strategies": states,
        "breakout": {"last_candle": "green" if green else "red", "levels": table,
                     "note": "Share of this stock's past candles of the same colour whose NEXT candle reached each level."},
        "candles": cs.recent(df, res["candles"], bars=60 if with_series else 5),
        "volume_profile": vp.current(df, res["intraday"], res["swings"]),
        "macd": {
            "last_cross": _event(df, res["macd_events"]["macd_cross"]),
            "last_zero_cross": _event(df, res["macd_events"]["macd_zero"]),
            "last_divergence": _event(df, res["macd_events"]["macd_div"]),
        },
    }
    if with_series:
        out["fvg_zones"] = res["zones"]
        markers = []
        for key in CATALOG:
            b, s = res["signals"][key]
            for i in np.nonzero((b | s).to_numpy())[0]:
                if i >= last - 300:
                    markers.append({"time": df.index[i].isoformat(), "key": key, "name": CATALOG[key][0],
                                    "side": "buy" if b.iloc[i] else "sell", "price": round(float(df["close"].iloc[i]), 2)})
        out["markers"] = sorted(markers, key=lambda m: m["time"])
    return out


def _event(df: pd.DataFrame, s: pd.Series) -> dict | None:
    nz = np.nonzero(s.to_numpy())[0]
    if not len(nz):
        return None
    i = int(nz[-1])
    return {"time": df.index[i].isoformat(), "direction": "up" if s.iloc[i] > 0 else "down", "bars_ago": len(df) - 1 - i}


# ---- backtest -------------------------------------------------------------
def _empty() -> dict:
    return {"buy_n": 0, "buy_hit": 0, "sell_n": 0, "sell_hit": 0, "ret": [], "pnl": [], "base_up": 0, "base_n": 0}


def raw_stats(df: pd.DataFrame, ind: pd.DataFrame, res: dict, horizon: int) -> dict[str, dict]:
    """Per strategy and candlestick: direction hit rate after `horizon` bars and simulated trades."""
    close = df["close"]
    fwd = (close.shift(-horizon) / close - 1) * 100
    valid = pd.Series(np.arange(len(df)) >= MIN_BARS, index=df.index)
    base = fwd[valid].dropna()
    base_up, base_n = int((base > 0).sum()), int(len(base))
    pairs = dict(res["signals"])
    for k, s in res["candles"].items():
        pairs[f"cdl_{k}"] = (s > 0, s < 0)
    out = {}
    for key, (b, s) in pairs.items():
        b, s = b & valid, s & valid
        acc = _empty()
        acc["base_up"], acc["base_n"] = base_up, base_n
        rb, rs = fwd[b].dropna(), fwd[s].dropna()
        acc["buy_n"], acc["buy_hit"] = int(len(rb)), int((rb > 0).sum())
        acc["sell_n"], acc["sell_hit"] = int(len(rs)), int((rs < 0).sum())
        acc["ret"] = [float(x) for x in rb] + [float(-x) for x in rs]
        trades, _ = simulate_trades(df, ind, b, s)
        acc["pnl"] = [float(t["pnl_pct"]) for t in trades]
        out[key] = acc
    return out


def merge_stats(raw: list[dict[str, dict]]) -> dict[str, dict]:
    acc: dict[str, dict] = {}
    for one in raw:
        for k, v in one.items():
            a = acc.setdefault(k, _empty())
            for f in ("buy_n", "buy_hit", "sell_n", "sell_hit", "base_up", "base_n"):
                a[f] += v[f]
            a["ret"] += v["ret"]
            a["pnl"] += v["pnl"]
    return {k: finalize(v) for k, v in acc.items()}


def finalize(a: dict) -> dict:
    n = a["buy_n"] + a["sell_n"]
    p_up = a["base_up"] / a["base_n"] if a["base_n"] else None
    hit = (a["buy_hit"] + a["sell_hit"]) / n if n else None
    base = (a["buy_n"] * p_up + a["sell_n"] * (1 - p_up)) / n if n and p_up is not None else None
    pnl = a["pnl"]
    return {
        "signals": n,
        "buy_signals": a["buy_n"],
        "sell_signals": a["sell_n"],
        "hit_rate": None if hit is None else round(100 * hit, 1),
        "base_rate": None if base is None else round(100 * base, 1),
        "edge_pts": None if hit is None or base is None else round(100 * (hit - base), 1),
        "avg_move_pct": round(sum(a["ret"]) / len(a["ret"]), 2) if a["ret"] else None,
        "trades": len(pnl),
        "trade_win_rate": round(100 * sum(p > 0 for p in pnl) / len(pnl), 1) if pnl else None,
        "avg_trade_pct": round(sum(pnl) / len(pnl), 2) if pnl else None,
    }


def rows(merged: dict[str, dict]) -> list[dict]:
    """Strategy and candlestick rows in catalog order, with names and descriptions."""
    out = []
    for key, (name, kind, desc) in CATALOG.items():
        out.append({"key": key, "group": "strategy", "name": name, "kind": kind, "description": desc,
                    **(merged.get(key) or finalize(_empty()))})
    for key, (name, bias, desc) in cs.CATALOG.items():
        out.append({"key": f"cdl_{key}", "group": "candlestick", "name": name, "kind": bias, "description": desc,
                    **(merged.get(f"cdl_{key}") or finalize(_empty()))})
    return out
