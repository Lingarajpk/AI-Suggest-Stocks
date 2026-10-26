"use client";

import { useEffect, useRef, useState } from "react";
import { wsToken } from "./auth";
import { showNotification } from "./notify";
import { currentWatchlist } from "./watchlist";
import type { Alert, Quote } from "./types";

/** "polling": the WebSocket is down or has no data, so prices come from the REST backup every few seconds. */
export type StreamStatus = "connecting" | "open" | "polling" | "reconnecting" | "error";

interface StreamState {
  quotes: Record<string, Quote>;
  status: StreamStatus;
  error: string | null;
  lastUpdate: string | null;
  session: string | null;
  alerts: Alert[];
  /** Newest alert received live on this page (not from the initial history load). */
  latestAlert: Alert | null;
}

export function isPriority(a: Alert): boolean {
  const list = currentWatchlist();
  return list.length === 0 || a.symbol == null || list.includes(a.symbol) || a.kind === "test" || a.kind === "auth";
}

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://127.0.0.1:8000/ws/quotes";
/** REST backup: poll this often while the stream is silent, and start after this long without stream quotes. */
const POLL_MS = 2000;
const STALE_MS = 8000;

/** Backend → browser quote stream. The backend owns the single upstream poller. */
export function useQuoteStream(): StreamState {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [latestAlert, setLatestAlert] = useState<Alert | null>(null);
  const [state, setState] = useState<Omit<StreamState, "alerts" | "latestAlert">>({
    quotes: {},
    status: "connecting",
    error: null,
    lastUpdate: null,
    session: null,
  });
  const retry = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let poll: ReturnType<typeof setInterval> | undefined;
    let closed = false;
    let lastStreamQuotes = 0;
    let lastPollOk = 0;
    let lastSession: string | null = null;

    // The REST call goes through the site's own /api proxy, so it reaches the right backend
    // even if the WebSocket address is wrong or the stream is blocked.
    const pollOnce = () => {
      if (closed || document.hidden) return;
      // Market closed: prices barely move, so check every 30 s instead.
      if (lastSession && lastSession !== "open" && Date.now() - lastPollOk < 30_000) return;
      fetch("/api/quotes", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((body) => {
          if (!body || closed || Date.now() - lastStreamQuotes < STALE_MS) return;
          const next: Record<string, Quote> = {};
          for (const q of [...body.quotes, ...body.indices] as Quote[]) if (q.last_price != null) next[q.symbol] = q;
          if (!Object.keys(next).length) return;
          lastPollOk = Date.now();
          lastSession = body.session;
          setState({ quotes: next, status: "polling", error: null, lastUpdate: body.server_time, session: body.session });
        })
        .catch(() => {});
    };
    const startPolling = () => {
      if (poll || closed) return;
      pollOnce();
      poll = setInterval(pollOnce, POLL_MS);
    };
    const stopPolling = () => {
      clearInterval(poll);
      poll = undefined;
    };
    // Watchdog: no stream quotes for a while (wrong address, upstream error, silent socket) → use the backup.
    const watchdog = setInterval(() => {
      if (Date.now() - lastStreamQuotes > STALE_MS) startPolling();
    }, STALE_MS / 2);

    const connect = async () => {
      // With a site login the stream needs a fresh short-lived token on every (re)connect.
      const token = await wsToken();
      if (closed) return;
      ws = new WebSocket(token ? `${WS_URL}?token=${encodeURIComponent(token)}` : WS_URL);
      ws.onopen = () => {
        retry.current = 0;
        setState((s) => ({ ...s, status: poll ? s.status : "open" }));
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "quotes") {
          const next: Record<string, Quote> = {};
          for (const q of msg.quotes as Quote[]) next[q.symbol] = q;
          lastStreamQuotes = Date.now();
          stopPolling();
          setState({ quotes: next, status: "open", error: null, lastUpdate: msg.server_time, session: msg.session });
        } else if (msg.type === "alert") {
          const a = msg.alert as Alert;
          setAlerts((list) => (list.some((x) => x.id === a.id) ? list : [a, ...list].slice(0, 100)));
          // Watchlist first: only its stocks (and system/test alerts) pop up and make a sound;
          // everything else just goes into the bell. With an empty watchlist, everything pops up.
          if (isPriority(a)) {
            setLatestAlert(a);
            showNotification(a);
          }
        } else if (msg.type === "error") {
          startPolling();
          // Only show the error if the backup is not delivering prices either.
          if (Date.now() - lastPollOk > STALE_MS) setState((s) => ({ ...s, error: msg.message }));
        }
      };
      ws.onclose = () => {
        if (closed) return;
        const delay = Math.min(30_000, 1000 * 2 ** retry.current++);
        startPolling();
        setState((s) => (s.status === "polling" ? s : { ...s, status: "reconnecting" }));
        timer = setTimeout(connect, delay);
      };
      ws.onerror = () => {
        startPolling();
        setState((s) => (s.status === "polling" ? s : { ...s, status: "error" }));
      };
    };

    // REST snapshot so the page has prices even before (or without) the first stream message.
    fetch("/api/quotes", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (!body || closed) return;
        const next: Record<string, Quote> = {};
        for (const q of [...body.quotes, ...body.indices] as Quote[]) if (q.last_price != null) next[q.symbol] = q;
        setState((s) => (s.lastUpdate ? s : { ...s, quotes: next, lastUpdate: body.server_time, session: body.session }));
      })
      .catch(() => {});

    fetch("/api/alerts?limit=50", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (!body || closed) return;
        // Merge with anything that arrived live first, newest first, without duplicates.
        setAlerts((list) => {
          const ids = new Set(list.map((a) => a.id));
          return [...list, ...(body.alerts as Alert[]).filter((a) => !ids.has(a.id))].sort((a, b) => b.id - a.id).slice(0, 100);
        });
      })
      .catch(() => {});

    void connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      clearInterval(watchdog);
      stopPolling();
      ws?.close();
    };
  }, []);

  return { ...state, alerts, latestAlert };
}
