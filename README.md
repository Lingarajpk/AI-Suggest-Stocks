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
| Indicators: EMA 9/20/50/200, SMA 20, RSI, MACD (+ signal/zero crosses, divergence), ATR, Bollinger, ADX/DI, Supertrend 10/3, session VWAP ±1σ/±2σ, anchored VWAP, confirmed swing pivots, rel. volume, ROC, swing S/R | ✅ |
| Signals: Strong Bullish → Strong Bearish, NO TRADE on weak/stale/insufficient data or R:R < 1 | ✅ |
| Dashboard, scanner + filters, signal cards, sector strength, watchlist (browser-local), stock detail charts | ✅ |
| Redis cache (optional; in-memory fallback) | ✅ |
| Chart patterns: 34 patterns from the *Big Book of Chart Patterns* (doubles/triples, H&S, triangles, wedges, flags/pennants, rectangles, channels, cups, rounding tops/bottoms, diamonds, broadening, gaps, islands), no-look-ahead detection, per-pattern backtest pooled across the universe | ✅ |
| Strategies: Trend + Momentum, Liquidity Sweep (swings, equal highs/lows, previous day H/L), FVG / IFVG, AMD (Power of 3, intraday), Breakout Probability, Supertrend flip, MACD divergence; each backtested vs. random across the universe | ✅ |
| Candlesticks: Doji, Hammer, Shooting Star, Engulfing, Morning/Evening Star, Inside Bar (trend-context aware), backtested the same way | ✅ |
| Order flow (proxy): volume profile (POC, 70% value area), delta and CVD estimated from candles, CVD divergence. Footprint, absorption and stacked imbalances need tick data that NSE/Upstox does not provide | ⚠️ estimated |
| Intraday desk (home page): NIFTY 50 / BANK NIFTY / SENSEX BUY / SELL / WAIT from the 5-minute outlook confirmed by the 15-minute trend, with entry, target, stop and a live 5-minute chart; stocks with a live intraday call below; alert when an index call turns BUY or SELL | ✅ |
| Combined outlook: up/down probability + BUY/SELL/HOLD from all strategies (logistic model, time-split tested on unseen data; strategy features kept only if they help on a held-out slice of the training data) | ✅ |
| Live news: Google News RSS per stock every 5 min, NVIDIA/keyword sentiment, news-adjusted probability (capped ±10 pts, self-learning from outcomes), news alerts | ✅ |
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
pytest
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
- a chart pattern breaks out on a just-completed candle (with target, stop and its measured hit rate)
- a strategy (Trend + Momentum, Liquidity Sweep, FVG/IFVG, AMD, Supertrend flip, MACD divergence) fires on a just-completed candle
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
  analysis/patterns.py    chart-pattern detection (confirmed swing pivots) + target-before-stop backtest
  analysis/strategies.py  entry strategies (trend+momentum, sweeps, FVG/IFVG, AMD, breakout probability) + backtests
  analysis/candles.py     candlestick patterns
  analysis/volume_profile.py  POC / value area, estimated delta / CVD
  analysis/probability.py combined outlook model (all strategies -> P(up)), out-of-sample tested
  news.py                 live news fetch, sentiment, news-adjusted probability, outcome learning
frontend/src/
  components/Dashboard.tsx, StockView.tsx, PriceChart.tsx (lightweight-charts v5) …
  lib/api.ts, useQuoteStream.ts, watchlist.ts
```

## API

`GET /api/health` · `GET /api/auth/upstox/login` · `GET /api/instruments` · `GET /api/quotes` ·
`GET /api/scanner?timeframe=1d|1h|15m|5m` · `GET /api/stocks/{symbol}?timeframe=…` · `POST /api/stocks/{symbol}/explain` ·
`GET /api/intraday` · `GET /api/today` · `GET /api/patterns/stats?timeframe=…` · `GET /api/strategies/stats?timeframe=…` · `GET /api/news` · `GET /api/news/{symbol}` · `GET /api/movers` · `GET /api/alerts` · `POST /api/alerts/test` · `WS /ws/quotes` (quotes + alerts)

## Known limitations

- Exchange holidays aren't modelled. On a holiday, quotes show as "stale" during normal hours.
- Corporate-action adjustment of historical candles isn't handled yet.
- Index rows show "Unavailable" if your Upstox entitlement doesn't return a quote for them.
