"use client";

import { useState, type ReactNode } from "react";
import { Info } from "lucide-react";
import type { Freshness, Signal, SignalLabel } from "@/lib/types";

/** Card: white (light) / raised (dark) surface with a quiet title row. */
export function Panel({ title, action, children, className = "" }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-line bg-panel shadow-[0_1px_2px_rgba(16,24,40,0.04)] ${className}`}>
      {title && (
        <header className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4 pb-1">
          <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
          {action}
        </header>
      )}
      <div className="px-5 pt-3 pb-5">{children}</div>
    </section>
  );
}

/** "How this works" text kept out of the way until asked for. */
export function HowItWorks({ children, label = "How this works" }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-3 text-xs">
      <button onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1 text-faint hover:text-accent" aria-expanded={open}>
        <Info className="size-3.5" /> {open ? "Hide details" : label}
      </button>
      {open && <div className="mt-1.5 leading-relaxed text-muted">{children}</div>}
    </div>
  );
}

const SIGNAL_STYLE: Record<SignalLabel, string> = {
  "Strong Bullish": "bg-bull/20 text-bull ring-bull/40",
  Bullish: "bg-bull/10 text-bull ring-bull/25",
  Neutral: "bg-muted/10 text-muted ring-muted/25",
  Bearish: "bg-bear/10 text-bear ring-bear/25",
  "Strong Bearish": "bg-bear/20 text-bear ring-bear/40",
};

export function SignalBadge({ signal }: { signal: Signal | null | undefined }) {
  if (!signal || signal.signal == null) {
    return <span className="rounded-md px-2 py-0.5 text-xs font-medium text-faint ring-1 ring-line">Unavailable</span>;
  }
  if (signal.status === "no_trade") {
    return (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap" title={signal.no_trade_reasons.join("\n")}>
        <span className="rounded-md bg-warn/10 px-2 py-0.5 text-xs font-semibold text-warn ring-1 ring-warn/30">NO TRADE</span>
        <span className="text-xs text-faint">{signal.signal}</span>
      </span>
    );
  }
  return (
    <span className={`whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ${SIGNAL_STYLE[signal.signal]}`}>{signal.signal}</span>
  );
}

const FRESHNESS: Record<Freshness, { label: string; cls: string; hint: string }> = {
  polled: { label: "Polled", cls: "text-bull", hint: "Fetched from the Upstox REST quote API during market hours" },
  stale: { label: "Stale", cls: "text-warn", hint: "Market open but no recent trade in this quote" },
  market_closed: { label: "Last close", cls: "text-muted", hint: "Market is closed; showing the last available price" },
  unavailable: { label: "Unavailable", cls: "text-bear", hint: "No quote returned for this instrument" },
  demo: { label: "Demo", cls: "text-warn", hint: "Synthetic demo data — not market data" },
};

export function FreshnessBadge({ freshness }: { freshness: Freshness | null | undefined }) {
  const f = FRESHNESS[freshness ?? "unavailable"];
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-medium uppercase tracking-wider ${f.cls}`} title={f.hint}>
      <span className="size-1.5 rounded-full bg-current" />
      {f.label}
    </span>
  );
}

export function ScoreBar({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="text-faint">—</span>;
  const pct = Math.min(100, Math.abs(score)) / 2;
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-1.5 w-20 rounded-full bg-line">
        <div className="absolute top-0 left-1/2 h-full w-px bg-faint" />
        <div
          className={`absolute top-0 h-full rounded-full ${score >= 0 ? "bg-bull" : "bg-bear"}`}
          style={score >= 0 ? { left: "50%", width: `${pct}%` } : { right: "50%", width: `${pct}%` }}
        />
      </div>
      <span className={`tabular w-10 text-right text-xs ${score > 0 ? "text-bull" : score < 0 ? "text-bear" : "text-muted"}`}>
        {score > 0 ? "+" : ""}
        {score.toFixed(0)}
      </span>
    </div>
  );
}

export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-8 animate-pulse rounded bg-panel-2" />
      ))}
    </div>
  );
}

const EVIDENCE: Record<string, { label: string; cls: string }> = {
  proven: { label: "Proven", cls: "bg-bull/10 text-bull ring-bull/30" },
  promising: { label: "Promising", cls: "bg-accent/10 text-accent ring-accent/30" },
  weak: { label: "Weak", cls: "bg-bear/10 text-bear ring-bear/30" },
  insufficient: { label: "Too few trades", cls: "bg-muted/10 text-muted ring-line" },
};

/** How far a backtest can be trusted: Wilson lower bound of the win rate vs break-even, and sample size. */
export function EvidenceBadge({ evidence }: { evidence?: { grade: string; text: string } | null }) {
  if (!evidence) return null;
  const e = EVIDENCE[evidence.grade] ?? EVIDENCE.insufficient;
  return (
    <span title={evidence.text} className={`whitespace-nowrap rounded-md px-1.5 py-0.5 text-[10px] font-semibold ring-1 ${e.cls}`}>
      {e.label}
    </span>
  );
}

/** Data problems found in the candles (missing bars, broken prices, unadjusted splits). */
export function DataQualityNote({ quality }: { quality?: { ok: boolean; issues: { level: string; text: string }[] } | null }) {
  if (!quality || !quality.issues.length) return null;
  return (
    <div className={`rounded-xl px-3 py-2 text-xs ${quality.ok ? "bg-warn/10 text-warn" : "bg-bear/10 text-bear"}`}>
      <p className="font-semibold">{quality.ok ? "Data warning" : "Data problem: no call until it clears"}</p>
      <ul className="mt-0.5 list-disc pl-4">
        {quality.issues.map((i) => (
          <li key={i.text}>{i.text}</li>
        ))}
      </ul>
    </div>
  );
}
