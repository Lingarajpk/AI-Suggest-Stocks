"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, BellOff, BellRing, X } from "lucide-react";
import { fmtTime } from "@/lib/format";
import { useNotifyPreference } from "@/lib/notify";
import type { Alert, Health } from "@/lib/types";

const DIR_STYLE = { up: "border-bull/40 text-bull", down: "border-bear/40 text-bear", system: "border-warn/40 text-warn" };

function AlertItem({ a }: { a: Alert }) {
  const body = (
    <>
      <div className="flex items-baseline justify-between gap-2">
        <span className={`text-sm font-semibold ${DIR_STYLE[a.direction].split(" ")[1]}`}>{a.title}</span>
        <span className="shrink-0 text-[11px] text-faint">{fmtTime(a.time)}</span>
      </div>
      <p className="mt-0.5 text-xs leading-relaxed text-muted">{a.message}</p>
    </>
  );
  return (
    <li className={`border-l-2 py-2 pl-3 ${DIR_STYLE[a.direction].split(" ")[0]}`}>
      {a.symbol ? (
        <Link href={`/stock/${a.symbol}`} className="block hover:opacity-80">
          {body}
        </Link>
      ) : (
        body
      )}
    </li>
  );
}

export function AlertsBell({ alerts, health }: { alerts: Alert[]; health?: Health }) {
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(0);
  const notify = useNotifyPreference();
  const ref = useRef<HTMLDivElement>(null);
  const unread = alerts.filter((a) => a.id > seen).length;

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const cfg = health?.alerts;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => {
          setOpen((o) => !o);
          setSeen(alerts[0]?.id ?? 0);
        }}
        className="relative inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-ink ring-1 ring-line hover:ring-accent/50"
        aria-label="Alerts"
      >
        {notify.enabled ? <BellRing className="size-3.5 text-accent" /> : <Bell className="size-3.5" />}
        Alerts
        {unread > 0 && (
          <span className="absolute -top-1.5 -right-1.5 grid min-w-5 place-items-center rounded-full bg-bear px-1 text-[10px] font-bold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-30 mt-2 w-[min(420px,calc(100vw-2rem))] rounded-xl border border-line bg-panel shadow-2xl shadow-black/50">
          <div className="border-b border-line px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-semibold">Automatic alerts</p>
              {!notify.supported ? (
                <span className="text-xs text-faint">Notifications not supported</span>
              ) : notify.enabled ? (
                <button onClick={notify.disable} className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
                  <BellOff className="size-3.5" /> Turn off pop-ups
                </button>
              ) : (
                <button onClick={notify.enable} className="rounded-md bg-accent px-2.5 py-1 text-xs font-semibold text-bg hover:bg-accent/90">
                  Turn on pop-ups &amp; sound
                </button>
              )}
            </div>
            {notify.denied && <p className="mt-1 text-xs text-warn">Notifications are blocked for this site. Allow them in your browser&apos;s site settings.</p>}
            {cfg && (
              <p className="mt-1.5 text-[11px] leading-relaxed text-faint">
                Market hours only: new BUY/SELL signals ({cfg.signal_timeframe}), day moves of {cfg.move_levels.map((l) => `±${l}%`).join(" / ")}, and moves of ±
                {cfg.fast_move_pct}% within {cfg.fast_window_minutes} min. Telegram: {cfg.telegram ? "on" : "not set up"}.
              </p>
            )}
          </div>
          <ul className="max-h-[60vh] space-y-1 overflow-y-auto px-4 py-2">
            {alerts.length ? alerts.map((a) => <AlertItem key={a.id} a={a} />) : <li className="py-6 text-center text-sm text-faint">No alerts yet today.</li>}
          </ul>
          <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-2">
            <p className="text-[11px] text-faint">Price moves and technical signals. Not investment advice.</p>
            <button
              onClick={() => fetch("/api/alerts/test", { method: "POST" })}
              className="shrink-0 rounded-md px-2 py-1 text-[11px] text-accent ring-1 ring-accent/30 hover:bg-accent/10"
            >
              Send test alert
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** In-page pop-up for each live alert, shown even when desktop notifications are off. */
export function AlertToast({ alert }: { alert: Alert | null }) {
  const [hiddenId, setHiddenId] = useState<number | null>(null);
  const visible = alert && alert.id !== hiddenId ? alert : null;

  useEffect(() => {
    if (!alert) return;
    const t = setTimeout(() => setHiddenId(alert.id), 10_000);
    return () => clearTimeout(t);
  }, [alert]);

  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-40 w-[min(380px,calc(100vw-2rem))]" style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}>
      <AnimatePresence>
        {visible && (
          <motion.div
            key={visible.id}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            className={`pointer-events-auto rounded-xl border bg-panel p-4 shadow-2xl shadow-black/50 ${DIR_STYLE[visible.direction].split(" ")[0]}`}
          >
            <div className="flex items-start justify-between gap-3">
              <ul className="min-w-0 flex-1">
                <AlertItem a={visible} />
              </ul>
              <button onClick={() => setHiddenId(visible.id)} aria-label="Dismiss" className="text-faint hover:text-ink">
                <X className="size-4" />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
