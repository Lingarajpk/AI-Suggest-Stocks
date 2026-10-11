import useSWR from "swr";
import type { Health, IntradayDesk, NewsFeed, PatternStatsResult, ScanResult, StockDetail, StockNews, StrategyStatsResult, Timeframe, TodayResult } from "./types";

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
    const detail = typeof body.detail === "string" ? body.detail : undefined;
    throw new ApiError(res.status, body.error ?? "http_error", body.message ?? detail ?? `HTTP ${res.status}`, body.login_url);
  }
  return body as T;
}

export const TIMEFRAME_LABEL: Record<Timeframe, string> = {
  "1m": "1-minute",
  "5m": "5-minute",
  "15m": "15-minute",
  "1h": "1-hour",
  "1d": "Daily",
};

export function useHealth() {
  return useSWR<Health, ApiError>("/api/health", fetcher, { refreshInterval: 30_000 });
}

export function useScanner(timeframe: Timeframe) {
  return useSWR<ScanResult, ApiError>(`/api/scanner?timeframe=${timeframe}`, fetcher, {
    refreshInterval: timeframe === "1d" ? 300_000 : 60_000,
    keepPreviousData: true,
  });
}

export function useStock(symbol: string, timeframe: Timeframe) {
  return useSWR<StockDetail, ApiError>(`/api/stocks/${encodeURIComponent(symbol)}?timeframe=${timeframe}`, fetcher, {
    refreshInterval: timeframe === "1d" ? 300_000 : timeframe === "1m" ? 15_000 : timeframe === "5m" ? 20_000 : 60_000,
    keepPreviousData: true,
  });
}

/** Intraday desk: index BUY/SELL calls (5-min entry, 15-min trend) and stocks with a live call. */
export function useIntraday() {
  return useSWR<IntradayDesk, ApiError>("/api/intraday", fetcher, { refreshInterval: 20_000, keepPreviousData: true });
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
