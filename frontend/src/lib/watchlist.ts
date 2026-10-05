"use client";

import { useCallback, useSyncExternalStore } from "react";

// Browser-local watchlist for the first milestone. Moves to PostgreSQL with user accounts later.
const KEY = "ai-stock:watchlist";
const listeners = new Set<() => void>();
const EMPTY: string[] = [];
let cache: { raw: string | null; list: string[] } = { raw: null, list: EMPTY };

function read(): string[] {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    return EMPTY;
  }
  if (raw !== cache.raw) {
    try {
      cache = { raw, list: raw ? JSON.parse(raw) : EMPTY };
    } catch {
      cache = { raw, list: EMPTY };
    }
  }
  return cache.list;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
}

export function useWatchlist() {
  const list = useSyncExternalStore(subscribe, read, () => EMPTY);
  const toggle = useCallback((symbol: string) => {
    const cur = read();
    const next = cur.includes(symbol) ? cur.filter((s) => s !== symbol) : [...cur, symbol];
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* storage unavailable */
    }
    listeners.forEach((l) => l());
  }, []);
  return { list, toggle, has: (s: string) => list.includes(s) };
}
