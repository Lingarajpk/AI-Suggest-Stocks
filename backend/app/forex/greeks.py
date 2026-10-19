"""Option greeks for currency options (Black-76 on the futures price, zero rates).

Upstox's quote API gives currency option prices but no greeks, so implied volatility is solved from the
premium and the greeks follow from it. Close enough to pick a strike; not for pricing.
"""

import math
from datetime import date, datetime, time

from app.market_session import IST

# NSE currency options expire at 12:30 IST on the expiry day.
EXPIRY_TIME = time(12, 30)
MIN_YEARS = 600 / (365 * 86400)  # never less than ten minutes, so greeks stay finite at expiry


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black(f: float, k: float, t: float, iv: float, sign: int) -> tuple[float, float, float, float, float]:
    """Price, delta, gamma, theta per day and vega per vol point; sign +1 = call, -1 = put."""
    sq = iv * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * iv * iv * t) / sq
    d2 = d1 - sq
    pdf = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    price = sign * (f * _cdf(sign * d1) - k * _cdf(sign * d2))
    delta = _cdf(d1) if sign > 0 else _cdf(d1) - 1
    gamma = pdf / (f * sq)
    theta = -f * pdf * iv / (2 * math.sqrt(t)) / 365
    vega = f * pdf * math.sqrt(t) / 100
    return price, delta, gamma, theta, vega


def implied_vol(price: float, f: float, k: float, t: float, sign: int) -> float | None:
    """Volatility that reproduces `price`, or None if the price is at or below intrinsic value."""
    intrinsic = max(0.0, sign * (f - k))
    if price is None or price <= intrinsic + 1e-6:
        return None
    lo, hi = 0.001, 2.0
    if black(f, k, t, hi, sign)[0] < price:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if black(f, k, t, mid, sign)[0] < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def years_to(expiry: str, now: datetime) -> float:
    end = datetime.combine(date.fromisoformat(expiry), EXPIRY_TIME, tzinfo=IST)
    return max((end - now).total_seconds() / (365 * 86400), MIN_YEARS)


def add_greeks(rows: list[dict], f: float, now: datetime) -> list[dict]:
    """Fill option_greeks (iv in %, delta, gamma, theta, vega) on raw chain rows, in place."""
    for r in rows:
        k = float(r["strike_price"])
        t = years_to(r["expiry"], now)
        for side, sign in (("call_options", 1), ("put_options", -1)):
            leg = r.get(side)
            if not leg or k <= 0 or f <= 0:
                continue
            md = leg.get("market_data") or {}
            bid, ask, ltp = md.get("bid_price"), md.get("ask_price"), md.get("ltp")
            price = (bid + ask) / 2 if bid and ask and ask >= bid else ltp
            iv = implied_vol(price, f, k, t, sign) if price else None
            if iv is None:
                continue
            _, delta, gamma, theta, vega = black(f, k, t, iv, sign)
            leg["option_greeks"] = {"iv": round(iv * 100, 2), "delta": round(delta, 4), "gamma": round(gamma, 4),
                                    "theta": round(theta, 5), "vega": round(vega, 5), "pop": None}
    return rows
