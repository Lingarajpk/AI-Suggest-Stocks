"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Calculator, Layers, ShieldAlert, Star } from "lucide-react";
import { TIMEFRAME_LABEL, TIMEFRAME_SHORT, isIntraday, useHealth, useStock } from "@/lib/api";
import { changeColor, fmtCompact, fmtNum, fmtPct, fmtPrice, fmtSigned, fmtTime } from "@/lib/format";
import type { StockDetail, Timeframe, Verdict } from "@/lib/types";
import { buildLiveBar } from "@/lib/liveBar";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { useWatchlist } from "@/lib/watchlist";
import { AiExplanation } from "./AiExplanation";
import { AppHeader } from "./AppHeader";
import { NewsSidebar } from "./NewsPanel";
import { OutlookCard, OutlookReliability } from "./OutlookCard";
import { PatternStatsTable, PatternsPanel } from "./PatternsPanel";
import { StrategiesPanel, StrategyStatsTable } from "./StrategiesPanel";
import { TrackRecordPanel } from "./TrackRecord";
import { TradeCalculator, type TradeSetup } from "./TradeCalculator";
import { TradeStatus } from "./TradeStatus";
import { Disclaimer } from "./Disclaimer";
import { CHART_LEGEND, PriceChart } from "./PriceChart";
import { StatusNotices } from "./StatusNotices";
import { FreshnessBadge, Panel, ScoreBar, SignalBadge, SkeletonRows } from "./ui";

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

