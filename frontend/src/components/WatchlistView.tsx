"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BellRing, Calculator, Plus, Search, Send, Smartphone, Star, X } from "lucide-react";
import { useHealth, useSearch, useWatchCalls } from "@/lib/api";
import { deviceId } from "@/lib/device";
import { changeColor, fmtPct, fmtPrice, fmtTime } from "@/lib/format";
import { useNotifyPreference } from "@/lib/notify";
import { usePush } from "@/lib/push";
import type { Quote, WatchCall } from "@/lib/types";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { useWatchlist } from "@/lib/watchlist";
import { AppHeader } from "./AppHeader";
import { Disclaimer } from "./Disclaimer";
import { StatusNotices } from "./StatusNotices";
import { TradeCalculator, type TradeSetup } from "./TradeCalculator";
import { Panel } from "./ui";

const TONE = {
  bull: "bg-bull text-bg",
  bear: "bg-bear text-bg",
  warn: "bg-warn text-bg",
  muted: "bg-muted/15 text-muted",
} as const;

const SUGGESTED = ["NIFTY 50", "BANK NIFTY", "RELIANCE", "HDFCBANK", "TCS", "INFY"];
const slug = (s: string) => s.replace(/\s+/g, "");

function useDevice() {
  const [id, setId] = useState("");
  // eslint-disable-next-line react-hooks/set-state-in-effect -- device id lives in localStorage (browser only)
  useEffect(() => setId(deviceId()), []);
  return id;
}

