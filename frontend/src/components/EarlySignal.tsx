"use client";

import { Check, X, Zap } from "lucide-react";
import { fmtPrice, fmtTime } from "@/lib/format";
import type { EarlySignal as Early, EarlyToday } from "@/lib/types";

/** Early signal on the forming 5-minute candle, next to the confirmed call. */
export function EarlySignal({ e, compact = false }: { e?: Early | null; compact?: boolean }) {
  if (!e) return null;
  const buy = e.side === "BUY";
  const tone = buy ? "text-bull" : "text-bear";
  const bg = buy ? "bg-bull/10 border-bull/30" : "bg-bear/10 border-bear/30";
  const closeAt = fmtTime(e.confirms_at);

  if (compact) {
    const label = e.status === "live" || e.status === "confirming" ? `⚡ Early ${e.side}` : e.status === "confirmed" ? `✔ Early ${e.side} confirmed` : `✖ Early ${e.side} cancelled`;
    return (
      <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-bold ${e.status === "cancelled" ? "bg-panel-2 text-muted line-through" : `${buy ? "bg-bull/15" : "bg-bear/15"} ${tone}`}`}
        title={`${label} at ${fmtPrice(e.price)} (${fmtTime(e.since)}); confirms at the ${closeAt} close`}>
        {label}
      </span>
    );
  }

  if (e.status === "confirmed" || e.status === "cancelled") {
    const ok = e.status === "confirmed";
    return (
      <p className={`mt-3 flex items-start gap-2 rounded-xl px-3.5 py-2.5 text-sm ${ok ? "bg-bull/10 text-bull" : "bg-panel-2 text-muted"}`}>
        {ok ? <Check className="mt-0.5 size-4 shrink-0" /> : <X className="mt-0.5 size-4 shrink-0" />}
        <span>
          <strong className={ok ? "" : "line-through"}>Early {e.side}</strong> at {fmtPrice(e.price)} ({fmtTime(e.since)}){" "}
          {ok
            ? `was confirmed at the ${closeAt} close${e.edge_pct != null ? `: entry ${e.edge_pct >= 0 ? `${e.edge_pct.toFixed(2)}% better` : `${(-e.edge_pct).toFixed(2)}% worse`} than waiting` : ""}.`
            : `was cancelled at the ${closeAt} close: the candle didn't confirm it. Stay out, or exit if you entered early.`}
        </span>
      </p>
    );
  }

  const lv = e.levels;
  return (
    <div className={`mt-3 rounded-xl border px-3.5 py-2.5 ${bg}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="relative flex size-2.5">
          {e.status === "live" && e.active !== false && <span className={`absolute inline-flex size-full animate-ping rounded-full opacity-60 ${buy ? "bg-bull" : "bg-bear"}`} />}
          <span className={`relative inline-flex size-2.5 rounded-full ${buy ? "bg-bull" : "bg-bear"}`} />
        </span>
        <Zap className={`size-4 ${tone}`} />
        <strong className={`text-sm ${tone}`}>EARLY {e.side}</strong>
        <span className="text-xs text-muted">not confirmed yet</span>
        <span className="ml-auto text-xs text-muted">
          {e.status === "confirming" ? "candle closed: checking…" : `confirms at ${closeAt} close`}
        </span>
      </div>
      <p className="mt-1 text-xs text-muted">
        Since {fmtTime(e.since)} at <span className="tabular font-semibold text-ink">{fmtPrice(e.price)}</span>: the forming 5-min candle already lines up
        with the 15-min trend.
        {e.status === "live" && e.active === false && <span className="font-semibold text-warn"> It has faded since: likely to cancel.</span>}
      </p>
      {lv && (
        <p className="tabular mt-1 text-xs">
          <span className="text-muted">Entry</span> {fmtPrice(lv.entry)} · <span className="text-bull">Target {fmtPrice(lv.target)}</span> ·{" "}
          <span className="text-bear">Stop {fmtPrice(lv.stop)}</span>
        </p>
      )}
    </div>
  );
}

/** Today's early signals: how often the close confirmed them and how much earlier / better they were. */
export function EarlyTodayLine({ t }: { t?: EarlyToday }) {
  if (!t || !t.fired) return null;
  const parts = [`${t.fired} early signal${t.fired === 1 ? "" : "s"} today`, `${t.confirmed} confirmed, ${t.cancelled} cancelled${t.open ? `, ${t.open} open` : ""}`];
  if (t.avg_minutes_earlier != null) parts.push(`${t.avg_minutes_earlier.toFixed(1)} min earlier on average`);
  if (t.avg_entry_edge_pct != null) parts.push(`entry ${t.avg_entry_edge_pct >= 0 ? "+" : ""}${t.avg_entry_edge_pct.toFixed(2)}% vs waiting`);
  return (
    <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
      <Zap className="size-3.5 text-warn" />
      {parts.join(" · ")}
    </p>
  );
}
