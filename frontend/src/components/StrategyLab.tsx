"use client";

import { useState } from "react";
import { Brain, FlaskConical, Trophy } from "lucide-react";
import { TIMEFRAME_LABEL, useHealth, useStrategyLab, useTearsheet } from "@/lib/api";
import { fmtDate, fmtTime } from "@/lib/format";
import type { StrategyLab as Lab, StrategyLabRow, Tearsheet, Timeframe } from "@/lib/types";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { AppHeader } from "./AppHeader";
import { Disclaimer } from "./Disclaimer";
import { EvidenceBadge, HowItWorks, Panel } from "./ui";

const TIMEFRAMES: Timeframe[] = ["5m", "15m", "1h", "1d"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pct = (v: number | null | undefined, dp = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(dp)}%`);
const tone = (v: number | null | undefined) => (v == null ? "text-faint" : v > 0 ? "text-bull" : v < 0 ? "text-bear" : "text-muted");

function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="text-faint">—</span>;
  const lo = Math.min(0, ...values), hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const w = 90, h = 22;
  const xy = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * h).toFixed(1)}`).join(" ");
  const up = values[values.length - 1] >= 0;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-[22px] w-[90px]" preserveAspectRatio="none" aria-hidden>
      <line x1="0" x2={w} y1={h - ((0 - lo) / span) * h} y2={h - ((0 - lo) / span) * h} className="stroke-line" strokeDasharray="2 2" />
      <polyline points={xy} fill="none" className={up ? "stroke-bull" : "stroke-bear"} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Line (and optional zero line) for equity, drawdown and rolling Sharpe. */
