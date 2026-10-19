"""Forex: NSE currency futures and options (USDINR, EURINR, GBPINR, JPYINR), kept separate from stocks.

Only rupee pairs are covered: they are what Indian residents can trade on a recognised exchange
(NSE currency derivatives through Upstox). Pairs such as EURUSD or gold through overseas forex apps
are not allowed for Indian residents, so they are deliberately left out.
"""

PAIRS = ("USDINR", "EURINR", "GBPINR", "JPYINR")

# Currency names, and what one unit of the quoted price is (JPYINR is quoted per 100 yen).
PAIR_INFO = {
    "USDINR": {"base": "USD", "currency": "US Dollar", "per": "1 US dollar"},
    "EURINR": {"base": "EUR", "currency": "Euro", "per": "1 euro"},
    "GBPINR": {"base": "GBP", "currency": "British Pound", "per": "1 British pound"},
    "JPYINR": {"base": "JPY", "currency": "Japanese Yen", "per": "100 Japanese yen"},
}
