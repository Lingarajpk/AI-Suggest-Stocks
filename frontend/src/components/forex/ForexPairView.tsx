"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { ArrowLeft, Calculator, Layers3 } from "lucide-react";
import { TIMEFRAME_LABEL, TIMEFRAME_SHORT, isIntraday, useHealth } from "@/lib/api";
import { changeColor, fmtPct, fmtTime } from "@/lib/format";
import { FX_OPEN_MIN, fmtFx, fxPlan, pairLabel, useForexPair, useForexQuotes, useFxPrefs, type ForexDetail } from "@/lib/forex";
import { buildLiveBar } from "@/lib/liveBar";
import { PriceDigits } from "@/lib/priceDigits";
import type { Timeframe, Verdict } from "@/lib/types";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { AppHeader } from "../AppHeader";
import { Disclaimer } from "../Disclaimer";
import { NewsSidebar } from "../NewsPanel";
import { OptionChainPanel } from "../OptionChainPanel";
import { OutlookCard, OutlookReliability } from "../OutlookCard";
import { PatternStatsTable, PatternsPanel } from "../PatternsPanel";
import { PriceChart, type ChartTrade } from "../PriceChart";
import { LayersMenu, useLayers } from "../StockView";
import { StrategiesPanel, StrategyStatsTable } from "../StrategiesPanel";
import { TradeStatus } from "../TradeStatus";
import { FreshnessBadge, HowItWorks, SignalBadge } from "../ui";
import { FX_SESSION_NOTE } from "./ForexDesk";
import { ForexCalculator, type FxSetup } from "./ForexCalculator";

const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "1d", "1w"];
const TABS = ["Overview", "Options", "Outlook", "Strategies", "Patterns", "News"] as const;
type Tab = (typeof TABS)[number];

const VERDICT_STYLE: Record<Verdict | "TRAINING", string> = {
  BUY: "bg-bull text-white",
  SELL: "bg-bear text-white",
  HOLD: "bg-warn/15 text-warn",
  "NO EDGE": "bg-panel-2 text-muted",
  "NO TRADE": "bg-warn/15 text-warn",
  TRAINING: "bg-panel-2 text-muted",
};

