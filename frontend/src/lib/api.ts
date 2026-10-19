import useSWR from "swr";
import type { CandleData, Health, IntradayDesk, OptionChain, Point, SearchResult, WatchCall, NewsFeed, PatternStatsResult, ScanResult, StockDetail, StockNews, StrategyStatsResult, Timeframe, TodayResult } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public loginUrl?: string,
  ) {
    super(message);
  }
}

// All calls go to /api/* which Next rewrites to the FastAPI backend.
// The browser never talks to Upstox or NVIDIA and never sees provider tokens.
export async function fetcher<T>(url: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch {
    throw new ApiError(0, "backend_unreachable", "Backend is not reachable. Is the FastAPI server running on port 8000?");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if ([502, 503, 504].includes(res.status) && !body.error) {
      // The proxy got no answer: on free hosting the backend sleeps when idle and takes ~30-60 s to wake.
      throw new ApiError(res.status, "backend_waking", "The data server is waking up or busy. Retrying automatically…");
    }
    const detail = typeof body.detail === "string" ? body.detail : undefined;
    throw new ApiError(res.status, body.error ?? "http_error", body.message ?? detail ?? `HTTP ${res.status}`, body.login_url);
  }
  return body as T;
}

export const TIMEFRAME_LABEL: Record<Timeframe, string> = {
  "1m": "1-minute",
  "3m": "3-minute",
  "5m": "5-minute",
  "15m": "15-minute",
  "30m": "30-minute",
  "1h": "1-hour",
  "1d": "Daily",
  "1w": "Weekly",
};

/** Compact tab labels, in the order the chart offers them. */
export const TIMEFRAME_SHORT: Record<Timeframe, string> = {
  "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "1d": "1D", "1w": "1W",
};

/** Bars inside a trading session (session VWAP applies). */
export const isIntraday = (tf: Timeframe) => tf !== "1d" && tf !== "1w";

export function useHealth() {
  return useSWR<Health, ApiError>("/api/health", fetcher, {
    // Poll faster while waiting for the owner to approve an Upstox token, so the page goes live promptly.
    refreshInterval: (h) => (h?.upstox?.approval_pending ? 5_000 : 30_000),
  });
}

export function useScanner(timeframe: Timeframe) {
  return useSWR<ScanResult, ApiError>(`/api/scanner?timeframe=${timeframe}`, fetcher, {
    refreshInterval: isIntraday(timeframe) ? 60_000 : 300_000,
    keepPreviousData: true,
  });
}

type CompactSeries = Record<string, (number | null)[]>;

/** The API sends each series as an array aligned to the candles (much smaller than {time, value}
 * objects); rebuild the points the charts use. Supertrend points also carry their direction. */
function expandSeries(candles: CandleData[], compact: CompactSeries | undefined): Record<string, Point[]> {
  const out: Record<string, Point[]> = {};
  const dirs = compact?.supertrend_dir;
  for (const [name, values] of Object.entries(compact ?? {})) {
    if (name === "supertrend_dir") continue;
    const pts: Point[] = [];
    values.forEach((v, i) => {
      const c = candles[i];
      if (v == null || !c) return;
      pts.push(name === "supertrend" ? { time: c.time, value: v, dir: dirs?.[i] ?? 0 } : { time: c.time, value: v });
    });
    out[name] = pts;
  }
  return out;
}

async function fetchStock(url: string): Promise<StockDetail> {
  const d = await fetcher<StockDetail & { series: CompactSeries; oscillators: CompactSeries }>(url);
  const first = d.candles[0]?.time ?? "";
  const h = d.history;
  return {
    ...d,
    series: expandSeries(d.candles, d.series as unknown as CompactSeries),
    oscillators: expandSeries(d.candles, d.oscillators as unknown as CompactSeries),
    // The chart only carries recent candles: drop older markers so they don't point at missing bars.
    history: h && { ...h, markers: h.markers?.filter((m) => m.time >= first), exits: h.exits?.filter((x) => x.entry_time >= first) },
  };
}

async function fetchIntraday(url: string): Promise<IntradayDesk> {
  const d = await fetcher<IntradayDesk>(url);
  return {
    ...d,
    indices: d.indices.map((c) =>
      c.chart ? { ...c, chart: { ...c.chart, series: expandSeries(c.chart.candles, c.chart.series as unknown as CompactSeries) } } : c,
    ),
  };
}