function AddStock({ onAdd, has }: { onAdd: (s: string) => void; has: (s: string) => boolean }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);
  const { data, isLoading } = useSearch(debounced);
  const results = debounced ? (data?.results ?? []) : [];
  return (
    <div className="relative">
      <label className="flex items-center gap-2 rounded-xl border border-line bg-panel px-3 py-2.5 focus-within:border-accent">
        <Search className="size-4 text-faint" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Add any NSE stock or index — e.g. TATAMOTORS, Zomato, Bank Nifty"
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-faint"
        />
        {q && (
          <button onClick={() => setQ("")} aria-label="Clear search" className="text-faint hover:text-ink">
            <X className="size-4" />
          </button>
        )}
      </label>
      {debounced && (
        <ul className="absolute z-20 mt-1 max-h-80 w-full overflow-y-auto rounded-xl border border-line bg-panel shadow-2xl shadow-black/50">
          {results.length === 0 ? (
            <li className="px-4 py-3 text-sm text-faint">{isLoading ? "Searching…" : "No matching NSE stock."}</li>
          ) : (
            results.map((r) => (
              <li key={r.symbol}>
                <button
                  disabled={has(r.symbol)}
                  onClick={() => {
                    onAdd(r.symbol);
                    setQ("");
                  }}
                  className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-panel-2 disabled:opacity-50"
                >
                  <span className="min-w-0">
                    <span className="font-semibold">{r.symbol}</span>
                    <span className="ml-2 truncate text-xs text-muted">{r.name}</span>
                  </span>
                  <span className="shrink-0 text-xs text-accent">{has(r.symbol) ? "Added" : <Plus className="size-4" />}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

function NotifySettings({ device }: { device: string }) {
  const notify = useNotifyPreference();
  const push = usePush();
  const health = useHealth();
  const [pushMsg, setPushMsg] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [tgMsg, setTgMsg] = useState<string | null>(null);
  const telegramReady = health.data?.alerts.telegram;

  const linkTelegram = async () => {
    const res = await fetch(`/api/watchlist/${device}/owner?key=${encodeURIComponent(key)}`, { method: "PUT" });
    setTgMsg(res.ok ? "Done: this device's watchlist now decides what goes to your Telegram." : res.status === 403 ? "Wrong owner key." : `Failed (HTTP ${res.status}).`);
  };

  return (
    <Panel title={<span className="inline-flex items-center gap-2"><BellRing className="size-4 text-accent" /> Notifications for these stocks</span>}>
      <ul className="space-y-3 text-sm">
        <li className="flex flex-wrap items-center justify-between gap-2">
          <span>
            <b>Pop-up + sound</b> <span className="text-xs text-muted">while this site is open</span>
          </span>
          {notify.enabled ? (
            <button onClick={notify.disable} className="rounded-md px-3 py-1 text-xs text-muted ring-1 ring-line">On · turn off</button>
          ) : (
            <button onClick={notify.enable} className="rounded-md bg-accent px-3 py-1 text-xs font-semibold text-bg">Turn on</button>
          )}
        </li>
        <li>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5">
              <Smartphone className="size-4 text-muted" /> <b>Phone / desktop notifications</b>
              <span className="text-xs text-muted">even when the site is closed</span>
            </span>
            {push.enabled ? (
              <button onClick={push.disable} className="rounded-md px-3 py-1 text-xs text-muted ring-1 ring-line">On · turn off</button>
            ) : (
              <button onClick={async () => setPushMsg(await push.enable())} className="rounded-md bg-accent px-3 py-1 text-xs font-semibold text-bg">Turn on</button>
            )}
          </div>
          {(pushMsg || push.needsHomeScreen) && !push.enabled && (
            <p className="mt-1 text-xs text-warn">{pushMsg ?? "On iPhone, first tap Share → Add to Home Screen and open AI Stock from there."}</p>
          )}
        </li>
        <li>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5">
              <Send className="size-4 text-muted" /> <b>Telegram</b> <span className="text-xs text-muted">owner only</span>
            </span>
            {!telegramReady && <span className="text-xs text-faint">not set up on the server</span>}
          </div>
          {telegramReady && (
            <div className="mt-1.5 flex flex-wrap gap-2">
              <input
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="Owner key"
                className="min-w-0 flex-1 rounded-md border border-line bg-panel-2 px-2 py-1 text-xs outline-none focus:border-accent"
              />
              <button onClick={linkTelegram} disabled={!key} className="rounded-md bg-accent px-3 py-1 text-xs font-semibold text-bg disabled:opacity-50">
                Use this list for Telegram
              </button>
            </div>
          )}
          {tgMsg && <p className="mt-1 text-xs text-muted">{tgMsg}</p>}
        </li>
      </ul>
      <p className="mt-3 text-[11px] text-faint">
        You get a notification when a stock here turns <b className="text-bull">BUY</b> or <b className="text-bear">SELL</b> (5-minute entry confirmed by the
        15-minute trend), and again when it&apos;s time to <b>exit</b>: target reached, stop-loss hit, or the signal ended. Market hours only.
      </p>
    </Panel>
  );
}

function setupFor(c: WatchCall): TradeSetup | null {
  const p = c.position;
  if (p && p.status === "open") return { symbol: c.symbol, side: p.side, entry: p.entry, stop: p.stop, target: p.target, source: "the open call" };
  const lv = c.levels;
  const price = c.price ?? 0;
  if (lv && (c.verdict === "BUY" || c.verdict === "SELL"))
    return { symbol: c.symbol, side: c.verdict, entry: (lv.entry_low + lv.entry_high) / 2, stop: lv.stop, target: lv.target, source: "the current call" };
  if (price && c.atr) {
    return { symbol: c.symbol, side: "BUY", entry: price, stop: +(price - 1.5 * c.atr).toFixed(2), target: +(price + 2.5 * c.atr).toFixed(2), source: "the price ± 5-min ATR (no active call)" };
  }
  return null;
}

function CallRow({ c, quote, onRemove }: { c: WatchCall; quote: Quote | null; onRemove: () => void }) {
  const [calc, setCalc] = useState(false);
  const price = quote?.last_price ?? c.price ?? null;
  const a = c.action;
  const p = c.position;
  const setup = setupFor(c);
  return (
    <li className="rounded-xl border border-line bg-panel p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/stock/${slug(c.symbol)}?tf=5m`} className="text-base font-semibold hover:text-accent">
            {c.symbol}
          </Link>
          <p className="tabular text-sm">
            {fmtPrice(price)} <span className={changeColor(quote?.change_pct)}>{fmtPct(quote?.change_pct)}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          {a && <span className={`rounded-lg px-3 py-1.5 text-sm font-bold ${TONE[a.tone]}`}>{a.label}</span>}
          <button onClick={onRemove} aria-label={`Remove ${c.symbol}`} className="text-faint hover:text-bear">
            <X className="size-4" />
          </button>
        </div>
      </div>
      {c.error ? (
        <p className="mt-2 text-xs text-bear">{c.error}</p>
      ) : (
        <>
          {a && <p className="mt-2 text-sm text-muted">{a.text}</p>}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
            {c.entry && <span>5-min: {c.entry.up_pct != null ? `${c.entry.up_pct.toFixed(0)}% up` : `score ${c.entry.score ?? "—"}`}</span>}
            {c.trend && <span>15-min: {c.trend.up_pct != null ? `${c.trend.up_pct.toFixed(0)}% up` : `score ${c.trend.score ?? "—"}`}</span>}
            {p && <span>Call since {fmtTime(p.opened)} at {fmtPrice(p.entry)}</span>}
            {p?.status === "open" && (
              <span>
                Target <b className="text-bull">{fmtPrice(p.target)}</b> · Stop <b className="text-bear">{fmtPrice(p.stop)}</b>
              </span>
            )}
          </div>
          {setup && (
            <button onClick={() => setCalc((v) => !v)} className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-accent hover:underline">
              <Calculator className="size-3.5" /> {calc ? "Hide calculator" : "Calculator"}
            </button>
          )}
          {calc && setup && (
            <div className="mt-2">
              <TradeCalculator setup={setup} onClose={() => setCalc(false)} />
            </div>
          )}
        </>
      )}
    </li>
  );
}

export function WatchlistView() {
  const health = useHealth();
  const stream = useQuoteStream();
  const watch = useWatchlist();
  const device = useDevice();
  const { data, error } = useWatchCalls(device, watch.list);
  const rows = data?.rows ?? [];
  const bySymbol = new Map(rows.map((r) => [r.symbol, r]));

  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1100px] space-y-5 px-4 py-5 sm:px-6">
        <StatusNotices health={health.data} healthError={health.error} streamError={stream.error} />
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <Star className="size-5 fill-warn text-warn" /> My watchlist
          </h1>
          <p className="text-xs text-muted">Your stocks come first: they get the BUY / SELL / exit notifications on this device.</p>
        </div>

        <AddStock onAdd={watch.add} has={watch.has} />

        {watch.list.length === 0 ? (
          <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center">
            <p className="text-sm text-muted">Your watchlist is empty. Add stocks above, or start with:</p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {SUGGESTED.map((s) => (
                <button key={s} onClick={() => watch.add(s)} className="rounded-full px-3 py-1 text-xs ring-1 ring-line hover:ring-accent">
                  + {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ul className="space-y-3">
            {watch.list.map((sym) => {
              const c = bySymbol.get(sym);
              return c ? (
                <CallRow key={sym} c={c} quote={stream.quotes[sym] ?? (c.quote as Quote | null) ?? null} onRemove={() => watch.remove(sym)} />
              ) : (
                <li key={sym} className="flex items-center justify-between rounded-xl border border-line bg-panel p-3 text-sm">
                  <span className="font-semibold">{sym}</span>
                  <span className="text-xs text-faint">{error ? error.message : "Loading call…"}</span>
                </li>
              );
            })}
          </ul>
        )}
        {data && <p className="text-[11px] text-faint">Updated {fmtTime(data.generated_at)} IST · refreshes every 20 s</p>}

        {device && <NotifySettings device={device} />}
      </main>
      <Disclaimer />
    </>
  );
}