const inr = (v: number) => `₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

/** The chart's setup if there is one, else the price ± ATR in the model's (or the rules') direction. */
function fxSetup(data: ForexDetail, price: number | null): FxSetup | null {
  const units = data.instrument.lot_units ?? 1000;
  const sc = data.signal.scenario;
  const label = `the ${TIMEFRAME_LABEL[data.timeframe].toLowerCase()} setup`;
  if (sc) {
    return { pair: data.instrument.symbol, side: sc.direction === "long" ? "BUY" : "SELL", entry: +((sc.entry_low + sc.entry_high) / 2).toFixed(4),
      stop: sc.stop_loss, target: sc.target, lotUnits: units, source: label };
  }
  const atr = data.snapshot.atr14;
  const p = price ?? data.last_close ?? null;
  if (!p || !atr) return null;
  const up = data.model_outlook.status === "ok" ? (data.model_outlook.up_pct ?? 50) >= 50 : !(data.signal.signal ?? "").includes("Bearish");
  const d = up ? 1 : -1;
  return { pair: data.instrument.symbol, side: up ? "BUY" : "SELL", entry: p, stop: +(p - d * 1.5 * atr).toFixed(4), target: +(p + d * 2.5 * atr).toFixed(4),
    lotUnits: units, source: "the price ± ATR (no active setup)" };
}

function Stat({ label, value, sub, tone = "text-ink" }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl bg-panel-2 px-3.5 py-2.5">
      <p className="text-[11px] font-medium text-muted">{label}</p>
      <p className={`tabular text-base font-semibold ${tone}`}>{value}</p>
      {sub && <p className={`tabular text-[11px] ${tone === "text-ink" ? "text-muted" : tone}`}>{sub}</p>}
    </div>
  );
}

function CallCard({ data, trade, onCalc, onOptions }: { data: ForexDetail; trade: ChartTrade | null; onCalc: () => void; onOptions: () => void }) {
  const o = data.model_outlook;
  const training = o.status === "training";
  const v: Verdict | "TRAINING" = training ? "TRAINING" : (o.verdict ?? "NO EDGE");
  const conflict = trade && (v === "BUY" || v === "SELL") && trade.side !== v;
  const sc = data.signal.scenario;
  return (
    <section className="rounded-2xl border border-line bg-panel p-4 shadow-[0_1px_2px_rgba(16,24,40,0.04)] sm:p-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <span className={`rounded-xl px-4 py-2 text-lg font-bold ${VERDICT_STYLE[v]}`}>
          {v === "BUY" ? "▲ BUY" : v === "SELL" ? "▼ SELL" : v === "TRAINING" ? "Analysing…" : v}
        </span>
        <div className="min-w-0">
          {o.status === "ok" && o.up_pct != null ? (
            <>
              <p className="text-sm font-semibold">
                <span className="text-bull">{o.up_pct.toFixed(0)}% up</span> <span className="text-faint">·</span>{" "}
                <span className="text-bear">{(100 - o.up_pct).toFixed(0)}% down</span>
              </p>
              <p className="text-xs text-muted">next {o.horizon_label} · {TIMEFRAME_LABEL[data.timeframe]} candles · model trained on the 4 pairs</p>
            </>
          ) : (
            <p className="max-w-sm text-xs text-muted">{o.message}</p>
          )}
        </div>
        <SignalBadge signal={data.signal} />
        <div className="ml-auto flex gap-2">
          <button onClick={onCalc} className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line px-4 text-sm font-semibold text-ink hover:border-accent/50">
            <Calculator className="size-4" /> Lot calculator
          </button>
          <button onClick={onOptions} className="inline-flex h-9 items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-semibold text-white hover:opacity-90">
            <Layers3 className="size-4" /> Options
          </button>
        </div>
      </div>
      {conflict && (
        <p className="mt-3 rounded-xl bg-warn/10 px-3.5 py-2 text-sm text-warn">
          Signals disagree: the model leans <b>{v}</b> but the chart setup is a <b>{trade.side}</b>. Better to wait until they agree.
        </p>
      )}
      {trade && (
        <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <Stat label={sc ? (trade.side === "BUY" ? "Buy around" : "Sell around") : `${trade.side} at (suggested)`} value={fmtFx(trade.entry)} sub={trade.qty > 0 ? `${trade.qty} lot${trade.qty > 1 ? "s" : ""}` : undefined} />
          <Stat label="Target" value={fmtFx(trade.target)} sub={trade.qty > 0 ? `${trade.profit >= 0 ? "+" : "−"}${inr(trade.profit)}` : undefined} tone="text-bull" />
          <Stat label="Stop-loss" value={fmtFx(trade.stop)} sub={trade.qty > 0 ? `−${inr(trade.loss)}` : undefined} tone="text-bear" />
          <Stat label="Risk : reward" value={trade.rr != null ? `1 : ${trade.rr.toFixed(1)}` : "—"} sub={`capital ${inr(trade.capital)} · risk ${trade.riskPct}%`} />
        </div>
      )}
      {trade && trade.qty > 0 && trade.profit <= 0 && (
        <p className="mt-3 rounded-xl bg-warn/10 px-3.5 py-2 text-sm text-warn">At this size the target does not cover Upstox charges: skip it or trade more lots.</p>
      )}
      <HowItWorks>
        <p>
          {sc
            ? `Levels from the ${TIMEFRAME_LABEL[data.timeframe].toLowerCase()} setup: ${sc.basis}.`
            : "No confirmed setup on this timeframe, so the levels are suggestions: stop 1.5 ATR and target 2.5 ATR from the last close."}{" "}
          Rupee amounts are after Upstox charges for the lots that risk your lot calculator&apos;s risk % of capital.
        </p>
        <p className="mt-1">
          Support {fmtFx(data.signal.levels.support)} · resistance {fmtFx(data.signal.levels.resistance)}
          {data.signal.no_trade_reasons.length > 0 && ` · NO TRADE because: ${data.signal.no_trade_reasons.join("; ")}`}
        </p>
      </HowItWorks>
    </section>
  );
}

export function ForexPairView({ pair, initialTimeframe = "15m" }: { pair: string; initialTimeframe?: Timeframe }) {
  const [timeframe, setTimeframe] = useState<Timeframe>(initialTimeframe);
  const [tab, setTab] = useState<Tab>("Overview");
  const tabsRef = useRef<HTMLDivElement>(null);
  const openTab = (t: Tab) => {
    setTab(t);
    requestAnimationFrame(() => tabsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  const { layers, toggle } = useLayers();
  const health = useHealth();
  const fxq = useForexQuotes();
  const stream = useQuoteStream(); // alerts bell only
  const { data, error, isLoading } = useForexPair(pair, timeframe);
  const prefs = useFxPrefs();

  const sym = data?.instrument.symbol ?? pair;
  const quote = fxq.quotes[sym] ?? data?.quote ?? null;
  const session = data?.session ?? fxq.session;
  const note = session ? FX_SESSION_NOTE[session] : null;
  const liveBar = useMemo(
    () => (data ? buildLiveBar(data.timeframe, data.forming_candle, data.candles.at(-1), quote, FX_OPEN_MIN) : null),
    [data, quote],
  );
  const setup = data ? fxSetup(data, quote?.last_price ?? null) : null;
  // From the last close (not the ticking price) so the chart lines stay put between candles.
  const trade = useMemo<ChartTrade | null>(() => {
    const s = data ? fxSetup(data, null) : null;
    const p = s && fxPlan({ ...prefs, side: s.side, entry: s.entry, stop: s.stop, target: s.target, lotUnits: s.lotUnits });
    if (!s || !p) return null;
    return { side: s.side, entry: s.entry, stop: s.stop, target: s.target, qty: p.lots, profit: p.atTarget.net, loss: -p.atStop.net, rr: p.rr,
      capital: prefs.capital, riskPct: prefs.riskPct, basis: s.source.replace(/^the /, ""), qtyLabel: "Lots" };
  }, [data, prefs]);
  const sig = data?.signal;

  return (
    <PriceDigits.Provider value={4}>
      <AppHeader
        health={health.data}
        healthError={health.error}
        stream={fxq.status}
        lastUpdate={fxq.lastUpdate}
        alerts={stream.alerts}
        latestAlert={stream.latestAlert}
        session={session ? { state: session, label: "Currency market" } : undefined}
      />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-3 py-5 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <Link href="/forex" className="mb-2 inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-ink">
              <ArrowLeft className="size-3.5" /> Forex
            </Link>
            <h1 className="text-2xl font-bold tracking-tight sm:text-[28px]">{pairLabel(sym)}</h1>
            <p className="truncate text-sm text-muted">
              {data?.pair ? `${data.pair.currency} in rupees · price = ₹ per ${data.pair.per}` : "…"}
              {data?.instrument.contract && ` · ${data.instrument.contract}`}
            </p>
          </div>
          <div className="text-right">
            <p className="tabular text-3xl font-bold tracking-tight sm:text-[34px]">₹{fmtFx(quote?.last_price ?? data?.last_close)}</p>
            <p className={`tabular text-sm font-semibold ${changeColor(quote?.change)}`}>
              {quote?.change == null ? "—" : `${quote.change > 0 ? "+" : ""}${fmtFx(quote.change)}`} ({fmtPct(quote?.change_pct)})
            </p>
            <div className="mt-1 flex items-center justify-end gap-2 text-[11px] text-faint">
              <FreshnessBadge freshness={quote?.freshness} />
              {quote && (
                <span className="hidden sm:inline">
                  O {fmtFx(quote.open)} · H {fmtFx(quote.high)} · L {fmtFx(quote.low)}
                </span>
              )}
            </div>
          </div>
        </div>

        {note && <p className="rounded-xl bg-warn/10 px-4 py-2.5 text-sm text-warn">{note}</p>}
        {error && !data && (
          <div className="rounded-2xl border border-bear/30 bg-bear/5 px-4 py-3 text-sm text-bear">
            {error.status === 404 ? `${pair} is not one of the forex pairs (USDINR, EURINR, GBPINR, JPYINR).` : error.message}
          </div>
        )}

        <section className="overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
          <div className="flex flex-wrap items-center justify-between gap-2 px-3 pt-3 sm:px-4">
            <div className="flex max-w-full gap-1 overflow-x-auto" role="tablist" aria-label="Candle size">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  role="tab"
                  aria-selected={tf === timeframe}
                  title={`${TIMEFRAME_LABEL[tf]} candles`}
                  onClick={() => setTimeframe(tf)}
                  className={`tabular h-8 shrink-0 rounded-full px-3 text-xs font-semibold ${tf === timeframe ? "bg-accent/10 text-accent" : "text-muted hover:bg-panel-2 hover:text-ink"}`}
                >
                  {TIMEFRAME_SHORT[tf]}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3">
              {data && data.timeframe !== timeframe && <span className="text-[11px] text-faint">Loading {TIMEFRAME_LABEL[timeframe]}…</span>}
              <LayersMenu layers={layers} toggle={toggle} intraday={isIntraday(timeframe)} />
            </div>
          </div>
          <div className="p-1 sm:p-2">
            {isLoading && !data ? (
              <div className="h-[68vh] min-h-[420px] max-h-[860px] animate-pulse rounded-xl bg-panel-2" />
            ) : data?.candles.length ? (
              <PriceChart
                candles={data.candles}
                series={data.series}
                oscillators={data.oscillators}
                support={sig?.levels.support}
                resistance={sig?.levels.resistance}
                intraday={isIntraday(data.timeframe)}
                showBands={layers.bands}
                markers={data.history?.markers ?? []}
                exits={data.history?.exits ?? []}
                showMarkers={layers.signals}
                scenario={sig?.scenario ?? null}
                patterns={data.patterns?.current ?? []}
                recentPatterns={data.patterns?.recent ?? []}
                showPatterns={layers.patterns}
                strategies={data.strategies}
                showStrategies={layers.strategies}
                showSupertrend={layers.supertrend}
                showVwapBands={layers.vwapBands}
                showEmas={layers.emas}
                showVwap={layers.vwap}
                showLevels={layers.levels}
                showIndicators={layers.indicators}
                liveBar={liveBar}
                trade={trade}
                precision={4}
              />
            ) : (
              !error && <p className="py-20 text-center text-sm text-faint">No candle data available.</p>
            )}
          </div>
        </section>

        {data && <CallCard data={data} trade={trade} onCalc={() => openTab("Overview")} onOptions={() => openTab("Options")} />}

        <div ref={tabsRef} className="flex max-w-full scroll-mt-20 gap-1 overflow-x-auto border-b border-line" role="tablist" aria-label="Details">
          {TABS.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={t === tab}
              onClick={() => setTab(t)}
              className={`shrink-0 border-b-2 px-3.5 py-2.5 text-sm font-semibold ${t === tab ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink"}`}
            >
              {t}
            </button>
          ))}
        </div>

        {data && tab === "Overview" && (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {setup && <ForexCalculator key={`fx-${sym}-${data.timeframe}-${setup.side}`} setup={setup} />}
            <div className="space-y-5">
              {data.history && <TradeStatus history={data.history} quote={quote} timeframe={data.timeframe} />}
              {data.costs && (
                <div className="rounded-2xl border border-line bg-panel p-4 text-sm text-muted">
                  <p className="font-semibold text-ink">What a trade costs</p>
                  <p className="mt-1">
                    One lot ≈ {inr(data.costs.lot_value)}; each 0.0025 tick is ₹{data.costs.tick_value.toFixed(2)} per lot. A round trip costs about{" "}
                    {inr(data.costs.per_lot_1)} on 1 lot ({data.costs.breakeven_ticks_1.toFixed(0)} ticks to break even) and about {inr(data.costs.per_lot_10)}{" "}
                    per lot on 10 lots ({data.costs.breakeven_ticks_10.toFixed(1)} ticks).
                  </p>
                </div>
              )}
              {sig && sig.risks.length > 0 && (
                <ul className="space-y-1 rounded-2xl border border-line bg-panel p-4 text-sm text-muted">
                  {sig.risks.map((r) => (
                    <li key={r}>• {r}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
        {data && tab === "Options" && <OptionChainPanel symbol={sym} index market="forex" />}
        {data && tab === "Outlook" && (
          <div className="space-y-4">
            <OutlookCard outlook={data.model_outlook} timeframe={data.timeframe} />
            <OutlookReliability outlook={data.model_outlook} />
          </div>
        )}
        {data && tab === "Strategies" && (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
            <StrategiesPanel block={data.strategies} timeframe={data.timeframe} market="forex" />
            <StrategyStatsTable timeframe={data.timeframe} market="forex" />
          </div>
        )}
        {data && tab === "Patterns" && (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
            <PatternsPanel block={data.patterns} timeframe={data.timeframe} quote={quote} source={data.source} market="forex" />
            <PatternStatsTable timeframe={data.timeframe} market="forex" />
          </div>
        )}
        {tab === "News" && (
          <div className="max-w-3xl">
            <NewsSidebar symbol={sym} market="forex" />
          </div>
        )}

        {data && (
          <p className="text-[11px] text-faint">
            {data.source === "demo" ? "Synthetic demo data" : "Upstox data (NSE currency futures)"} · updated {fmtTime(data.generated_at, true)} IST ·{" "}
            {sig?.bars ?? 0} {TIMEFRAME_LABEL[data.timeframe].toLowerCase()} candles · signals use completed candles only
          </p>
        )}
      </main>
      <Disclaimer />
    </PriceDigits.Provider>
  );
}
