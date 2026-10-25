"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Calculator, Wallet, X } from "lucide-react";
import { MIN_STOP_PCT, kellyRiskPct, plan, type Product, type SizeLimit } from "@/lib/charges";
import { fmtPrice } from "@/lib/format";
import { placeOrder, useDevice } from "@/lib/paper";
import type { OpeningRange } from "@/lib/types";
import { DayTraderPanel } from "./DayTraderTools";

const PREFS = "ai-stock:calc";
export const PREFS_EVENT = "ai-stock:calc-changed";

/** Capital and risk % saved by the calculator; the chart uses the same values. */
export function loadPrefs(): { capital: number; riskPct: number } {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS) ?? "{}");
    return { capital: Number(p.capital) || 100000, riskPct: Number(p.riskPct) || 1 };
  } catch {
    return { capital: 100000, riskPct: 1 };
  }
}

const rs = (v: number) => `₹${Math.round(v).toLocaleString("en-IN")}`;
const signed = (v: number) => `${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

export interface TradeSetup {
  symbol: string;
  side: "BUY" | "SELL";
  entry: number;
  stop: number;
  target: number;
  product?: Product;
  source?: string;
}

function Num({ label, value, onChange, step = "0.05", prefix }: { label: string; value: number; onChange: (v: number) => void; step?: string; prefix?: string }) {
  return (
    <label className="block min-w-0">
      <span className="text-[11px] text-muted">{label}</span>
      <span className="mt-0.5 flex items-center rounded-md border border-line bg-panel-2 focus-within:border-accent">
        {prefix && <span className="pl-2 text-xs text-faint">{prefix}</span>}
        <input
          type="number"
          inputMode="decimal"
          step={step}
          value={Number.isFinite(value) ? value : ""}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="tabular w-full min-w-0 bg-transparent px-2 py-1.5 text-sm text-ink outline-none"
        />
      </span>
    </label>
  );
}

/** This chart's past signal trades on one side: feeds the quarter-Kelly cap. */
export interface TrackRecord {
  winLowPct: number | null;
  avgWinPct: number | null;
  avgLossPct: number | null;
  trades: number;
}

type SizingMode = "risk" | "volatility" | "kelly";
const SIZING_KEY = "ai-stock:sizing";
const LIMIT_TEXT: Record<SizeLimit, (riskPct: number) => string> = {
  risk: (r) => `your ${r}% risk at the stop`,
  capital: () => "your capital",
  volatility: (r) => `the volatility cap (one ATR move = ${r}% of capital)`,
  kelly: () => "quarter-Kelly from this chart's track record",
};

/** Position size from risk, with Upstox charges and net result at the target and at the stop. */
export function TradeCalculator({ setup, onClose, atr, openingRange, record, chandelier }: {
  setup: TradeSetup; onClose?: () => void; atr?: number | null; openingRange?: OpeningRange | null;
  record?: Partial<Record<"BUY" | "SELL", TrackRecord>>;
  /** Chandelier exit levels (22-candle extreme -/+ 3 ATR) for a long and a short. */
  chandelier?: Partial<Record<"BUY" | "SELL", number | null>>;
}) {
  const [mode, setMode] = useState<SizingMode>("risk");
  useEffect(() => {
    try {
      const m = localStorage.getItem(SIZING_KEY);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of the saved sizing mode
      if (m === "volatility" || m === "kelly") setMode(m);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const chooseMode = (m: SizingMode) => {
    setMode(m);
    try {
      localStorage.setItem(SIZING_KEY, m);
    } catch {
      /* storage unavailable */
    }
  };
  const [capital, setCapital] = useState(100000);
  const [riskPct, setRiskPct] = useState(1);
  const [side, setSide] = useState(setup.side);
  const [product, setProduct] = useState<Product>(setup.product ?? "intraday");
  const [entry, setEntry] = useState(setup.entry);
  const [stop, setStop] = useState(setup.stop);
  const [target, setTarget] = useState(setup.target);
  // Switching BUY <-> SELL mirrors the stop and target around the entry, so they stay on the right sides.
  const changeSide = (next: "BUY" | "SELL") => {
    if (next === side) return;
    setSide(next);
    if (Number.isFinite(entry) && Number.isFinite(stop) && Number.isFinite(target)) {
      setStop(+(2 * entry - stop).toFixed(2));
      setTarget(+(2 * entry - target).toFixed(2));
    }
  };

  useEffect(() => {
    const p = loadPrefs();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of saved preferences
    setCapital(p.capital);
    setRiskPct(p.riskPct);
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(PREFS, JSON.stringify({ capital, riskPct }));
      window.dispatchEvent(new Event(PREFS_EVENT));
    } catch {
      /* storage unavailable */
    }
  }, [capital, riskPct]);

  const rec = record?.[side];
  const kellyPct = mode === "kelly" && rec && rec.trades >= 30 ? kellyRiskPct(rec.winLowPct, rec.avgWinPct, rec.avgLossPct) : null;
  const result = useMemo(
    () => plan({ capital, riskPct, side, entry, stop, target, product, atr: mode === "volatility" ? atr : null, kellyPct }),
    [capital, riskPct, side, entry, stop, target, product, mode, atr, kellyPct],
  );
  const tooTight = Number.isFinite(entry) && Number.isFinite(stop) && entry > 0 && (Math.abs(entry - stop) / entry) * 100 < MIN_STOP_PCT;
  const shortDelivery = side === "SELL" && product === "delivery";
  const wrongSide = side === "BUY" ? !(stop < entry && target > entry) : !(stop > entry && target < entry);

  return (
    <section className="rounded-xl border border-accent/30 bg-panel p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <Calculator className="size-4 text-accent" /> Trade calculator · {setup.symbol}
        </p>
        {onClose && (
          <button onClick={onClose} aria-label="Close calculator" className="text-faint hover:text-ink">
            <X className="size-4" />
          </button>
        )}
      </div>
      {setup.source && <p className="-mt-2 mb-3 text-[11px] text-faint">Pre-filled from {setup.source}. Edit any value.</p>}

      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        <div className="flex rounded-lg border border-line bg-panel-2 p-0.5">
          {(["BUY", "SELL"] as const).map((s) => (
            <button key={s} onClick={() => changeSide(s)}
              className={`rounded-md px-3 py-1 font-semibold ${side === s ? (s === "BUY" ? "bg-bull/20 text-bull" : "bg-bear/20 text-bear") : "text-muted"}`}>
              {s}
            </button>
          ))}
        </div>
        <div className="flex rounded-lg border border-line bg-panel-2 p-0.5">
          {(["intraday", "delivery"] as const).map((p) => (
            <button key={p} onClick={() => setProduct(p)}
              className={`rounded-md px-3 py-1 font-semibold capitalize ${product === p ? "bg-accent/15 text-accent" : "text-muted"}`}>
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">Size by</span>
        <div className="flex rounded-lg border border-line bg-panel-2 p-0.5">
          {(
            [
              ["risk", "Risk at stop", "Risk % of capital if the stop is hit (default)."],
              ["volatility", "+ Volatility cap", "Also cap the size so one ATR move costs no more than your risk %."],
              ["kelly", "+ Kelly cap", "Also cap at quarter-Kelly from this chart's past trades, using the low end of their win rate."],
            ] as const
          ).map(([m, label, title]) => (
            <button key={m} title={title} onClick={() => chooseMode(m)}
              disabled={m === "volatility" ? !atr : m === "kelly" ? !rec : false}
              className={`rounded-md px-2.5 py-1 font-medium disabled:opacity-40 ${mode === m ? "bg-accent/15 text-accent" : "text-muted"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {mode === "kelly" && rec && rec.trades < 30 && (
        <p className="-mt-1 mb-2 text-[11px] text-warn">Only {rec.trades} past {side} trades on this chart: too few for Kelly, so it is not applied.</p>
      )}
      {mode === "kelly" && kellyPct === 0 && (
        <p className="-mt-1 mb-2 text-[11px] text-bear">Kelly says skip: even this chart&apos;s best-case record has no edge after costs at the low end of its win rate.</p>
      )}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Num label="Capital" value={capital} onChange={setCapital} step="1000" prefix="₹" />
        <Num label="Risk per trade %" value={riskPct} onChange={setRiskPct} step="0.25" />
        <Num label="Entry" value={entry} onChange={setEntry} />
        <Num label="Stop-loss" value={stop} onChange={setStop} />
        <Num label="Target" value={target} onChange={setTarget} />
      </div>

      {shortDelivery ? (
        <p className="mt-3 text-xs text-warn">Selling first (short) is only allowed intraday. Switch to Intraday or BUY.</p>
      ) : wrongSide ? (
        <p className="mt-3 text-xs text-warn">For a {side}, the stop must be {side === "BUY" ? "below" : "above"} the entry and the target {side === "BUY" ? "above" : "below"} it.</p>
      ) : tooTight ? (
        <p className="mt-3 text-xs text-warn">
          The stop is within {MIN_STOP_PCT}% of the entry: the spread and normal noise would hit it. Widen it before sizing.
        </p>
      ) : !result || result.qty === 0 ? (
        <p className="mt-3 text-xs text-warn">The capital or risk is too small for even one share at this stop distance.</p>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-lg bg-accent/10 p-3">
              <p className="text-[11px] text-muted">Quantity</p>
              <p className="tabular text-2xl font-bold text-accent">{result.qty.toLocaleString("en-IN")}</p>
              <p className="text-[10px] text-faint" title={Object.entries(result.limits).map(([k, v]) => `${k}: ${v}`).join(" · ")}>
                limited by {LIMIT_TEXT[result.limitedBy](riskPct)}
              </p>
            </div>
            <div className="rounded-lg bg-panel-2/70 p-3">
              <p className="text-[11px] text-muted">Money needed</p>
              <p className="tabular text-lg font-semibold">{rs(result.invested)}</p>
              <p className="text-[10px] text-faint">{fmtPrice(entry)} × {result.qty}</p>
            </div>
            <div className="rounded-lg bg-bear/10 p-3">
              <p className="text-[11px] text-muted">If stop hits</p>
              <p className="tabular text-lg font-semibold text-bear">{signed(result.atStop.net)}</p>
              <p className="text-[10px] text-faint">risk {rs(result.riskRs)} + charges {rs(result.atStop.charges.total)}</p>
            </div>
            <div className="rounded-lg bg-bull/10 p-3">
              <p className="text-[11px] text-muted">If target hits</p>
              <p className="tabular text-lg font-semibold text-bull">{signed(result.atTarget.net)}</p>
              <p className="text-[10px] text-faint">reward {rs(result.rewardRs)} − charges {rs(result.atTarget.charges.total)}</p>
            </div>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs sm:grid-cols-4">
            <dt className="text-muted">Risk : reward</dt>
            <dd className="tabular text-right sm:text-left">{result.rr ? `1 : ${result.rr.toFixed(2)}` : "—"}</dd>
            <dt className="text-muted">Breakeven (after charges)</dt>
            <dd className="tabular text-right sm:text-left">{fmtPrice(result.breakeven)}</dd>
          </dl>
          <details className="mt-2 text-xs text-muted">
            <summary className="cursor-pointer text-faint hover:text-ink">Charges breakdown (at target)</summary>
            <dl className="mt-1 grid grid-cols-2 gap-y-0.5 sm:max-w-sm">
              {(
                [
                  ["Brokerage", result.atTarget.charges.brokerage],
                  ["STT", result.atTarget.charges.stt],
                  ["Exchange charges", result.atTarget.charges.exchange],
                  ["SEBI fee", result.atTarget.charges.sebi],
                  ["Stamp duty", result.atTarget.charges.stamp],
                  ["DP charge", result.atTarget.charges.dp],
                  ["GST", result.atTarget.charges.gst],
                ] as const
              ).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt>{k}</dt>
                  <dd className="tabular text-right">₹{v.toFixed(2)}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-1 text-[11px] text-faint">Approximate Upstox rates; check the current charges on upstox.com. Slippage not included.</p>
          </details>
        </>
      )}
      {!shortDelivery && !wrongSide && !tooTight && result && result.qty > 0 && (
        <PaperThis symbol={setup.symbol} side={side} qty={result.qty} product={product} stop={stop} target={target} source={setup.source} />
      )}
      {!shortDelivery && (
        <DayTraderPanel
          plan={{
            market: "stocks", symbol: setup.symbol, side, entry, stop, target, dp: 2, qtyLabel: "Qty", intraday: product === "intraday",
            qty: result?.qty ?? 0, lossAtStop: result && result.qty > 0 ? -result.atStop.net : null, atr, openingRange,
            chandelier: chandelier?.[side] ?? null,
          }}
          onLevels={(s, t) => {
            setStop(s);
            setTarget(t);
          }}
        />
      )}
    </section>
  );
}

