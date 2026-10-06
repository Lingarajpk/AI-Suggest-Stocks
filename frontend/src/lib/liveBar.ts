import type { CandleData, Quote, Timeframe } from "./types";

const IST_MS = 5.5 * 3600 * 1000;
const BAR_MINUTES: Record<Timeframe, number> = { "1m": 1, "15m": 15, "1h": 60, "1d": 0 };
const SESSION_OPEN_MIN = 9 * 60 + 15;

/** Start of the bar containing `ms`, as an ISO string (bars are aligned to the 09:15 IST open). */
function barStart(ms: number, tf: Timeframe): string {
  const ist = new Date(ms + IST_MS); // fields read with getUTC* are IST wall-clock values
  const dayStartUtc = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate()) - IST_MS;
  if (tf === "1d") return new Date(dayStartUtc).toISOString();
  const mins = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  const size = BAR_MINUTES[tf];
  const offset = Math.max(0, Math.floor((mins - SESSION_OPEN_MIN) / size) * size);
  return new Date(dayStartUtc + (SESSION_OPEN_MIN + offset) * 60_000).toISOString();
}

/**
 * The still-forming candle, kept current with the latest quote. Display only:
 * signals and indicators are always computed from completed candles on the backend.
 */
export function buildLiveBar(tf: Timeframe, forming: CandleData | null | undefined, lastCompleted: CandleData | undefined, quote: Quote | null): CandleData | null {
  const lastMs = lastCompleted ? Date.parse(lastCompleted.time) : 0;
  if (!quote || (quote.freshness !== "polled" && quote.freshness !== "demo")) {
    return forming ?? null;
  }
  const price = quote.last_price;
  const at = Date.parse(quote.last_trade_time ?? quote.fetched_at);

  if (tf === "1d") {
    const start = barStart(at, "1d");
    if (Date.parse(start) <= lastMs) return null;
    return {
      time: start,
      open: quote.open ?? price,
      high: Math.max(quote.high ?? price, price),
      low: Math.min(quote.low ?? price, price),
      close: price,
      volume: quote.volume ?? 0,
    };
  }

  const start = barStart(at, tf);
  if (Date.parse(start) <= lastMs) return null;
  if (forming && Date.parse(forming.time) === Date.parse(start)) {
    return { ...forming, high: Math.max(forming.high, price), low: Math.min(forming.low, price), close: price };
  }
  // A new bar began after the last fetch: start it from the live price.
  return { time: start, open: price, high: price, low: price, close: price, volume: 0 };
}
