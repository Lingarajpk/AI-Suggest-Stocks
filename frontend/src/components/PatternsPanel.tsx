"use client";

import Link from "next/link";
import { Shapes } from "lucide-react";
import { TIMEFRAME_LABEL, usePatternStats } from "@/lib/api";
import { fmtDate, fmtPct, fmtPrice } from "@/lib/format";
import type { PatternBlock, PatternInstance, PatternStats, PatternStatsRow, Quote, ScanRow, Timeframe } from "@/lib/types";
import { Panel } from "./ui";

const MIN_SAMPLES = 20;

export function Stars({ n }: { n: number }) {
  return (
    <span className="tabular text-[11px] text-warn" title={`Book rating ${n}/4 (Big Book of Chart Patterns)`}>
      {"★".repeat(n)}
      <span className="text-faint">{"★".repeat(4 - n)}</span>
    </span>
  );
}

function biasCls(p: PatternInstance) {
  const dir = p.direction ?? (p.bias === "bullish" ? "up" : p.bias === "bearish" ? "down" : null);
  return dir === "up" ? "text-bull ring-bull/30" : dir === "down" ? "text-bear ring-bear/30" : "text-muted ring-line";
}

function Measured({ s, label }: { s: PatternStats | undefined; label: string }) {
  if (!s || !s.resolved) return <span className="text-faint">{label}: no completed breakouts yet</span>;
  const few = s.resolved < MIN_SAMPLES;
  return (
    <span className={few ? "text-faint" : "text-muted"}>
      {label}: target reached <b className="tabular text-ink">{s.success_rate}%</b> of {s.resolved} breakouts · profitable{" "}
      {s.profitable_rate}% · avg {fmtPct(s.avg_pnl_pct)}
      {few && " (too few to judge)"}
    </span>
  );
}

