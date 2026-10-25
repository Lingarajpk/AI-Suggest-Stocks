"use client";

import { useState } from "react";
import { BookOpen, Briefcase, ListOrdered, NotebookPen, RotateCcw, Send, Wallet } from "lucide-react";
import { useHealth } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import {
  cancelOrder, closePosition, placeOrder, resetPaper, saveLesson, useDevice, usePaper,
  type NewOrder, type PaperAccount, type PaperTrade,
} from "@/lib/paper";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { AppHeader } from "./AppHeader";
import { Disclaimer } from "./Disclaimer";
import { EvidenceBadge, HowItWorks, Panel } from "./ui";

const inr = (v: number, dp = 0) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: dp, minimumFractionDigits: dp })}`;
const signed = (v: number) => `${v >= 0 ? "+" : "−"}${inr(Math.abs(v))}`;
const tone = (v: number | null | undefined) => (v == null ? "text-faint" : v > 0 ? "text-bull" : v < 0 ? "text-bear" : "text-muted");
const REASON: Record<string, string> = { target: "Target", stop: "Stop", manual: "Closed", square_off: "Square-off" };

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block min-w-0">
      <span className="text-[11px] text-muted">{label}</span>
      <div className="mt-0.5">{children}</div>
    </label>
  );
}

const INPUT = "tabular w-full rounded-md border border-line bg-panel-2 px-2 py-1.5 text-sm text-ink outline-none focus:border-accent";
const num = (v: string) => (v.trim() === "" ? null : Number(v));

function Ticket({ device }: { device: string }) {
  const [f, setF] = useState({ symbol: "", side: "BUY" as "BUY" | "SELL", qty: "10", type: "market" as NewOrder["type"], product: "intraday" as NewOrder["product"],
    price: "", trigger: "", stop: "", target: "", trail: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const set = (k: keyof typeof f, v: string) => setF((x) => ({ ...x, [k]: v }));
  const submit = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await placeOrder(device, {
        symbol: f.symbol.trim().toUpperCase(), side: f.side, qty: Number(f.qty), type: f.type, product: f.product, note: f.note,
        price: num(f.price), trigger: num(f.trigger), stop: num(f.stop), target: num(f.target), trail_pct: num(f.trail),
      });
      const o = r.order;
      setMsg({ ok: true, text: o.status === "filled" ? `Filled at ₹${o.fill_price}` : o.status === "rejected" ? `Rejected: ${o.reason}` : "Order placed: it fills when the market reaches it (during market hours)." });
      setF((x) => ({ ...x, note: "", price: "", trigger: "", stop: "", target: "", trail: "" }));
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title={<span className="inline-flex items-center gap-2"><Send className="size-4 text-accent" /> New paper order</span>}>
      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        <div className="flex rounded-lg border border-line bg-panel-2 p-0.5">
          {(["BUY", "SELL"] as const).map((s) => (
            <button key={s} onClick={() => set("side", s)} className={`rounded-md px-3 py-1 font-semibold ${f.side === s ? (s === "BUY" ? "bg-bull/20 text-bull" : "bg-bear/20 text-bear") : "text-muted"}`}>
              {s}
            </button>
          ))}
        </div>
        <div className="flex rounded-lg border border-line bg-panel-2 p-0.5">
          {(["intraday", "delivery"] as const).map((p) => (
            <button key={p} onClick={() => set("product", p)} className={`rounded-md px-3 py-1 font-semibold capitalize ${f.product === p ? "bg-accent/15 text-accent" : "text-muted"}`}>
              {p}
            </button>
          ))}
        </div>
        <div className="flex rounded-lg border border-line bg-panel-2 p-0.5">
          {(["market", "limit", "stop"] as const).map((t) => (
            <button key={t} onClick={() => set("type", t)} className={`rounded-md px-3 py-1 font-semibold capitalize ${f.type === t ? "bg-accent/15 text-accent" : "text-muted"}`}>
              {t}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Field label="Symbol">
          <input className={INPUT} value={f.symbol} onChange={(e) => set("symbol", e.target.value)} placeholder="RELIANCE" />
        </Field>
        <Field label="Quantity">
          <input className={INPUT} type="number" min={1} value={f.qty} onChange={(e) => set("qty", e.target.value)} />
        </Field>
        {f.type === "limit" && (
          <Field label="Limit price">
            <input className={INPUT} type="number" step="0.05" value={f.price} onChange={(e) => set("price", e.target.value)} />
          </Field>
        )}
        {f.type === "stop" && (
          <Field label="Trigger price">
            <input className={INPUT} type="number" step="0.05" value={f.trigger} onChange={(e) => set("trigger", e.target.value)} />
          </Field>
        )}
        <Field label="Stop-loss (bracket, optional)">
          <input className={INPUT} type="number" step="0.05" value={f.stop} onChange={(e) => set("stop", e.target.value)} />
        </Field>
        <Field label="Target (bracket, optional)">
          <input className={INPUT} type="number" step="0.05" value={f.target} onChange={(e) => set("target", e.target.value)} />
        </Field>
        <Field label="Trailing stop % (optional)">
          <input className={INPUT} type="number" step="0.1" value={f.trail} onChange={(e) => set("trail", e.target.value)} />
        </Field>
      </div>
      <Field label="Why are you taking this trade? (kept in your journal)">
        <textarea className={`${INPUT} min-h-[60px]`} value={f.note} onChange={(e) => set("note", e.target.value)}
          placeholder="e.g. 20-candle breakout with volume, daily trend agrees, stop under the range low" />
      </Field>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button onClick={submit} disabled={busy || !device} className={`rounded-full px-5 py-2 text-sm font-semibold text-white disabled:opacity-50 ${f.side === "BUY" ? "bg-bull" : "bg-bear"}`}>
          {busy ? "Placing…" : `Paper ${f.side}`}
        </button>
        {msg && <p className={`text-xs ${msg.ok ? "text-bull" : "text-bear"}`}>{msg.text}</p>}
      </div>
    </Panel>
  );
}

function Lesson({ device, t }: { device: string; t: PaperTrade }) {
  const [text, setText] = useState(t.lesson);
  const [saved, setSaved] = useState(false);
  return (
    <div className="mt-1 flex gap-2">
      <input className={`${INPUT} py-1 text-xs`} value={text} onChange={(e) => { setText(e.target.value); setSaved(false); }} placeholder="Lesson from this trade…" />
      <button onClick={async () => { await saveLesson(device, t.id, { lesson: text }); setSaved(true); }} className="shrink-0 rounded-md border border-line px-2 text-xs text-muted hover:text-ink">
        {saved ? "Saved" : "Save"}
      </button>
    </div>
  );
}

function Journal({ device, data }: { device: string; data: PaperAccount }) {
  const s = data.stats;
  return (
    <Panel title={<span className="inline-flex items-center gap-2"><BookOpen className="size-4 text-accent" /> Journal</span>}
      action={s.evidence && <EvidenceBadge evidence={s.evidence} />}>
      {s.trades === 0 ? (
        <p className="text-sm text-faint">No closed trades yet. Every closed paper trade lands here with your reason, the result after charges, and how far it went for and against you.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
            {(
              [
                ["Trades", String(s.trades)],
                ["Win rate", `${s.win_rate}%`],
                ["Net (after charges)", signed(s.net_total ?? 0), tone(s.net_total)],
                ["Avg trade", `${(s.avg_pct ?? 0) > 0 ? "+" : ""}${s.avg_pct}%`, tone(s.avg_pct)],
                ["Profit factor", s.profit_factor?.toFixed(2) ?? "—"],
                ["Avg best move", `+${s.avg_mfe_pct}%`, "text-bull"],
                ["Avg worst move", `${s.avg_mae_pct}%`, "text-bear"],
              ] as [string, string, string?][]
            ).map(([k, v, c]) => (
              <div key={k} className="rounded-lg bg-panel-2 px-2.5 py-2">
                <p className="text-[10px] text-muted">{k}</p>
                <p className={`tabular text-sm font-semibold ${c ?? "text-ink"}`}>{v}</p>
              </div>
            ))}
          </div>
          {s.evidence && <p className="mt-2 text-xs text-muted">{s.evidence.text}</p>}
          {s.by_setup && s.by_setup.length > 1 && (
            <p className="mt-1 text-xs text-faint">
              By setup: {s.by_setup.map((b) => `${b.setup} ${b.trades} trades, ${b.avg_pct > 0 ? "+" : ""}${b.avg_pct}% avg`).join(" · ")}
            </p>
          )}
          <ul className="mt-3 divide-y divide-line/60">
            {data.journal.map((t) => (
              <li key={t.id} className="py-2 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">
                    <span className={t.side === "long" ? "text-bull" : "text-bear"}>{t.side === "long" ? "▲" : "▼"}</span> {t.symbol} · {t.qty} ·{" "}
                    <span className="tabular font-normal text-muted">{t.entry} → {t.exit}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="rounded bg-panel-2 px-1.5 py-0.5 text-[10px] text-muted">{REASON[t.reason] ?? t.reason}</span>
                    <b className={`tabular ${tone(t.net)}`}>{signed(t.net)}</b>
                    <span className={`tabular ${tone(t.net_pct)}`}>({t.net_pct > 0 ? "+" : ""}{t.net_pct}%)</span>
                  </span>
                </div>
                <p className="mt-0.5 text-muted">
                  Why: {t.note}
                  <span className="text-faint"> · {fmtTime(t.opened, true)} → {fmtTime(t.closed, true)} · best +{t.mfe_pct}% / worst {t.mae_pct}% · charges {inr(t.charges, 2)}</span>
                </p>
                {t.exit_note && <p className="text-muted">Exit: {t.exit_note}</p>}
                <Lesson device={device} t={t} />
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}

/** Paper trading: practise the app's plans with fake money, filled from real quotes, with a journal. */
export function PaperTrading() {
  const health = useHealth();
  const stream = useQuoteStream();
  const device = useDevice();
  const { data, error } = usePaper(device);
  const [closing, setClosing] = useState<{ id: string; note: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const a = data?.account;
  const eq = data?.equity ?? [];
  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-4 py-5 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-bold"><Wallet className="size-5 text-accent" /> Paper trading</h1>
            <p className="text-sm text-muted">Practise with fake money. Fills come from real quotes on the server; nothing reaches a broker.</p>
          </div>
          {data && (
            <button onClick={() => { const c = window.prompt("Reset the paper account. Starting capital (₹):", String(a?.capital ?? 1000000)); if (c) act(() => resetPaper(device, Number(c))); }}
              className="inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs text-muted hover:text-ink">
              <RotateCcw className="size-3.5" /> Reset
            </button>
          )}
        </div>
        {(error || err) && <p className="rounded-xl bg-bear/10 px-4 py-2 text-sm text-bear">{err ?? error?.message}</p>}
        {data && a && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {(
                [
                  ["Account value", inr(a.equity), `${a.return_pct > 0 ? "+" : ""}${a.return_pct}% since start`, tone(a.return_pct)],
                  ["Cash", inr(a.cash), `of ${inr(a.capital)} starting`, ""],
                  ["Open P&L", signed(a.unrealized), `${data.positions.length} position${data.positions.length === 1 ? "" : "s"}`, tone(a.unrealized)],
                  ["Market", data.session === "open" ? "Open: orders fill" : "Closed: orders wait", "fills only in market hours", data.session === "open" ? "text-bull" : "text-warn"],
                ] as const
              ).map(([k, v, sub, c]) => (
                <div key={k} className="rounded-2xl border border-line bg-panel px-4 py-3">
                  <p className="text-[11px] text-muted">{k}</p>
                  <p className={`tabular text-lg font-bold ${c}`}>{v}</p>
                  <p className="text-[11px] text-faint">{sub}</p>
                </div>
              ))}
            </div>
            {eq.length > 1 && (
              <EquityLine points={eq} capital={a.capital} />
            )}
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <Ticket device={device} />
              <Panel title={<span className="inline-flex items-center gap-2"><Briefcase className="size-4 text-accent" /> Open positions</span>}>
                {data.positions.length === 0 ? (
                  <p className="text-sm text-faint">No open positions.</p>
                ) : (
                  <ul className="divide-y divide-line/60">
                    {data.positions.map((p) => (
                      <li key={p.id} className="py-2 text-xs">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-semibold">
                            <span className={p.side === "long" ? "text-bull" : "text-bear"}>{p.side === "long" ? "▲ LONG" : "▼ SHORT"}</span> {p.symbol} · {p.qty} @ {p.entry}
                            <span className="ml-1 rounded bg-panel-2 px-1 py-0.5 text-[10px] font-normal capitalize text-muted">{p.product}</span>
                          </span>
                          <span className={`tabular font-semibold ${tone(p.unrealized)}`}>{signed(p.unrealized)} ({p.unrealized_pct > 0 ? "+" : ""}{p.unrealized_pct}%)</span>
                        </div>
                        <p className="mt-0.5 text-muted">
                          Now {p.last}
                          {p.legs.stop && <> · stop {p.legs.stop.trigger}{p.legs.stop.trail_pct ? ` (trailing ${p.legs.stop.trail_pct}%)` : ""}</>}
                          {p.legs.target && <> · target {p.legs.target.price}</>} · since {fmtTime(p.opened, true)}
                        </p>
                        <p className="text-faint">Why: {p.note}</p>
                        {closing?.id === p.id ? (
                          <div className="mt-1 flex gap-2">
                            <input className={`${INPUT} py-1 text-xs`} autoFocus value={closing.note} onChange={(e) => setClosing({ id: p.id, note: e.target.value })} placeholder="Why are you exiting? (optional)" />
                            <button onClick={() => act(async () => { await closePosition(device, p.id, closing.note); setClosing(null); })} className="shrink-0 rounded-md bg-ink px-2 text-xs font-semibold text-panel">Close now</button>
                            <button onClick={() => setClosing(null)} className="shrink-0 text-xs text-muted">Cancel</button>
                          </div>
                        ) : (
                          <button onClick={() => setClosing({ id: p.id, note: "" })} className="mt-1 rounded-md border border-line px-2 py-0.5 text-[11px] text-muted hover:text-ink">Close at market</button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
            <Panel title={<span className="inline-flex items-center gap-2"><ListOrdered className="size-4 text-accent" /> Orders</span>}>
              {data.orders.length === 0 ? (
                <p className="text-sm text-faint">No orders yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-xs">
                    <thead className="text-left text-muted">
                      <tr className="border-b border-line">
                        <th className="py-1.5 pr-2 font-medium">Placed</th>
                        <th className="py-1.5 pr-2 font-medium">Order</th>
                        <th className="py-1.5 pr-2 font-medium">Bracket</th>
                        <th className="py-1.5 pr-2 font-medium">Status</th>
                        <th className="py-1.5 font-medium" />
                      </tr>
                    </thead>
                    <tbody>
                      {data.orders.map((o) => (
                        <tr key={o.id} className="border-b border-line/60">
                          <td className="py-1.5 pr-2 text-faint">{fmtTime(o.placed, true)}</td>
                          <td className="py-1.5 pr-2">
                            <span className={o.side === "BUY" ? "text-bull" : "text-bear"}>{o.side}</span> {o.qty} {o.symbol} · {o.type}
                            {o.price ? ` @ ${o.price}` : ""}{o.trigger ? ` trigger ${o.trigger}` : ""} · <span className="capitalize">{o.product}</span>
                          </td>
                          <td className="py-1.5 pr-2 text-muted">
                            {[o.stop && `stop ${o.stop}`, o.target && `target ${o.target}`, o.trail_pct && `trail ${o.trail_pct}%`].filter(Boolean).join(" · ") || "—"}
                          </td>
                          <td className="py-1.5 pr-2">
                            <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${o.status === "open" ? "bg-accent/10 text-accent" : o.status === "filled" ? "bg-bull/10 text-bull" : "bg-panel-2 text-muted"}`}>{o.status}</span>
                            {o.fill_price && <span className="ml-1 text-faint">@ {o.fill_price}</span>}
                            {o.reason && <span className="ml-1 text-faint">{o.reason}</span>}
                          </td>
                          <td className="py-1.5 text-right">
                            {o.status === "open" && (
                              <button onClick={() => act(() => cancelOrder(device, o.id))} className="rounded-md border border-line px-2 py-0.5 text-[11px] text-muted hover:text-bear">Cancel</button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
            <Journal device={device} data={data} />
            <HowItWorks label="Paper trading rules">
              <span className="inline-flex items-start gap-1.5"><NotebookPen className="mt-0.5 size-3.5 shrink-0" /> {data.rules} Your account is kept for this browser (no login).</span>
            </HowItWorks>
          </>
        )}
      </main>
      <Disclaimer />
    </>
  );
}

function EquityLine({ points, capital }: { points: [string, number][]; capital: number }) {
  const vals = points.map((p) => p[1]);
  const lo = Math.min(capital, ...vals), hi = Math.max(capital, ...vals);
  const span = hi - lo || 1;
  const w = 600, h = 70;
  const y = (v: number) => h - ((v - lo) / span) * (h - 6) - 3;
  const xy = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= capital;
  return (
    <div className="rounded-2xl border border-line bg-panel px-4 py-3">
      <p className="text-[11px] text-muted">Account value by day</p>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-[70px] w-full" preserveAspectRatio="none" role="img" aria-label="Paper account value by day">
        <line x1="0" x2={w} y1={y(capital)} y2={y(capital)} className="stroke-line" strokeDasharray="3 3" />
        <polyline points={xy} fill="none" className={up ? "stroke-bull" : "stroke-bear"} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}
