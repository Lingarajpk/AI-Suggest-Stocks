"""NSE/BSE regular-session helpers (IST).

Exchange holidays are not modelled yet; on a holiday the session check will say
"open" during normal hours, but quotes will be flagged stale because no trades occur.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_session_open(at: datetime | None = None) -> bool:
    at = (at or now_ist()).astimezone(IST)
    return is_trading_weekday(at.date()) and SESSION_OPEN <= at.time() < SESSION_CLOSE


def session_state(at: datetime | None = None) -> str:
    at = (at or now_ist()).astimezone(IST)
    if not is_trading_weekday(at.date()):
        return "closed"
    if at.time() < SESSION_OPEN:
        return "pre_open"
    if at.time() < SESSION_CLOSE:
        return "open"
    return "closed"


def next_upstox_token_expiry(issued_at: datetime) -> datetime:
    """Upstox access tokens expire at 03:30 IST on the following day (per Upstox docs)."""
    issued = issued_at.astimezone(IST)
    expiry = datetime.combine(issued.date(), time(3, 30), tzinfo=IST)
    if issued >= expiry:
        expiry += timedelta(days=1)
    return expiry
