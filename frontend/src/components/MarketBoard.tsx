"use client";

import { Activity, Ban, CalendarClock, Compass, Gauge, Layers } from "lucide-react";
import { useHealth, useMarketContext } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { MarketContext } from "@/lib/types";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { AppHeader } from "./AppHeader";
import { Disclaimer } from "./Disclaimer";
import { HowItWorks, Panel } from "./ui";

const pct = (v: number | null | undefined, dp = 1) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(dp)}%`);
const tone = (v: number | null | undefined) => (v == null ? "text-faint" : v > 0 ? "text-bull" : v < 0 ? "text-bear" : "text-muted");

const VERDICT = {
  risk_on: { label: "Risk-on", cls: "bg-bull/10 text-bull ring-bull/30" },
  defensive: { label: "Defensive", cls: "bg-bear/10 text-bear ring-bear/30" },
  mixed: { label: "Mixed", cls: "bg-warn/10 text-warn ring-warn/30" },
} as const;

const QUADRANT = {
  leading: { label: "Leading", cls: "bg-bull/10 text-bull" },
  improving: { label: "Improving", cls: "bg-accent/10 text-accent" },
  weakening: { label: "Weakening", cls: "bg-warn/10 text-warn" },
  lagging: { label: "Lagging", cls: "bg-bear/10 text-bear" },
} as const;

function Sparkline({ points }: { points: [string, number][] }) {
  if (points.length < 2) return null;
  const vals = points.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const w = 240, h = 48;
  const xy = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * h).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-12 w-full" preserveAspectRatio="none" role="img" aria-label="India VIX, last 120 sessions">
      <polyline points={xy} fill="none" className="stroke-accent" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function RegimePanel({ data }: { data: MarketContext }) {
  const r = data.regime;
  if (!r) return null;
  const v = VERDICT[r.verdict];
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Compass className="size-4 text-accent" /> Market regime
        </span>
      }
      action={<span className={`rounded-full px-3 py-1 text-sm font-semibold ring-1 ${v.cls}`}>{v.label}</span>}
    >
      <p className="text-sm text-muted">{r.text}</p>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {r.votes.map((x) => (
          <li key={x.name} className="flex items-start gap-2 rounded-xl bg-panel-2 px-3 py-2 text-xs">
            <span className={`mt-0.5 font-bold ${x.vote > 0 ? "text-bull" : x.vote < 0 ? "text-bear" : "text-muted"}`}>
              {x.vote > 0 ? "▲" : x.vote < 0 ? "▼" : "•"}
            </span>
            <span>
              <b className="text-ink">{x.name}</b> <span className="text-muted">{x.detail}</span>
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function VixPanel({ data }: { data: MarketContext }) {
  const v = data.vix;
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Gauge className="size-4 text-accent" /> India VIX
        </span>
      }
    >
      {!v ? (
        <p className="text-sm text-faint">India VIX is not available from the data source right now.</p>
      ) : (
        <>
          <div className="flex items-end justify-between gap-3">
            <div>
              <p className="tabular text-3xl font-bold">{v.level.toFixed(2)}</p>
              <p className="text-xs text-muted">
                <span className={tone(v.change_1d_pct)}>{pct(v.change_1d_pct)}</span> today ·{" "}
                <span className={tone(v.change_5d_pct)}>{pct(v.change_5d_pct)}</span> in 5 days
              </p>
            </div>
            <div className="text-right">
              <p className="text-[11px] text-muted">vs last year</p>
              <p className="tabular text-lg font-semibold">{v.percentile_1y}th pct</p>
              <p className="text-xs capitalize text-muted">{v.state}</p>
            </div>
          </div>
          <div className="mt-2 h-2 rounded-full bg-panel-2">
            <div className="h-2 rounded-full bg-accent" style={{ width: `${Math.max(3, v.percentile_1y)}%` }} />
          </div>
          <Sparkline points={v.history} />
          <p className="mt-1 text-xs text-muted">{v.text}</p>
        </>
      )}
    </Panel>
  );
}

function BreadthPanel({ data }: { data: MarketContext }) {
  const b = data.breadth;
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Activity className="size-4 text-accent" /> Breadth
        </span>
      }
      action={b && <span className="text-xs text-muted">{b.stocks} stocks</span>}
    >
      {!b ? (
        <p className="text-sm text-faint">Not enough history yet.</p>
      ) : (
        <>
          {[
            ["Above 20-day avg", b.above_20dma_pct],
            ["Above 50-day avg", b.above_50dma_pct],
          ].map(([label, v]) => (
            <div key={label as string} className="mb-2">
              <p className="flex justify-between text-xs">
                <span className="text-muted">{label}</span>
                <b className="tabular">{v}%</b>
              </p>
              <div className="mt-1 h-2 rounded-full bg-bear/20">
                <div className="h-2 rounded-full bg-bull" style={{ width: `${v}%` }} />
              </div>
            </div>
          ))}
          <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
            <div className="rounded-lg bg-panel-2 py-1.5">
              <p className="text-muted">Up / down</p>
              <p className="tabular font-semibold">
                <span className="text-bull">{b.advancers}</span> / <span className="text-bear">{b.decliners}</span>
              </p>
            </div>
            <div className="rounded-lg bg-panel-2 py-1.5">
              <p className="text-muted">20-day highs</p>
              <p className="tabular font-semibold text-bull">{b.new_20d_highs}</p>
            </div>
            <div className="rounded-lg bg-panel-2 py-1.5">
              <p className="text-muted">20-day lows</p>
              <p className="tabular font-semibold text-bear">{b.new_20d_lows}</p>
            </div>
          </div>
          <p className="mt-2 text-xs text-muted">{b.text}</p>
        </>
      )}
    </Panel>
  );
}

function SectorPanel({ data }: { data: MarketContext }) {
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Layers className="size-4 text-accent" /> Sector rotation vs NIFTY 50
        </span>
      }
    >
      {!data.sectors.length ? (
        <p className="text-sm text-faint">Sector indices are not available from the data source right now.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-xs">
            <thead className="text-left text-muted">
              <tr className="border-b border-line">
                <th className="py-1.5 pr-2 font-medium">Sector</th>
                <th className="py-1.5 pr-2 text-right font-medium">1D</th>
                <th className="py-1.5 pr-2 text-right font-medium">1W</th>
                <th className="py-1.5 pr-2 text-right font-medium">1M</th>
                <th className="py-1.5 pr-2 text-right font-medium">3M</th>
                <th className="py-1.5 pr-2 text-right font-medium" title="1-month return minus NIFTY 50's">vs NIFTY 1M</th>
                <th className="py-1.5 pr-2 text-right font-medium" title="Average z-score of relative strength and acceleration">Score</th>
                <th className="py-1.5 text-right font-medium">Phase</th>
              </tr>
            </thead>
            <tbody>
              {data.sectors.map((s) => (
                <tr key={s.sector} className="border-b border-line/60">
                  <td className="py-1.5 pr-2 font-medium">{s.sector.replace("NIFTY ", "")}</td>
                  {(["1d", "1w", "1m", "3m"] as const).map((k) => (
                    <td key={k} className={`tabular py-1.5 pr-2 text-right ${tone(s.returns[k])}`}>
                      {pct(s.returns[k])}
                    </td>
                  ))}
                  <td className={`tabular py-1.5 pr-2 text-right font-semibold ${tone(s.relative["1m"])}`}>{pct(s.relative["1m"])}</td>
                  <td className={`tabular py-1.5 pr-2 text-right ${tone(s.score)}`}>{s.score > 0 ? "+" : ""}{s.score.toFixed(2)}</td>
                  <td className="py-1.5 text-right">
                    <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${QUADRANT[s.quadrant].cls}`}>{QUADRANT[s.quadrant].label}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <HowItWorks>
            Leading = stronger than NIFTY over 3 months and still speeding up. Weakening = still stronger but slowing. Improving = weaker
            but turning up. Lagging = weaker and still slowing. Score is the average z-score of relative strength over 1 week, 1 month and 3
            months plus the acceleration (1-week daily pace minus 3-month daily pace).
          </HowItWorks>
        </div>
      )}
    </Panel>
  );
}

