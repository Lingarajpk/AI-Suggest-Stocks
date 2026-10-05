import type { ReactNode } from "react";
import type { Freshness, Signal, SignalLabel } from "@/lib/types";

export function Panel({ title, action, children, className = "" }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold tracking-wide text-ink">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
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
