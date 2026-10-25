"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Calculator, Layers, Layers3, ShieldAlert, Star } from "lucide-react";
import { TIMEFRAME_LABEL, TIMEFRAME_SHORT, isIntraday, useHealth, useStock } from "@/lib/api";
import { changeColor, fmtNum, fmtPct, fmtPrice, fmtSigned, fmtTime } from "@/lib/format";
import type { StockDetail, Timeframe, Verdict } from "@/lib/types";
import { plan } from "@/lib/charges";
import { buildLiveBar } from "@/lib/liveBar";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { useWatchlist } from "@/lib/watchlist";
import { AiExplanation } from "./AiExplanation";
import { AppHeader } from "./AppHeader";
import { NewsSidebar } from "./NewsPanel";
import { OPTION_SYMBOLS, OptionChainPanel } from "./OptionChainPanel";
import { OutlookCard, OutlookReliability } from "./OutlookCard";
import { PatternStatsTable, PatternsPanel } from "./PatternsPanel";
import { StrategiesPanel, StrategyStatsTable } from "./StrategiesPanel";
import { TrackRecordPanel } from "./TrackRecord";
import { PREFS_EVENT, TradeCalculator, loadPrefs, type TradeSetup } from "./TradeCalculator";
import { TradeStatus } from "./TradeStatus";
import { Disclaimer } from "./Disclaimer";
import { CHART_LEGEND, PriceChart, type ChartTrade } from "./PriceChart";
import { StatusNotices } from "./StatusNotices";
import { DataQualityNote, FreshnessBadge, HowItWorks, Panel, ScoreBar, SignalBadge, SkeletonRows } from "./ui";

const TIMEFRAMES: Timeframe[] = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w"];

