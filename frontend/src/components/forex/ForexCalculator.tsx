"use client";

import { useState } from "react";
import { X } from "lucide-react";
import { FX_TICK, fmtFx, fxPlan, loadFxPrefs, MARGIN_PCT, pairLabel, saveFxPrefs } from "@/lib/forex";
import type { OpeningRange } from "@/lib/types";
import { DayTraderPanel } from "../DayTraderTools";

export interface FxSetup {
  pair: string;
  side: "BUY" | "SELL";
  entry: number;
  stop: number;
  target: number;
  lotUnits: number;
  source: string;
}

const inr = (v: number) => `₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;
const signed = (v: number) => `${v >= 0 ? "+" : "−"}${inr(v)}`;

function Num({ label, value, onChange, step = "0.0025" }: { label: string; value: number; onChange: (v: number) => void; step?: string }) {
  return (
    <label className="block">
      <span className="text-[11px] font-medium text-muted">{label}</span>
      <input
        type="number"
        inputMode="decimal"
        step={step}
        value={Number.isFinite(value) ? value : ""}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="tabular mt-0.5 w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
      />
    </label>
  );
}

/** Position size in lots for a currency-futures trade, with Upstox charges and an estimated margin. */
export function ForexCalculator({ setup, onClose, atr, openingRange }: {
  setup: FxSetup; onClose?: () => void; atr?: number | null; openingRange?: OpeningRange | null;
}) {
  const [side, setSide] = useState(setup.side);
  const [entry, setEntry] = useState(setup.entry);
  const [stop, setStop] = useState(setup.stop);
  const [target, setTarget] = useState(setup.target);
  const [prefs, setPrefs] = useState(loadFxPrefs);
  const update = (p: Partial<typeof prefs>) => {
    const next = { ...prefs, ...p };
    setPrefs(next);
    saveFxPrefs(next);
  };
  // Switching side mirrors the levels around the entry, so a BUY setup becomes a sensible SELL.
  const flip = (s: "BUY" | "SELL") => {
    if (s === side) return;
    setSide(s);
    setStop(+(2 * entry - stop).toFixed(4));
    setTarget(+(2 * entry - target).toFixed(4));
  };
  const plan = fxPlan({ ...prefs, side, entry, stop, target, lotUnits: setup.lotUnits });
  const ticks = (a: number, b: number) => Math.round(Math.abs(a - b) / FX_TICK);

  return (
    <section className="rounded-2xl border border-line bg-panel p-4 shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
      <div className="flex items-center justify-between gap-2">
        <p className="font-semibold">
          Lot calculator · {pairLabel(setup.pair)} <span className="text-xs font-normal text-muted">from {setup.source}</span>
        </p>
        {onClose && (
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-panel-2 hover:text-ink" aria-label="Close calculator">
            <X className="size-4" />
          </button>
        )}
      </div>

      <div className="mt-3 inline-flex rounded-full bg-panel-2 p-1 text-sm font-semibold">
        {(["BUY", "SELL"] as const).map((s) => (
          <button
            key={s}
            onClick={() => flip(s)}
            className={`rounded-full px-4 py-1 ${side === s ? (s === "BUY" ? "bg-bull text-white" : "bg-bear text-white") : "text-muted"}`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-3">
        <Num label="Entry" value={entry} onChange={setEntry} />
        <Num label={`Stop-loss (${ticks(entry, stop)} ticks)`} value={stop} onChange={setStop} />
        <Num label={`Target (${ticks(entry, target)} ticks)`} value={target} onChange={setTarget} />
        <Num label="Capital ₹" value={prefs.capital} onChange={(v) => update({ capital: v })} step="1000" />
        <Num label="Risk per trade %" value={prefs.riskPct} onChange={(v) => update({ riskPct: v })} step="0.25" />
      </div>

      {!plan ? (
        <p className="mt-3 rounded-xl bg-warn/10 px-3 py-2 text-sm text-warn">
          Check the levels: for a {side} the stop must be {side === "BUY" ? "below" : "above"} the entry.
        </p>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2 text-center sm:grid-cols-4">
            <div className="rounded-xl bg-panel-2 px-2 py-2.5">
              <p className="text-[11px] text-muted">Lots</p>
              <p className="tabular text-xl font-bold">{plan.lots}</p>
              <p className="tabular text-[11px] text-muted">{plan.units.toLocaleString("en-IN")} units</p>
            </div>
            <div className="rounded-xl bg-bull/10 px-2 py-2.5">
              <p className="text-[11px] text-muted">If target hits</p>
              <p className="tabular text-lg font-semibold text-bull">{signed(plan.atTarget.net)}</p>
              <p className="text-[10px] text-faint">after {inr(plan.atTarget.charges)} charges</p>
            </div>
            <div className="rounded-xl bg-bear/10 px-2 py-2.5">
              <p className="text-[11px] text-muted">If stop hits</p>
              <p className="tabular text-lg font-semibold text-bear">{signed(plan.atStop.net)}</p>
              <p className="text-[10px] text-faint">after {inr(plan.atStop.charges)} charges</p>
            </div>
            <div className="rounded-xl bg-panel-2 px-2 py-2.5">
              <p className="text-[11px] text-muted">Margin (est.)</p>
              <p className="tabular text-lg font-semibold">{inr(plan.margin)}</p>
              <p className="text-[10px] text-faint">≈{MARGIN_PCT}% of contract value</p>
            </div>
          </div>
          {plan.lots === 0 ? (
            <p className="mt-3 rounded-xl bg-warn/10 px-3 py-2 text-sm text-warn">
              One lot risks {inr(plan.riskPerLot)} at this stop, more than {prefs.riskPct}% of {inr(prefs.capital)}. Raise the capital or risk %, or
              use a closer stop.
            </p>
          ) : (
            plan.atTarget.net <= 0 && (
              <p className="mt-3 rounded-xl bg-warn/10 px-3 py-2 text-sm text-warn">
                Even at the target this loses money: the move is too small to cover the charges at {plan.lots} lot{plan.lots > 1 ? "s" : ""}. Skip it, or trade
                more lots.
              </p>
            )
          )}
          <p className="mt-3 text-xs text-muted">
            Break-even at <b className="tabular text-ink">{fmtFx(plan.breakeven)}</b>
            {plan.rr != null && <> · risk : reward 1 : {plan.rr.toFixed(1)}</>}
            {plan.limitedBy === "margin" && plan.lots > 0 && <> · limited by margin, not by risk</>}. One lot = {setup.lotUnits.toLocaleString("en-IN")}{" "}
            units, so every 0.0025 move is ₹{(FX_TICK * setup.lotUnits).toFixed(2)} per lot. Upstox charges ₹20 per order (or 0.05%) plus small exchange
            fees; there is no STT on currency futures.
          </p>
        </>
      )}
      <DayTraderPanel
        plan={{
          market: "forex", symbol: setup.pair, side, entry, stop, target, dp: 4, qtyLabel: "Lots", intraday: true,
          qty: plan?.lots ?? 0, lossAtStop: plan && plan.lots > 0 ? -plan.atStop.net : null, atr, openingRange,
        }}
        onLevels={(s, t) => {
          setStop(s);
          setTarget(t);
        }}
      />
    </section>
  );
}
