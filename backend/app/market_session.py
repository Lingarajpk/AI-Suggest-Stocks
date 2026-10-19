"""Trading-session helpers (IST) for NSE/BSE equities and NSE currency derivatives.

Exchange holidays are not modelled yet; on a holiday the session check will say
"open" during normal hours, but quotes will be flagged stale because no trades occur.
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

    @property
    def open_minutes(self) -> int:
        return self.open.hour * 60 + self.open.minute


EQUITY = MarketHours(SESSION_OPEN, SESSION_CLOSE)
# NSE currency futures and options (USDINR, EURINR, GBPINR, JPYINR): 09:00-17:00.
CURRENCY = MarketHours(time(9, 0), time(17, 0))


def hours_for(instrument) -> MarketHours:
    """Session hours of an instrument (anything with a `kind`): currency pairs trade longer than equities."""
    return CURRENCY if getattr(instrument, "kind", None) == "currency" else EQUITY


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_session_open(at: datetime | None = None, hours: MarketHours = EQUITY) -> bool:
    at = (at or now_ist()).astimezone(IST)
    return is_trading_weekday(at.date()) and hours.open <= at.time() < hours.close


def session_state(at: datetime | None = None, hours: MarketHours = EQUITY) -> str:
    at = (at or now_ist()).astimezone(IST)
    if not is_trading_weekday(at.date()):
        return "closed"
    if at.time() < hours.open:
        return "pre_open"
    if at.time() < hours.close:
        return "open"
    return "closed"


def next_upstox_token_expiry(issued_at: datetime) -> datetime:
    """Upstox access tokens expire at 03:30 IST on the following day (per Upstox docs)."""
    issued = issued_at.astimezone(IST)
    expiry = datetime.combine(issued.date(), time(3, 30), tzinfo=IST)
    if issued >= expiry:
        expiry += timedelta(days=1)
    return expiry
