"""Trading-session helpers (IST) for NSE/BSE equities and NSE currency derivatives.

Exchange holidays come from Upstox's market-holiday calendar (set_calendar), loaded at start-up and
daily. Until it is loaded (or in demo mode) only weekends are treated as closed.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


@dataclass(frozen=True)
class MarketHours:
    open: time
    close: time
    exchange: str = "NSE"  # whose holiday calendar applies (Upstox names: NSE, CDS for currency)

    @property
    def open_minutes(self) -> int:
        return self.open.hour * 60 + self.open.minute


EQUITY = MarketHours(SESSION_OPEN, SESSION_CLOSE, "NSE")
# NSE currency futures and options (USDINR, EURINR, GBPINR, JPYINR): 09:00-17:00.
CURRENCY = MarketHours(time(9, 0), time(17, 0), "CDS")

# exchange -> {date: holiday name} for full-day closures, and {date: (open, close, name)} for special sessions
# (a Saturday trading session, Muhurat trading) that replace the normal hours on that date.
_CLOSED: dict[str, dict[date, str]] = {}
_SPECIAL: dict[str, dict[date, tuple[time, time, str]]] = {}


def set_calendar(exchange: str, closed: dict[date, str], special: dict[date, tuple[time, time, str]]) -> None:
    _CLOSED[exchange] = dict(closed)
    _SPECIAL[exchange] = dict(special)


def calendar_loaded(exchange: str = "NSE") -> bool:
    return exchange in _CLOSED


def holiday_name(d: date, hours: "MarketHours | None" = None) -> str | None:
    """The exchange holiday on `d`, if the calendar says the market is closed all day."""
    ex = (hours or EQUITY).exchange
    return None if d in _SPECIAL.get(ex, {}) else _CLOSED.get(ex, {}).get(d)


def special_session(d: date, hours: "MarketHours | None" = None) -> tuple[time, time, str] | None:
    return _SPECIAL.get((hours or EQUITY).exchange, {}).get(d)


def is_trading_day(d: date, hours: "MarketHours | None" = None) -> bool:
    if special_session(d, hours):
        return True
    return is_trading_weekday(d) and holiday_name(d, hours) is None


def _window(d: date, hours: "MarketHours") -> tuple[time, time] | None:
    """Open and close on date `d`: the special session's hours, the normal hours, or None (closed)."""
    sp = special_session(d, hours)
    if sp:
        return sp[0], sp[1]
    return (hours.open, hours.close) if is_trading_day(d, hours) else None


def hours_for(instrument) -> MarketHours:
    """Session hours of an instrument (anything with a `kind`): currency pairs trade longer than equities."""
    return CURRENCY if getattr(instrument, "kind", None) == "currency" else EQUITY


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_session_open(at: datetime | None = None, hours: MarketHours = EQUITY) -> bool:
    at = (at or now_ist()).astimezone(IST)
    w = _window(at.date(), hours)
    return w is not None and w[0] <= at.time() < w[1]


def session_state(at: datetime | None = None, hours: MarketHours = EQUITY) -> str:
    at = (at or now_ist()).astimezone(IST)
    w = _window(at.date(), hours)
    if w is None:
        return "closed"
    if at.time() < w[0]:
        return "pre_open"
    if at.time() < w[1]:
        return "open"
    return "closed"


def next_upstox_token_expiry(issued_at: datetime) -> datetime:
    """Upstox access tokens expire at 03:30 IST on the following day (per Upstox docs)."""
    issued = issued_at.astimezone(IST)
    expiry = datetime.combine(issued.date(), time(3, 30), tzinfo=IST)
    if issued >= expiry:
        expiry += timedelta(days=1)
    return expiry
