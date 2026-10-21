"use client";

import { useEffect, useRef, useState } from "react";
import { History, ShieldCheck, ShieldX, Wrench } from "lucide-react";
import { ApiError, fetcher } from "@/lib/api";
import { fmtPx } from "@/lib/format";
import {
  DEFAULT_TOOLS, R_TARGETS, clearToday, logTrade, riskCheck, saveTools, squareOffFor, stopChoices, targetFor, todaysTrades,
  useTradeLog, useTraderTools, type ToolMarket, type ToolSettings,
} from "@/lib/traderTools";

const inr = (v: number) => `₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

// ---- header switch + settings ------------------------------------------------------
export function ToolsToggle() {
  const tools = useTraderTools();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const field = (key: keyof ToolSettings, label: string, type: "number" | "time" = "number") => (
    <label className="flex items-center justify-between gap-3 text-xs text-muted">
      {label}
      <input
        type={type}
        value={String(tools[key])}
        onChange={(e) => saveTools({ [key]: type === "number" ? Number(e.target.value) || 0 : e.target.value })}
        className="tabular w-24 rounded-md border border-line bg-panel-2 px-2 py-1 text-right text-ink outline-none focus:border-accent"
      />
    </label>
  );
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className={`grid size-9 place-items-center rounded-full hover:bg-panel-2 ${tools.enabled ? "text-accent" : "text-muted hover:text-ink"}`}
        aria-label="Day-trader tools"
        title={tools.enabled ? "Day-trader tools: on" : "Day-trader tools: off"}
      >
        <Wrench className="size-[18px]" />
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-[min(20rem,calc(100vw-2rem))] space-y-3 rounded-2xl border border-line bg-panel p-4 shadow-xl">
          <label className="flex items-center justify-between gap-3">
            <span>
              <span className="block text-sm font-semibold text-ink">Day-trader tools</span>
              <span className="block text-[11px] text-muted">Time Machine odds, risk rules, square-off time and stop choices in every calculator.</span>
            </span>
            <input type="checkbox" checked={tools.enabled} onChange={(e) => saveTools({ enabled: e.target.checked })} className="size-5 shrink-0 accent-accent" />
          </label>
          {tools.enabled && (
            <div className="space-y-1.5 border-t border-line pt-3">
              <p className="text-[11px] font-semibold text-muted">Your limits</p>
              {field("maxLossPerTrade", "Max loss per trade ₹")}
              {field("dailyLossLimit", "Daily loss limit ₹")}
              {field("maxTradesPerDay", "Max trades per day")}
              {field("minRR", "Min reward : risk")}
              <p className="pt-1 text-[11px] font-semibold text-muted">Stocks (IST)</p>
              {field("noEntriesBefore", "No entries before", "time")}
              {field("noEntriesAfter", "No entries after", "time")}
              {field("squareOff", "Square off by", "time")}
              <p className="pt-1 text-[11px] font-semibold text-muted">Forex (IST)</p>
              {field("fxNoEntriesBefore", "No entries before", "time")}
              {field("fxNoEntriesAfter", "No entries after", "time")}
              {field("fxSquareOff", "Square off by", "time")}
              <button onClick={() => saveTools({ ...DEFAULT_TOOLS, enabled: true })} className="pt-1 text-[11px] text-faint hover:text-ink">
                Reset to defaults
              </button>
            </div>
          )}
          <p className="text-[10px] leading-relaxed text-faint">Saved in this browser only. Ideas from the open-source day-trader skill pack (MIT).</p>
        </div>
      )}
    </div>
  );
}

// ---- the panel under a calculator --------------------------------------------------
export interface ToolsPlan {
  market: ToolMarket;
  symbol: string;
  side: "BUY" | "SELL";
  entry: number;
  stop: number;
  target: number;
  /** Net rupees lost at the stop for the planned size (positive), or null when there is no size yet. */
  lossAtStop: number | null;
  qty: number;
  qtyLabel: string;
  intraday: boolean;
  atr?: number | null;
  openingRange?: { high: number; low: number; complete: boolean } | null;
  dp: number;
}

/** Renders nothing unless the Day-trader tools are switched on. */
export function DayTraderPanel({ plan, onLevels }: { plan: ToolsPlan; onLevels?: (stop: number, target: number) => void }) {
  const tools = useTraderTools();
  const [setupStop] = useState(plan.stop);
  if (!tools.enabled) return null;
  const valid = plan.side === "BUY" ? plan.stop < plan.entry && plan.target > plan.entry : plan.stop > plan.entry && plan.target < plan.entry;
  const choices = stopChoices({ side: plan.side, entry: plan.entry, setupStop, atr: plan.atr, openingRange: plan.openingRange, dp: plan.dp });
  const risk = Math.abs(plan.entry - plan.stop);
  const curR = risk > 0 ? Math.abs(plan.target - plan.entry) / risk : null;
  return (
    <div className="mt-4 space-y-3 border-t border-dashed border-line pt-4">
      <p className="flex items-center gap-2 text-xs font-semibold text-accent">
        <Wrench className="size-3.5" /> Day-trader tools
        {plan.intraday && <span className="font-normal text-muted">· square off by {squareOffFor(tools, plan.market)} IST, win or lose</span>}
      </p>
      {onLevels && Number.isFinite(plan.entry) && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted">Stop:</span>
          {choices.map((c) => (
            <button
              key={c.key}
              onClick={() => onLevels(c.stop, targetFor(plan.side, plan.entry, c.stop, curR ?? 2, plan.dp))}
              title={fmtPx(c.stop, plan.dp)}
              className={`rounded-full border px-2.5 py-1 ${Math.abs(c.stop - plan.stop) < 10 ** -plan.dp ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-ink"}`}
            >
              {c.label}
            </button>
          ))}
          <span className="ml-2 text-muted">Target:</span>
          {R_TARGETS.map((r) => (
            <button
              key={r}
              onClick={() => onLevels(plan.stop, targetFor(plan.side, plan.entry, plan.stop, r, plan.dp))}
              className={`rounded-full border px-2.5 py-1 ${curR != null && Math.abs(curR - r) < 0.01 ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-ink"}`}
            >
              {r}R
            </button>
          ))}
        </div>
      )}
      {valid ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <TimeMachineCard plan={plan} until={plan.intraday ? squareOffFor(tools, plan.market) : null} />
          <RiskCard plan={plan} tools={tools} />
        </div>
      ) : (
        <p className="text-xs text-warn">Set the stop and target on the right sides of the entry to run the Time Machine and the risk check.</p>
      )}
    </div>
  );
}

