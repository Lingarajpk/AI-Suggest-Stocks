"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Search, Star } from "lucide-react";
import { changeColor, fmtNum, fmtPct, fmtPrice } from "@/lib/format";
import type { Quote, ScanRow, SignalLabel } from "@/lib/types";
import { FreshnessBadge, ScoreBar, SignalBadge } from "./ui";

const SIGNALS: (SignalLabel | "All")[] = ["All", "Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish"];

export function ScannerTable({
  rows,
  quotes,
  watchlist,
  onToggleWatch,
}: {
  rows: ScanRow[];
  quotes: Record<string, Quote>;
  watchlist: string[];
  onToggleWatch: (s: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("All");
  const [signal, setSignal] = useState<(typeof SIGNALS)[number]>("All");
  const [tradeableOnly, setTradeableOnly] = useState(false);
  const [watchOnly, setWatchOnly] = useState(false);

  const sectors = useMemo(() => ["All", ...Array.from(new Set(rows.map((r) => r.instrument.sector ?? "Other"))).sort()], [rows]);

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    return rows
      .filter((r) => !q || r.instrument.symbol.includes(q) || r.instrument.name.toUpperCase().includes(q))
      .filter((r) => sector === "All" || (r.instrument.sector ?? "Other") === sector)
      .filter((r) => signal === "All" || r.analysis?.signal.signal === signal)
      .filter((r) => !tradeableOnly || r.analysis?.signal.status === "ok")
      .filter((r) => !watchOnly || watchlist.includes(r.instrument.symbol))
      .sort((a, b) => (b.analysis?.signal.score ?? -999) - (a.analysis?.signal.score ?? -999));
  }, [rows, query, sector, signal, tradeableOnly, watchOnly, watchlist]);

  const select = "rounded-md border border-line bg-panel-2 px-2 py-1.5 text-xs text-ink outline-none focus:border-accent";

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 rounded-md border border-line bg-panel-2 px-2 py-1.5 focus-within:border-accent">
          <Search className="size-3.5 text-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search symbol or name"
            className="w-40 bg-transparent text-xs outline-none placeholder:text-faint"
          />
        </label>
        <select value={sector} onChange={(e) => setSector(e.target.value)} className={select} aria-label="Sector">
          {sectors.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <select value={signal} onChange={(e) => setSignal(e.target.value as typeof signal)} className={select} aria-label="Signal">
          {SIGNALS.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={tradeableOnly} onChange={(e) => setTradeableOnly(e.target.checked)} className="accent-accent" />
          Hide no-trade
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={watchOnly} onChange={(e) => setWatchOnly(e.target.checked)} className="accent-accent" />
          Watchlist only
        </label>
        <span className="ml-auto text-xs text-faint">
          {filtered.length} of {rows.length}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="w-8 py-2" />
              <th className="py-2 font-medium">Symbol</th>
              <th className="py-2 font-medium">Sector</th>
              <th className="py-2 text-right font-medium">Price</th>
              <th className="py-2 text-right font-medium">Change</th>
              <th className="py-2 pl-6 font-medium">Signal</th>
              <th className="py-2 font-medium">Score</th>
              <th className="py-2 text-right font-medium">RSI</th>
              <th className="py-2 text-right font-medium">ADX</th>
              <th className="py-2 text-right font-medium">Rel vol</th>
              <th className="py-2 pl-4 font-medium">Quote</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => {
              const sym = r.instrument.symbol;
              const q = quotes[sym] ?? r.quote;
              const snap = r.analysis?.snapshot ?? {};
              return (
                <tr key={sym} className="border-b border-line/60 hover:bg-panel-2/60">
                  <td className="py-2">
                    <button onClick={() => onToggleWatch(sym)} aria-label={`Toggle ${sym} in watchlist`} className="text-faint hover:text-warn">
                      <Star className={`size-3.5 ${watchlist.includes(sym) ? "fill-warn text-warn" : ""}`} />
                    </button>
                  </td>
                  <td className="py-2">
                    <Link href={`/stock/${sym}`} className="font-medium hover:text-accent">
                      {sym}
                    </Link>
                    <p className="max-w-[200px] truncate text-xs text-faint">{r.instrument.name}</p>
                  </td>
                  <td className="py-2 text-xs text-muted">{r.instrument.sector ?? "—"}</td>
                  <td className="tabular py-2 text-right">{fmtPrice(q?.last_price ?? r.analysis?.last_close)}</td>
                  <td className={`tabular py-2 text-right ${changeColor(q?.change_pct)}`}>{fmtPct(q?.change_pct)}</td>
                  <td className="py-2 pl-6">
                    {r.error ? <span className="text-xs text-bear" title={r.error}>Error</span> : <SignalBadge signal={r.analysis?.signal} />}
                  </td>
                  <td className="py-2">
                    <ScoreBar score={r.analysis?.signal.score} />
                  </td>
                  <td className="tabular py-2 text-right text-xs">{fmtNum(snap.rsi14)}</td>
                  <td className="tabular py-2 text-right text-xs">{fmtNum(snap.adx14)}</td>
                  <td className="tabular py-2 text-right text-xs">{snap.rel_volume == null ? "—" : `${snap.rel_volume.toFixed(2)}x`}</td>
                  <td className="py-2 pl-4">
                    <FreshnessBadge freshness={q?.freshness} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!filtered.length && <p className="py-8 text-center text-sm text-faint">No instruments match these filters.</p>}
      </div>
    </div>
  );
}
