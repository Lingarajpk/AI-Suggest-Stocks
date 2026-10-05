# AI Stock: Market Intelligence Platform

An API-first stock analysis platform for Indian equities (NSE/BSE). It has a FastAPI backend that gets market data from the **Upstox Developer API**, computes indicators and data-quality-aware technical signals, and streams quotes over WebSocket to a **Next.js** dashboard.

> Not investment advice. Signals are rule-based technical scores, not validated predictions. The app never places trades.

## Status (milestone 1: data foundation)

| Area | State |
|---|---|
| Upstox OAuth login, token stored server-side, 03:30 IST expiry, 401 → re-login | ✅ |
| Instrument keys resolved from Upstox's instrument master (no hard-coded keys) | ✅ verified for all 16 default symbols + NIFTY 50 / Nifty Bank / SENSEX |
| Historical candles (v3), chunked to Upstox range limits; forming candle excluded | ✅ verified against live API (15m / 1h / daily) |
| Full market quotes (v2), batched ≤500 keys, freshness labels | ✅ code + parser tests; needs your token to verify live |
| Backend → browser WebSocket (single shared upstream poller) | ✅ |
| Indicators: EMA 9/20/50/200, RSI, MACD, ATR, Bollinger, ADX/DI, session VWAP, rel. volume, ROC, swing S/R | ✅ |
| Signals: Strong Bullish → Strong Bearish, NO TRADE on weak/stale/insufficient data or R:R < 1 | ✅ |
| Dashboard, scanner + filters, signal cards, sector strength, watchlist (browser-local), stock detail charts | ✅ |
| Redis cache (optional; in-memory fallback) | ✅ |
| Upstream Upstox WebSocket feed (protobuf) | ⏳ quotes are REST-polled for now and labelled "Polled" |
| ML prediction models + backtesting, NVIDIA NIM explanations, news, PostgreSQL history | ⏳ next phases |

## Run it

**Backend** (Python 3.12+)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements-dev.txt
copy .env.example .env          # cp on macOS/Linux, then edit
uvicorn app.main:app --reload --port 8000
pytest                          # 14 tests
```

**Frontend** (Node 20+)

```bash
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

`DATA_MODE=demo` (default) serves **synthetic** data with a permanent "Demo data" banner. It's for UI work only.

## Connecting Upstox

1. In your Upstox app **"AI Stock"** (https://account.upstox.com/developer/apps), set the redirect URL to exactly
   `http://localhost:8000/api/auth/upstox/callback`.
2. In `backend/.env` set `DATA_MODE=upstox`, `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`. Restart the backend.
3. Open the dashboard → **Log in with Upstox**. The token is stored in `backend/data/.upstox_token.json` (gitignored) and expires daily at 03:30 IST, so log in again each trading day.

Credentials never reach the browser: the frontend only calls `/api/*`, which Next proxies to FastAPI.

**Before going further, check** your account's current rate limits, data entitlements (especially index quotes) and Upstox's redistribution terms. The client throttles itself to 10 req/s and 250 req/min (configurable).

## Alerts

The backend watches the market during trading hours (even with no browser open) and raises an alert when:

- a stock gets a new ▲BUY / ▼SELL signal on a just-completed candle (`ALERT_SIGNAL_TIMEFRAME`, default 15m)
- a stock's day change crosses `ALERT_MOVE_LEVELS` (default ±2% and ±4%, once per day each)
- a stock moves `ALERT_FAST_MOVE_PCT` (default 1%) within `ALERT_FAST_WINDOW_MINUTES` (default 15), with a cooldown

Alerts appear in the header bell and as in-page pop-ups. Click **Turn on pop-ups & sound** for desktop notifications (the site must be open in a tab).
For phone alerts, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. **Send test alert** checks the whole path.

## Layout

```
backend/app/
  main.py                 FastAPI routes, OAuth, WebSocket hub
  service.py              scan / analysis orchestration
  providers/base.py       provider-neutral interface (swap in another licensed provider)
  providers/upstox.py     Upstox REST client + parsers
  providers/upstox_instruments.py   instrument-master download & symbol → key resolution
  providers/demo.py       synthetic demo data
  analysis/indicators.py  indicator maths
  analysis/signals.py     rule-based score, NO TRADE rules, ATR scenarios, risk flags
frontend/src/
  components/Dashboard.tsx, StockView.tsx, PriceChart.tsx (lightweight-charts v5) …
  lib/api.ts, useQuoteStream.ts, watchlist.ts
```

## API

`GET /api/health` · `GET /api/auth/upstox/login` · `GET /api/instruments` · `GET /api/quotes` ·
`GET /api/scanner?timeframe=1d|1h|15m` · `GET /api/stocks/{symbol}?timeframe=…` · `POST /api/stocks/{symbol}/explain` ·
`GET /api/today` · `GET /api/movers` · `GET /api/alerts` · `POST /api/alerts/test` · `WS /ws/quotes` (quotes + alerts)

## Known limitations

- Exchange holidays aren't modelled. On a holiday, quotes show as "stale" during normal hours.
- Corporate-action adjustment of historical candles isn't handled yet.
- Index rows show "Unavailable" if your Upstox entitlement doesn't return a quote for them.