// ---- Time Machine ---------------------------------------------------------------------
interface TimeMachineResult {
  r_multiple: number;
  replays: number;
  seed: number;
  start: string;
  until: string;
  start_label: string;
  sessions: number;
  p_target: number;
  p_stop: number;
  p_neither: number;
  p_green: number;
  close: { p5: number; median: number; p95: number };
  avg_r: number;
  cost_r: number;
  avg_r_after_costs: number;
  verdict: string;
  notes: string[];
  history: { sessions: number; target: number; stop: number; neither: number; avg_r: number | null };
  bands: { time: string; p5: number; p25: number; p50: number; p75: number; p95: number }[];
  method: string;
  source: string;
}

function TimeMachineCard({ plan, until }: { plan: ToolsPlan; until: string | null }) {
  const [state, setState] = useState<{ key: string; data?: TimeMachineResult; error?: string; loading?: boolean } | null>(null);
  const base = plan.market === "forex" ? "/api/forex/timemachine" : "/api/timemachine";
  const r4 = (v: number) => +v.toFixed(Math.max(plan.dp, 2));
  const qs = new URLSearchParams({ side: plan.side, entry: String(r4(plan.entry)), stop: String(r4(plan.stop)), target: String(r4(plan.target)) });
  if (until) qs.set("until", until);
  const url = `${base}/${encodeURIComponent(plan.symbol.replace(/\s+/g, ""))}?${qs}`;
  const run = async () => {
    setState({ key: url, loading: true });
    try {
      setState({ key: url, data: await fetcher<TimeMachineResult>(url) });
    } catch (e) {
      setState({ key: url, error: e instanceof ApiError ? e.message : "The Time Machine could not run." });
    }
  };
  const d = state?.data;
  const stale = state && state.key !== url;
  return (
    <section className="rounded-xl border border-line bg-panel-2/50 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-sm font-semibold">
          <History className="size-4 text-accent" /> Time Machine
        </p>
        <button onClick={run} disabled={state?.loading} className="rounded-full bg-accent px-3 py-1 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-60">
          {state?.loading ? "Replaying…" : d ? (stale ? "Run again" : "Re-run") : "Run 10,000 replays"}
        </button>
      </div>
      {!state && (
        <p className="mt-2 text-xs text-muted">
          Replays the rest of the session 10,000 times from this {plan.market === "forex" ? "pair" : "stock"}&apos;s own last ~60 sessions, and checks how
          the same trade did on each of them.
        </p>
      )}
      {state?.error && <p className="mt-2 text-xs text-warn">{state.error}</p>}
      {d && (
        <div className={`mt-2 space-y-2 ${stale ? "opacity-50" : ""}`}>
          {stale && <p className="text-[11px] text-warn">The levels changed since this run: run again.</p>}
          <div className="flex h-2.5 overflow-hidden rounded-full">
            <div className="bg-bull" style={{ width: `${d.p_target}%` }} />
            <div className="bg-bear" style={{ width: `${d.p_stop}%` }} />
            <div className="bg-line" style={{ width: `${d.p_neither}%` }} />
          </div>
          <dl className="grid grid-cols-3 gap-2 text-center text-xs">
            <div>
              <dt className="text-muted">Target first</dt>
              <dd className="tabular text-base font-semibold text-bull">{d.p_target.toFixed(1)}%</dd>
            </div>
            <div>
              <dt className="text-muted">Stop first</dt>
              <dd className="tabular text-base font-semibold text-bear">{d.p_stop.toFixed(1)}%</dd>
            </div>
            <div>
              <dt className="text-muted">Neither by {d.until}</dt>
              <dd className="tabular text-base font-semibold">{d.p_neither.toFixed(1)}%</dd>
            </div>
          </dl>
          <p className="text-sm font-semibold text-ink">{d.verdict}</p>
          {d.notes.map((n) => (
            <p key={n} className="text-xs text-muted">{n}</p>
          ))}
          <FanChart d={d} plan={plan} />
          <p className="text-xs text-muted">
            Price at {d.until}: median {fmtPx(d.close.median, plan.dp)}, 90% of replays between {fmtPx(d.close.p5, plan.dp)} and {fmtPx(d.close.p95, plan.dp)}.
            Average R {d.avg_r >= 0 ? "+" : ""}{d.avg_r.toFixed(2)}, <b className={d.avg_r_after_costs >= 0 ? "text-bull" : "text-bear"}>
              {d.avg_r_after_costs >= 0 ? "+" : ""}{d.avg_r_after_costs.toFixed(2)} after costs</b>.
          </p>
          <p className="text-xs text-muted">
            <b className="text-ink">History:</b> the same trade at {d.start} on each of the last {d.history.sessions} sessions hit the target{" "}
            {d.history.target}×, the stop {d.history.stop}×, neither {d.history.neither}×
            {d.history.avg_r != null && ` (average R ${d.history.avg_r >= 0 ? "+" : ""}${d.history.avg_r.toFixed(2)})`}.
          </p>
          <p className="text-[10px] text-faint">
            {d.replays.toLocaleString("en-IN")} replays, {d.start_label} to {d.until}, seed {d.seed}, from {d.sessions} sessions
            {d.source === "demo" ? " of demo data" : ""}. A simulation, not a prediction.
          </p>
        </div>
      )}
    </section>
  );
}