const BAR_MS: Partial<Record<Timeframe, number>> = { "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000 };
const IST_MS = 5.5 * 3600_000;
const OPEN_MS = (9 * 60 + 15) * 60_000; // bars are aligned to the 09:15 IST open

/** Milliseconds until `delay` after the current intraday bar closes (so the new candle is picked up at once). */
export function msToNextBar(timeframe: Timeframe, delay = 5_000, now = Date.now()): number | null {
  const bar = BAR_MS[timeframe];
  if (!bar) return null;
  const sinceOpen = (((now + IST_MS) % 86_400_000) - OPEN_MS + 86_400_000) % 86_400_000;
  return bar - (sinceOpen % bar) + delay;
}

/** Poll every `normal` ms, but also right after each candle closes. */
function barAware(timeframe: Timeframe, normal: number, delay?: number): number {
  const next = msToNextBar(timeframe, delay);
  return next == null ? normal : Math.min(normal, next);
}

export function useStock(symbol: string, timeframe: Timeframe) {
  const normal = !isIntraday(timeframe) ? 300_000 : timeframe === "1m" || timeframe === "3m" ? 15_000 : timeframe === "5m" ? 20_000 : 60_000;
  return useSWR<StockDetail, ApiError>(`/api/stocks/${encodeURIComponent(symbol)}?timeframe=${timeframe}`, fetchStock, {
    // While the combined model trains in the background, check back quickly so the outlook appears.
    refreshInterval: (d) => (d?.model_outlook?.status === "training" ? 4_000 : barAware(timeframe, normal)),
    keepPreviousData: true,
  });
}

/** BUY / SELL / exit calls for this device's watchlist (refetched when the list changes). */
export function useWatchCalls(device: string, symbols: string[]) {
  const key = device && symbols.length ? `/api/watchlist/${device}/calls?s=${encodeURIComponent(symbols.join(","))}` : null;
  return useSWR<{ generated_at: string; session: string; rows: WatchCall[] }, ApiError>(key, fetcher, {
    refreshInterval: 20_000,
    keepPreviousData: true,
  });
}

export function useSearch(query: string) {
  const q = query.trim();
  return useSWR<{ results: SearchResult[] }, ApiError>(q ? `/api/instruments/search?q=${encodeURIComponent(q)}` : null, fetcher, {
    keepPreviousData: true,
    dedupingInterval: 30_000,
  });
}

/** Intraday desk: index BUY/SELL calls (5-min entry, 15-min trend) and stocks with a live call. */
export function useIntraday() {
  // The desk is served from the backend's cache, so polling often is cheap; also refetch just after each
  // 5-minute candle closes (the backend rebuilds the desk ~4 s after the close).
  return useSWR<IntradayDesk, ApiError>("/api/intraday", fetchIntraday, { refreshInterval: () => barAware("5m", 10_000, 9_000), keepPreviousData: true });
}

/** Index option chain with an OI summary and which option to buy (NIFTY 50, BANK NIFTY, SENSEX only). */
export function useOptionChain(symbol: string | null, expiry: string | null) {
  const key = symbol ? `/api/options/${encodeURIComponent(symbol)}${expiry ? `?expiry=${expiry}` : ""}` : null;
  return useSWR<OptionChain, ApiError>(key, fetcher, { refreshInterval: 15_000, keepPreviousData: true });
}

/** Per-pattern success rates measured across the whole universe (the pattern "training"). */
export function usePatternStats(timeframe: Timeframe) {
  return useSWR<PatternStatsResult, ApiError>(`/api/patterns/stats?timeframe=${timeframe}`, fetcher, {
    refreshInterval: 30 * 60_000,
    keepPreviousData: true,
  });
}

/** Each strategy's and candlestick's hit rate vs. random, pooled across the universe. */
export function useStrategyStats(timeframe: Timeframe) {
  return useSWR<StrategyStatsResult, ApiError>(`/api/strategies/stats?timeframe=${timeframe}`, fetcher, {
    refreshInterval: 30 * 60_000,
    keepPreviousData: true,
  });
}

/** Live headlines for one stock + news-adjusted probability (backend polls the feed every few minutes). */
export function useStockNews(symbol: string) {
  return useSWR<StockNews, ApiError>(`/api/news/${encodeURIComponent(symbol)}`, fetcher, { refreshInterval: 60_000, keepPreviousData: true });
}

export function useNewsFeed(limit = 20) {
  return useSWR<NewsFeed, ApiError>(`/api/news?limit=${limit}`, fetcher, { refreshInterval: 60_000, keepPreviousData: true });
}

export function useToday() {
  return useSWR<TodayResult, ApiError>("/api/today", fetcher, { refreshInterval: 120_000, keepPreviousData: true });
}

/** How a signal's past hit rate compares with the random base rate. */
export function edgeVerdict(winRate: number | null, baseRate: number | null, count: number) {
  if (winRate == null || baseRate == null || count === 0) return { label: "No past signals", tone: "muted" as const };
  if (count < 20) return { label: `Only ${count} past signals — too few to judge`, tone: "muted" as const };
  const edge = winRate - baseRate;
  if (edge >= 5) return { label: `Beat random by ${edge.toFixed(0)} pts`, tone: "bull" as const };
  if (edge <= -5) return { label: `Worse than random by ${(-edge).toFixed(0)} pts`, tone: "bear" as const };
  return { label: "No clear edge vs random", tone: "warn" as const };
}