function useLayers() {
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

function LayersMenu({ layers, toggle, intraday }: { layers: Record<LayerKey, boolean>; toggle: (k: LayerKey) => void; intraday: boolean }) {
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
      <button onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-ink ring-1 ring-line hover:ring-accent/60">
        <Layers className="size-3.5" /> Layers <span className="text-faint">{on}</span>
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-72 rounded-xl border border-line bg-panel p-2 shadow-2xl shadow-black/50">
          {LAYERS.filter((l) => !l.intradayOnly || intraday).map((l) => (
            <label key={l.key} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-panel-2">
              <input type="checkbox" checked={layers[l.key]} onChange={() => toggle(l.key)} className="accent-accent" />
              {l.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- action bar: one clear instruction above the chart -------------------------
const VERDICT_STYLE: Record<Verdict | "TRAINING", string> = {
  BUY: "bg-bull text-bg",
  SELL: "bg-bear text-bg",
  HOLD: "bg-warn/20 text-warn",
  "NO EDGE": "bg-muted/15 text-muted",
  "NO TRADE": "bg-warn/20 text-warn",
  TRAINING: "bg-muted/15 text-muted",
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
    const up = !(data.model_outlook.status === "ok" && (data.model_outlook.up_pct ?? 50) < 50);
    const d = up ? 1 : -1;
    return { symbol: sym, side: up ? "BUY" : "SELL", entry: p, stop: +(p - d * 1.5 * atr).toFixed(2), target: +(p + d * 2.5 * atr).toFixed(2), product, source: "the price ± ATR (no active setup)" };
  }
  return null;
}

function ActionBar({ data, price, onCalc, calcOpen }: { data: StockDetail; price: number | null; onCalc: () => void; calcOpen: boolean }) {
  const o = data.model_outlook;
  const training = o.status === "training";
  const v: Verdict | "TRAINING" = training ? "TRAINING" : (o.verdict ?? "NO EDGE");
  const sc = data.signal.scenario;
  return (
    <section className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border border-line bg-panel px-4 py-3">
      <div className="flex items-center gap-3">
        <span className={`rounded-lg px-3 py-1.5 text-lg font-bold ${VERDICT_STYLE[v]}`}>
          {v === "BUY" ? "▲ BUY" : v === "SELL" ? "▼ SELL" : v === "TRAINING" ? "…" : v}
        </span>
        <div className="text-xs">
          {o.status === "ok" && o.up_pct != null ? (
            <>
              <p>
                <b className="text-bull">{o.up_pct.toFixed(0)}% up</b> · <b className="text-bear">{(100 - o.up_pct).toFixed(0)}% down</b>
              </p>
              <p className="text-faint">next {o.horizon_label} · {TIMEFRAME_LABEL[data.timeframe]} candles</p>
            </>
          ) : (
            <p className="max-w-xs text-muted">{o.message}</p>
          )}
        </div>
      </div>
      {sc ? (
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
          <div>
            <dt className="text-faint">{sc.direction === "long" ? "Buy zone" : "Sell zone"}</dt>
            <dd className="tabular">{fmtPrice(sc.entry_low)}–{fmtPrice(sc.entry_high)}</dd>
          </div>
          <div>
            <dt className="text-faint">Target</dt>
            <dd className="tabular text-bull">{fmtPrice(sc.target)}</dd>
          </div>
          <div>
            <dt className="text-faint">Stop-loss</dt>
            <dd className="tabular text-bear">{fmtPrice(sc.stop_loss)}</dd>
          </div>
          <div>
            <dt className="text-faint">Risk:Reward</dt>
            <dd className="tabular">1 : {sc.risk_reward.toFixed(1)}</dd>
          </div>
        </dl>
      ) : (
        <p className="text-xs text-faint">No trade setup on this timeframe right now.</p>
      )}
      <button
        onClick={onCalc}
        className={`ml-auto inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold ${calcOpen ? "bg-accent/15 text-accent" : "bg-accent text-bg hover:bg-accent/90"}`}
        disabled={!calculatorSetup(data, price)}
      >
        <Calculator className="size-3.5" /> Calculator
      </button>
    </section>
  );
}

// ---- detail tabs ------------------------------------------------------------
const TABS = ["Trade plan", "Outlook", "Strategies", "Patterns", "News", "Signal details"] as const;
type Tab = (typeof TABS)[number];

export function StockView({ symbol, initialTimeframe = "1d" }: { symbol: string; initialTimeframe?: Timeframe }) {
  const [timeframe, setTimeframe] = useState<Timeframe>(initialTimeframe);
  const [tab, setTab] = useState<Tab>("Trade plan");
  const [calcOpen, setCalcOpen] = useState(false);
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

  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1600px] space-y-4 px-3 py-4 sm:px-6">
        <StatusNotices health={health.data} healthError={health.error} streamError={stream.error} />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <Link href="/" className="mb-1 inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
              <ArrowLeft className="size-3.5" /> Intraday desk
            </Link>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">{name}</h1>
              <button onClick={() => watch.toggle(name)} aria-label={watch.has(name) ? "Remove from watchlist" : "Add to watchlist"} title="Watchlist" className="text-faint hover:text-warn">
                <Star className={`size-5 ${watch.has(name) ? "fill-warn text-warn" : ""}`} />
              </button>
              {sig && <SignalBadge signal={sig} />}
            </div>
            <p className="truncate text-xs text-muted">
              {data?.instrument.name ?? "…"} {data?.instrument.sector && `· ${data.instrument.sector}`}
            </p>
          </div>
          <div className="text-right">
            <p className="tabular text-3xl font-semibold">{fmtPrice(quote?.last_price ?? data?.last_close)}</p>
            <p className={`tabular text-sm ${changeColor(quote?.change)}`}>
              {fmtSigned(quote?.change)} ({fmtPct(quote?.change_pct)})
            </p>
            <div className="mt-0.5 flex items-center justify-end gap-2 text-[11px] text-faint">
              <FreshnessBadge freshness={quote?.freshness} />
              {quote && (
                <span className="hidden sm:inline">
                  O {fmtPrice(quote.open)} · H {fmtPrice(quote.high)} · L {fmtPrice(quote.low)} · Vol {fmtCompact(quote.volume)}
                </span>
              )}
            </div>
          </div>
        </div>

        {error && !data && (
          <div className="rounded-xl border border-bear/30 bg-bear/5 px-4 py-3 text-sm text-bear">
            {error.status === 404 ? `${symbol} was not found on NSE.` : error.message}
          </div>
        )}

        {data && <ActionBar data={data} price={quote?.last_price ?? null} onCalc={() => setCalcOpen((v) => !v)} calcOpen={calcOpen} />}
        {calcOpen && setup && <TradeCalculator key={`${name}-${timeframe}`} setup={setup} onClose={() => setCalcOpen(false)} />}

        <section className="rounded-xl border border-line bg-panel">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
            <div className="flex max-w-full overflow-x-auto rounded-lg border border-line bg-panel-2 p-0.5" role="tablist" aria-label="Candle size">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  role="tab"
                  aria-selected={tf === timeframe}
                  title={`${TIMEFRAME_LABEL[tf]} candles`}
                  onClick={() => setTimeframe(tf)}
                  className={`tabular shrink-0 rounded-md px-2.5 py-1 text-xs font-semibold ${tf === timeframe ? "bg-accent/15 text-accent" : "text-muted hover:text-ink"}`}
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
              <div className="h-[72vh] min-h-[460px] max-h-[900px] animate-pulse rounded-lg bg-panel-2" />
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
              />
            ) : (
              !error && <p className="py-20 text-center text-sm text-faint">No candle data available.</p>
            )}
          </div>
          <details className="border-t border-line px-3 py-2 text-[11px] text-muted">
            <summary className="cursor-pointer text-faint hover:text-ink">
              Legend · {sig?.bars ?? 0} completed candles · last {fmtTime(sig?.last_candle_time, true)} IST
            </summary>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
              {CHART_LEGEND.map((l) => (
                <span key={l.label} className="inline-flex items-center gap-1.5">
                  <span className="h-0.5 w-3" style={{ background: l.color }} />
                  {l.label}
                </span>
              ))}
            </div>
          </details>
        </section>

        <div className="flex max-w-full gap-1 overflow-x-auto border-b border-line" role="tablist" aria-label="Details">
          {TABS.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={t === tab}
              onClick={() => setTab(t)}
              className={`shrink-0 border-b-2 px-3 py-2 text-sm font-medium ${t === tab ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"}`}
            >
              {t}
            </button>
          ))}
        </div>

        {data && tab === "Trade plan" && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="space-y-4">
              {setup && <TradeCalculator key={`plan-${name}-${data.timeframe}`} setup={setup} />}
            </div>
            <div className="space-y-4">
              {data.history && <TradeStatus history={data.history} quote={quote} timeframe={data.timeframe} />}
              <ScenarioPanel data={data} />
              <RisksPanel risks={sig?.risks ?? []} />
            </div>
          </div>
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

        {data && tab === "Signal details" && (
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
                      <dd className="tabular text-right">{key === "plus_di" ? `${fmtNum(snap.plus_di)} / ${fmtNum(snap.minus_di)}` : f(snap[key] ?? null)}</dd>
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
          <p className="text-xs text-faint">
            Source: {data.source === "demo" ? "synthetic demo generator" : "Upstox Developer API"} · generated {fmtTime(data.generated_at, true)} IST ·{" "}
            {TIMEFRAME_LABEL[data.timeframe]} candles · the current candle is drawn live from quotes; signals use completed candles only
          </p>
        )}
      </main>
      <Disclaimer />
    </>
  );
}

