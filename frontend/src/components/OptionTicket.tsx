"use client";

import { useEffect, useMemo, useState } from "react";
import { Copy, Minus, Plus, X } from "lucide-react";
import { roundTrip } from "@/lib/charges";
import { fmtDate, fmtPrice } from "@/lib/format";
import type { OptionChain, OptionLeg } from "@/lib/types";
import { loadPrefs } from "./TradeCalculator";

export interface TicketContract {
  kind: "CE" | "PE";
  strike: number;
  leg: OptionLeg;
}

const TICK = 0.05;
const tick = (v: number) => Math.round(Math.round(v / TICK) * TICK * 100) / 100;
const inr = (v: number) => `₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

/** Suggested premium stop and target for any contract: the pick's own levels, else the index move through delta and gamma. */
export function suggestLevels(data: OptionChain, c: TicketContract): { entry: number; stop: number; target: number } {
  const cand = data.recommendation.candidates[c.kind];
  const entry = tick(c.leg.ask && c.leg.ask > 0 ? c.leg.ask : (c.leg.ltp ?? 0));
  if (cand && cand.strike === c.strike) return { entry: cand.entry, stop: cand.stop, target: cand.target };
  const spot = data.spot ?? 0;
  const moveT = cand ? Math.abs(cand.underlying_target - spot) : spot * 0.005;
  const moveS = cand ? Math.abs(spot - cand.underlying_stop) : spot * 0.003;
  const d = Math.abs(c.leg.delta ?? 0) || 0.5;
  const g = c.leg.gamma ?? 0;
  return {
    entry,
    target: tick(entry + d * moveT + 0.5 * g * moveT * moveT),
    stop: tick(Math.max(TICK, entry - d * moveS + 0.5 * g * moveS * moveS)),
  };
}

function Field({ label, value, onChange, onClear, hint, disabled }: {
  label: React.ReactNode; value: number | null; onChange: (v: number | null) => void; onClear?: () => void; hint?: string; disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="pt-2 text-sm text-ink">
        <span className="inline-flex items-center gap-2">
          {label}
          {onClear && value != null && (
            <button onClick={onClear} className="text-muted hover:text-ink" aria-label="Clear">
              <X className="size-3.5 rounded-full ring-1 ring-current" />
            </button>
          )}
        </span>
      </div>
      <div className="w-36 text-right">
        <input
          type="number"
          inputMode="decimal"
          step="0.05"
          disabled={disabled}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value === "" ? null : parseFloat(e.target.value))}
          className="tabular w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-right text-base text-ink outline-none focus:border-accent disabled:opacity-50"
        />
        {hint && <p className="mt-0.5 text-[11px] text-faint">{hint}</p>}
      </div>
    </div>
  );
}

/** Broker-style order ticket for one option contract. Plans the trade only: nothing is sent to Upstox. */
export function OptionTicket({ data, contract, onClose }: { data: OptionChain; contract: TicketContract; onClose: () => void }) {
  const lot = data.lot_size ?? 1;
  const ltp = contract.leg.ltp ?? 0;
  const suggested = useMemo(() => suggestLevels(data, contract), [data, contract]);
  const [lots, setLots] = useState(1);
  const [market, setMarket] = useState(false);
  const [price, setPrice] = useState<number | null>(suggested.entry);
  const [stop, setStop] = useState<number | null>(suggested.stop);
  const [target, setTarget] = useState<number | null>(suggested.target);
  const [trail, setTrail] = useState(false);
  const [trailBy, setTrailBy] = useState<number | null>(tick(Math.max(TICK, suggested.entry * 0.1)));
  const [copied, setCopied] = useState(false);
  const [balance, setBalance] = useState(0);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of the calculator's saved capital
    setBalance(loadPrefs().capital);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const qty = lots * lot;
  const buy = market ? ltp : (price ?? 0);
  const fromMarket = ltp > 0 && !market && price ? ((price - ltp) / ltp) * 100 : null;
  const result = (exit: number) => {
    const c = roundTrip("option", qty, buy, exit);
    return { gross: (exit - buy) * qty, charges: c.total, net: (exit - buy) * qty - c.total };
  };
  const atTarget = target != null && target > 0 ? result(target) : null;
  const atStop = stop != null && stop > 0 ? result(stop) : null;
  const buyCharges = roundTrip("option", qty, buy, 0).total;
  const required = buy * qty + buyCharges;
  const bad = (stop != null && stop >= buy) || (target != null && target <= buy);
  const rr = atTarget && atStop && atStop.net < 0 ? atTarget.net / -atStop.net : null;
  const name = `${data.symbol} ${fmtDate(data.expiry)} ${contract.strike.toLocaleString("en-IN")} ${contract.kind === "CE" ? "Call" : "Put"}`;
  const chgPct = contract.leg.change != null && ltp - contract.leg.change > 0 ? (contract.leg.change / (ltp - contract.leg.change)) * 100 : null;

  const copy = async () => {
    const text = [
      `BUY ${name}`,
      `Qty ${qty} (${lots} lot${lots > 1 ? "s" : ""} x ${lot})`,
      market ? "Price: MARKET" : `Price: LIMIT ${fmtPrice(price)}`,
      stop != null ? `Stop-loss: ${fmtPrice(stop)}${trail && trailBy ? ` (trail by ${fmtPrice(trailBy)})` : ""}` : null,
      target != null ? `Target: ${fmtPrice(target)}` : null,
    ].filter(Boolean).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 sm:items-center" onClick={onClose}>
      <div
        role="dialog"
        aria-label={`Order ticket ${name}`}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[92vh] w-full max-w-md overflow-y-auto rounded-t-2xl border border-line bg-panel shadow-2xl sm:rounded-2xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <p className="font-semibold text-ink">{name}</p>
            <p className="tabular text-xs text-muted">
              ₹{fmtPrice(ltp)}
              {chgPct != null && <span className={chgPct >= 0 ? "text-bull" : "text-bear"}> ({chgPct >= 0 ? "+" : ""}{chgPct.toFixed(2)}%)</span>}
              {contract.leg.delta != null && ` · delta ${Math.abs(contract.leg.delta).toFixed(2)}`}
              {contract.leg.iv != null && ` · IV ${contract.leg.iv.toFixed(1)}%`}
            </p>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-panel-2 hover:text-ink" aria-label="Close">
            <X className="size-5" />
          </button>
        </header>

        <div className="space-y-4 px-4 py-4">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-ink">
              Qty <span className="text-xs text-muted">{lots} lot × {lot}</span>
            </p>
            <div className="flex w-36 items-center justify-between rounded-lg border border-line bg-panel-2">
              <button onClick={() => setLots((n) => Math.max(1, n - 1))} className="px-3 py-2 text-muted hover:text-ink" aria-label="One lot less">
                <Minus className="size-4" />
              </button>
              <span className="tabular text-base font-semibold text-ink">{qty}</span>
              <button onClick={() => setLots((n) => Math.min(100, n + 1))} className="px-3 py-2 text-muted hover:text-ink" aria-label="One lot more">
                <Plus className="size-4" />
              </button>
            </div>
          </div>

          <Field
            label={
              <button onClick={() => setMarket((m) => !m)} className="text-sm text-ink">
                Price <strong>{market ? "Market" : "Limit"}</strong> <span className="text-muted">⇅</span>
              </button>
            }
            value={market ? ltp : price}
            onChange={setPrice}
            disabled={market}
            hint={fromMarket != null ? `${fromMarket >= 0 ? "+" : ""}${fromMarket.toFixed(2)}% from market` : market ? "at the market price" : undefined}
          />

          <div>
            <Field label="Stoploss (SL)" value={stop} onChange={setStop} onClear={() => setStop(null)} hint={`suggested ₹${fmtPrice(suggested.stop)}`} />
            <label className="mt-1 flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={trail} onChange={(e) => setTrail(e.target.checked)} className="accent-accent" /> Trail
              {trail && (
                <span className="flex items-center gap-1.5 text-xs">
                  by ₹
                  <input
                    type="number"
                    step="0.05"
                    value={trailBy ?? ""}
                    onChange={(e) => setTrailBy(e.target.value === "" ? null : parseFloat(e.target.value))}
                    className="tabular w-20 rounded-md border border-line bg-panel-2 px-2 py-1 text-ink outline-none focus:border-accent"
                  />
                  as the premium rises
                </span>
              )}
            </label>
          </div>

          <Field label="Target (TGT)" value={target} onChange={setTarget} onClear={() => setTarget(null)} hint={`suggested ₹${fmtPrice(suggested.target)}`} />

          {bad && <p className="rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn">For a buy, the stop-loss must be below the price and the target above it.</p>}
          {!bad && balance > 0 && required > balance && (
            <p className="rounded-lg bg-warn/10 px-3 py-2 text-center text-xs text-warn">
              Capital {inr(balance)} (from the calculator) is not enough for {inr(required)}.
            </p>
          )}

          <div className="grid grid-cols-2 gap-2 text-center">
            <div className="rounded-lg bg-bull/10 px-2 py-2">
              <p className="text-[11px] text-muted">If target hits</p>
              <p className="tabular text-base font-semibold text-bull">{atTarget ? `${atTarget.net >= 0 ? "+" : "−"}${inr(atTarget.net)}` : "—"}</p>
              {atTarget && <p className="text-[10px] text-faint">after {inr(atTarget.charges)} charges</p>}
            </div>
            <div className="rounded-lg bg-bear/10 px-2 py-2">
              <p className="text-[11px] text-muted">If stop-loss hits</p>
              <p className="tabular text-base font-semibold text-bear">{atStop ? `${atStop.net >= 0 ? "+" : "−"}${inr(atStop.net)}` : "—"}</p>
              {atStop && <p className="text-[10px] text-faint">after {inr(atStop.charges)} charges</p>}
            </div>
          </div>
          {rr != null && <p className="text-center text-xs text-muted">Risk : reward 1 : {rr.toFixed(1)}</p>}
        </div>

        <footer className="sticky bottom-0 space-y-2 border-t border-line bg-panel px-4 py-3">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>Capital: {balance > 0 ? inr(balance) : "—"}</span>
            <span>
              Approx req: <strong className="tabular text-ink">{inr(required)}</strong>
            </span>
          </div>
          <button
            onClick={copy}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-bull py-3 text-sm font-semibold text-white hover:opacity-90"
          >
            <Copy className="size-4" /> {copied ? "Copied" : "Copy order for your broker app"}
          </button>
          <p className="text-center text-[10px] text-faint">Plan only: nothing is sent to Upstox. Place the order in your broker app.</p>
        </footer>
      </div>
    </div>
  );
}
