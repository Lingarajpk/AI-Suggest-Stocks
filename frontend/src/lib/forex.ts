"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { ApiError, barAware, expandSeries, fetchStock, fetcher, isIntraday } from "./api";
import { roundTrip } from "./charges";
import { fmtPx } from "./format";
import type { IntradayCard, Quote, StockDetail, Timeframe } from "./types";
import type { StreamStatus } from "./useQuoteStream";

/** Forex: NSE currency futures (rupee pairs), served by /api/forex and kept apart from stocks. */
export const FX_PAIRS = ["USDINR", "EURINR", "GBPINR", "JPYINR"] as const;
export const FX_DP = 4;
export const FX_TICK = 0.0025;
/** Currency futures trade 09:00-17:00 IST; bars are aligned to 09:00. */
export const FX_OPEN_MIN = 9 * 60;
const FX_OPEN_MS = FX_OPEN_MIN * 60_000;

export const fmtFx = (v: number | null | undefined) => fmtPx(v, FX_DP);
export const pairLabel = (p: string) => `${p.slice(0, 3)}/${p.slice(3)}`;
export const PAIR_SIGN: Record<string, string> = { USDINR: "$", EURINR: "€", GBPINR: "£", JPYINR: "¥" };

export type Session = "open" | "closed" | "pre_open";

export interface PairInfo {
  base: string;
  currency: string;
  per: string;
}

/** Round-trip charges per lot at 1 and 10 lots, and the ticks needed to cover them. */
export interface LotCosts {
  lot_value: number;
  tick_value: number;
  per_lot_1: number;
  per_lot_10: number;
  breakeven_ticks_1: number;
  breakeven_ticks_10: number;
}

export interface ForexCard extends IntradayCard {
  pair?: PairInfo;
  costs?: LotCosts | null;
}

export interface ForexDesk {
  generated_at: string;
  source: "upstox" | "demo";
  session: Session;
  market_hours: string;
  entry_timeframe: Timeframe;
  trend_timeframe: Timeframe;
  pairs: ForexCard[];
  unavailable: string[];
  backtest_cost_pct: number;
  method: string;
}

export interface ForexDetail extends StockDetail {
  pair?: PairInfo;
  session: Session;
  costs: LotCosts | null;
}

export interface ForexQuotes {
  source: "upstox" | "demo";
  session: Session;
  server_time: string;
  quotes: Quote[];
  contracts: Record<string, { contract: string | null; expiry: string | null; lot_units: number | null }>;
  unavailable: string[];
}

/** Live prices of the four pairs: polled every 2 s while the currency market is open (30 s otherwise). */
export function useForexQuotes() {
  const { data, error } = useSWR<ForexQuotes, ApiError>("/api/forex/quotes", fetcher, {
    refreshInterval: (d) => (d?.session === "open" ? 2_000 : 30_000),
    dedupingInterval: 1_000,
    keepPreviousData: true,
  });
  const quotes = useMemo(() => Object.fromEntries((data?.quotes ?? []).map((q) => [q.symbol, q])) as Record<string, Quote>, [data]);
  const status: StreamStatus = error ? (data ? "reconnecting" : "error") : data ? "open" : "connecting";
  return { quotes, session: data?.session ?? null, contracts: data?.contracts ?? {}, lastUpdate: data?.server_time ?? null, status, error };
}

async function fetchDesk(url: string): Promise<ForexDesk> {
  const d = await fetcher<ForexDesk>(url);
  return {
    ...d,
    pairs: d.pairs.map((c) =>
      c.chart ? { ...c, chart: { ...c.chart, series: expandSeries(c.chart.candles, c.chart.series as unknown as Record<string, (number | null)[]>) } } : c,
    ),
  };
}

export function useForexDesk() {
  // Cached on the backend; also refetched just after each 5-minute candle closes.
  return useSWR<ForexDesk, ApiError>("/api/forex/desk", fetchDesk, {
    refreshInterval: () => barAware("5m", 10_000, 9_000, FX_OPEN_MS),
    keepPreviousData: true,
  });
}

