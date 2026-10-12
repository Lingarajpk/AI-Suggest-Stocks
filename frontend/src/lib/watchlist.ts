"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { deviceId } from "./device";

// This device's watchlist. The browser keeps the list (instant, works offline) and mirrors it to
// the server, which uses it to send this device BUY/SELL notifications for exactly these stocks.
const KEY = "ai-stock:watchlist";
const listeners = new Set<() => void>();
const EMPTY: string[] = [];
let cache: { raw: string | null; list: string[] } = { raw: null, list: EMPTY };
let synced = false;

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

/** Current list outside React (e.g. to decide which live alerts get a pop-up). */
export function currentWatchlist(): string[] {
  return typeof window === "undefined" ? EMPTY : read();
}

function write(list: string[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* storage unavailable */
  }
  listeners.forEach((l) => l());
}

/** Send the list to the server; adopt its cleaned-up version (canonical symbols, unknown ones dropped). */
async function pushToServer(list: string[]) {
  const id = deviceId();
  if (!id) return;
  try {
    const res = await fetch(`/api/watchlist/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: list }),
    });
    if (!res.ok) return;
    const body = (await res.json()) as { symbols: string[] };
    // Only adopt it if the user hasn't changed the list meanwhile.
    if (JSON.stringify(read()) === JSON.stringify(list) && JSON.stringify(body.symbols) !== JSON.stringify(list)) write(body.symbols);
  } catch {
    /* offline: synced on the next change or page load */
  }
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

  // Once per page load, make sure the server has this device's list (it may have restarted).
  useEffect(() => {
    if (synced) return;
    synced = true;
    if (read().length) void pushToServer(read());
  }, []);

  const set = useCallback((next: string[]) => {
    write(next);
    void pushToServer(next);
  }, []);
  const toggle = useCallback(
    (symbol: string) => {
      const cur = read();
      set(cur.includes(symbol) ? cur.filter((s) => s !== symbol) : [...cur, symbol]);
    },
    [set],
  );
  const add = useCallback((symbol: string) => !read().includes(symbol) && set([...read(), symbol]), [set]);
  const remove = useCallback((symbol: string) => set(read().filter((s) => s !== symbol)), [set]);
  return { list, toggle, add, remove, has: (s: string) => list.includes(s) };
}
