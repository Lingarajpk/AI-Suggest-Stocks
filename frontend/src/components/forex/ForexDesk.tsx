"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Calculator, Clock, Globe, Info, Maximize2 } from "lucide-react";
import { useHealth } from "@/lib/api";
import { changeColor, fmtPct, fmtTime } from "@/lib/format";
import { FX_OPEN_MIN, FX_PAIRS, PAIR_SIGN, fmtFx, pairLabel, useForexDesk, useForexQuotes, type ForexCard, type LotCosts } from "@/lib/forex";
import { buildLiveBar } from "@/lib/liveBar";
import type { IntradayLeg, IntradayVerdict, Quote } from "@/lib/types";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { AppHeader } from "../AppHeader";
import { Disclaimer } from "../Disclaimer";
import { IntradayChart } from "../IntradayChart";
import { FreshnessBadge, HowItWorks } from "../ui";
import { ForexCalculator } from "./ForexCalculator";

const VERDICT: Record<IntradayVerdict, { label: string; bar: string; chip: string }> = {
  BUY: { label: "▲ BUY", bar: "bg-bull", chip: "bg-bull text-white" },
  SELL: { label: "▼ SELL", bar: "bg-bear", chip: "bg-bear text-white" },
  WAIT: { label: "WAIT", bar: "bg-warn", chip: "bg-warn/15 text-warn" },
  "NO TRADE": { label: "NO TRADE", bar: "bg-line", chip: "bg-panel-2 text-muted" },
};

export const FX_SESSION_NOTE = {
  open: null,
  pre_open: "Currency market opens at 09:00 IST: calls use the last completed candles until then.",
  closed: "Currency market closed (it trades 09:00–17:00 IST on weekdays): these are the last calls, not live.",
} as const;

const inr = (v: number) => `₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;
const signedChange = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${fmtFx(v)}`);

function Leg({ leg, label }: { leg: IntradayLeg; label: string }) {
  const tone = leg.lean > 0 ? "text-bull" : leg.lean < 0 ? "text-bear" : "text-muted";
  return (
    <div className="min-w-0 flex-1 rounded-xl bg-panel-2 px-3 py-2">
      <p className="flex items-center justify-between text-[11px] text-muted">
        <span>{label}</span>
        <span className={tone}>{leg.lean > 0 ? "▲ up" : leg.lean < 0 ? "▼ down" : "— flat"}</span>
      </p>
      {leg.up_pct != null ? (
        <p className="tabular text-base font-semibold">
          <span className="text-bull">{leg.up_pct.toFixed(0)}%</span>
          <span className="text-faint"> / </span>
          <span className="text-bear">{(100 - leg.up_pct).toFixed(0)}%</span>
          <span className="ml-1 text-[10px] font-normal text-faint">model</span>
        </p>
      ) : (
        <p className="text-xs text-muted">
          Rule score <b className={tone}>{leg.score == null ? "—" : `${leg.score > 0 ? "+" : ""}${leg.score.toFixed(0)}`}</b>
        </p>
      )}
    </div>
  );
}