export function useForexPair(pair: string, timeframe: Timeframe) {
  const normal = !isIntraday(timeframe) ? 300_000 : timeframe === "1m" ? 15_000 : timeframe === "5m" ? 20_000 : 60_000;
  return useSWR<ForexDetail, ApiError>(`/api/forex/pairs/${encodeURIComponent(pair)}?timeframe=${timeframe}`, fetchStock as (u: string) => Promise<ForexDetail>, {
    refreshInterval: (d) => (d?.model_outlook?.status === "training" ? 4_000 : barAware(timeframe, normal, undefined, FX_OPEN_MS)),
    keepPreviousData: true,
  });
}

// ---- lots maths ---------------------------------------------------------------
const PREFS = "ai-stock:fx-calc";
export const FX_PREFS_EVENT = "ai-stock:fx-calc-changed";
/** Exchange margin is roughly 2-3% of the contract value for rupee futures; an estimate, Upstox shows the exact figure. */
export const MARGIN_PCT = 3;

export interface FxPrefs {
  capital: number;
  riskPct: number;
}

export function loadFxPrefs(): FxPrefs {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS) ?? "{}");
    return { capital: Number(p.capital) || 100000, riskPct: Number(p.riskPct) || 1 };
  } catch {
    return { capital: 100000, riskPct: 1 };
  }
}

export function saveFxPrefs(p: FxPrefs) {
  try {
    localStorage.setItem(PREFS, JSON.stringify(p));
    window.dispatchEvent(new Event(FX_PREFS_EVENT));
  } catch {
    /* storage unavailable */
  }
}

export function useFxPrefs(): FxPrefs {
  const [prefs, setPrefs] = useState<FxPrefs>({ capital: 100000, riskPct: 1 });
  useEffect(() => {
    const read = () => setPrefs(loadFxPrefs());
    read();
    window.addEventListener(FX_PREFS_EVENT, read);
    window.addEventListener("storage", read);
    return () => {
      window.removeEventListener(FX_PREFS_EVENT, read);
      window.removeEventListener("storage", read);
    };
  }, []);
  return prefs;
}

export interface FxPlan {
  lots: number;
  units: number;
  limitedBy: "risk" | "margin";
  margin: number;
  riskPerLot: number;
  atTarget: { gross: number; charges: number; net: number };
  atStop: { gross: number; charges: number; net: number };
  breakeven: number;
  rr: number | null;
}

/** Lots that risk `riskPct` of capital at the stop (charges included), capped by the estimated margin. */
export function fxPlan(o: { capital: number; riskPct: number; side: "BUY" | "SELL"; entry: number; stop: number; target: number; lotUnits: number }): FxPlan | null {
  const { capital, riskPct, side, entry, stop, target, lotUnits } = o;
  if (!(capital > 0 && riskPct > 0 && entry > 0 && stop > 0 && target > 0 && lotUnits > 0)) return null;
  const long = side === "BUY";
  const move = long ? entry - stop : stop - entry;
  if (move <= 0) return null;
  const costPerLot = (lots: number) => roundTrip("currency", lots * lotUnits, entry, stop).total / Math.max(lots, 1);
  // Brokerage is per order, so the per-lot cost falls with size: settle the lot count in two passes.
  let lots = Math.floor((capital * riskPct) / 100 / (move * lotUnits + costPerLot(1)));
  lots = Math.max(0, Math.floor((capital * riskPct) / 100 / (move * lotUnits + costPerLot(Math.max(lots, 1)))));
  const marginPerLot = (entry * lotUnits * MARGIN_PCT) / 100;
  const byMargin = Math.floor(capital / marginPerLot);
  const limitedBy = lots <= byMargin ? "risk" : "margin";
  lots = Math.min(lots, byMargin);
  const units = lots * lotUnits;
  const outcome = (exit: number) => {
    const gross = units * (long ? exit - entry : entry - exit);
    const charges = units > 0 ? roundTrip("currency", units, long ? entry : exit, long ? exit : entry).total : 0;
    return { gross, charges, net: gross - charges };
  };
  const atTarget = outcome(target);
  const reward = long ? target - entry : entry - target;
  return {
    lots,
    units,
    limitedBy,
    margin: lots * marginPerLot,
    riskPerLot: move * lotUnits,
    atTarget,
    atStop: outcome(stop),
    breakeven: units > 0 ? entry + (long ? 1 : -1) * (atTarget.charges / units) : entry,
    rr: reward > 0 ? reward / move : null,
  };
}
