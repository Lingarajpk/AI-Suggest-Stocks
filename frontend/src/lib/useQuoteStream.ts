"use client";

import { useEffect, useRef, useState } from "react";
import { showNotification } from "./notify";
import type { Alert, Quote } from "./types";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "error";

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

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/quotes";

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
    let closed = false;

    const connect = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        retry.current = 0;
        setState((s) => ({ ...s, status: "open" }));
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "quotes") {
          const next: Record<string, Quote> = {};
          for (const q of msg.quotes as Quote[]) next[q.symbol] = q;
          setState({ quotes: next, status: "open", error: null, lastUpdate: msg.server_time, session: msg.session });
        } else if (msg.type === "alert") {
          const a = msg.alert as Alert;
          setAlerts((list) => (list.some((x) => x.id === a.id) ? list : [a, ...list].slice(0, 100)));
          setLatestAlert(a);
          showNotification(a);
        } else if (msg.type === "error") {
          setState((s) => ({ ...s, error: msg.message }));
        }
      };
      ws.onclose = () => {
        if (closed) return;
        const delay = Math.min(30_000, 1000 * 2 ** retry.current++);
        setState((s) => ({ ...s, status: "reconnecting" }));
        timer = setTimeout(connect, delay);
      };
      ws.onerror = () => setState((s) => ({ ...s, status: "error" }));
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

    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return { ...state, alerts, latestAlert };
}