/** 5–95% and 25–75% bands of the replayed price over the rest of the session, with the entry, stop and target. */
function FanChart({ d, plan }: { d: TimeMachineResult; plan: ToolsPlan }) {
  const b = d.bands;
  if (b.length < 2) return null;
  const W = 320, H = 110;
  const lo = Math.min(...b.map((x) => x.p5), plan.stop, plan.target);
  const hi = Math.max(...b.map((x) => x.p95), plan.stop, plan.target);
  const x = (i: number) => (i / (b.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo || 1)) * H;
  const area = (a: "p5" | "p25", z: "p95" | "p75") =>
    `M${b.map((p, i) => `${x(i)},${y(p[z])}`).join("L")}L${[...b].reverse().map((p, i) => `${x(b.length - 1 - i)},${y(p[a])}`).join("L")}Z`;
  const line = (v: number, cls: string) => <line x1={0} x2={W} y1={y(v)} y2={y(v)} className={cls} strokeDasharray="4 3" strokeWidth={1} />;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-28 w-full" preserveAspectRatio="none" role="img" aria-label="Range of replayed prices">
      <path d={area("p5", "p95")} className="fill-accent/10" />
      <path d={area("p25", "p75")} className="fill-accent/20" />
      <path d={`M${b.map((p, i) => `${x(i)},${y(p.p50)}`).join("L")}`} className="fill-none stroke-accent" strokeWidth={1.5} />
      {line(plan.target, "stroke-bull")}
      {line(plan.stop, "stroke-bear")}
      {line(plan.entry, "stroke-muted")}
    </svg>
  );
}

