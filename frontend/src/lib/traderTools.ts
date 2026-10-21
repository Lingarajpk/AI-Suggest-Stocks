"use client";

import { useSyncExternalStore } from "react";

/**
 * Optional "Day-trader tools" (off by default): Time Machine, risk rules with a trades-today log,
 * square-off time and extra stop choices. Ideas from the day-trader skill pack
 * (github.com/Jakeschincariol/day-trader-skill, MIT). Everything here stays in this browser.
 */
export type ToolMarket = "stocks" | "forex";

export interface ToolSettings {
  enabled: boolean;
  maxLossPerTrade: number; // ₹, loss at the stop including charges
  dailyLossLimit: number; // ₹, sum of today's max losses
  maxTradesPerDay: number;
  minRR: number;
  noEntriesBefore: string; // "HH:MM" IST, stocks
  noEntriesAfter: string;
  squareOff: string;
  fxNoEntriesBefore: string; // currency futures (09:00-17:00 session)
  fxNoEntriesAfter: string;
  fxSquareOff: string;
}

export const DEFAULT_TOOLS: ToolSettings = {
  enabled: false,
  maxLossPerTrade: 1000,
  dailyLossLimit: 3000,
  maxTradesPerDay: 5,
  minRR: 1.5,
  noEntriesBefore: "09:30",
  noEntriesAfter: "14:45",
  squareOff: "15:15",
  fxNoEntriesBefore: "09:15",
  fxNoEntriesAfter: "16:15",
  fxSquareOff: "16:45",
};

const KEY = "ai-stock:trader-tools";
const LOG_KEY = "ai-stock:trader-log";
const EVENT = "ai-stock:trader-tools-changed";

let cache: { raw: string | null; value: ToolSettings } | null = null;

function read(): ToolSettings {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    /* storage unavailable */
  }
  if (cache && cache.raw === raw) return cache.value;
  let value = DEFAULT_TOOLS;
  try {
    value = { ...DEFAULT_TOOLS, ...(raw ? JSON.parse(raw) : {}) };
  } catch {
    /* corrupt: defaults */
  }
  cache = { raw, value };
  return value;
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

export function saveTools(next: Partial<ToolSettings>) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...read(), ...next }));
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new Event(EVENT));
}

/** The tool settings; `enabled` is false until the user turns the tools on in the header. */
export function useTraderTools(): ToolSettings {
  return useSyncExternalStore(subscribe, read, () => DEFAULT_TOOLS);
}

export const squareOffFor = (s: ToolSettings, m: ToolMarket) => (m === "forex" ? s.fxSquareOff : s.squareOff);

// ---- trades-taken log (for the daily limits) --------------------------------------
export interface LoggedTrade {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  entry: number;
  stop: number;
  qtyLabel: string;
  maxLoss: number;
  at: string;
}

let logCache: { raw: string | null; value: LoggedTrade[] } | null = null;

function readLog(): LoggedTrade[] {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(LOG_KEY);
  } catch {
    /* storage unavailable */
  }
  if (logCache && logCache.raw === raw) return logCache.value;
  let value: LoggedTrade[] = [];
  try {
    value = raw ? JSON.parse(raw) : [];
  } catch {
    value = [];
  }
  logCache = { raw, value };
  return value;
}

const EMPTY: LoggedTrade[] = [];
export function useTradeLog(): LoggedTrade[] {
  return useSyncExternalStore(subscribe, readLog, () => EMPTY);
}

/** IST calendar day and minutes since midnight. */
export function istNow(now = Date.now()) {
  const d = new Date(now + 5.5 * 3600_000);
  return { day: d.toISOString().slice(0, 10), minutes: d.getUTCHours() * 60 + d.getUTCMinutes(), weekday: d.getUTCDay() };
}

export const todaysTrades = (log: LoggedTrade[], now = Date.now()) => log.filter((t) => istNow(Date.parse(t.at)).day === istNow(now).day);

