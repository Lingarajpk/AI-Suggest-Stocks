"""Chart-pattern detection and per-pattern backtest (Big Book of Chart Patterns, core set).

Every pattern is built from swing pivots that are only used once they are *confirmed*
(price has reversed by a multiple of ATR), so detection never looks ahead. Each detected
pattern then follows the book's recipe:

  setup      the shape (prior trend + pivots within ATR-based tolerances)
  trigger    a close beyond the breakout line; "volume confirmed" when relative volume >= 1
  target     the book's measured move
  stop       the level that invalidates the pattern

The backtest walks forward from the breakout and records whether the target or the stop
was hit first. These are in-sample statistics, before costs and slippage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FINE_MULT = 1.25  # zigzag reversal (x ATR) for triangles, flags, wedges, rectangles
COARSE_MULT = 2.25  # for doubles, triples, head & shoulders, cups, broadening
VOLUME_CONFIRM = 1.0  # breakout volume vs 20-bar average ("above average volume")

# key -> (name, bias, kind, book stars 1-4)
CATALOG: dict[str, tuple[str, str, str, int]] = {
    "broadening_bottom": ("Broadening Bottom", "bullish", "reversal", 1),
    "broadening_top": ("Broadening Top", "bearish", "reversal", 1),
    "cup_handle": ("Cup and Handle", "bullish", "continuation", 2),
    "cup_handle_inverted": ("Cup and Handle (Inverted)", "bearish", "continuation", 3),
    "double_bottom": ("Double Bottom", "bullish", "reversal", 2),
    "double_top": ("Double Top", "bearish", "reversal", 2),
    "triple_bottom": ("Triple Bottom", "bullish", "reversal", 3),
    "triple_top": ("Triple Top", "bearish", "reversal", 2),
    "flag_bull": ("Bull Flag", "bullish", "continuation", 3),
    "flag_bear": ("Bear Flag", "bearish", "continuation", 2),
    "flag_high_tight": ("High & Tight Flag", "bullish", "continuation", 4),
    "pennant_bull": ("Bull Pennant", "bullish", "continuation", 2),
    "pennant_bear": ("Bear Pennant", "bearish", "continuation", 2),
    "hs_top": ("Head & Shoulders", "bearish", "reversal", 4),
    "hs_inverse": ("Inverse Head & Shoulders", "bullish", "reversal", 3),
    "hs_inverse_cont": ("Inverse Head & Shoulders (Continuation)", "bullish", "continuation", 3),
    "triangle_ascending": ("Ascending Triangle", "bullish", "continuation", 2),
    "triangle_descending": ("Descending Triangle", "bearish", "continuation", 2),
    "triangle_symmetrical": ("Symmetrical Triangle", "either", "continuation", 2),
    "wedge_rising": ("Rising Wedge", "bearish", "continuation", 2),
    "wedge_falling": ("Falling Wedge", "bullish", "continuation", 2),
    "rectangle_top": ("Rectangle Top", "either", "continuation", 1),
    "rectangle_bottom": ("Rectangle Bottom", "either", "reversal", 2),
    "gap_breakaway": ("Breakaway Gap", "either", "continuation", 3),
    "gap_continuation": ("Continuation Gap", "either", "continuation", 2),
    "gap_exhaustion": ("Exhaustion Gap", "either", "reversal", 3),
    "gap_area": ("Area Gap", "either", "reversal", 3),
    "island_reversal": ("Island Reversal", "either", "reversal", 1),
    "rounding_bottom": ("Rounding Bottom", "bullish", "reversal", 2),
    "rounding_top": ("Rounding Top", "bearish", "reversal", 2),
    "channel_up": ("Ascending Channel", "either", "continuation", 2),
    "channel_down": ("Descending Channel", "either", "continuation", 2),
    "diamond_top": ("Diamond Top", "bearish", "reversal", 2),
    "diamond_bottom": ("Diamond Bottom", "bullish", "reversal", 3),
}

RULES: dict[str, str] = {
    "double_bottom": "Two equal lows after a downtrend; breakout on a close above the high between them. Target: that high + pattern height.",
    "double_top": "Two equal highs after an uptrend; breakout on a close below the low between them. Target: that low − pattern height.",
    "triple_bottom": "Three equal lows after a downtrend; close above the highs between them. Target: breakout + height.",
    "triple_top": "Three equal highs after an uptrend; close below the lows between them. Target: breakout − height.",
    "hs_top": "Head above two shoulders after an uptrend; close below the neckline. Target: neckline − (head − neckline).",
    "hs_inverse": "Head below two shoulders after a downtrend; close above the neckline. Target: neckline + (neckline − head).",
    "hs_inverse_cont": "Inverse head & shoulders inside an uptrend; close above the neckline. Target: neckline + height.",
    "triangle_ascending": "Flat highs and rising lows; close above the highs. Target: breakout + widest height.",
    "triangle_descending": "Flat lows and falling highs; close below the lows. Target: breakout − widest height.",
    "triangle_symmetrical": "Falling highs and rising lows; close outside either line before the apex. Target: breakout ± widest height.",
    "wedge_rising": "Rising, converging highs and lows (5+ touches); close below the lower line. Target: lowest low of the wedge.",
    "wedge_falling": "Falling, converging highs and lows (5+ touches); close above the upper line. Target: highest high of the wedge.",
    "rectangle_top": "Price boxed between flat highs and lows after an uptrend; close outside the box. Target: breakout ± box height.",
    "rectangle_bottom": "Price boxed between flat highs and lows after a downtrend; close outside the box. Target: breakout ± box height.",
    "broadening_top": "Higher highs and lower lows (megaphone) after an uptrend; close below the lower line. Target: lowest low − height.",
    "broadening_bottom": "Higher highs and lower lows (megaphone) after a downtrend; close above the upper line. Target: highest high + height.",
    "flag_bull": "Steep rise (pole) then a short flat/down channel; close above the channel. Target: breakout + pole.",
    "flag_bear": "Steep fall (pole) then a short flat/up channel; close below the channel. Target: breakout − pole.",
    "flag_high_tight": "Price nearly doubles, then a tight pause (<20% pullback); close above it. Target: breakout + half the pole.",
    "pennant_bull": "Steep rise then a small converging triangle; close above it. Target: breakout + pole.",
    "pennant_bear": "Steep fall then a small converging triangle; close below it. Target: breakout − pole.",
    "cup_handle": "Rounded U-shaped cup with a short handle in an uptrend; close above the right lip. Target: lip + cup depth.",
    "cup_handle_inverted": "Inverted rounded cup with a short handle in a downtrend; close below the right lip. Target: lip − cup depth.",
    "gap_breakaway": "Gap out of a consolidation on high volume that holds the next bar. Target: gap-day extreme + move from the box.",
    "gap_continuation": "Gap in the middle of a strong trend that holds the next bar. Target: gap middle + prior move.",
    "gap_exhaustion": "Large gap late in a trend on high volume, then a close back through the gap day. Target: the pre-gap bar's extreme.",
    "gap_area": "Gap inside a consolidation that hooks back within a few bars. Target: gap filled (pre-gap level).",
    "island_reversal": "Gap one way, then a gap back the other way, leaving an island. Target: island range projected from the second gap.",
    "rounding_bottom": "Slow U-shaped bowl (closes fit a curve) after a decline; close above the right rim. Target: rim + bowl depth.",
    "rounding_top": "Slow upside-down bowl after a rise; close below the right rim. Target: rim − dome height.",
    "channel_up": "Price rising between two parallel lines (2+ touches each); close outside either line. Target: breakout ± channel width.",
    "channel_down": "Price falling between two parallel lines (2+ touches each); close outside either line. Target: breakout ± channel width.",
    "diamond_top": "Widening swings then narrowing swings (a diamond) after a rise; close outside the right side. Target: breakout ± widest height.",
    "diamond_bottom": "Widening then narrowing swings after a decline; close outside the right side. Target: breakout ± widest height.",
}


@dataclass(frozen=True)
class Pivot:
    idx: int
    price: float
    kind: str  # "H" or "L"
    confirm: int  # first bar at whose close the pivot is known


@dataclass
class Setup:
    key: str
    start: int
    end: int  # last pivot bar of the shape
    known: int  # bar at which the whole shape is known
    points: list[tuple[int, float]]
    up: tuple | None  # breakout-up line (i1, p1, i2, p2) or None
    down: tuple | None  # breakout-down line
    target: dict[int, float | None]  # absolute target per direction, None = measured from the breakout level
    height: dict[int, float]  # measured-move height per direction
    stop: dict[int, float]
    max_wait: int
    max_hold: int
    lines: list[tuple[int, float, int, float]] = field(default_factory=list)
    variant: str | None = None
    entry_at: int | None = None  # gap patterns: fixed entry bar (no line breakout)
    entry_dir: int = 0


def line_at(line: tuple, i: int) -> float:
    i1, p1, i2, p2 = line
    if i2 == i1:
        return p2
    return p1 + (p2 - p1) * (i - i1) / (i2 - i1)


def flat(price: float, i1: int, i2: int) -> tuple:
    return (i1, price, i2, price)


def fit(points: list[Pivot]) -> tuple:
    """Least-squares line through pivots, returned as two end points."""
    xs = np.array([p.idx for p in points], dtype=float)
    ys = np.array([p.price for p in points], dtype=float)
    if len(points) == 2 or np.ptp(xs) == 0:
        return (points[0].idx, points[0].price, points[-1].idx, points[-1].price)
    slope, icpt = np.polyfit(xs, ys, 1)
    return (int(xs[0]), float(icpt + slope * xs[0]), int(xs[-1]), float(icpt + slope * xs[-1]))


def slope(line: tuple) -> float:
    i1, p1, i2, p2 = line
    return 0.0 if i2 == i1 else (p2 - p1) / (i2 - i1)


def zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray, mult: float) -> list[Pivot]:
    """Alternating swing highs/lows; a pivot is confirmed when price reverses by mult x ATR."""
    n = len(high)
    out: list[Pivot] = []
    if n < 3:
        return out
    up: bool | None = None
    hi_i = lo_i = 0
    for i in range(1, n):
        th = mult * atr[i]
        if up is None:
            if high[i] > high[hi_i]:
                hi_i = i
            if low[i] < low[lo_i]:
                lo_i = i
            if high[hi_i] - low[lo_i] >= th:
                if hi_i > lo_i:
                    out.append(Pivot(lo_i, float(low[lo_i]), "L", i))
                    up = True
                else:
                    out.append(Pivot(hi_i, float(high[hi_i]), "H", i))
                    up = False
            continue
        if up:
            if high[i] >= high[hi_i]:
                hi_i = i
            elif high[hi_i] - low[i] >= th:
                out.append(Pivot(hi_i, float(high[hi_i]), "H", i))
                up = False
                lo_i = hi_i + 1 + int(np.argmin(low[hi_i + 1 : i + 1]))
        else:
            if low[i] <= low[lo_i]:
                lo_i = i
            elif high[i] - low[lo_i] >= th:
                out.append(Pivot(lo_i, float(low[lo_i]), "L", i))
                up = True
                hi_i = lo_i + 1 + int(np.argmax(high[lo_i + 1 : i + 1]))
    return out


class Detector:
    def __init__(self, df: pd.DataFrame, atr: pd.Series, rel_volume: pd.Series) -> None:
        self.o = df["open"].to_numpy(float)
        self.h = df["high"].to_numpy(float)
        self.l = df["low"].to_numpy(float)
        self.c = df["close"].to_numpy(float)
        self.n = len(df)
        a = atr.to_numpy(float).copy()
        rng = self.h - self.l
        fallback = pd.Series(rng).expanding().mean().to_numpy()
        a = np.where(np.isfinite(a) & (a > 0), a, fallback)
        self.atr = np.where(a > 0, a, np.maximum(self.c * 0.001, 1e-9))
        self.rv = rel_volume.to_numpy(float)

    # ---- helpers -------------------------------------------------------
    def tol(self, i: int) -> float:
        return max(0.6 * self.atr[i], 0.004 * self.c[i])

    def prior_trend(self, start: int, price: float, span: int) -> int:
        lb = max(15, span)
        ref = start - lb
        if ref < 0:
            return 0
        move = price - self.c[ref]
        a = self.atr[start]
        return 1 if move > 2.5 * a else -1 if move < -2.5 * a else 0

    def wait_hold(self, span: int) -> tuple[int, int]:
        return int(np.clip(span, 10, 60)), int(np.clip(2 * span, 20, 120))

    # ---- pivot templates -------------------------------------------------
    def coarse(self, piv: list[Pivot]) -> list[Setup]:
        out: list[Setup] = []
        for j in range(len(piv)):
            w = piv[: j + 1]
            for fn in (self._double, self._triple, self._hs, self._broadening, self._cup, self._rounding):
                s = fn(w)
                if s:
                    out.append(s)
        return out

    def fine(self, piv: list[Pivot]) -> list[Setup]:
        out: list[Setup] = []
        for j in range(len(piv)):
            w = piv[: j + 1]
            for fn in (self._triangle, self._wedge, self._rectangle, self._flag, self._channel, self._diamond):
                s = fn(w)
                if s:
                    out.append(s)
        return out

    def _double(self, w: list[Pivot]) -> Setup | None:
        if len(w) < 3:
            return None
        a, m, b = w[-3:]
        t = self.tol(b.idx)
        if abs(a.price - b.price) > t or b.idx - a.idx < 5:
            return None
        bottom = a.kind == "L"
        height = (m.price - min(a.price, b.price)) if bottom else (max(a.price, b.price) - m.price)
        if height < 2 * self.atr[b.idx]:
            return None
        span = b.idx - a.idx
        trend = self.prior_trend(a.idx, a.price, span)
        if (bottom and trend != -1) or (not bottom and trend != 1):
            return None
        wait, hold = self.wait_hold(span)
        variant = f"{self._shape(a)} & {self._shape(b)}"
        if bottom:
            lo = min(a.price, b.price)
            return Setup("double_bottom", a.idx, b.idx, b.confirm, [(a.idx, a.price), (m.idx, m.price), (b.idx, b.price)],
                         flat(m.price, m.idx, b.idx), None, {1: None}, {1: height}, {1: lo}, wait, hold,
                         [(a.idx, lo, b.idx, lo)], variant)
        hi = max(a.price, b.price)
        return Setup("double_top", a.idx, b.idx, b.confirm, [(a.idx, a.price), (m.idx, m.price), (b.idx, b.price)],
                     None, flat(m.price, m.idx, b.idx), {-1: None}, {-1: height}, {-1: hi}, wait, hold,
                     [(a.idx, hi, b.idx, hi)], variant)

    def _shape(self, p: Pivot) -> str:
        """Adam = narrow V (one bar at the extreme), Eve = wide rounded extreme."""
        lo, hi = max(0, p.idx - 3), min(self.n, p.idx + 4)
        band = 0.35 * self.atr[p.idx]
        near = (self.l[lo:hi] <= p.price + band) if p.kind == "L" else (self.h[lo:hi] >= p.price - band)
        return "Adam" if int(near.sum()) <= 2 else "Eve"

    def _triple(self, w: list[Pivot]) -> Setup | None:
        if len(w) < 5:
            return None
        p = w[-5:]
        ext = [p[0], p[2], p[4]]
        mids = [p[1], p[3]]
        t = self.tol(p[4].idx)
        prices = [x.price for x in ext]
        if max(prices) - min(prices) > 1.5 * t:
            return None
        bottom = p[0].kind == "L"
        level = max(m.price for m in mids) if bottom else min(m.price for m in mids)
        height = (level - min(prices)) if bottom else (max(prices) - level)
        if height < 2 * self.atr[p[4].idx]:
            return None
        span = p[4].idx - p[0].idx
        trend = self.prior_trend(p[0].idx, p[0].price, span)
        if (bottom and trend != -1) or (not bottom and trend != 1):
            return None
        wait, hold = self.wait_hold(span)
        pts = [(x.idx, x.price) for x in p]
        if bottom:
            lo = min(prices)
            return Setup("triple_bottom", p[0].idx, p[4].idx, p[4].confirm, pts, flat(level, p[1].idx, p[4].idx), None,
                         {1: None}, {1: height}, {1: lo}, wait, hold, [(p[0].idx, lo, p[4].idx, lo)])
        hi = max(prices)
        return Setup("triple_top", p[0].idx, p[4].idx, p[4].confirm, pts, None, flat(level, p[1].idx, p[4].idx),
                     {-1: None}, {-1: height}, {-1: hi}, wait, hold, [(p[0].idx, hi, p[4].idx, hi)])

    def _hs(self, w: list[Pivot]) -> Setup | None:
        if len(w) < 5:
            return None
        s1, n1, hd, n2, s2 = w[-5:]
        t = self.tol(s2.idx)
        top = s1.kind == "H"
        if top and not (hd.price > max(s1.price, s2.price) + t):
            return None
        if not top and not (hd.price < min(s1.price, s2.price) - t):
            return None
        if abs(s1.price - s2.price) > 3 * t:
            return None
        neck = (n1.idx, n1.price, n2.idx, n2.price)
        # Top: neckline horizontal or rising; bottom: horizontal or falling (per the book).
        if top and n2.price < n1.price - t:
            return None
        if not top and n2.price > n1.price + t:
            return None
        if top and min(s1.price, s2.price) <= max(n1.price, n2.price):
            return None
        if not top and max(s1.price, s2.price) >= min(n1.price, n2.price):
            return None
        height = abs(hd.price - line_at(neck, hd.idx))
        if height < 2 * self.atr[s2.idx]:
            return None
        span = s2.idx - s1.idx
        trend = self.prior_trend(s1.idx, s1.price, span)
        wait, hold = self.wait_hold(span)
        pts = [(x.idx, x.price) for x in (s1, n1, hd, n2, s2)]
        ext = (n1.idx, n1.price, s2.idx + wait, line_at(neck, s2.idx + wait))
        if top:
            if trend != 1:
                return None
            return Setup("hs_top", s1.idx, s2.idx, s2.confirm, pts, None, neck, {-1: None}, {-1: height}, {-1: s2.price},
                         wait, hold, [ext])
        if trend == 0:
            return None
        key = "hs_inverse" if trend == -1 else "hs_inverse_cont"
        return Setup(key, s1.idx, s2.idx, s2.confirm, pts, neck, None, {1: None}, {1: height}, {1: s2.price},
                     wait, hold, [ext])

    def _broadening(self, w: list[Pivot]) -> Setup | None:
        if len(w) < 5:
            return None
        p = w[-5:]
        highs = [x for x in p if x.kind == "H"]
        lows = [x for x in p if x.kind == "L"]
        t = self.tol(p[-1].idx)
        if not (all(b.price > a.price + 0.5 * t for a, b in zip(highs, highs[1:]))
                and all(b.price < a.price - 0.5 * t for a, b in zip(lows, lows[1:]))):
            return None
        span = p[-1].idx - p[0].idx
        trend = self.prior_trend(p[0].idx, p[0].price, span)
        if trend == 0:
            return None
        up, dn = fit(highs), fit(lows)
        hh, ll = max(x.price for x in highs), min(x.price for x in lows)
        height = hh - ll
        wait, hold = self.wait_hold(span)
        pts = [(x.idx, x.price) for x in p]
        lines = [up, dn]
        if trend == 1:
            return Setup("broadening_top", p[0].idx, p[-1].idx, p[-1].confirm, pts, None, dn,
                         {-1: ll - height}, {-1: height}, {-1: hh}, wait, hold, lines)
        return Setup("broadening_bottom", p[0].idx, p[-1].idx, p[-1].confirm, pts, up, None,
                     {1: hh + height}, {1: height}, {1: ll}, wait, hold, lines)

    def _cup(self, w: list[Pivot]) -> Setup | None:
        if len(w) < 4:
            return None
        rlip, handle = w[-2], w[-1]
        normal = rlip.kind == "H"
        t = self.tol(handle.idx)
        for llip in reversed(w[:-2]):
            if llip.kind != rlip.kind:
                continue
            span = rlip.idx - llip.idx
            if span < 25:
                continue
            if span > 300:
                break
            if abs(llip.price - rlip.price) > 2 * t:
                continue
            seg_h, seg_l = self.h[llip.idx + 1 : rlip.idx], self.l[llip.idx + 1 : rlip.idx]
            if normal:
                if seg_h.size and seg_h.max() > max(llip.price, rlip.price):
                    continue
                bottom = float(seg_l.min())
                depth = min(llip.price, rlip.price) - bottom
                closes = self.c[llip.idx : rlip.idx + 1]
                near = (closes <= bottom + depth / 3).mean()
                handle_ok = handle.price >= bottom + 0.5 * depth
            else:
                if seg_l.size and seg_l.min() < min(llip.price, rlip.price):
                    continue
                bottom = float(seg_h.max())
                depth = bottom - max(llip.price, rlip.price)
                closes = self.c[llip.idx : rlip.idx + 1]
                near = (closes >= bottom - depth / 3).mean()
                handle_ok = handle.price <= bottom - 0.5 * depth
            if depth < 3 * self.atr[rlip.idx] or near < 0.25 or not handle_ok:
                return None
            if handle.confirm - rlip.idx > span / 2:
                return None
            trend = self.prior_trend(llip.idx, llip.price, span)
            if (normal and trend != 1) or (not normal and trend != -1):
                return None
            wait, hold = self.wait_hold(max(10, span // 3))
            cup_i = llip.idx + 1 + int(np.argmin(seg_l) if normal else np.argmax(seg_h))
            pts = [(llip.idx, llip.price), (cup_i, bottom), (rlip.idx, rlip.price), (handle.idx, handle.price)]
            if normal:
                return Setup("cup_handle", llip.idx, handle.idx, handle.confirm, pts, flat(rlip.price, rlip.idx, handle.idx), None,
                             {1: None}, {1: depth}, {1: handle.price}, wait, hold, [(llip.idx, llip.price, rlip.idx, rlip.price)])
            return Setup("cup_handle_inverted", llip.idx, handle.idx, handle.confirm, pts, None, flat(rlip.price, rlip.idx, handle.idx),
                         {-1: None}, {-1: depth}, {-1: handle.price}, wait, hold, [(llip.idx, llip.price, rlip.idx, rlip.price)])
        return None

    def _rounding(self, w: list[Pivot]) -> Setup | None:
        """Rim, bowl, rim: closes between the rims follow a parabola (R² >= 0.75) turning near the middle."""
        if len(w) < 3:
            return None
        a, m, b = w[-3:]
        bottom = m.kind == "L"
        span = b.idx - a.idx
        if span < 30:
            return None
        x = np.arange(a.idx, b.idx + 1, dtype=float)
        y = self.c[a.idx : b.idx + 1]
        xn = (x - x[0]) / span
        coef = np.polyfit(xn, y, 2)
        ss = float(((y - y.mean()) ** 2).sum())
        r2 = 1 - float(((y - np.polyval(coef, xn)) ** 2).sum()) / ss if ss > 0 else 0.0
        vertex = -coef[1] / (2 * coef[0]) if coef[0] != 0 else -1.0
        if r2 < 0.75 or not 0.3 <= vertex <= 0.7 or (coef[0] > 0) != bottom:
            return None
        depth = (min(a.price, b.price) - m.price) if bottom else (m.price - max(a.price, b.price))
        if depth < 3 * self.atr[b.idx]:
            return None
        trend = self.prior_trend(a.idx, a.price, span)
        if (bottom and trend == 1) or (not bottom and trend == -1):
            return None
        wait, hold = self.wait_hold(max(10, span // 3))
        knots = [int(v) for v in np.linspace(a.idx, b.idx, 7).round()]
        curve = [(k, float(np.polyval(coef, (k - x[0]) / span))) for k in knots]
        lines = [(curve[k][0], curve[k][1], curve[k + 1][0], curve[k + 1][1]) for k in range(len(curve) - 1)]
        pts = [(a.idx, a.price), (m.idx, m.price), (b.idx, b.price)]
        # Invalidated if price falls back into the lower half of the bowl (upper half of the dome).
        pull = m.price + depth / 2 if bottom else m.price - depth / 2
        if bottom:
            return Setup("rounding_bottom", a.idx, b.idx, b.confirm, pts, flat(b.price, b.idx, b.confirm), None,
                         {1: None}, {1: depth}, {1: pull}, wait, hold, lines)
        return Setup("rounding_top", a.idx, b.idx, b.confirm, pts, None, flat(b.price, b.idx, b.confirm),
                     {-1: None}, {-1: depth}, {-1: pull}, wait, hold, lines)

    def _channel(self, w: list[Pivot]) -> Setup | None:
        """Parallel sloping lines through 2+ highs and 2+ lows (a flat one is a rectangle)."""
        for k in (6, 5, 4):
            if len(w) < k:
                continue
            p = w[-k:]
            highs = [x for x in p if x.kind == "H"]
            lows = [x for x in p if x.kind == "L"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            span = p[-1].idx - p[0].idx
            if span < 20:
                continue
            up, dn = fit(highs), fit(lows)
            su, sd = slope(up), slope(dn)
            end = p[-1].idx
            a = self.atr[end]
            w0 = line_at(up, p[0].idx) - line_at(dn, p[0].idx)
            w1 = line_at(up, end) - line_at(dn, end)
            if min(w0, w1) < 2 * a or abs(w1 - w0) > 0.25 * max(w0, w1):
                continue
            rise = (su + sd) / 2 * span
            if abs(rise) < 1.5 * a or su * sd <= 0:
                continue
            t = self.tol(end)
            if any(abs(x.price - line_at(up, x.idx)) > t for x in highs) or any(abs(x.price - line_at(dn, x.idx)) > t for x in lows):
                continue
            xs = np.arange(p[0].idx, end + 1)
            upper = np.array([line_at(up, i) for i in xs])
            lower = np.array([line_at(dn, i) for i in xs])
            closes = self.c[p[0].idx : end + 1]
            if (closes > upper + t).any() or (closes < lower - t).any():
                continue
            key = "channel_up" if rise > 0 else "channel_down"
            wait, hold = self.wait_hold(span // 2)
            width = (w0 + w1) / 2
            ext_u = (p[0].idx, line_at(up, p[0].idx), end + wait, line_at(up, end + wait))
            ext_d = (p[0].idx, line_at(dn, p[0].idx), end + wait, line_at(dn, end + wait))
            return Setup(key, p[0].idx, end, p[-1].confirm, [(x.idx, x.price) for x in p], up, dn,
                         {1: None, -1: None}, {1: width, -1: width}, {1: lows[-1].price, -1: highs[-1].price},
                         wait, hold, [ext_u, ext_d])
        return None

    def _diamond(self, w: list[Pivot]) -> Setup | None:
        """Six swings: widening (higher high, lower low) then narrowing (lower high, higher low)."""
        if len(w) < 6:
            return None
        p = w[-6:]
        highs = [x for x in p if x.kind == "H"]
        lows = [x for x in p if x.kind == "L"]
        if len(highs) != 3 or len(lows) != 3:
            return None
        end = p[-1].idx
        t = self.tol(end)
        h0, h1, h2 = highs
        l0, l1, l2 = lows
        if not (h1.price > h0.price + 0.5 * t and h1.price > h2.price + 0.5 * t
                and l1.price < l0.price - 0.5 * t and l1.price < l2.price - 0.5 * t):
            return None
        height = h1.price - l1.price
        span = end - p[0].idx
        if height < 3 * self.atr[end] or span < 20:
            return None
        trend = self.prior_trend(p[0].idx, p[0].price, span)
        if trend == 0:
            return None
        up = (h1.idx, h1.price, h2.idx, h2.price)
        dn = (l1.idx, l1.price, l2.idx, l2.price)
        wait, hold = self.wait_hold(span // 2)
        wait = self._apex_wait(up, dn, end, wait)
        lines = [(h0.idx, h0.price, h1.idx, h1.price), (l0.idx, l0.price, l1.idx, l1.price),
                 (h1.idx, h1.price, end + wait, line_at(up, end + wait)), (l1.idx, l1.price, end + wait, line_at(dn, end + wait))]
        key = "diamond_top" if trend == 1 else "diamond_bottom"
        return Setup(key, p[0].idx, end, p[-1].confirm, [(x.idx, x.price) for x in p], up, dn,
                     {1: None, -1: None}, {1: height, -1: height}, {1: l2.price, -1: h2.price}, wait, hold, lines)

    def _apex_wait(self, up: tuple, dn: tuple, end: int, default: int) -> int:
        su, sd = slope(up), slope(dn)
        if su - sd >= 0:  # not converging
            return default
        gap = line_at(up, end) - line_at(dn, end)
        bars = gap / (sd - su)
        return int(np.clip(bars, 3, default))

    def _triangle(self, w: list[Pivot]) -> Setup | None:
        for k in (5, 4):
            if len(w) < k:
                continue
            p = w[-k:]
            highs = [x for x in p if x.kind == "H"]
            lows = [x for x in p if x.kind == "L"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            t = self.tol(p[-1].idx)
            hp, lp = [x.price for x in highs], [x.price for x in lows]
            flat_hi = max(hp) - min(hp) <= t
            flat_lo = max(lp) - min(lp) <= t
            rising_lo = all(b > a + 0.5 * t for a, b in zip(lp, lp[1:]))
            falling_hi = all(b < a - 0.5 * t for a, b in zip(hp, hp[1:]))
            span = p[-1].idx - p[0].idx
            if span < 8:
                continue
            height = max(hp) - min(lp)
            if height < 2 * self.atr[p[-1].idx]:
                continue
            wait, hold = self.wait_hold(span)
            pts = [(x.idx, x.price) for x in p]
            trend = self.prior_trend(p[0].idx, p[0].price, span)
            if flat_hi and rising_lo and trend != -1:
                lvl = max(hp)
                up, dn = flat(lvl, highs[0].idx, p[-1].idx), fit(lows)
                wait = self._apex_wait(up, dn, p[-1].idx, wait)
                return Setup("triangle_ascending", p[0].idx, p[-1].idx, p[-1].confirm, pts, up, None,
                             {1: None}, {1: height}, {1: lp[-1]}, wait, hold, [up, dn])
            if flat_lo and falling_hi and trend != 1:
                lvl = min(lp)
                up, dn = fit(highs), flat(lvl, lows[0].idx, p[-1].idx)
                wait = self._apex_wait(up, dn, p[-1].idx, wait)
                return Setup("triangle_descending", p[0].idx, p[-1].idx, p[-1].confirm, pts, None, dn,
                             {-1: None}, {-1: height}, {-1: hp[-1]}, wait, hold, [up, dn])
            if falling_hi and rising_lo:
                up, dn = fit(highs), fit(lows)
                wait = self._apex_wait(up, dn, p[-1].idx, wait)
                h0 = hp[0] - lp[0]
                return Setup("triangle_symmetrical", p[0].idx, p[-1].idx, p[-1].confirm, pts, up, dn,
                             {1: None, -1: None}, {1: h0, -1: h0}, {1: lp[-1], -1: hp[-1]}, wait, hold, [up, dn])
        return None

    def _wedge(self, w: list[Pivot]) -> Setup | None:
        if len(w) < 5:
            return None
        p = w[-5:]
        highs = [x for x in p if x.kind == "H"]
        lows = [x for x in p if x.kind == "L"]
        hp, lp = [x.price for x in highs], [x.price for x in lows]
        up, dn = fit(highs), fit(lows)
        su, sd = slope(up), slope(dn)
        span = p[-1].idx - p[0].idx
        if span < 10:
            return None
        wait, hold = self.wait_hold(span)
        pts = [(x.idx, x.price) for x in p]
        rising = all(b > a for a, b in zip(hp, hp[1:])) and all(b > a for a, b in zip(lp, lp[1:]))
        falling = all(b < a for a, b in zip(hp, hp[1:])) and all(b < a for a, b in zip(lp, lp[1:]))
        if rising and sd > su > 0:
            wait = self._apex_wait(up, dn, p[-1].idx, wait)
            return Setup("wedge_rising", p[0].idx, p[-1].idx, p[-1].confirm, pts, None, dn,
                         {-1: min(lp)}, {-1: max(hp) - min(lp)}, {-1: max(hp)}, wait, hold, [up, dn])
        if falling and su < sd < 0:
            wait = self._apex_wait(up, dn, p[-1].idx, wait)
            return Setup("wedge_falling", p[0].idx, p[-1].idx, p[-1].confirm, pts, up, None,
                         {1: max(hp)}, {1: max(hp) - min(lp)}, {1: min(lp)}, wait, hold, [up, dn])
        return None

    def _rectangle(self, w: list[Pivot]) -> Setup | None:
        for k in (5, 4):
            if len(w) < k:
                continue
            p = w[-k:]
            highs = [x.price for x in p if x.kind == "H"]
            lows = [x.price for x in p if x.kind == "L"]
            t = self.tol(p[-1].idx)
            if max(highs) - min(highs) > t or max(lows) - min(lows) > t:
                continue
            top, bot = max(highs), min(lows)
            height = top - bot
            span = p[-1].idx - p[0].idx
            if height < 2 * self.atr[p[-1].idx] or span < 10:
                continue
            trend = self.prior_trend(p[0].idx, p[0].price, span)
            if trend == 0:
                continue
            wait, hold = self.wait_hold(span)
            key = "rectangle_top" if trend == 1 else "rectangle_bottom"
            up, dn = flat(top, p[0].idx, p[-1].idx), flat(bot, p[0].idx, p[-1].idx)
            return Setup(key, p[0].idx, p[-1].idx, p[-1].confirm, [(x.idx, x.price) for x in p], up, dn,
                         {1: None, -1: None}, {1: height, -1: height}, {1: bot, -1: top}, wait, hold, [up, dn])
        return None

    def _flag(self, w: list[Pivot]) -> Setup | None:
        for k in (5, 4):
            if len(w) < k:
                continue
            p = w[-k:]
            base, peak = p[0], p[1]
            bull = peak.kind == "H"
            rest = p[2:]
            pole = abs(peak.price - base.price)
            pole_bars = peak.idx - base.idx
            a = self.atr[peak.idx]
            if pole < 4 * a or pole_bars < 2 or pole_bars > 20 or pole / pole_bars < 0.35 * a:
                continue
            body_end = p[-1].idx
            dur = body_end - peak.idx
            if dur > max(20, 3 * pole_bars) or dur < 3:
                continue
            t = self.tol(peak.idx)
            highs = [peak] + [x for x in rest if x.kind == peak.kind]
            lows = [x for x in rest if x.kind != peak.kind]
            if not lows or len(highs) < 2:
                continue
            if bull:
                if any(x.price > peak.price + 0.25 * t for x in highs[1:]):
                    continue
                retrace = (peak.price - min(x.price for x in lows)) / pole
            else:
                if any(x.price < peak.price - 0.25 * t for x in highs[1:]):
                    continue
                retrace = (max(x.price for x in lows) - peak.price) / pole
            if retrace > 0.5:
                continue
            edge = fit(highs)
            other = fit(lows) if len(lows) >= 2 else None
            converging = other is not None and (
                (slope(edge) < 0 < slope(other)) if bull else (slope(edge) > 0 > slope(other))
            )
            pts = [(x.idx, x.price) for x in p]
            wait, _ = self.wait_hold(max(10, dur))
            _, hold = self.wait_hold(pole_bars + dur)
            lo_ext = min(x.price for x in lows) if bull else max(x.price for x in lows)
            lines = [(base.idx, base.price, peak.idx, peak.price), edge] + ([other] if other else [])
            if bull:
                # High & tight: price nearly doubled within ~40 bars, shallow pause.
                lb = max(0, peak.idx - 40)
                low40 = float(self.l[lb : peak.idx + 1].min())
                if low40 > 0 and peak.price / low40 >= 1.9 and retrace <= 0.2:
                    return Setup("flag_high_tight", base.idx, body_end, p[-1].confirm, pts, edge, None,
                                 {1: None}, {1: (peak.price - low40) / 2}, {1: lo_ext}, wait, hold, lines)
                key = "pennant_bull" if converging else "flag_bull"
                return Setup(key, base.idx, body_end, p[-1].confirm, pts, edge, None,
                             {1: None}, {1: pole}, {1: lo_ext}, wait, hold, lines)
            key = "pennant_bear" if converging else "flag_bear"
            return Setup(key, base.idx, body_end, p[-1].confirm, pts, None, edge,
                         {-1: None}, {-1: pole}, {-1: lo_ext}, wait, hold, lines)
        return None

    # ---- gaps -------------------------------------------------------------
    def gaps(self) -> list[Setup]:
        h, l, c, a, rv = self.h, self.l, self.c, self.atr, self.rv
        out: list[Setup] = []
        for t in range(21, self.n):
            up = l[t] > h[t - 1] + 0.25 * a[t - 1]
            dn = h[t] < l[t - 1] - 0.25 * a[t - 1]
            if not (up or dn):
                continue
            d = 1 if up else -1
            box_hi, box_lo = h[t - 20 : t].max(), l[t - 20 : t].min()
            consolidating = box_hi - box_lo <= 4 * a[t - 1]
            move = c[t - 1] - c[t - 21]
            gap_edge = h[t - 1] if up else l[t - 1]  # pre-gap extreme = fill level
            hold_next = t + 1 < self.n and ((l[t + 1] > gap_edge) if up else (h[t + 1] < gap_edge))
            vol_hi = np.isfinite(rv[t]) and rv[t] >= 1.5
            wait, hold = 5, 40
            pts = [(t - 1, float(gap_edge)), (t, float(l[t] if up else h[t]))]

            # Island reversal: gap, an island of bars, then a gap back.
            for u in range(t + 1, min(self.n, t + 31)):
                isl_hi, isl_lo = h[t:u].max(), l[t:u].min()
                if up and h[u] < isl_lo:
                    rng = isl_hi - isl_lo
                    out.append(Setup("island_reversal", t, u, u, [(t, float(l[t])), (u, float(h[u]))], None, None,
                                     {-1: isl_lo - rng}, {-1: rng}, {-1: float(isl_hi)}, 1, hold,
                                     [(t, isl_hi, u - 1, isl_hi), (t, isl_lo, u - 1, isl_lo)], "bearish island",
                                     entry_at=u, entry_dir=-1))
                    break
                if dn and l[u] > isl_hi:
                    rng = isl_hi - isl_lo
                    out.append(Setup("island_reversal", t, u, u, [(t, float(h[t])), (u, float(l[u]))], None, None,
                                     {1: isl_hi + rng}, {1: rng}, {1: float(isl_lo)}, 1, hold,
                                     [(t, isl_hi, u - 1, isl_hi), (t, isl_lo, u - 1, isl_lo)], "bullish island",
                                     entry_at=u, entry_dir=1))
                    break
                if (up and l[u] <= h[t - 1]) or (dn and h[u] >= l[t - 1]):
                    break  # gap filled without a second gap: no island

            breaks_box = (c[t] > box_hi) if up else (c[t] < box_lo)
            if consolidating and breaks_box and vol_hi and hold_next:
                ext = h[t] if up else l[t]
                base = box_lo if up else box_hi
                out.append(Setup("gap_breakaway", t - 20, t, t + 1, pts, None, None, {d: float(ext + (ext - base))},
                                 {d: float(abs(ext - base))}, {d: float(gap_edge)}, 1, hold, [], "up" if up else "down",
                                 entry_at=t + 1, entry_dir=d))
            elif not consolidating and d * move >= 4 * a[t - 1]:
                if hold_next:
                    mid = (gap_edge + (l[t] if up else h[t])) / 2
                    start_px = l[t - 20 : t].min() if up else h[t - 20 : t].max()
                    out.append(Setup("gap_continuation", t - 20, t, t + 1, pts, None, None, {d: float(mid + (mid - start_px))},
                                     {d: float(abs(mid - start_px))}, {d: float(gap_edge)}, 1, hold, [], "up" if up else "down",
                                     entry_at=t + 1, entry_dir=d))
                # Exhaustion: big gap late in the trend, then a close back through the gap day.
                if vol_hi and abs(l[t] - h[t - 1] if up else l[t - 1] - h[t]) >= 0.75 * a[t - 1]:
                    for u in range(t + 1, min(self.n, t + 1 + wait)):
                        if (c[u] < l[t]) if up else (c[u] > h[t]):
                            ext = h[t : u + 1].max() if up else l[t : u + 1].min()
                            out.append(Setup("gap_exhaustion", t - 20, t, u, pts, None, None, {-d: float(gap_edge)},
                                             {-d: float(abs(c[u] - gap_edge))}, {-d: float(ext)}, 1, hold, [],
                                             "up gap" if up else "down gap", entry_at=u, entry_dir=-d))
                            break
            elif consolidating and not breaks_box:
                for u in range(t + 1, min(self.n, t + 1 + wait)):
                    if (c[u] < l[t]) if up else (c[u] > h[t]):
                        ext = h[t : u + 1].max() if up else l[t : u + 1].min()
                        out.append(Setup("gap_area", t, t, u, pts, None, None, {-d: float(gap_edge)},
                                         {-d: float(abs(c[u] - gap_edge))}, {-d: float(ext)}, 1, hold, [],
                                         "up gap" if up else "down gap", entry_at=u, entry_dir=-d))
                        break
        return out

    # ---- breakout + outcome ---------------------------------------------
    def resolve(self, s: Setup) -> dict:
        n, c, h, l = self.n, self.c, self.h, self.l
        res: dict = {"status": "forming", "direction": None, "entry": None, "level": None}
        last_wait = s.known + s.max_wait
        entry = d = None
        level = None
        if s.entry_at is not None:
            if s.entry_at >= n:
                res["status"] = "forming"
                return res
            entry, d, level = s.entry_at, s.entry_dir, float(c[s.entry_at])
        else:
            for t in range(s.end + 1, min(n, last_wait + 1)):
                hit = None
                if s.up is not None and c[t] > line_at(s.up, t):
                    hit = 1
                elif s.down is not None and c[t] < line_at(s.down, t):
                    hit = -1
                if hit is None:
                    # Invalidation of one-sided patterns before any breakout.
                    if s.up is not None and s.down is None and c[t] < s.stop[1]:
                        res["status"] = "failed"
                        res["failed_at"] = t
                        return res
                    if s.down is not None and s.up is None and c[t] > s.stop[-1]:
                        res["status"] = "failed"
                        res["failed_at"] = t
                        return res
                    continue
                e = max(t, s.known)  # can't trade before the shape is known
                lvl_line = s.up if hit == 1 else s.down
                if e > t and not ((c[e] > line_at(lvl_line, e)) if hit == 1 else (c[e] < line_at(lvl_line, e))):
                    res["status"] = "failed"
                    res["failed_at"] = e
                    return res
                entry, d, level = e, hit, float(line_at(lvl_line, t))
                break
            if entry is None:
                if n - 1 >= last_wait:
                    res["status"] = "expired_unbroken"
                return res

        tgt = s.target.get(d)
        target = float(tgt) if tgt is not None else level + d * s.height[d]
        stop = float(s.stop[d])
        px = float(c[entry])
        a = self.atr[entry]
        if d * (stop - px) >= 0:  # stop on the wrong side: fall back to 1.5 ATR
            stop = px - d * 1.5 * a
        res.update(direction=d, entry=entry, level=level, entry_price=px, target=target, stop=stop,
                   volume_confirmed=bool(self.rv[entry] >= VOLUME_CONFIRM) if np.isfinite(self.rv[entry]) else None)
        if d * (target - px) <= 0:
            res["status"] = "target_at_breakout"
            return res
        res["status"] = "breakout"
        for k in range(entry + 1, min(n, entry + 1 + s.max_hold)):
            if (l[k] <= stop) if d > 0 else (h[k] >= stop):
                fill = min(stop, self.o[k]) if d > 0 else max(stop, self.o[k])  # gap through the stop fills at the open
                res.update(status="stopped", exit=k, exit_price=float(fill))
                break
            if (h[k] >= target) if d > 0 else (l[k] <= target):
                fill = max(target, self.o[k]) if d > 0 else min(target, self.o[k])
                res.update(status="target_hit", exit=k, exit_price=float(fill))
                break
        else:
            if entry + s.max_hold < n:
                k = entry + s.max_hold
                res.update(status="expired", exit=k, exit_price=float(c[k]))
        if "exit_price" in res:
            res["pnl_pct"] = round(d * (res["exit_price"] / px - 1) * 100, 2)
        else:
            res["pnl_pct"] = round(d * (c[-1] / px - 1) * 100, 2)
        return res


def _dedupe(resolved: list[tuple[Setup, dict]]) -> list[tuple[Setup, dict]]:
    """A pattern that keeps growing is re-detected at every new pivot. A newer shape replaces
    an older overlapping one of the same type only while the older one was still waiting for
    its breakout when the newer one became known; otherwise the older one stands (no lookahead)."""
    resolved = sorted(resolved, key=lambda sr: (sr[0].known, sr[0].end))
    kept: list[tuple[Setup, dict]] = []
    for s, r in resolved:
        if s.entry_at is None:
            clash = next(((k, kr) for k, kr in reversed(kept) if k.entry_at is None and k.key == s.key
                          and s.start <= k.end and k.start <= s.end), None)
            if clash is not None:
                k, kr = clash
                acted = kr.get("entry") if kr.get("entry") is not None else kr.get("failed_at")
                if acted is not None and acted <= s.known:
                    continue  # the older pattern already broke out or failed; this is a re-detection
                kept.remove(clash)
        kept.append((s, r))
    return kept


def detect(df: pd.DataFrame, ind: pd.DataFrame) -> list[dict]:
    """All pattern instances in the history, oldest first, each with its outcome."""
    if len(df) < 40:
        return []
    det = Detector(df, ind["atr14"], ind["rel_volume"])
    coarse = zigzag(det.h, det.l, det.atr, COARSE_MULT)
    fine = zigzag(det.h, det.l, det.atr, FINE_MULT)
    resolved = _dedupe([(s, det.resolve(s)) for s in det.coarse(coarse) + det.fine(fine)])
    resolved += [(s, det.resolve(s)) for s in det.gaps()]
    idx = df.index
    iso = lambda i: idx[min(max(i, 0), len(idx) - 1)].isoformat()  # noqa: E731
    out = []
    for s, r in resolved:
        name, bias, kind, stars = CATALOG[s.key]
        d = r.get("direction")
        last = len(df) - 1
        item = {
            "key": s.key,
            "name": name,
            "variant": s.variant,
            "bias": bias,
            "kind": kind,
            "stars": stars,
            "rule": RULES[s.key],
            "status": r["status"],
            "direction": "up" if d == 1 else "down" if d == -1 else None,
            "start_time": iso(s.start),
            "end_time": iso(s.end),
            "detected_time": iso(s.known),
            "points": [{"time": iso(i), "price": round(p, 2)} for i, p in s.points],
            "lines": [[{"time": iso(i1), "price": round(p1, 2)}, {"time": iso(min(i2, last)), "price": round(line_at((i1, p1, i2, p2), min(i2, last)), 2)}]
                      for i1, p1, i2, p2 in s.lines if min(i2, last) > i1],
        }
        if r["status"] == "forming":
            t = last
            item["trigger_up"] = round(line_at(s.up, t), 2) if s.up is not None else None
            item["trigger_down"] = round(line_at(s.down, t), 2) if s.down is not None else None
            proj = {}
            for dd, lvl in ((1, item["trigger_up"]), (-1, item["trigger_down"])):
                if lvl is None or dd not in s.height:
                    continue
                tg = s.target.get(dd)
                proj["up" if dd == 1 else "down"] = {
                    "target": round(float(tg) if tg is not None else lvl + dd * s.height[dd], 2),
                    "stop": round(float(s.stop[dd]), 2),
                }
            item["projection"] = proj
            item["expires_time"] = None  # computed by the caller from bar spacing if needed
            item["bars_left"] = max(0, s.known + s.max_wait - last)
        if r.get("entry") is not None:
            item.update(
                breakout_time=iso(r["entry"]),
                breakout_level=round(r["level"], 2),
                entry_price=round(r["entry_price"], 2),
                target=round(r["target"], 2),
                stop=round(r["stop"], 2),
                volume_confirmed=r.get("volume_confirmed"),
                pnl_pct=r.get("pnl_pct"),
                exit_time=iso(r["exit"]) if "exit" in r else None,
                exit_price=round(r["exit_price"], 2) if "exit_price" in r else None,
                bars_since_breakout=last - r["entry"],
            )
        if r.get("failed_at") is not None:
            item["failed_time"] = iso(r["failed_at"])
        out.append(item)
    out.sort(key=lambda x: x["detected_time"])
    return out


RESOLVED = ("target_hit", "stopped", "expired")


def finalize(s: dict) -> dict:
    n = s["wins"] + s["losses"] + s["expired"]
    return {
        "detected": s["detected"],
        "resolved": n,
        "failed_to_break": s["failed"],
        "wins": s["wins"],
        "losses": s["losses"],
        "expired": s["expired"],
        "success_rate": round(100 * s["wins"] / n, 1) if n else None,
        "profitable_rate": round(100 * sum(p > 0 for p in s["pnl"]) / len(s["pnl"]), 1) if s["pnl"] else None,
        "avg_pnl_pct": round(sum(s["pnl"]) / len(s["pnl"]), 2) if s["pnl"] else None,
        "volume_confirmed_n": s["vol_n"],
        "volume_confirmed_success_rate": round(100 * s["vol_wins"] / s["vol_n"], 1) if s["vol_n"] else None,
    }


def merge_stats(raw: list[dict]) -> dict:
    """Combine raw (pre-finalize) accumulators across instruments."""
    acc: dict[str, dict] = {}
    for one in raw:
        for k, v in one.items():
            a = acc.setdefault(k, {"detected": 0, "failed": 0, "wins": 0, "losses": 0, "expired": 0, "pnl": [], "vol_wins": 0, "vol_n": 0})
            for f in ("detected", "failed", "wins", "losses", "expired", "vol_wins", "vol_n"):
                a[f] += v[f]
            a["pnl"] += v["pnl"]
    return {k: finalize(v) for k, v in acc.items()}


def raw_stats(items: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for it in items:
        s = stats.setdefault(it["key"], {"detected": 0, "failed": 0, "wins": 0, "losses": 0, "expired": 0, "pnl": [], "vol_wins": 0, "vol_n": 0})
        s["detected"] += 1
        st = it["status"]
        if st in ("failed", "expired_unbroken"):
            s["failed"] += 1
        if st in RESOLVED:
            s["wins"] += st == "target_hit"
            s["losses"] += st == "stopped"
            s["expired"] += st == "expired"
            s["pnl"].append(it["pnl_pct"])
            if it.get("volume_confirmed"):
                s["vol_n"] += 1
                s["vol_wins"] += st == "target_hit"
    return stats


def current(items: list[dict], max_age_bars: int = 10) -> list[dict]:
    """Patterns that matter now: still forming, or broke out recently and still running."""
    out = []
    for it in items:
        if it["status"] == "forming":
            out.append(it)
        elif it["status"] in ("breakout", "target_at_breakout") and it.get("bars_since_breakout", 999) <= max_age_bars * 3:
            out.append(it)
        elif it["status"] in RESOLVED and it.get("bars_since_breakout", 999) <= 2:
            out.append(it)
    return out