/** Send this plan to paper trading as a bracket (market entry, stop and target), with the reason for the journal. */
function PaperThis({ symbol, side, qty, product, stop, target, source }: {
  symbol: string; side: "BUY" | "SELL"; qty: number; product: Product; stop: number; target: number; source?: string;
}) {
  const device = useDevice();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-accent/40 px-3 py-1.5 text-xs font-semibold text-accent hover:bg-accent/10">
        <Wallet className="size-3.5" /> Paper trade this plan
      </button>
    );
  }
  const go = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await placeOrder(device, { symbol, side, qty, type: "market", product: product === "delivery" ? "delivery" : "intraday", stop, target, note, setup: source ?? null });
      const o = r.order;
      setMsg({ ok: o.status !== "rejected", text: o.status === "filled" ? `Filled at ₹${o.fill_price}, with the stop and target attached.` : o.status === "rejected" ? `Rejected: ${o.reason}` : "Placed: it fills at the next quote while the market is open." });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-3 rounded-lg border border-accent/30 bg-accent/5 p-3 text-xs">
      <p className="font-semibold text-ink">
        Paper {side} {qty} {symbol} at market · stop {stop} · target {target} · <span className="capitalize">{product}</span>
      </p>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why this trade? (kept in your paper journal)"
        className="mt-2 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-sm text-ink outline-none focus:border-accent" />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button onClick={go} disabled={busy || note.trim().length < 3 || !device} className="rounded-full bg-accent px-4 py-1.5 font-semibold text-white disabled:opacity-50">
          {busy ? "Placing…" : "Place paper order"}
        </button>
        <button onClick={() => setOpen(false)} className="text-muted">Cancel</button>
        {msg && (
          <span className={msg.ok ? "text-bull" : "text-bear"}>
            {msg.text} {msg.ok && <Link href="/paper" className="underline">Open paper trading</Link>}
          </span>
        )}
      </div>
      <p className="mt-1 text-[10px] text-faint">Fake money only. The server fills it from its own quote; nothing reaches a broker.</p>
    </div>
  );
}
