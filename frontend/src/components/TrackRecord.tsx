"use client";

import { History } from "lucide-react";
import { TIMEFRAME_LABEL, edgeVerdict } from "@/lib/api";
import { fmtDate, fmtPct } from "@/lib/format";
import type { SideStats, SignalHistory, Timeframe } from "@/lib/types";
import { Panel } from "./ui";

const TONE = { bull: "text-bull ring-bull/30", bear: "text-bear ring-bear/30", warn: "text-warn ring-warn/30", muted: "text-muted ring-line" };

export function EdgeBadge({ winRate, baseRate, count }: { winRate: number | null; baseRate: number | null; count: number }) {
  const v = edgeVerdict(winRate, baseRate, count);
  return <span className={`whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ${TONE[v.tone]}`}>{v.label}</span>;
}

function SideRow({ label, stats, baseRate, tone }: { label: string; stats: SideStats; baseRate: number | null; tone: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel-2/50 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className={`text-xs font-semibold ${tone}`}>{label}</span>
        <EdgeBadge winRate={stats.win_rate} baseRate={baseRate} count={stats.count} />
      </div>
      {stats.count ? (
        <p className="mt-1.5 text-sm">
          Right <span className="tabular font-semibold">{stats.win_rate}%</span> of {stats.count} times
          <span className="text-muted"> · random: {baseRate ?? "—"}%</span>
          <span className="block text-xs text-muted">Average move after signal {fmtPct(stats.avg_return_pct)}</span>
        </p>
      ) : (
        <p className="mt-1.5 text-sm text-faint">No completed signals in this period.</p>
      )}
    </div>
  );
}

export function TrackRecordPanel({ history, timeframe }: { history: SignalHistory; timeframe: Timeframe }) {
  const up = history.base_rate_up_pct;
  const down = up == null ? null : Math.round((100 - up) * 10) / 10;
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <History className="size-4 text-accent" /> Signal track record
        </span>
      }
    >
      <p className="mb-3 text-xs text-muted">
        How past ▲BUY / ▼SELL points on this chart worked out after {history.horizon_label} ({TIMEFRAME_LABEL[timeframe]} candles,{" "}
        {fmtDate(history.period_start)} → {fmtDate(history.period_end)}).
      </p>
      <div className="space-y-2">
        <SideRow label="▲ BUY signals (price then rose)" stats={history.buy} baseRate={up} tone="text-bull" />
        <SideRow label="▼ SELL signals (price then fell)" stats={history.sell} baseRate={down} tone="text-bear" />
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-faint">
        &ldquo;Random&rdquo; is how often price moved that way over the same period from any bar. A signal is only useful if it beats random by a clear margin over
        many cases. {history.notes}
      </p>
    </Panel>
  );
}