export function logTrade(t: Omit<LoggedTrade, "id" | "at">) {
  // Keep a week at most: only today's entries count for the limits.
  const keep = readLog().filter((x) => Date.now() - Date.parse(x.at) < 7 * 86_400_000);
  const next = [...keep, { ...t, id: Math.random().toString(36).slice(2), at: new Date().toISOString() }];
  try {
    localStorage.setItem(LOG_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new Event(EVENT));
}

export function clearToday() {
  const rest = readLog().filter((t) => istNow(Date.parse(t.at)).day !== istNow().day);
  try {
    localStorage.setItem(LOG_KEY, JSON.stringify(rest));
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new Event(EVENT));
}

// ---- the rule check ---------------------------------------------------------------
export interface RiskInput {
  market: ToolMarket;
  side: "BUY" | "SELL";
  entry: number;
  stop: number;
  target: number;
  /** Net rupees lost if the stop hits (charges included), as a positive number. */
  lossAtStop: number;
  /** Intraday rules (entry window, square-off) apply to day trades only. */
  intraday: boolean;
}

export interface RiskCheckRow {
  name: string;
  ok: boolean;
  detail: string;
}

const toMin = (hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
};
const inr = (v: number) => `₹${Math.round(v).toLocaleString("en-IN")}`;

/** PASS only if every rule holds. Exiting a trade is never checked here: getting out is always allowed. */
export function riskCheck(p: RiskInput, s: ToolSettings, log: LoggedTrade[], now = Date.now()): { pass: boolean; rows: RiskCheckRow[] } {
  const long = p.side === "BUY";
  const risk = long ? p.entry - p.stop : p.stop - p.entry;
  const reward = long ? p.target - p.entry : p.entry - p.target;
  const today = todaysTrades(log, now);
  const used = today.reduce((a, t) => a + t.maxLoss, 0);
  const rows: RiskCheckRow[] = [];
  rows.push({ name: "Stop-loss", ok: risk > 0, detail: risk > 0 ? `on the right side of the entry` : `must be ${long ? "below" : "above"} the entry` });
  rows.push({
    name: "Loss at the stop",
    ok: p.lossAtStop <= s.maxLossPerTrade,
    detail: `${inr(p.lossAtStop)} of your ${inr(s.maxLossPerTrade)} max per trade`,
  });
  const rr = risk > 0 ? reward / risk : 0;
  rows.push({ name: "Reward : risk", ok: rr >= s.minRR, detail: `1 : ${rr.toFixed(2)} (minimum 1 : ${s.minRR})` });
  rows.push({
    name: "Daily loss limit",
    ok: used + p.lossAtStop <= s.dailyLossLimit,
    detail: `${inr(used)} used today + ${inr(p.lossAtStop)} = ${inr(used + p.lossAtStop)} of ${inr(s.dailyLossLimit)}`,
  });
  rows.push({ name: "Trades today", ok: today.length < s.maxTradesPerDay, detail: `${today.length} of ${s.maxTradesPerDay} taken` });
  if (p.intraday) {
    const { minutes, weekday } = istNow(now);
    const fx = p.market === "forex";
    const from = toMin(fx ? s.fxNoEntriesBefore : s.noEntriesBefore);
    const to = toMin(fx ? s.fxNoEntriesAfter : s.noEntriesAfter);
    const weekend = weekday === 0 || weekday === 6;
    rows.push({
      name: "Entry window",
      ok: !weekend && minutes >= from && minutes <= to,
      detail: weekend ? "market closed (weekend)" : `new entries ${fx ? s.fxNoEntriesBefore : s.noEntriesBefore}–${fx ? s.fxNoEntriesAfter : s.noEntriesAfter} IST`,
    });
  }
  return { pass: rows.every((r) => r.ok), rows };
}

// ---- stop choices -------------------------------------------------------------------
export interface StopChoice {
  key: string;
  label: string;
  stop: number;
}

/** Ways to place the stop: the setup's own, the opening range, a fixed %, or ATR multiples. Rounded away from the entry. */
export function stopChoices(o: {
  side: "BUY" | "SELL";
  entry: number;
  setupStop: number;
  atr?: number | null;
  openingRange?: { high: number; low: number; complete: boolean } | null;
  dp: number;
}): StopChoice[] {
  const { side, entry, setupStop, atr, openingRange: or, dp } = o;
  const long = side === "BUY";
  const f = 10 ** dp;
  const away = (v: number) => (long ? Math.floor(v * f) / f : Math.ceil(v * f) / f);
  const out: StopChoice[] = [{ key: "setup", label: "Setup", stop: setupStop }];
  if (or?.complete) {
    out.push({ key: "or-mid", label: "Range mid", stop: away((or.high + or.low) / 2) });
    out.push({ key: "or-edge", label: long ? "Range low" : "Range high", stop: away(long ? or.low : or.high) });
  }
  out.push({ key: "pct-0.5", label: "0.5%", stop: away(entry * (long ? 0.995 : 1.005)) });
  out.push({ key: "pct-1", label: "1%", stop: away(entry * (long ? 0.99 : 1.01)) });
  if (atr) out.push({ key: "atr-1", label: "1 ATR", stop: away(long ? entry - atr : entry + atr) });
  // Only stops on the right side of the entry and outside normal noise (0.05% of the price).
  return out.filter((c) => (long ? c.stop < entry : c.stop > entry) && Math.abs(entry - c.stop) / entry >= 0.0005);
}

export const R_TARGETS = [1, 1.5, 2, 2.5, 3];

export function targetFor(side: "BUY" | "SELL", entry: number, stop: number, r: number, dp: number) {
  const f = 10 ** dp;
  return Math.round((entry + (side === "BUY" ? 1 : -1) * r * Math.abs(entry - stop)) * f) / f;
}