function CalendarPanel({ data }: { data: MarketContext }) {
  const ban = data.fo_ban;
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <CalendarClock className="size-4 text-accent" /> Expiries &amp; F&amp;O ban
        </span>
      }
    >
      <ul className="space-y-1.5 text-sm">
        {data.expiries.map((e) => (
          <li key={e.index} className="flex items-center justify-between">
            <span>{e.index}</span>
            <span className={`tabular ${e.is_today ? "font-semibold text-warn" : "text-muted"}`}>
              {e.is_today ? "Expiry today" : `${e.next} · in ${e.days} day${e.days === 1 ? "" : "s"}`}
            </span>
          </li>
        ))}
      </ul>
      {data.expiries.some((e) => e.is_today) && (
        <p className="mt-2 rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn">
          Expiry day: option premiums decay fast and moves can be erratic into the close. Smaller size, tighter plans.
        </p>
      )}
      <div className="mt-3 border-t border-line pt-3">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-muted">
          <Ban className="size-3.5" /> F&amp;O ban{ban.trade_date ? ` · ${ban.trade_date}` : ""}
        </p>
        {ban.status !== "ok" ? (
          <p className="mt-1 text-xs text-faint">{ban.message}</p>
        ) : ban.symbols.length ? (
          <>
            <p className="mt-1 flex flex-wrap gap-1.5">
              {ban.symbols.map((s) => (
                <span key={s} className="rounded-md bg-bear/10 px-2 py-0.5 text-xs font-semibold text-bear">
                  {s}
                </span>
              ))}
            </p>
            <p className="mt-1 text-[11px] text-faint">Only trades that reduce existing positions are allowed in these stocks&apos; futures and options.</p>
          </>
        ) : (
          <p className="mt-1 text-xs text-muted">No stocks in the ban period.</p>
        )}
      </div>
    </Panel>
  );
}

/** Market board: is this a market to press or to protect? */
export function MarketBoard() {
  const health = useHealth();
  const stream = useQuoteStream();
  const { data, error, isLoading } = useMarketContext();
  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-4 py-5 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h1 className="text-xl font-bold">Market board</h1>
            <p className="text-sm text-muted">The backdrop for every trade: volatility, participation, which sectors lead, and what&apos;s on the calendar.</p>
          </div>
          {data && (
            <p className="text-xs text-faint">
              Updated {fmtTime(data.generated_at, true)}
              {data.source === "demo" && " · demo data"}
            </p>
          )}
        </div>
        {error ? (
          <p className="rounded-xl bg-bear/10 px-4 py-3 text-sm text-bear">{error.message}</p>
        ) : isLoading && !data ? (
          <p className="text-sm text-faint">Loading the market board…</p>
        ) : data ? (
          <>
            <RegimePanel data={data} />
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
              <VixPanel data={data} />
              <BreadthPanel data={data} />
              <CalendarPanel data={data} />
            </div>
            <SectorPanel data={data} />
            <p className="text-[11px] text-faint">{data.method}</p>
          </>
        ) : null}
      </main>
      <Disclaimer />
    </>
  );
}
