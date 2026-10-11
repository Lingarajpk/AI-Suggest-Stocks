"""Static sector labels for the default test universe. Extend when the universe grows."""

SECTORS: dict[str, str] = {
    "RELIANCE": "Energy",
    "TCS": "IT",
    "INFY": "IT",
    "HDFCBANK": "Banking",
    "ICICIBANK": "Banking",
    "SBIN": "Banking",
    "KOTAKBANK": "Banking",
    "AXISBANK": "Banking",
    "BAJFINANCE": "Financial Services",
    "BHARTIARTL": "Telecom",
    "ITC": "FMCG",
    "HINDUNILVR": "FMCG",
    "LT": "Capital Goods",
    "MARUTI": "Automobile",
    "SUNPHARMA": "Pharma",
    "TATASTEEL": "Metals",
}


# Search names for live news. Symbols missing here fall back to the instrument's own name.
NEWS_NAMES: dict[str, str] = {
    "RELIANCE": "Reliance Industries",
    "TCS": "Tata Consultancy Services",
    "INFY": "Infosys",
    "HDFCBANK": "HDFC Bank",
    "ICICIBANK": "ICICI Bank",
    "SBIN": "State Bank of India",
    "KOTAKBANK": "Kotak Mahindra Bank",
    "AXISBANK": "Axis Bank",
    "BAJFINANCE": "Bajaj Finance",
    "BHARTIARTL": "Bharti Airtel",
    "ITC": "ITC Ltd",
    "HINDUNILVR": "Hindustan Unilever",
    "LT": "Larsen & Toubro",
    "MARUTI": "Maruti Suzuki",
    "SUNPHARMA": "Sun Pharma",
    "TATASTEEL": "Tata Steel",
    "NIFTY 50": "Nifty",
    "BANK NIFTY": "Bank Nifty",
    "SENSEX": "Sensex",
}