function LineChart({ points, area = false, label, height = 160 }: { points: [string, number][]; area?: boolean; label: string; height?: number }) {
  if (points.length < 2) return <p className="py-6 text-center text-xs text-faint">Not enough data for {label.toLowerCase()}.</p>;
  const vals = points.map((p) => p[1]);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const w = 600, h = height;
  const y = (v: number) => h - ((v - lo) / span) * (h - 8) - 4;
  const xy = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${y(v).toFixed(1)}`);
  const neg = vals[vals.length - 1] < 0 || area;
  return (
    <figure>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ height }} preserveAspectRatio="none" role="img" aria-label={label}>
        <line x1="0" x2={w} y1={y(0)} y2={y(0)} className="stroke-line" strokeDasharray="3 3" />
        {area && <polygon points={`0,${y(0)} ${xy.join(" ")} ${w},${y(0)}`} className="fill-bear/15" />}
        <polyline points={xy.join(" ")} fill="none" className={neg ? "stroke-bear" : "stroke-bull"} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="mt-1 flex justify-between text-[10px] text-faint">
        <span>{fmtDate(points[0][0])}</span>
        <span>
          {label} · low {lo.toFixed(2)} · high {hi.toFixed(2)}
        </span>
        <span>{fmtDate(points[points.length - 1][0])}</span>
      </figcaption>
    </figure>
  );
}

function MonthlyHeatmap({ monthly, yearly }: { monthly: Tearsheet["monthly"]; yearly: Tearsheet["yearly"] }) {
  const byYear = new Map<number, Map<number, number>>();
  for (const m of monthly) {
    const [y, mo] = m.month.split("-").map(Number);
    if (!byYear.has(y)) byYear.set(y, new Map());
    byYear.get(y)!.set(mo, m.pct);
  }
  const max = Math.max(0.01, ...monthly.map((m) => Math.abs(m.pct)));
  const cell = (v: number | undefined) => {
    if (v == null) return { background: "transparent" };
    const a = Math.min(1, Math.abs(v) / max) * 0.55 + 0.08;
    return { background: v >= 0 ? `rgb(22 163 74 / ${a})` : `rgb(220 38 38 / ${a})` };
  };
  const years = [...byYear.keys()].sort();
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-center text-[11px]">
        <thead className="text-muted">
          <tr>
            <th className="py-1 text-left font-medium">Year</th>
            {MONTHS.map((m) => (
              <th key={m} className="py-1 font-medium">
                {m}
              </th>
            ))}
            <th className="py-1 font-medium">Year</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}>
              <td className="py-0.5 text-left font-medium">{y}</td>
              {MONTHS.map((_, i) => {
                const v = byYear.get(y)?.get(i + 1);
                return (
                  <td key={i} className="tabular px-0.5 py-0.5">
                    <span className="block rounded px-1 py-1" style={cell(v)}>
                      {v == null ? "" : v.toFixed(1)}
                    </span>
                  </td>
                );
              })}
              <td className={`tabular py-0.5 font-semibold ${tone(yearly.find((x) => x.year === y)?.pct)}`}>{pct(yearly.find((x) => x.year === y)?.pct, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Distribution({ bins }: { bins: Tearsheet["distribution"] }) {
  const max = Math.max(1, ...bins.map((b) => b.count));
  return (
    <div>
      <div className="flex h-28 items-end gap-0.5">
        {bins.map((b) => (
          <div
            key={b.from}
            title={`${b.from}% to ${b.to}%: ${b.count} trades`}
            className={`flex-1 rounded-t ${b.to <= 0 ? "bg-bear/60" : b.from >= 0 ? "bg-bull/60" : "bg-muted/40"}`}
            style={{ height: `${(b.count / max) * 100}%` }}
          />
        ))}
      </div>
      <p className="mt-1 flex justify-between text-[10px] text-faint">
        <span>{bins[0]?.from}%</span>
        <span>result per trade, after costs</span>
        <span>{bins[bins.length - 1]?.to}%</span>
      </p>
    </div>
  );
}

function SheetView({ timeframe, row }: { timeframe: Timeframe; row: StrategyLabRow }) {
  const { data, error, isLoading } = useTearsheet(timeframe, row.key);
  const t = data?.tearsheet;
  const s = t?.stats;
  const tiles: [string, string, string?][] = s
    ? [
        ["Total return", pct(s.total_return_pct), tone(s.total_return_pct)],
        ["Per year", pct(s.annual_return_pct), tone(s.annual_return_pct)],
        ["Sharpe", s.sharpe?.toFixed(2) ?? "—"],
        ["Sortino", s.sortino?.toFixed(2) ?? "—"],
        ["Max drawdown", pct(s.max_drawdown_pct), "text-bear"],
        ["Calmar", s.calmar?.toFixed(2) ?? "—"],
        ["Win rate", `${s.win_rate}%`],
        ["Profit factor", s.profit_factor?.toFixed(2) ?? "—"],
        ["Avg trade", pct(s.expectancy_pct, 3), tone(s.expectancy_pct)],
        ["Avg win / loss", `${pct(s.avg_win_pct)} / ${pct(s.avg_loss_pct)}`],
        ["Best / worst", `${pct(s.best_trade_pct)} / ${pct(s.worst_trade_pct)}`],
        ["Longest drawdown", `${s.longest_drawdown_days} days`],
      ]
    : [];
  return (
    <Panel
      title={
        <span className="inline-flex flex-wrap items-center gap-2">
          {row.name} <EvidenceBadge evidence={row.evidence} />
        </span>
      }
      action={s && <span className="text-xs text-muted">{s.trades} trades · {fmtDate(s.period[0])} – {fmtDate(s.period[1])}</span>}
    >
      <p className="text-xs text-muted">{row.description}</p>
      {row.evidence && <p className="mt-1 text-xs text-faint">{row.evidence.text}</p>}
      {error ? (
        <p className="mt-3 text-sm text-bear">{error.message}</p>
      ) : isLoading && !t ? (
        <p className="mt-3 text-sm text-faint">Building the tearsheet…</p>
      ) : !t ? (
        <p className="mt-3 text-sm text-faint">Too few trades for a tearsheet.</p>
      ) : (
        <div className="mt-3 space-y-5">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
            {tiles.map(([k, v, c]) => (
              <div key={k} className="rounded-lg bg-panel-2 px-2.5 py-2">
                <p className="text-[10px] text-muted">{k}</p>
                <p className={`tabular text-sm font-semibold ${c ?? "text-ink"}`}>{v}</p>
              </div>
            ))}
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold text-muted">Equity (% of capital)</p>
            <LineChart points={t.equity} label="Equity" />
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold text-muted">Drawdown (% below the previous peak)</p>
            <LineChart points={t.drawdown} label="Drawdown" area height={100} />
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold text-muted">Monthly returns (%)</p>
            <MonthlyHeatmap monthly={t.monthly} yearly={t.yearly} />
          </div>
          <div className="grid gap-5 lg:grid-cols-2">
            <div>
              <p className="mb-1 text-xs font-semibold text-muted">Rolling 63-day Sharpe</p>
              <LineChart points={t.rolling_sharpe} label="Rolling Sharpe" height={110} />
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold text-muted">Trade results</p>
              <Distribution bins={t.distribution} />
            </div>
          </div>
          <p className="text-[11px] text-faint">{t.method}</p>
        </div>
      )}
    </Panel>
  );
}

function MetaCard({ lab }: { lab: Lab }) {
  const m = lab.meta;
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Brain className="size-4 text-accent" /> Trade filter (meta-labelling)
        </span>
      }
      action={
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${m.useful ? "bg-bull/10 text-bull" : m.status === "training" ? "bg-panel-2 text-muted" : "bg-warn/10 text-warn"}`}
        >
          {m.status === "training" ? "Training…" : m.useful ? "On: passed the unseen-data test" : "Off: did not beat the test"}
        </span>
      }
    >
      {m.test ? (
        <p className="text-sm text-muted">
          On {m.test.trades.toLocaleString()} trades it never saw ({fmtDate(m.test.test_period[0])} – {fmtDate(m.test.test_period[1])}), the half it
          rated best won <b className="text-ink">{m.test.top_half_win_pct}%</b> and averaged <b className={tone(m.test.top_half_avg_pct)}>{pct(m.test.top_half_avg_pct, 3)}</b> after
          costs, vs <b className="text-ink">{m.test.base_win_pct}%</b> and <b className={tone(m.test.all_avg_pct)}>{pct(m.test.all_avg_pct, 3)}</b> for every signal
          {m.test.bottom_half_avg_pct != null && (
            <>
              {" "}
              (the lower half: <span className={tone(m.test.bottom_half_avg_pct)}>{pct(m.test.bottom_half_avg_pct, 3)}</span>)
            </>
          )}
          .
        </p>
      ) : (
        <p className="text-sm text-faint">{m.status === "training" ? "Learning from every past strategy trade…" : "Not enough trades to train it."}</p>
      )}
      <HowItWorks>
        A second model learns, from the market at the moment of entry and which strategy fired, how likely each signal is to make money after
        costs (each past trade&apos;s label is its own stop / target / exit outcome). It is fitted on trades that closed before a cut-off and tested on
        trades that opened after it. It is switched on only if it beats the plain win rate there and its top-rated half actually earned more.
        When on, each fresh strategy signal on a stock page shows its chance of winning. It never changes a signal.
      </HowItWorks>
    </Panel>
  );
}