const INDICATOR_ROWS: [string, string, (v: number | null) => string][] = [
  ["EMA 9", "ema9", fmtPrice],
  ["EMA 20", "ema20", fmtPrice],
  ["EMA 50", "ema50", fmtPrice],
  ["EMA 200", "ema200", fmtPrice],
  ["RSI 14", "rsi14", (v) => fmtNum(v)],
  ["MACD", "macd", (v) => fmtNum(v, 2)],
  ["MACD signal", "macd_signal", (v) => fmtNum(v, 2)],
  ["MACD hist", "macd_hist", (v) => fmtNum(v, 2)],
  ["ATR 14", "atr14", fmtPrice],
  ["ADX 14", "adx14", (v) => fmtNum(v)],
  ["+DI / −DI", "plus_di", (v) => fmtNum(v)],
  ["BB upper", "bb_upper", fmtPrice],
  ["BB lower", "bb_lower", fmtPrice],
  ["VWAP", "vwap", fmtPrice],
  ["Rel volume", "rel_volume", (v) => (v == null ? "—" : `${v.toFixed(2)}x`)],
  ["ROC 10", "roc10", (v) => fmtPct(v)],
  ["Stochastic %K / %D", "stoch_k", (v) => fmtNum(v)],
  ["MFI 14", "mfi14", (v) => fmtNum(v)],
  ["Z-score (20)", "zscore20", (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}`)],
  ["TTM squeeze", "squeeze_on", (v) => (v == null ? "—" : v ? "ON (coiled)" : "off")],
  ["Keltner upper", "kc_upper", fmtPrice],
  ["Keltner lower", "kc_lower", fmtPrice],
  ["Donchian 20 high", "dc_upper", fmtPrice],
  ["Donchian 20 low", "dc_lower", fmtPrice],
  ["Chandelier exit (long)", "chandelier_long", fmtPrice],
  ["Chandelier exit (short)", "chandelier_short", fmtPrice],
  ["CPR pivot", "cpr_p", fmtPrice],
  ["CPR top / bottom", "cpr_tc", fmtPrice],
  ["R1 / S1", "cpr_r1", fmtPrice],
];

// ---- chart layers (remembered per browser) ------------------------------------
type LayerKey = "emas" | "vwap" | "signals" | "levels" | "patterns" | "strategies" | "supertrend" | "bands" | "vwapBands" | "indicators";
const LAYERS: { key: LayerKey; label: string; intradayOnly?: boolean }[] = [
  { key: "emas", label: "Moving averages (EMA 20/50/200)" },
  { key: "vwap", label: "VWAP", intradayOnly: true },
  { key: "signals", label: "Buy / sell / exit points" },
  { key: "levels", label: "Support, resistance, target & stop lines" },
  { key: "patterns", label: "Chart patterns" },
  { key: "strategies", label: "Strategies, candlesticks, FVG, volume profile" },
  { key: "supertrend", label: "Supertrend" },
  { key: "bands", label: "Bollinger bands" },
  { key: "vwapBands", label: "VWAP ±1σ/±2σ bands", intradayOnly: true },
  { key: "indicators", label: "RSI & MACD panes" },
];
// A clean chart by default: price, averages, VWAP and the buy/sell points.
const DEFAULT_LAYERS: Record<LayerKey, boolean> = {
  emas: true, vwap: true, signals: true, levels: true, patterns: false, strategies: false, supertrend: false, bands: false, vwapBands: false, indicators: false,
};
const LAYER_KEY = "ai-stock:layers";

export function useLayers() {
  const [layers, setLayers] = useState(DEFAULT_LAYERS);
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(LAYER_KEY) ?? "null");
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of a saved preference
      if (saved) setLayers({ ...DEFAULT_LAYERS, ...saved });
    } catch {
      /* ignore */
    }
  }, []);
  const toggle = (k: LayerKey) =>
    setLayers((cur) => {
      const next = { ...cur, [k]: !cur[k] };
      try {
        localStorage.setItem(LAYER_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable */
      }
      return next;
    });
  return { layers, toggle };
}

export function LayersMenu({ layers, toggle, intraday }: { layers: Record<LayerKey, boolean>; toggle: (k: LayerKey) => void; intraday: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const on = LAYERS.filter((l) => layers[l.key] && (!l.intradayOnly || intraday)).length;
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen((o) => !o)} className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line px-3 text-xs font-semibold text-ink hover:border-accent/50">
        <Layers className="size-3.5" /> Layers <span className="text-faint">{on}</span>
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-80 rounded-2xl border border-line bg-panel p-2 shadow-xl">
          {LAYERS.filter((l) => !l.intradayOnly || intraday).map((l) => (
            <label key={l.key} className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm hover:bg-panel-2">
              <input type="checkbox" checked={layers[l.key]} onChange={() => toggle(l.key)} className="size-4 accent-accent" />
              {l.label}
            </label>
          ))}
          <div className="mt-1 border-t border-line px-2.5 pt-2 pb-1">
            <p className="mb-1.5 text-[11px] font-semibold text-muted">Legend</p>
            <div className="grid grid-cols-1 gap-1 text-[11px] text-muted">
              {CHART_LEGEND.map((l) => (
                <span key={l.label} className="inline-flex items-center gap-2">
                  <span className="h-0.5 w-4 shrink-0 rounded" style={{ background: l.color }} />
                  {l.label}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- call card: one clear instruction under the chart ------------------------------
const VERDICT_STYLE: Record<Verdict | "TRAINING", string> = {
  BUY: "bg-bull text-white",
  SELL: "bg-bear text-white",
  HOLD: "bg-warn/15 text-warn",
  "NO EDGE": "bg-panel-2 text-muted",
  "NO TRADE": "bg-warn/15 text-warn",
  TRAINING: "bg-panel-2 text-muted",
};

function calculatorSetup(data: StockDetail, price: number | null): TradeSetup | null {
  const sc = data.signal.scenario;
  const sym = data.instrument.symbol;
  const product = isIntraday(data.timeframe) ? "intraday" : "delivery";
  if (sc) {
    return {
      symbol: sym, side: sc.direction === "long" ? "BUY" : "SELL", entry: +((sc.entry_low + sc.entry_high) / 2).toFixed(2),
      stop: sc.stop_loss, target: sc.target, product, source: `the ${TIMEFRAME_LABEL[data.timeframe].toLowerCase()} setup`,
    };
  }
  const atr = data.snapshot.atr14;
  const p = price ?? data.last_close ?? null;
  if (p && atr) {
    // Direction from the outlook model when it is ready, otherwise from the rule signal.
    const label = data.signal.signal ?? "";
    const up = data.model_outlook.status === "ok" ? (data.model_outlook.up_pct ?? 50) >= 50 : !label.includes("Bearish");
    const d = up ? 1 : -1;
    return { symbol: sym, side: up ? "BUY" : "SELL", entry: p, stop: +(p - d * 1.5 * atr).toFixed(2), target: +(p + d * 2.5 * atr).toFixed(2), product, source: "the price ± ATR (no active setup)" };
  }
  return null;
}

/** Capital and risk % from the calculator, kept in sync when the calculator changes them. */
export function useCalcPrefs() {
  const [prefs, setPrefs] = useState({ capital: 100000, riskPct: 1 });
  useEffect(() => {
    const read = () => setPrefs(loadPrefs());
    read();
    window.addEventListener(PREFS_EVENT, read);
    window.addEventListener("storage", read);
    return () => {
      window.removeEventListener(PREFS_EVENT, read);
      window.removeEventListener("storage", read);
    };
  }, []);
  return prefs;
}

/** The suggested trade with quantity and net rupees at the target and at the stop, for the chart. */
function chartTrade(setup: TradeSetup | null, prefs: { capital: number; riskPct: number }): ChartTrade | null {
  if (!setup) return null;
  const p = plan({ ...prefs, side: setup.side, entry: setup.entry, stop: setup.stop, target: setup.target, product: setup.product ?? "intraday" });
  if (!p) return null;
  return {
    side: setup.side, entry: setup.entry, stop: setup.stop, target: setup.target, qty: p.qty,
    profit: p.atTarget.net, loss: -p.atStop.net, rr: p.rr, capital: prefs.capital, riskPct: prefs.riskPct,
    basis: (setup.source ?? "setup").replace(/^the /, ""),
  };
}

const inr = (v: number) => `₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;

function Stat({ label, value, sub, tone = "text-ink" }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl bg-panel-2 px-3.5 py-2.5">
      <p className="text-[11px] font-medium text-muted">{label}</p>
      <p className={`tabular text-base font-semibold ${tone}`}>{value}</p>
      {sub && <p className={`tabular text-[11px] ${tone === "text-ink" ? "text-muted" : tone}`}>{sub}</p>}
    </div>
  );
}

/** The call for this chart: direction, odds, and the trade in rupees, with the two main actions. */
function CallCard({ data, trade, onCalc, onOptions }: { data: StockDetail; trade: ChartTrade | null; onCalc: () => void; onOptions: () => void }) {
  const o = data.model_outlook;
  const sig = data.signal;
  const sc = sig.scenario;
  const training = o.status === "training";
  const v: Verdict | "TRAINING" = training ? "TRAINING" : (o.verdict ?? "NO EDGE");
  // The model's call and the chart's rule setup can point opposite ways: say so instead of mixing them.
  const conflict = trade && (v === "BUY" || v === "SELL") && trade.side !== v;
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
              <p className="text-xs text-muted">
                next {o.horizon_label} · {TIMEFRAME_LABEL[data.timeframe]} candles
              </p>
            </>
          ) : (
            <p className="max-w-sm text-xs text-muted">{o.message}</p>
          )}
        </div>
        <SignalBadge signal={sig} />
        <div className="ml-auto flex gap-2">
          <button onClick={onCalc} className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line px-4 text-sm font-semibold text-ink hover:border-accent/50">
            <Calculator className="size-4" /> Calculator
          </button>
          <button onClick={onOptions} className="inline-flex h-9 items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-semibold text-white hover:opacity-90">
            <Layers3 className="size-4" /> Option chain
          </button>
        </div>
      </div>
      {conflict && (
        <p className="mt-3 rounded-xl bg-warn/10 px-3.5 py-2 text-sm text-warn">
          Signals disagree: the model leans <b>{v}</b> but the chart setup below is a <b>{trade.side}</b>. Better to wait until they agree.
        </p>
      )}
      {trade && (
        <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <Stat label={sc ? (trade.side === "BUY" ? "Buy around" : "Sell around") : `${trade.side} at (suggested)`} value={`₹${fmtPrice(trade.entry)}`} sub={trade.qty > 0 ? `Qty ${trade.qty}` : undefined} />
          <Stat label="Target" value={`₹${fmtPrice(trade.target)}`} sub={trade.qty > 0 ? `+${inr(trade.profit)}` : undefined} tone="text-bull" />
          <Stat label="Stop-loss" value={`₹${fmtPrice(trade.stop)}`} sub={trade.qty > 0 ? `−${inr(trade.loss)}` : undefined} tone="text-bear" />
          <Stat label="Risk : reward" value={trade.rr != null ? `1 : ${trade.rr.toFixed(1)}` : "—"} sub={`capital ${inr(trade.capital)} · risk ${trade.riskPct}%`} />
        </div>
      )}
      <HowItWorks>
        <p>
          {sc
            ? `Levels from the ${TIMEFRAME_LABEL[data.timeframe].toLowerCase()} setup: ${sc.basis}.`
            : "No confirmed setup on this timeframe, so the levels are suggestions: stop 1.5 ATR and target 2.5 ATR from the last close."}{" "}
          Amounts are after Upstox charges for the quantity that risks your calculator&apos;s risk % of capital.
        </p>
        <p className="mt-1">
          Support {fmtPrice(sig.levels.support)} · resistance {fmtPrice(sig.levels.resistance)}
          {sig.no_trade_reasons.length > 0 && ` · NO TRADE because: ${sig.no_trade_reasons.join("; ")}`}
        </p>
      </HowItWorks>
    </section>
  );
}