function CurrentPattern({ p, quote, pooled, local }: { p: PatternInstance; quote: Quote | null; pooled?: PatternStats; local?: PatternStats }) {
  const live = quote?.last_price;
  const broke = p.status !== "forming";
  const up = p.direction === "up";
  const livePnl = broke && live != null && p.entry_price ? (up ? 1 : -1) * (live / p.entry_price - 1) * 100 : null;
  return (
    <li className="rounded-lg border border-line bg-panel-2/50 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">
          {p.name}
          {p.variant && <span className="font-normal text-muted"> · {p.variant}</span>}
        </span>
        <span className="flex items-center gap-2">
          <Stars n={p.stars} />
          <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ${biasCls(p)}`}>
            {broke ? (up ? "▲ Broke out up" : "▼ Broke down") : p.bias === "either" ? "Forming · either way" : `Forming · ${p.bias}`}
          </span>
        </span>
      </div>

      {!broke ? (
        <div className="mt-2 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
          {(["up", "down"] as const).map((d) => {
            const trig = d === "up" ? p.trigger_up : p.trigger_down;
            const proj = p.projection?.[d];
            if (trig == null || !proj) return null;
            return (
              <p key={d} className={d === "up" ? "text-bull" : "text-bear"}>
                Trigger: close {d === "up" ? "above" : "below"} <b className="tabular">{fmtPrice(trig)}</b> → target {fmtPrice(proj.target)}, stop{" "}
                {fmtPrice(proj.stop)}
              </p>
            );
          })}
          <p className="text-faint sm:col-span-2">Valid for about {p.bars_left} more candles; needs above-average volume on the breakout.</p>
        </div>
      ) : (
        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs sm:grid-cols-4">
          <dt className="text-muted">Breakout</dt>
          <dd className="tabular">
            {fmtPrice(p.breakout_level)} <span className="text-faint">{fmtDate(p.breakout_time)}</span>
          </dd>
          <dt className="text-muted">Volume</dt>
          <dd className={p.volume_confirmed ? "text-bull" : "text-warn"}>{p.volume_confirmed == null ? "n/a" : p.volume_confirmed ? "Confirmed" : "Below average"}</dd>
          <dt className="text-muted">Target</dt>
          <dd className="tabular text-bull">{fmtPrice(p.target)}</dd>
          <dt className="text-muted">Stop</dt>
          <dd className="tabular text-bear">{fmtPrice(p.stop)}</dd>
          <dt className="text-muted">Since breakout</dt>
          <dd className="tabular">{fmtPct(livePnl ?? p.pnl_pct)}</dd>
          <dt className="text-muted">Status</dt>
          <dd>{STATUS_TEXT[p.status]}</dd>
        </dl>
      )}

      <p className="mt-2 text-[11px] leading-relaxed text-faint">{p.rule}</p>
      <p className="mt-1 flex flex-col gap-0.5 text-[11px]">
        <Measured s={pooled} label="All stocks" />
        <Measured s={local} label="This stock" />
      </p>
    </li>
  );
}

const STATUS_TEXT: Record<PatternInstance["status"], string> = {
  forming: "Forming",
  breakout: "Running",
  target_hit: "Target reached",
  stopped: "Stopped out",
  expired: "Expired (no target/stop)",
  failed: "Failed before breakout",
  expired_unbroken: "Never broke out",
  target_at_breakout: "Target already reached at breakout",
};

const RESULT: Record<string, { label: string; cls: string }> = {
  target_hit: { label: "✓ Target", cls: "text-bull" },
  stopped: { label: "✗ Stop", cls: "text-bear" },
  expired: { label: "⌛ Expired", cls: "text-muted" },
};

export function PatternsPanel({
  block,
  timeframe,
  quote,
  source,
}: {
  block: PatternBlock | undefined;
  timeframe: Timeframe;
  quote: Quote | null;
  source: "upstox" | "demo";
}) {
  const stats = usePatternStats(timeframe);
  const pooled = Object.fromEntries((stats.data?.patterns ?? []).map((r) => [r.key, r]));
  const current = block?.current ?? [];
  const recent = [...(block?.recent ?? [])].reverse().slice(0, 8);
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Shapes className="size-4 text-accent" /> Chart patterns
        </span>
      }
      action={<span className="text-xs text-muted">{TIMEFRAME_LABEL[timeframe]} · core 28 from the Big Book</span>}
    >
      {current.length ? (
        <ul className="space-y-2">
          {current.map((p) => (
            <CurrentPattern
              key={`${p.key}-${p.start_time}-${p.detected_time}`}
              p={p}
              quote={quote}
              pooled={pooled[p.key]}
              local={block?.stock_stats?.[p.key]}
            />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-faint">No pattern is forming or has just broken out on these candles.</p>
      )}

      {recent.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 text-xs font-medium text-muted">Recent completed patterns on this stock</p>
          <table className="w-full text-xs">
            <tbody>
              {recent.map((p) => (
                <tr key={`${p.key}-${p.breakout_time}`} className="border-b border-line/60">
                  <td className="py-1 pr-2 text-faint">{fmtDate(p.breakout_time)}</td>
                  <td className="py-1 pr-2">
                    {p.direction === "up" ? "▲" : "▼"} {p.name}
                  </td>
                  <td className={`py-1 pr-2 ${RESULT[p.status]?.cls ?? ""}`}>{RESULT[p.status]?.label ?? p.status}</td>
                  <td className={`tabular py-1 text-right ${(p.pnl_pct ?? 0) >= 0 ? "text-bull" : "text-bear"}`}>{fmtPct(p.pnl_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[11px] text-faint">
        Patterns are detected from confirmed swing points only (no look-ahead). Results are in-sample, before costs
        {source === "demo" ? ", and measured on synthetic demo data" : ""}. A pattern is a setup, not a forecast.
      </p>
    </Panel>
  );
}

export function PatternStatsTable({ timeframe }: { timeframe: Timeframe }) {
  const { data, error, isLoading } = usePatternStats(timeframe);
  const rows: PatternStatsRow[] = [...(data?.patterns ?? [])].sort((a, b) => b.resolved - a.resolved || b.stars - a.stars);
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Shapes className="size-4 text-accent" /> Pattern track record · all stocks
        </span>
      }
      action={
        <span className="text-xs text-muted">
          {TIMEFRAME_LABEL[timeframe]} · {data ? `${data.instruments} stocks` : "…"}
        </span>
      }
    >
      {error ? (
        <p className="text-sm text-bear">{error.message}</p>
      ) : isLoading && !data ? (
        <p className="text-sm text-faint">Measuring every pattern on every stock… (first run takes a few seconds)</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-xs">
            <thead className="text-left text-muted">
              <tr className="border-b border-line">
                <th className="py-1.5 pr-2 font-medium">Pattern</th>
                <th className="py-1.5 pr-2 font-medium">Book</th>
                <th className="py-1.5 pr-2 text-right font-medium">Breakouts</th>
                <th className="py-1.5 pr-2 text-right font-medium">Target hit</th>
                <th className="py-1.5 pr-2 text-right font-medium">Profitable</th>
                <th className="py-1.5 pr-2 text-right font-medium">Avg P&amp;L</th>
                <th className="py-1.5 text-right font-medium" title="Breakouts on above-average volume only">
                  Vol-confirmed hit
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const few = r.resolved < MIN_SAMPLES;
                return (
                  <tr key={r.key} className={`border-b border-line/60 ${few ? "text-faint" : ""}`} title={r.rule}>
                    <td className="py-1 pr-2">
                      <span className={r.bias === "bullish" ? "text-bull" : r.bias === "bearish" ? "text-bear" : "text-muted"}>●</span> {r.name}
                    </td>
                    <td className="py-1 pr-2">
                      <Stars n={r.stars} />
                    </td>
                    <td className="tabular py-1 pr-2 text-right">{r.resolved}</td>
                    <td className="tabular py-1 pr-2 text-right">{r.success_rate == null ? "—" : `${r.success_rate}%`}</td>
                    <td className="tabular py-1 pr-2 text-right">{r.profitable_rate == null ? "—" : `${r.profitable_rate}%`}</td>
                    <td className={`tabular py-1 pr-2 text-right ${(r.avg_pnl_pct ?? 0) >= 0 ? "" : "text-bear"}`}>{fmtPct(r.avg_pnl_pct)}</td>
                    <td className="tabular py-1 text-right">
                      {r.volume_confirmed_success_rate == null ? "—" : `${r.volume_confirmed_success_rate}% (${r.volume_confirmed_n})`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-faint">
            {data?.method} Greyed rows have fewer than {MIN_SAMPLES} breakouts — too few to trust.
            {data?.source === "demo" && " Demo mode: these numbers come from synthetic prices, not the market."}
          </p>
        </div>
      )}
    </Panel>
  );
}

/** Dashboard: patterns forming or just broken out across the scanned stocks. */
export function ActivePatterns({ rows, timeframe }: { rows: ScanRow[]; timeframe: Timeframe }) {
  const items = rows
    .flatMap((r) => (r.analysis?.patterns?.current ?? []).map((p) => ({ sym: r.instrument.symbol, p })))
    .sort((a, b) => Number(b.p.status !== "forming") - Number(a.p.status !== "forming") || b.p.stars - a.p.stars)
    .slice(0, 10);
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Shapes className="size-4 text-accent" /> Chart patterns now
        </span>
      }
      action={<span className="text-xs text-muted">{TIMEFRAME_LABEL[timeframe]}</span>}
    >
      {items.length ? (
        <ul className="space-y-1.5 text-sm">
          {items.map(({ sym, p }) => (
            <li key={`${sym}-${p.key}-${p.detected_time}`} className="flex items-center justify-between gap-2">
              <Link href={`/stock/${sym}?tf=${timeframe}`} className="font-medium hover:text-accent">
                {sym}
              </Link>
              <span className="min-w-0 flex-1 truncate text-xs text-muted">{p.name}</span>
              <span className={`whitespace-nowrap text-[11px] ${biasCls(p).split(" ")[0]}`}>
                {p.status === "forming" ? "forming" : p.direction === "up" ? "▲ broke out" : "▼ broke down"}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-faint">No patterns forming right now.</p>
      )}
    </Panel>
  );
}