function ScenarioPanel({ data }: { data: StockDetail }) {
  const sig = data.signal;
  return (
    <Panel title="Setup levels">
      {sig.scenario ? (
        <dl className="grid grid-cols-2 gap-y-1.5 text-sm">
          <dt className="text-muted">Direction</dt>
          <dd className={`text-right font-medium ${sig.scenario.direction === "long" ? "text-bull" : "text-bear"}`}>{sig.scenario.direction === "long" ? "BUY" : "SELL"}</dd>
          <dt className="text-muted">Entry zone</dt>
          <dd className="tabular text-right">
            {fmtPrice(sig.scenario.entry_low)}–{fmtPrice(sig.scenario.entry_high)}
          </dd>
          <dt className="text-muted">Target</dt>
          <dd className="tabular text-right text-bull">
            {fmtPrice(sig.scenario.target)}
            {sig.scenario.target_capped_by && <span className="text-xs text-faint"> ({sig.scenario.target_capped_by})</span>}
          </dd>
          <dt className="text-muted">Stop-loss</dt>
          <dd className="tabular text-right text-bear">{fmtPrice(sig.scenario.stop_loss)}</dd>
          <dt className="text-muted">Risk / reward</dt>
          <dd className="tabular text-right">1 : {sig.scenario.risk_reward.toFixed(2)}</dd>
          <dd className="col-span-2 mt-1 text-[11px] text-faint">{sig.scenario.basis}</dd>
        </dl>
      ) : (
        <p className="text-sm text-faint">No setup: the signal did not pass the evidence and data-quality checks.</p>
      )}
      <dl className="mt-3 grid grid-cols-2 gap-y-1 border-t border-line pt-3 text-xs">
        <dt className="text-muted">Support (swing)</dt>
        <dd className="tabular text-right">{fmtPrice(sig.levels.support)}</dd>
        <dt className="text-muted">Resistance (swing)</dt>
        <dd className="tabular text-right">{fmtPrice(sig.levels.resistance)}</dd>
      </dl>
    </Panel>
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
