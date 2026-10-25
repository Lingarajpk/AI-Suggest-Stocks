"use client";

import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { type ApiError, fetcher } from "./api";
import { deviceId } from "./device";
import type { Evidence } from "./types";

export interface PaperOrder {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  type: "market" | "limit" | "stop";
  product: "intraday" | "delivery";
  status: "open" | "filled" | "cancelled" | "rejected";
  price: number | null;
  trigger: number | null;
  stop: number | null;
  target: number | null;
  trail_pct: number | null;
  note: string;
  setup: string | null;
  placed: string;
  filled_at?: string;
  fill_price?: number;
  reason?: string;
}

export interface PaperPosition {
  id: string;
  symbol: string;
  side: "long" | "short";
  qty: number;
  entry: number;
  last: number;
  product: "intraday" | "delivery";
  opened: string;
  note: string;
  setup: string | null;
  unrealized: number;
  unrealized_pct: number;
  legs: Partial<Record<"stop" | "target", { id: string; trigger: number | null; price: number | null; trail_pct: number | null }>>;
}

export interface PaperTrade {
  id: string;
  symbol: string;
  side: "long" | "short";
  qty: number;
  product: string;
  entry: number;
  exit: number;
  opened: string;
  closed: string;
  reason: "target" | "stop" | "manual" | "square_off";
  gross: number;
  charges: number;
  net: number;
  net_pct: number;
  mfe_pct: number;
  mae_pct: number;
  note: string;
  setup: string | null;
  exit_note: string;
  lesson: string;
}

export interface PaperAccount {
  account: { capital: number; equity: number; cash: number; unrealized: number; return_pct: number };
  positions: PaperPosition[];
  orders: PaperOrder[];
  journal: PaperTrade[];
  stats: {
    trades: number;
    win_rate?: number;
    net_total?: number;
    avg_pct?: number;
    profit_factor?: number | null;
    avg_mfe_pct?: number;
    avg_mae_pct?: number;
    evidence?: Evidence;
    by_setup?: { setup: string; trades: number; avg_pct: number; win_rate: number }[];
    exits?: Record<string, number>;
  };
  equity: [string, number][];
  session: "open" | "closed" | "pre_open";
  rules: string;
}

export interface NewOrder {
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  type: "market" | "limit" | "stop";
  product: "intraday" | "delivery";
  note: string;
  price?: number | null;
  trigger?: number | null;
  stop?: number | null;
  target?: number | null;
  trail_pct?: number | null;
  setup?: string | null;
}

/** This browser's device id (empty until mounted, so the first render matches the server's). */
export function useDevice(): string {
  const [id, setId] = useState("");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read the browser's id once mounted
    setId(deviceId());
  }, []);
  return id;
}

const key = (device: string) => (device ? `/api/paper/${device}` : null);

export function usePaper(device: string) {
  return useSWR<PaperAccount, ApiError>(key(device), fetcher, { refreshInterval: 5_000, keepPreviousData: true });
}

async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? data.message ?? `HTTP ${res.status}`);
  return data as T;
}

export async function placeOrder(device: string, order: NewOrder): Promise<PaperAccount & { order: PaperOrder }> {
  const r = await send<PaperAccount & { order: PaperOrder }>(`/api/paper/${device}/orders`, "POST", order);
  await mutate(key(device), r, false);
  return r;
}

export async function cancelOrder(device: string, id: string) {
  await mutate(key(device), await send<PaperAccount>(`/api/paper/${device}/orders/${id}`, "DELETE"), false);
}

export async function closePosition(device: string, id: string, note: string) {
  await mutate(key(device), await send<PaperAccount>(`/api/paper/${device}/positions/${id}/close`, "POST", { note }), false);
}

export async function saveLesson(device: string, id: string, fields: { exit_note?: string; lesson?: string }) {
  await send(`/api/paper/${device}/journal/${id}`, "PATCH", fields);
  await mutate(key(device));
}

export async function resetPaper(device: string, capital: number) {
  await mutate(key(device), await send<PaperAccount>(`/api/paper/${device}/reset`, "POST", { capital }), false);
}
