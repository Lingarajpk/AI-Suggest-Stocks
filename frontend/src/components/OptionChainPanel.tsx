"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { Layers3 } from "lucide-react";
import { useOptionChain } from "@/lib/api";
import { fmtCompact, fmtDate, fmtPrice, fmtTime } from "@/lib/format";
import type { OptionChain, OptionLeg, OptionPick, OptionRow } from "@/lib/types";
import { OptionTicket } from "./OptionTicket";
import { Panel, SkeletonRows } from "./ui";

type OnPick = (kind: "CE" | "PE", strike: number) => void;

/** Indices with an option chain; F&O stocks have one too (the backend checks). */
export const OPTION_SYMBOLS = ["NIFTY 50", "BANK NIFTY", "SENSEX"];

const inr = (v: number | null | undefined) => (v == null ? "—" : `₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`);
const signedK = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${fmtCompact(Math.abs(v))}`);

/** Option chain for an index or F&O stock; stocks without options get a one-line note. */
export function OptionChainPanel({ symbol, index = false }: { symbol: string; index?: boolean }) {
  const [expiry, setExpiry] = useState<string | null>(null);
  const { data, error, isLoading } = useOptionChain(symbol, expiry);
  const [ticket, setTicket] = useState<{ kind: "CE" | "PE"; strike: number } | null>(null);
  const onPick: OnPick = (kind, strike) => setTicket({ kind, strike });
  // The ticket follows the live chain: its leg (price, greeks) refreshes with every poll.
  const ticketRow = ticket && data?.rows.find((r) => r.strike === ticket.strike);
  const ticketLeg = ticket && ticketRow ? (ticket.kind === "CE" ? ticketRow.call : ticketRow.put) : null;
  if (!index && error?.code === "no_options") {
    return (
      <p className="rounded-2xl border border-line bg-panel px-4 py-3 text-xs text-faint">
        <Layers3 className="mr-1.5 inline size-3.5" />
        {symbol} has no F&amp;O options, so there is no option chain. Option chains show for NIFTY 50, BANK NIFTY, SENSEX and F&amp;O stocks.
      </p>
    );
  }

  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Layers3 className="size-4 text-accent" /> Option chain · {symbol}
        </span>
      }
      action={
        data && (
          <label className="flex items-center gap-2 text-xs text-muted">
            Expiry
            <select
              value={data.expiry}
              onChange={(e) => setExpiry(e.target.value)}
              className="rounded-md border border-line bg-panel-2 px-2 py-1 text-xs text-ink outline-none focus:border-accent"
            >
              {data.expiries.map((x) => (
                <option key={x} value={x}>
                  {fmtDate(x)}
                </option>
              ))}
            </select>
          </label>
        )
      }
    >
      {error ? (
        <p className="text-sm text-warn">{error.message}</p>
      ) : isLoading || !data ? (
        <SkeletonRows rows={6} />
      ) : (
        <div className="space-y-4">
          <Recommendation data={data} onPick={onPick} />
          <Summary data={data} />
          <p className="text-[11px] text-faint">Tap any call or put price to open an order ticket.</p>
          <ChainTable data={data} onPick={onPick} />
          <p className="text-[11px] leading-relaxed text-faint">
            {data.source === "demo" ? "DEMO: synthetic option prices. " : ""}Updated {fmtTime(data.generated_at)} · lot {data.lot_size ?? "—"} ·
            premium target/stop are estimates from the option&apos;s delta and gamma for the index&apos;s target and stop; time decay
            (theta) works against option buyers. Not investment advice.
          </p>
        </div>
      )}
      {data && ticket && ticketLeg && (
        <OptionTicket
          key={`${data.expiry}-${ticket.kind}-${ticket.strike}`}
          data={data}
          contract={{ kind: ticket.kind, strike: ticket.strike, leg: ticketLeg }}
          onClose={() => setTicket(null)}
        />
      )}
    </Panel>
  );
}

function Recommendation({ data, onPick }: { data: OptionChain; onPick: OnPick }) {
  const rec = data.recommendation;
  const pick = rec.pick;
  if (rec.action === "WAIT" || !pick) {
    const ce = rec.candidates.CE, pe = rec.candidates.PE;
    return (
      <div className="rounded-lg border border-line bg-panel-2 p-3">
        <p className="flex flex-wrap items-center gap-2">
          <span className="rounded-md bg-muted/15 px-2.5 py-1 text-sm font-bold text-muted">WAIT</span>
          <span className="text-sm text-ink">No option to buy right now</span>
        </p>
        <p className="mt-1.5 text-xs text-muted">{rec.reason}</p>
        {(ce || pe) && (
          <div className="mt-2 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            {ce && <Candidate label="If it turns BUY" pick={ce} symbol={data.symbol} onPick={onPick} />}
            {pe && <Candidate label="If it turns SELL" pick={pe} symbol={data.symbol} onPick={onPick} />}
          </div>
        )}
      </div>
    );
  }
  const call = pick.kind === "CE";
  return (
    <div className={`rounded-lg border p-3 ${call ? "border-bull/40 bg-bull/5" : "border-bear/40 bg-bear/5"}`}>
      <p className="flex flex-wrap items-center gap-2">
        <span className={`rounded-md px-2.5 py-1 text-sm font-bold ${call ? "bg-bull text-white" : "bg-bear text-white"}`}>
          BUY {pick.strike.toLocaleString("en-IN")} {pick.kind}
        </span>
        <span className="text-xs text-muted">
          {pick.moneyness} · delta {pick.delta.toFixed(2)}
          {pick.iv != null && ` · IV ${pick.iv.toFixed(1)}%`} · expiry {fmtDate(data.expiry)}
        </span>
      </p>
      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        <Level label="Buy at (premium)" value={pick.entry} sub={pick.lot_cost != null ? `${inr(pick.lot_cost)} per lot` : undefined} tone="text-ink" />
        <Level label="Target" value={pick.target} sub={pick.profit_per_lot != null ? `+${inr(pick.profit_per_lot)} / lot` : undefined} tone="text-bull" />
        <Level label="Stop-loss" value={pick.stop} sub={pick.loss_per_lot != null ? `−${inr(pick.loss_per_lot)} / lot` : undefined} tone="text-bear" />
      </div>
      <p className="mt-2 text-xs text-muted">
        Exit with the index: target when {data.symbol} reaches {fmtPrice(pick.underlying_target)}, stop if it hits {fmtPrice(pick.underlying_stop)}.
        {pick.theta_per_lot != null && ` Time decay costs about ${inr(pick.theta_per_lot)} per lot per day.`}
      </p>
      <p className={`mt-1.5 text-xs ${rec.against_oi ? "text-warn" : "text-faint"}`}>{rec.reason}</p>
      <button
        onClick={() => onPick(pick.kind, pick.strike)}
        className={`mt-3 w-full rounded-lg py-2.5 text-sm font-semibold text-white hover:opacity-90 ${call ? "bg-bull" : "bg-bear"}`}
      >
        Open order ticket · {pick.strike.toLocaleString("en-IN")} {pick.kind}
      </button>
    </div>
  );
}

function Level({ label, value, sub, tone }: { label: string; value: number; sub?: string; tone: string }) {
  return (
    <div className="rounded-md bg-panel px-2 py-2">
      <p className="text-[11px] text-muted">{label}</p>
      <p className={`tabular text-lg font-semibold ${tone}`}>₹{fmtPrice(value)}</p>
      {sub && <p className={`tabular text-[11px] ${tone}`}>{sub}</p>}
    </div>
  );
}

function Candidate({ label, pick, symbol, onPick }: { label: string; pick: OptionPick; symbol: string; onPick: OnPick }) {
  const call = pick.kind === "CE";
  return (
    <button onClick={() => onPick(pick.kind, pick.strike)} className="rounded-md bg-panel px-2.5 py-2 text-left hover:ring-1 hover:ring-line">
      <p className="text-[11px] text-muted">{label}</p>
      <p className={`font-semibold ${call ? "text-bull" : "text-bear"}`}>
        {pick.strike.toLocaleString("en-IN")} {pick.kind} @ ₹{fmtPrice(pick.entry)}
      </p>
      <p className="tabular text-[11px] text-faint">
        target ₹{fmtPrice(pick.target)} (+{inr(pick.profit_per_lot)}/lot) · stop ₹{fmtPrice(pick.stop)} (−{inr(pick.loss_per_lot)}/lot) · if {symbol}{" "}
        {call ? "rises to" : "falls to"} {fmtPrice(pick.underlying_target)}
      </p>
    </button>
  );
}

function Summary({ data }: { data: OptionChain }) {
  const s = data.summary;
  const tone = s.bias === "bullish" ? "text-bull" : s.bias === "bearish" ? "text-bear" : "text-muted";
  const chips: [string, string, string?][] = [
    ["Spot", fmtPrice(data.spot)],
    ["PCR (OI)", s.pcr?.toFixed(2) ?? "—", s.pcr == null ? undefined : s.pcr >= 1.2 ? "text-bull" : s.pcr <= 0.8 ? "text-bear" : undefined],
    ["Max pain", s.max_pain?.toLocaleString("en-IN") ?? "—"],
    ["Support (put OI)", s.put_wall?.toLocaleString("en-IN") ?? "—", "text-bull"],
    ["Resistance (call OI)", s.call_wall?.toLocaleString("en-IN") ?? "—", "text-bear"],
    ["ATM IV", s.atm_iv != null ? `${s.atm_iv.toFixed(1)}%` : "—"],
    ["Expected move (straddle)", s.straddle != null ? `±${fmtPrice(s.straddle)}` : "—"],
    ["OI leans", s.bias, tone],
  ];
  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {chips.map(([k, v, c]) => (
          <div key={k} className="rounded-md border border-line bg-panel-2 px-2.5 py-1.5">
            <p className="text-[10px] text-muted">{k}</p>
            <p className={`tabular text-sm font-semibold capitalize ${c ?? "text-ink"}`}>{v}</p>
          </div>
        ))}
      </div>
      {s.notes.length > 0 && <p className="mt-2 text-[11px] text-faint">{s.notes.join(" · ")}</p>}
    </div>
  );
}

function ChainTable({ data, onPick }: { data: OptionChain; onPick: OnPick }) {
  const box = useRef<HTMLDivElement>(null);
  const atmRow = useRef<HTMLTableRowElement>(null);
  const spot = data.spot ?? 0;
  const atm = data.summary.atm;
  const pick = data.recommendation.pick;
  const maxOi = Math.max(1, ...data.rows.flatMap((r) => [r.call?.oi ?? 0, r.put?.oi ?? 0]));

  // Centre the at-the-money strike once per expiry, without moving the page.
  useEffect(() => {
    const b = box.current, row = atmRow.current;
    if (b && row) b.scrollTop = row.offsetTop - b.clientHeight / 2 + row.clientHeight / 2;
  }, [data.expiry, data.symbol]);

  const spotIndex = data.rows.findIndex((r) => r.strike > spot);
  return (
    <div ref={box} className="relative max-h-[520px] overflow-auto rounded-lg border border-line">
      <table className="w-full min-w-[520px] text-right text-xs">
        <thead className="sticky top-0 z-10 bg-panel-2 text-[10px] uppercase tracking-wide text-muted">
          <tr>
            <th colSpan={3} className="border-b border-line px-2 py-1.5 text-center text-bull">Calls (CE)</th>
            <th className="border-b border-line px-2 py-1.5 text-center">Strike</th>
            <th colSpan={3} className="border-b border-line px-2 py-1.5 text-center text-bear">Puts (PE)</th>
          </tr>
          <tr>
            <th className="px-2 py-1 font-medium">OI</th>
            <th className="px-2 py-1 font-medium">Chg OI</th>
            <th className="px-2 py-1 font-medium">LTP</th>
            <th className="px-2 py-1 text-center font-medium">·</th>
            <th className="px-2 py-1 text-left font-medium">LTP</th>
            <th className="px-2 py-1 text-left font-medium">Chg OI</th>
            <th className="px-2 py-1 text-left font-medium">OI</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {data.rows.map((r, i) => (
            <Fragment key={r.strike}>
              {i === spotIndex && (
                <tr>
                  <td colSpan={7} className="bg-accent/10 py-0.5 text-center text-[10px] font-semibold text-accent">
                    ▲ Spot {fmtPrice(spot)}
                  </td>
                </tr>
              )}
              <Row
                row={r}
                spot={spot}
                maxOi={maxOi}
                atm={r.strike === atm}
                picked={pick && pick.strike === r.strike ? pick.kind : null}
                rowRef={r.strike === atm ? atmRow : undefined}
                onPick={onPick}
              />
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({ row, spot, maxOi, atm, picked, rowRef, onPick }: {
  row: OptionRow; spot: number; maxOi: number; atm: boolean; picked: "CE" | "PE" | null; rowRef?: React.Ref<HTMLTableRowElement>; onPick: OnPick;
}) {
  const ltpButton = (kind: "CE" | "PE", leg: OptionLeg | null) =>
    leg?.ltp ? (
      <button onClick={() => onPick(kind, row.strike)} className="rounded px-1 underline-offset-2 hover:bg-accent/15 hover:underline" title="Open order ticket">
        {fmtPrice(leg.ltp)}
      </button>
    ) : (
      "—"
    );
  const callItm = row.strike < spot, putItm = row.strike > spot;
  const ring = (k: "CE" | "PE") => (picked === k ? "outline outline-2 -outline-offset-2 " + (k === "CE" ? "outline-bull" : "outline-bear") : "");
  return (
    <tr ref={rowRef} className={`border-t border-line/60 ${atm ? "bg-accent/5" : ""}`}>
      <OiCell leg={row.call} maxOi={maxOi} side="call" className={`${callItm ? "bg-warn/5" : ""} ${ring("CE")}`} />
      <td className={`px-2 py-1 ${chgTone(row.call?.oi_change)} ${callItm ? "bg-warn/5" : ""}`}>{signedK(row.call?.oi_change)}</td>
      <td className={`px-2 py-1 font-semibold text-ink ${callItm ? "bg-warn/5" : ""} ${ring("CE")}`}>{ltpButton("CE", row.call)}</td>
      <td className={`px-2 py-1 text-center font-semibold ${atm ? "text-accent" : "text-ink"}`}>{row.strike.toLocaleString("en-IN")}</td>
      <td className={`px-2 py-1 text-left font-semibold text-ink ${putItm ? "bg-warn/5" : ""} ${ring("PE")}`}>{ltpButton("PE", row.put)}</td>
      <td className={`px-2 py-1 text-left ${chgTone(row.put?.oi_change)} ${putItm ? "bg-warn/5" : ""}`}>{signedK(row.put?.oi_change)}</td>
      <OiCell leg={row.put} maxOi={maxOi} side="put" className={`${putItm ? "bg-warn/5" : ""} ${ring("PE")}`} />
    </tr>
  );
}

const chgTone = (v: number | null | undefined) => (v == null || v === 0 ? "text-faint" : v > 0 ? "text-bull" : "text-bear");

/** Open interest with a bar showing its size relative to the biggest strike. */
function OiCell({ leg, maxOi, side, className }: { leg: OptionLeg | null; maxOi: number; side: "call" | "put"; className: string }) {
  const pct = Math.round(((leg?.oi ?? 0) / maxOi) * 100);
  return (
    <td className={`relative px-2 py-1 ${side === "put" ? "text-left" : ""} ${className}`}>
      <span
        className={`absolute inset-y-1 ${side === "call" ? "right-0 bg-bear/20" : "left-0 bg-bull/20"} rounded-sm`}
        style={{ width: `${pct}%` }}
      />
      <span className="relative text-muted">{fmtCompact(leg?.oi)}</span>
    </td>
  );
}
