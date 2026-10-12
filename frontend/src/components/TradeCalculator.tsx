"use client";

import { useEffect, useMemo, useState } from "react";
import { Calculator, X } from "lucide-react";
import { plan, type Product } from "@/lib/charges";
import { fmtPrice } from "@/lib/format";

const PREFS = "ai-stock:calc";

function loadPrefs(): { capital: number; riskPct: number } {
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

/** Position size from risk, with Upstox charges and net result at the target and at the stop. */
export function TradeCalculator({ setup, onClose }: { setup: TradeSetup; onClose?: () => void }) {
  const [capital, setCapital] = useState(100000);
  const [riskPct, setRiskPct] = useState(1);
  const [side, setSide] = useState(setup.side);
  const [product, setProduct] = useState<Product>(setup.product ?? "intraday");
  const [entry, setEntry] = useState(setup.entry);
  const [stop, setStop] = useState(setup.stop);
  const [target, setTarget] = useState(setup.target);

  useEffect(() => {
    const p = loadPrefs();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of saved preferences
    setCapital(p.capital);
    setRiskPct(p.riskPct);
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(PREFS, JSON.stringify({ capital, riskPct }));
    } catch {
      /* storage unavailable */
    }
  }, [capital, riskPct]);

  const result = useMemo(() => plan({ capital, riskPct, side, entry, stop, target, product }), [capital, riskPct, side, entry, stop, target, product]);
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
            <button key={s} onClick={() => setSide(s)}
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
      ) : !result || result.qty === 0 ? (
        <p className="mt-3 text-xs text-warn">The capital or risk is too small for even one share at this stop distance.</p>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-lg bg-accent/10 p-3">
              <p className="text-[11px] text-muted">Quantity</p>
              <p className="tabular text-2xl font-bold text-accent">{result.qty.toLocaleString("en-IN")}</p>
              <p className="text-[10px] text-faint">limited by {result.limitedBy === "risk" ? `your ${riskPct}% risk` : "your capital"}</p>
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
    </section>
  );
}