/** Every strategy ranked on the same footing, with a full tearsheet for the one selected. */
export function StrategyLab() {
  const health = useHealth();
  const stream = useQuoteStream();
  const [tf, setTf] = useState<Timeframe>("1d");
  const [picked, setPicked] = useState<string | null>(null);
  const { data, error, isLoading } = useStrategyLab(tf);
  const row = data?.rows.find((r) => r.key === picked) ?? data?.rows[0];
  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-4 py-5 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-bold">
              <FlaskConical className="size-5 text-accent" /> Strategy lab
            </h1>
            <p className="text-sm text-muted">Every strategy, on every stock, after costs — ranked by how far its record can be trusted.</p>
          </div>
          <div className="flex rounded-lg border border-line bg-panel p-1">
            {TIMEFRAMES.map((x) => (
              <button
                key={x}
                onClick={() => setTf(x)}
                className={`rounded-md px-3 py-1 text-sm font-medium ${tf === x ? "bg-accent text-white" : "text-muted hover:text-ink"}`}
              >
                {TIMEFRAME_LABEL[x]}
              </button>
            ))}
          </div>
        </div>
        {error ? (
          <p className="rounded-xl bg-bear/10 px-4 py-3 text-sm text-bear">{error.message}</p>
        ) : isLoading && !data ? (
          <p className="text-sm text-faint">Backtesting every strategy on every stock… the first run takes a little while.</p>
        ) : data ? (
          <>
            <Panel
              title={
                <span className="inline-flex items-center gap-2">
                  <Trophy className="size-4 text-accent" /> Leaderboard · {TIMEFRAME_LABEL[tf]}
                </span>
              }
              action={
                <span className="text-xs text-faint">
                  {data.instruments} instruments · updated {fmtTime(data.generated_at, true)}
                  {data.source === "demo" && " · demo data"}
                </span>
              }
            >
              <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] text-xs">
                  <thead className="text-left text-muted">
                    <tr className="border-b border-line">
                      <th className="py-1.5 pr-2 font-medium">#</th>
                      <th className="py-1.5 pr-2 font-medium">Strategy</th>
                      <th className="py-1.5 pr-2 text-right font-medium">Trades</th>
                      <th className="py-1.5 pr-2 text-right font-medium">Win</th>
                      <th className="py-1.5 pr-2 text-right font-medium">Avg trade</th>
                      <th className="py-1.5 pr-2 text-right font-medium">PF</th>
                      <th className="py-1.5 pr-2 text-right font-medium">Sharpe</th>
                      <th className="py-1.5 pr-2 text-right font-medium">Max DD</th>
                      <th className="py-1.5 pr-2 text-right font-medium" title="Deflated Sharpe: chance the edge is not luck">
                        Real?
                      </th>
                      <th className="py-1.5 font-medium">Equity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((r, i) => (
                      <tr
                        key={r.key}
                        onClick={() => setPicked(r.key)}
                        className={`cursor-pointer border-b border-line/60 hover:bg-panel-2 ${row?.key === r.key ? "bg-accent/5" : ""}`}
                      >
                        <td className="py-1.5 pr-2 text-faint">{i + 1}</td>
                        <td className="py-1.5 pr-2">
                          <span className="inline-flex flex-wrap items-center gap-1.5 font-medium">
                            {r.name} <EvidenceBadge evidence={r.evidence} />
                            {r.decay?.status === "fading" && <span className="text-[10px] text-warn">↓ fading</span>}
                          </span>
                        </td>
                        <td className="tabular py-1.5 pr-2 text-right">{r.stats?.trades ?? 0}</td>
                        <td className="tabular py-1.5 pr-2 text-right">{r.stats ? `${r.stats.win_rate}%` : "—"}</td>
                        <td className={`tabular py-1.5 pr-2 text-right ${tone(r.stats?.expectancy_pct)}`}>{pct(r.stats?.expectancy_pct, 3)}</td>
                        <td className="tabular py-1.5 pr-2 text-right">{r.stats?.profit_factor?.toFixed(2) ?? "—"}</td>
                        <td className="tabular py-1.5 pr-2 text-right">{r.stats?.sharpe?.toFixed(2) ?? "—"}</td>
                        <td className="tabular py-1.5 pr-2 text-right text-bear">{r.stats ? pct(r.stats.max_drawdown_pct) : "—"}</td>
                        <td className="tabular py-1.5 pr-2 text-right">{r.deflated_sharpe == null ? "—" : `${Math.round(r.deflated_sharpe * 100)}%`}</td>
                        <td className="py-1.5">
                          <Spark values={r.spark} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-faint">{data.method} Tap a row for its tearsheet.</p>
            </Panel>
            <MetaCard lab={data} />
            {row && <SheetView key={`${tf}-${row.key}`} timeframe={tf} row={row} />}
          </>
        ) : null}
      </main>
      <Disclaimer />
    </>
  );
}