function PairCard({ card, quote }: { card: ForexCard; quote: Quote | null }) {
  const sym = card.instrument.symbol;
  const [calc, setCalc] = useState(false);
  const liveBar = useMemo(
    () => (card.chart ? buildLiveBar("5m", card.chart.forming_candle, card.chart.candles.at(-1), quote, FX_OPEN_MIN) : null),
    [card.chart, quote],
  );
  if (card.error) {
    return (
      <section className="rounded-2xl border border-line bg-panel p-5 text-sm">
        <p className="font-semibold">{pairLabel(sym)}</p>
        <p className="mt-1 text-bear">{card.error}</p>
      </section>
    );
  }
  const v = VERDICT[card.verdict];
  const price = quote?.last_price ?? card.price;
  const lv = card.levels;
  const units = card.instrument.lot_units ?? 1000;
  const entry = lv ? (lv.entry_low + lv.entry_high) / 2 : null;
  const perLot = (to: number) => (entry != null ? Math.abs(to - entry) * units : 0);
  return (
    <section className="relative flex min-w-0 flex-col overflow-hidden rounded-2xl border border-line bg-panel p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
      <span className={`absolute inset-x-0 top-0 h-1 ${v.bar}`} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/forex/${sym}?tf=5m`} className="flex items-center gap-2 text-sm font-semibold text-muted hover:text-accent">
            <span className="grid size-6 place-items-center rounded-full bg-accent/10 text-xs font-bold text-accent">{PAIR_SIGN[sym] ?? "¤"}</span>
            {pairLabel(sym)}
          </Link>
          <p className="tabular text-[26px] leading-tight font-bold tracking-tight">{fmtFx(price)}</p>
          <p className={`tabular text-sm font-semibold ${changeColor(quote?.change)}`}>
            {signedChange(quote?.change)} ({fmtPct(quote?.change_pct)})
          </p>
          <p className="mt-0.5 truncate text-[11px] text-faint">{card.instrument.contract ?? card.instrument.name}</p>
        </div>
        <div className="text-right">
          <span className={`inline-block rounded-xl px-3.5 py-1.5 text-lg font-bold ${v.chip}`}>{v.label}</span>
          <p className="mt-1.5 flex items-center justify-end gap-1 text-[11px] text-faint">
            <Clock className="size-3" />
            {card.since_known && card.since ? `since ${fmtTime(card.since)}` : `as of ${fmtTime(card.last_candle_time)}`}
          </p>
          <div className="mt-0.5 flex justify-end">
            <FreshnessBadge freshness={quote?.freshness} />
          </div>
        </div>
      </div>

      <p className="mt-3 text-sm text-muted">{card.reason}</p>
      <div className="mt-3 flex gap-2">
        <Leg leg={card.entry} label="5-min entry" />
        <Leg leg={card.trend} label="15-min trend" />
      </div>

      {lv && entry != null ? (
        <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
          <div className="rounded-xl bg-panel-2 px-1 py-2">
            <dt className="text-[11px] text-muted">Entry</dt>
            <dd className="tabular text-sm font-semibold">{fmtFx(entry)}</dd>
          </div>
          <div className="rounded-xl bg-bull/10 px-1 py-2">
            <dt className="text-[11px] text-muted">Target</dt>
            <dd className="tabular text-sm font-semibold text-bull">{fmtFx(lv.target)}</dd>
            <dd className="tabular text-[10px] text-bull">+{inr(perLot(lv.target))}/lot</dd>
          </div>
          <div className="rounded-xl bg-bear/10 px-1 py-2">
            <dt className="text-[11px] text-muted">Stop-loss</dt>
            <dd className="tabular text-sm font-semibold text-bear">{fmtFx(lv.stop)}</dd>
            <dd className="tabular text-[10px] text-bear">−{inr(perLot(lv.stop))}/lot</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-3 rounded-xl bg-panel-2 px-3.5 py-2.5 text-sm text-muted">No entry yet: wait for both timeframes to agree.</p>
      )}
      {lv && card.costs && <CostLine costs={card.costs} gross={perLot(lv.target)} />}
      {card.action && card.action.label !== card.verdict && (
        <p className={`mt-2 rounded-xl px-3.5 py-2.5 text-sm font-semibold ${card.action.tone === "warn" ? "bg-warn/10 text-warn" : card.action.tone === "bull" ? "bg-bull/10 text-bull" : "bg-bear/10 text-bear"}`}>
          {card.action.label}: {card.action.text}
        </p>
      )}
      {calc && lv && entry != null && (card.verdict === "BUY" || card.verdict === "SELL") && (
        <div className="mt-3">
          <ForexCalculator
            setup={{ pair: sym, side: card.verdict, entry: +entry.toFixed(4), stop: lv.stop, target: lv.target, lotUnits: units, source: "this call" }}
            onClose={() => setCalc(false)}
            atr={card.atr}
            openingRange={card.opening_range}
          />
        </div>
      )}

      <div className="mt-3 -mx-2">
        {card.chart ? <IntradayChart card={card} liveBar={liveBar} height={280} precision={4} /> : <div className="h-[280px] animate-pulse rounded-xl bg-panel-2" />}
      </div>

      <HowItWorks label="More details">
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
          <dt>Session VWAP</dt>
          <dd className="tabular text-right text-ink">{fmtFx(card.session_vwap)}</dd>
          <dt>Supertrend (5-min)</dt>
          <dd className={`text-right ${card.supertrend.dir === 1 ? "text-bull" : card.supertrend.dir === -1 ? "text-bear" : ""}`}>
            {card.supertrend.dir === 1 ? "up" : card.supertrend.dir === -1 ? "down" : "—"} at {fmtFx(card.supertrend.line)}
          </dd>
          <dt>Support / resistance</dt>
          <dd className="tabular text-right text-ink">
            {fmtFx(card.support)} / {fmtFx(card.resistance)}
          </dd>
          <dt>ATR (5-min) · Risk:Reward</dt>
          <dd className="tabular text-right text-ink">
            {fmtFx(card.atr)} · {lv ? `1 : ${lv.risk_reward.toFixed(1)}` : "—"}
          </dd>
          <dt>Contract</dt>
          <dd className="text-right text-ink">
            {card.instrument.contract ?? "—"} · {units.toLocaleString("en-IN")} units/lot
          </dd>
        </dl>
        {card.reliability.skill_pct != null && (
          <p className="mt-2">
            5-min model skill vs guessing on unseen data: {card.reliability.skill_pct > 0 ? "+" : ""}
            {card.reliability.skill_pct}% (trained on the four currency pairs only).
          </p>
        )}
      </HowItWorks>
      {card.risks.length > 0 && <p className="mt-2 text-xs text-warn">⚠ {card.risks[0]}</p>}

      <div className="mt-auto flex gap-2 pt-4">
        {lv && (
          <button onClick={() => setCalc((x) => !x)} className="inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-full border border-line text-sm font-semibold hover:border-accent/50">
            <Calculator className="size-4" /> {calc ? "Hide calculator" : "Lot calculator"}
          </button>
        )}
        <Link href={`/forex/${sym}?tf=5m`} className="inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-full bg-accent text-sm font-semibold text-white hover:opacity-90">
          <Maximize2 className="size-4" /> Open chart
        </Link>
      </div>
    </section>
  );
}

/** One honest line: what the target is worth per lot against what a round trip costs. */
function CostLine({ costs, gross }: { costs: LotCosts; gross: number }) {
  const net1 = gross - costs.per_lot_1;
  const net10 = gross - costs.per_lot_10;
  return (
    <p className={`mt-2 rounded-xl px-3.5 py-2 text-xs ${net1 <= 0 ? "bg-warn/10 text-warn" : "bg-panel-2 text-muted"}`}>
      After charges at target: <b className="tabular">{net1 >= 0 ? "+" : "−"}{inr(net1)}/lot</b> trading 1 lot,{" "}
      <b className="tabular">{net10 >= 0 ? "+" : "−"}{inr(net10)}/lot</b> trading 10 lots.
      {net1 <= 0 && " One lot does not cover the charges on this move."}
    </p>
  );
}

export function ForexDesk() {
  const health = useHealth();
  const fxq = useForexQuotes();
  const stream = useQuoteStream(); // for the alerts bell only
  const { data, error } = useForexDesk();
  const session = data?.session ?? fxq.session;
  const note = session ? FX_SESSION_NOTE[session] : null;
  const costs = data?.pairs.find((c) => c.costs)?.costs;

  return (
    <>
      <AppHeader
        health={health.data}
        healthError={health.error}
        stream={fxq.status}
        lastUpdate={fxq.lastUpdate}
        alerts={stream.alerts}
        latestAlert={stream.latestAlert}
        session={session ? { state: session, label: "Currency market", holiday: health.data?.currency_holiday } : undefined}
      />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-4 py-5 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
              <Globe className="size-5 text-accent" /> Forex
            </h1>
            <p className="text-sm text-muted">Dollar, euro, pound and yen against the rupee · NSE currency futures · 09:00–17:00 IST</p>
          </div>
          <p className="text-xs text-faint">{data ? `Updated ${fmtTime(data.generated_at)} IST` : "Loading…"}</p>
        </div>

        {note && <p className="rounded-xl bg-warn/10 px-4 py-2.5 text-sm text-warn">{note}</p>}
        {data?.source === "demo" && <p className="rounded-xl bg-warn/10 px-4 py-2.5 text-sm text-warn">Demo data: synthetic currency prices, not the market.</p>}

        {costs && (
          <div className="flex gap-3 rounded-2xl border border-line bg-panel px-4 py-3 text-sm">
            <Info className="mt-0.5 size-4 shrink-0 text-accent" />
            <p className="text-muted">
              <b className="text-ink">Costs matter more in forex.</b> One lot is {costs.lot_value ? `≈${inr(costs.lot_value)}` : "1,000 units"} and moves only ₹
              {costs.tick_value.toFixed(2)} per 0.0025 tick. A round trip costs about {inr(costs.per_lot_1)} on 1 lot (≈{costs.breakeven_ticks_1.toFixed(0)}{" "}
              ticks just to break even) but about {inr(costs.per_lot_10)} per lot on 10 lots (≈{costs.breakeven_ticks_10.toFixed(1)} ticks). Check the
              after-charges line on each card.
            </p>
          </div>
        )}

        {!data && error ? (
          <div className="rounded-2xl border border-line bg-panel px-6 py-14 text-center">
            <p className="text-lg font-semibold">{error.status === 401 ? "Live data is offline" : "The forex desk could not load"}</p>
            <p className="mx-auto mt-1 max-w-xl text-sm text-muted">
              {error.status === 401 ? "The Upstox connection needs renewing by the site owner. This page fills in on its own once it is." : error.message}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {!data
              ? FX_PAIRS.map((p) => (
                  <div key={p} className="space-y-3 rounded-2xl border border-line bg-panel p-5">
                    <p className="text-sm font-semibold text-muted">{pairLabel(p)}</p>
                    <div className="h-8 w-40 animate-pulse rounded-lg bg-panel-2" />
                    <div className="h-16 animate-pulse rounded-xl bg-panel-2" />
                    <div className="h-[280px] animate-pulse rounded-xl bg-panel-2" />
                  </div>
                ))
              : data.pairs.map((card) => <PairCard key={card.instrument.instrument_key} card={card} quote={fxq.quotes[card.instrument.symbol] ?? card.quote} />)}
          </div>
        )}
        {data && error && <p className="text-xs text-warn">Last refresh failed ({error.message}); showing the previous calls.</p>}
        {data && data.unavailable.length > 0 && <p className="text-xs text-warn">Not available from the data provider: {data.unavailable.join(", ")}.</p>}

        {data && (
          <HowItWorks label="How the forex calls are made">
            <p>{data.method}</p>
            <p className="mt-1">
              Backtests on these pairs subtract about {data.backtest_cost_pct.toFixed(3)}% per round trip (charges and one tick of slippage, costed on 10 lots).
              Source: {data.source === "demo" ? "synthetic demo data" : "Upstox (NSE currency derivatives)"}.
            </p>
            <p className="mt-1">
              Only rupee pairs on NSE are covered. Pairs such as EUR/USD or gold through overseas forex apps are not permitted for Indian residents, so
              they are not shown.
            </p>
          </HowItWorks>
        )}
      </main>
      <Disclaimer />
    </>
  );
}