// ---- risk check -----------------------------------------------------------------------
function RiskCard({ plan, tools }: { plan: ToolsPlan; tools: ToolSettings }) {
  const log = useTradeLog();
  const today = todaysTrades(log);
  const used = today.reduce((a, t) => a + t.maxLoss, 0);
  if (plan.lossAtStop == null || plan.qty <= 0) {
    return (
      <section className="rounded-xl border border-line bg-panel-2/50 p-3 text-xs text-muted">
        <p className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-ink"><ShieldCheck className="size-4 text-accent" /> Risk check</p>
        No size yet: raise the capital or risk % so at least one {plan.qtyLabel === "Lots" ? "lot" : "share"} fits.
      </section>
    );
  }
  const res = riskCheck({ market: plan.market, side: plan.side, entry: plan.entry, stop: plan.stop, target: plan.target,
    lossAtStop: plan.lossAtStop, intraday: plan.intraday }, tools, log);
  return (
    <section className={`rounded-xl border p-3 ${res.pass ? "border-bull/40 bg-bull/5" : "border-bear/40 bg-bear/5"}`}>
      <p className={`flex items-center gap-1.5 text-sm font-semibold ${res.pass ? "text-bull" : "text-bear"}`}>
        {res.pass ? <ShieldCheck className="size-4" /> : <ShieldX className="size-4" />}
        Risk check: {res.pass ? "PASS" : "BLOCK"}
      </p>
      <ul className="mt-2 space-y-1 text-xs">
        {res.rows.map((r) => (
          <li key={r.name} className="flex justify-between gap-3">
            <span className={r.ok ? "text-muted" : "font-semibold text-bear"}>{r.ok ? "✓" : "✕"} {r.name}</span>
            <span className={`tabular text-right ${r.ok ? "text-muted" : "text-bear"}`}>{r.detail}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2 text-[11px] text-muted">
        <span>
          Today: {today.length} trade{today.length === 1 ? "" : "s"} · {inr(used)} of {inr(tools.dailyLossLimit)} risked
        </span>
        <span className="flex gap-2">
          {today.length > 0 && <button onClick={clearToday} className="text-faint hover:text-ink">Clear today</button>}
          <button
            disabled={!res.pass}
            onClick={() => logTrade({ symbol: plan.symbol, side: plan.side, entry: plan.entry, stop: plan.stop, qtyLabel: `${plan.qty} ${plan.qtyLabel.toLowerCase()}`, maxLoss: plan.lossAtStop ?? 0 })}
            className="rounded-full border border-line px-2.5 py-0.5 font-semibold text-ink hover:border-accent/50 disabled:opacity-40"
            title={res.pass ? "Count this trade toward today's limits" : "Blocked by your rules"}
          >
            I took this trade
          </button>
        </span>
      </div>
      <p className="mt-1 text-[10px] text-faint">A rules check, not a recommendation. Getting out of a trade is never blocked.</p>
    </section>
  );
}