// ---- detail tabs ------------------------------------------------------------
const TABS = ["Overview", "Options", "Outlook", "Strategies", "Patterns", "News", "Details"] as const;
type Tab = (typeof TABS)[number];

export function StockView({ symbol, initialTimeframe = "1d" }: { symbol: string; initialTimeframe?: Timeframe }) {
  const [timeframe, setTimeframe] = useState<Timeframe>(initialTimeframe);
  const [tab, setTab] = useState<Tab>("Overview");
  const tabsRef = useRef<HTMLDivElement>(null);
  const openTab = (t: Tab) => {
    setTab(t);
    requestAnimationFrame(() => tabsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  const { layers, toggle } = useLayers();
  const health = useHealth();
  const stream = useQuoteStream();
  const { data, error, isLoading } = useStock(symbol, timeframe);
  const watch = useWatchlist();

  // Indices are routed as NIFTY50 / BANKNIFTY but quoted and watched as "NIFTY 50" / "BANK NIFTY".
  const name = data?.instrument.symbol ?? symbol;
  const quote = stream.quotes[name] ?? data?.quote ?? null;
  const sig = data?.signal;
  const snap = data?.snapshot ?? {};
  const liveBar = useMemo(
    // data.timeframe (not the selected tab) so a stale response never gets a mismatched live bar.
    () => (data ? buildLiveBar(data.timeframe, data.forming_candle, data.candles.at(-1), quote) : null),
    [data, quote],
  );
  const intraday = isIntraday(timeframe);
  const setup = data ? calculatorSetup(data, quote?.last_price ?? null) : null;
  const prefs = useCalcPrefs();
  // Last close (not the ticking price) so the chart lines stay put between candles.
  const trade = useMemo(() => chartTrade(data ? calculatorSetup(data, null) : null, prefs), [data, prefs]);

  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-3 py-5 sm:px-6">
        <StatusNotices health={health.data} healthError={health.error} streamError={stream.error} />

        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <Link href="/" className="mb-2 inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-ink">
              <ArrowLeft className="size-3.5" /> Intraday
            </Link>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold tracking-tight sm:text-[28px]">{name}</h1>
              <button
                onClick={() => watch.toggle(name)}
                aria-label={watch.has(name) ? "Remove from watchlist" : "Add to watchlist"}
                title={watch.has(name) ? "In your watchlist" : "Add to watchlist"}
                className="grid size-8 place-items-center rounded-full text-faint hover:bg-panel-2 hover:text-warn"
              >
                <Star className={`size-5 ${watch.has(name) ? "fill-warn text-warn" : ""}`} />
              </button>
            </div>
            <p className="truncate text-sm text-muted">
              {data?.instrument.name ?? "…"} {data?.instrument.sector && `· ${data.instrument.sector}`}
            </p>
          </div>
          <div className="text-right">
            <p className="tabular text-3xl font-bold tracking-tight sm:text-[34px]">₹{fmtPrice(quote?.last_price ?? data?.last_close)}</p>
            <p className={`tabular text-sm font-semibold ${changeColor(quote?.change)}`}>
              {fmtSigned(quote?.change)} ({fmtPct(quote?.change_pct)})
            </p>
            <div className="mt-1 flex items-center justify-end gap-2 text-[11px] text-faint">
              <FreshnessBadge freshness={quote?.freshness} />
              {quote && (
                <span className="hidden sm:inline">
                  O {fmtPrice(quote.open)} · H {fmtPrice(quote.high)} · L {fmtPrice(quote.low)}
                </span>
              )}
            </div>
          </div>
        </div>

        {error && !data && (
          <div className="rounded-2xl border border-bear/30 bg-bear/5 px-4 py-3 text-sm text-bear">
            {error.status === 404 ? `${symbol} was not found on NSE.` : error.message}
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
              <LayersMenu layers={layers} toggle={toggle} intraday={intraday} />
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
            {setup && (
              <TradeCalculator
                key={`plan-${name}-${data.timeframe}-${setup.side}`}
                setup={setup}
                atr={data.snapshot.atr14}
                openingRange={data.opening_range}
              />
            )}
            <div className="space-y-5">
              <DataQualityNote quality={data.data_quality} />
              {data.history && <TradeStatus history={data.history} quote={quote} timeframe={data.timeframe} />}
              <RisksPanel risks={sig?.risks ?? []} />
            </div>
          </div>
        )}

        {data && tab === "Options" && (
          <OptionChainPanel symbol={data.instrument.symbol} index={OPTION_SYMBOLS.includes(data.instrument.symbol)} />
        )}

        {data && tab === "Outlook" && (
          <div className="space-y-4">
            <OutlookCard outlook={data.model_outlook} timeframe={data.timeframe} />
            <OutlookReliability outlook={data.model_outlook} />
          </div>
        )}

        {data && tab === "Strategies" && (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
            <StrategiesPanel block={data.strategies} timeframe={data.timeframe} />
            <StrategyStatsTable timeframe={data.timeframe} />
          </div>
        )}

        {data && tab === "Patterns" && (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
            <PatternsPanel block={data.patterns} timeframe={data.timeframe} quote={quote} source={data.source} />
            <PatternStatsTable timeframe={data.timeframe} />
          </div>
        )}

        {tab === "News" && (
          <div className="max-w-3xl">
            <NewsSidebar symbol={symbol} />
          </div>
        )}

        {data && tab === "Details" && (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Panel title="Why this signal?" action={sig && <ScoreBar score={sig.score} />}>
                {!sig ? (
                  <SkeletonRows rows={5} />
                ) : (
                  <div className="space-y-3 text-sm">
                    {sig.no_trade_reasons.length > 0 && (
                      <div className="rounded-lg border border-warn/30 bg-warn/5 p-3 text-xs text-warn">
                        <p className="mb-1 font-semibold">NO TRADE</p>
                        <ul className="space-y-0.5">
                          {sig.no_trade_reasons.map((r) => (
                            <li key={r}>• {r}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <table className="w-full text-xs">
                      <tbody>
                        {sig.evidence.map((e) => (
                          <tr key={e.factor} className="border-b border-line/60 align-top">
                            <td className="py-1.5 pr-2 text-muted">
                              {e.factor}
                              {e.weight > 0 && <span className="text-faint"> · w{e.weight}</span>}
                            </td>
                            <td className="py-1.5 text-right">{e.detail}</td>
                            <td className={`tabular py-1.5 pl-2 text-right ${e.score > 0 ? "text-bull" : e.score < 0 ? "text-bear" : "text-faint"}`}>
                              {e.weight > 0 ? (e.score > 0 ? "+" : "") + e.score.toFixed(2) : ""}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="text-[11px] text-faint">
                      Weighted indicator score in [−100, +100] ({sig.method}). Thresholds: ±20 bullish/bearish, ±50 strong. Not a probability.
                    </p>
                  </div>
                )}
              </Panel>
              <Panel title="Indicators">
                <dl className="grid grid-cols-2 gap-y-1 text-xs">
                  {INDICATOR_ROWS.map(([label, key, f]) => (
                    <div key={key} className="contents">
                      <dt className="text-muted">{label}</dt>
                      <dd className="tabular text-right">
                        {key === "plus_di"
                          ? `${fmtNum(snap.plus_di)} / ${fmtNum(snap.minus_di)}`
                          : key === "stoch_k"
                            ? `${fmtNum(snap.stoch_k)} / ${fmtNum(snap.stoch_d)}`
                            : key === "cpr_tc"
                              ? `${fmtPrice(snap.cpr_tc ?? null)} / ${fmtPrice(snap.cpr_bc ?? null)}`
                              : key === "cpr_r1"
                                ? `${fmtPrice(snap.cpr_r1 ?? null)} / ${fmtPrice(snap.cpr_s1 ?? null)}`
                                : f(snap[key] ?? null)}
                      </dd>
                    </div>
                  ))}
                </dl>
              </Panel>
            </div>
            {data.history && <TrackRecordPanel history={data.history} timeframe={data.timeframe} />}
            <AiExplanation key={`${symbol}-${timeframe}`} symbol={symbol} timeframe={timeframe} enabled={!!health.data?.nvidia_configured} />
          </div>
        )}

        {data && (
          <p className="text-[11px] text-faint">
            {data.source === "demo" ? "Synthetic demo data" : "Upstox data"} · updated {fmtTime(data.generated_at, true)} IST · {sig?.bars ?? 0}{" "}
            {TIMEFRAME_LABEL[data.timeframe].toLowerCase()} candles · signals use completed candles only
          </p>
        )}
      </main>
      <Disclaimer />
    </>
  );
}

function RisksPanel({ risks }: { risks: string[] }) {
  return (
    <Panel title={<span className="inline-flex items-center gap-2"><ShieldAlert className="size-4 text-warn" /> Risk factors</span>}>
      {risks.length ? (
        <ul className="space-y-1 text-sm text-muted">
          {risks.map((r) => (
            <li key={r}>• {r}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-faint">No indicator-based risk flags. Market, event and liquidity risk always apply.</p>
      )}
    </Panel>
  );
}
