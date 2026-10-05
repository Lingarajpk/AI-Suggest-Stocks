"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, BrainCircuit, Newspaper, ShieldAlert, Star } from "lucide-react";
import { TIMEFRAME_LABEL, useHealth, useStock } from "@/lib/api";
import { changeColor, fmtCompact, fmtNum, fmtPct, fmtPrice, fmtSigned, fmtTime } from "@/lib/format";
import type { Timeframe } from "@/lib/types";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { useWatchlist } from "@/lib/watchlist";
import { AiExplanation } from "./AiExplanation";
import { AppHeader } from "./AppHeader";
import { TrackRecordPanel } from "./TrackRecord";
import { Disclaimer } from "./Disclaimer";
import { CHART_LEGEND, PriceChart } from "./PriceChart";
import { StatusNotices } from "./StatusNotices";
import { FreshnessBadge, Panel, ScoreBar, SignalBadge, SkeletonRows } from "./ui";

const TIMEFRAMES: Timeframe[] = ["1d", "1h", "15m"];

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

export function StockView({ symbol }: { symbol: string }) {
  const [timeframe, setTimeframe] = useState<Timeframe>("1d");
  const [showBands, setShowBands] = useState(false);
  const [showMarkers, setShowMarkers] = useState(true);
  const health = useHealth();
  const stream = useQuoteStream();
  const { data, error, isLoading } = useStock(symbol, timeframe);
  const watch = useWatchlist();

  const quote = stream.quotes[symbol] ?? data?.quote ?? null;
  const sig = data?.signal;
  const snap = data?.snapshot ?? {};

  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-4 py-5 sm:px-6">
        <StatusNotices health={health.data} healthError={health.error} streamError={stream.error} />
        <Link href="/" className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
          <ArrowLeft className="size-3.5" /> Dashboard
        </Link>

        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight">{symbol}</h1>
              <button onClick={() => watch.toggle(symbol)} aria-label="Toggle watchlist" className="text-faint hover:text-warn">
                <Star className={`size-5 ${watch.has(symbol) ? "fill-warn text-warn" : ""}`} />
              </button>
              {sig && <SignalBadge signal={sig} />}
            </div>
            <p className="text-sm text-muted">
              {data?.instrument.name ?? "…"} · {data?.instrument.exchange} {data?.instrument.sector && `· ${data.instrument.sector}`}
            </p>
            {data?.instrument.isin && <p className="text-xs text-faint">ISIN {data.instrument.isin} · key {data.instrument.instrument_key}</p>}
          </div>
          <div className="text-right">
            <p className="tabular text-3xl font-semibold">{fmtPrice(quote?.last_price ?? data?.last_close)}</p>
            <p className={`tabular text-sm ${changeColor(quote?.change)}`}>
              {fmtSigned(quote?.change)} ({fmtPct(quote?.change_pct)})
            </p>
            <div className="mt-1 flex items-center justify-end gap-3 text-xs text-faint">
              <FreshnessBadge freshness={quote?.freshness} />
              {quote?.last_trade_time && <span>Last trade {fmtTime(quote.last_trade_time)}</span>}
            </div>
          </div>
        </div>

        {quote && (
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
            {[
              ["Open", fmtPrice(quote.open)],
              ["High", fmtPrice(quote.high)],
              ["Low", fmtPrice(quote.low)],
              ["Prev close", fmtPrice(quote.prev_close)],
              ["Volume", fmtCompact(quote.volume)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-lg border border-line bg-panel px-3 py-2">
                <p className="text-xs text-muted">{k}</p>
                <p className="tabular">{v}</p>
              </div>
            ))}
          </div>
        )}

        {error && (
          <div className="rounded-xl border border-bear/30 bg-bear/5 px-4 py-3 text-sm text-bear">
            {error.status === 404 ? `${symbol} is not in the configured instrument universe.` : error.message}
          </div>
        )}

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
          <Panel
            title={
              <div className="flex rounded-lg border border-line bg-panel-2 p-0.5">
                {TIMEFRAMES.map((tf) => (
                  <button
                    key={tf}
                    onClick={() => setTimeframe(tf)}
                    className={`rounded-md px-3 py-1 text-xs font-medium ${tf === timeframe ? "bg-accent/15 text-accent" : "text-muted hover:text-ink"}`}
                  >
                    {TIMEFRAME_LABEL[tf]}
                  </button>
                ))}
              </div>
            }
            action={
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-1.5 text-xs text-muted">
                  <input type="checkbox" checked={showMarkers} onChange={(e) => setShowMarkers(e.target.checked)} className="accent-accent" />
                  Buy/sell signals
                </label>
                <label className="flex items-center gap-1.5 text-xs text-muted">
                  <input type="checkbox" checked={showBands} onChange={(e) => setShowBands(e.target.checked)} className="accent-accent" />
                  Bollinger bands
                </label>
              </div>
            }
          >
            {isLoading && !data ? (
              <div className="h-[560px] animate-pulse rounded-lg bg-panel-2" />
            ) : data?.candles.length ? (
              <>
                <PriceChart
                  candles={data.candles}
                  series={data.series}
                  oscillators={data.oscillators}
                  support={sig?.levels.support}
                  resistance={sig?.levels.resistance}
                  intraday={timeframe !== "1d"}
                  showBands={showBands}
                  markers={data.history?.markers ?? []}
                  showMarkers={showMarkers}
                  scenario={sig?.scenario ?? null}
                />
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
                  {CHART_LEGEND.map((l) => (
                    <span key={l.label} className="inline-flex items-center gap-1.5">
                      <span className="h-0.5 w-3" style={{ background: l.color }} />
                      {l.label}
                    </span>
                  ))}
                  <span>Panes: price · RSI 14 · MACD 12/26/9 · S/R dashed</span>
                  <span className="ml-auto">
                    {sig?.bars} completed candles · last {fmtTime(sig?.last_candle_time, true)} IST
                  </span>
                </div>
              </>
            ) : (
              !error && <p className="py-20 text-center text-sm text-faint">No candle data available.</p>
            )}
          </Panel>

          <div className="space-y-5">
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

            {data?.history && <TrackRecordPanel history={data.history} timeframe={timeframe} />}

            <Panel title="Scenario levels">
              {sig?.scenario ? (
                <dl className="grid grid-cols-2 gap-y-1.5 text-sm">
                  <dt className="text-muted">Direction</dt>
                  <dd className={`text-right font-medium ${sig.scenario.direction === "long" ? "text-bull" : "text-bear"}`}>{sig.scenario.direction}</dd>
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
                <p className="text-sm text-faint">No scenario: the signal did not pass evidence and data-quality checks.</p>
              )}
              <dl className="mt-3 grid grid-cols-2 gap-y-1 border-t border-line pt-3 text-xs">
                <dt className="text-muted">Support (swing)</dt>
                <dd className="tabular text-right">{fmtPrice(sig?.levels.support)}</dd>
                <dt className="text-muted">Resistance (swing)</dt>
                <dd className="tabular text-right">{fmtPrice(sig?.levels.resistance)}</dd>
              </dl>
            </Panel>

            <Panel title={<span className="inline-flex items-center gap-2"><ShieldAlert className="size-4 text-warn" /> Risk factors</span>}>
              {sig?.risks.length ? (
                <ul className="space-y-1 text-sm text-muted">
                  {sig.risks.map((r) => (
                    <li key={r}>• {r}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-faint">No indicator-based risk flags. Market, event and liquidity risk always apply.</p>
              )}
            </Panel>
          </div>
        </div>

        {data && (
          <AiExplanation key={`${symbol}-${timeframe}`} symbol={symbol} timeframe={timeframe} enabled={!!health.data?.nvidia_configured} />
        )}

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <Panel title="Indicators" className="lg:col-span-1">
            <dl className="grid grid-cols-2 gap-y-1 text-xs">
              {INDICATOR_ROWS.map(([label, key, f]) => (
                <div key={key} className="contents">
                  <dt className="text-muted">{label}</dt>
                  <dd className="tabular text-right">{key === "plus_di" ? `${fmtNum(snap.plus_di)} / ${fmtNum(snap.minus_di)}` : f(snap[key] ?? null)}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title={<span className="inline-flex items-center gap-2"><BrainCircuit className="size-4 text-accent" /> Model outlook</span>}>
            <p className="text-sm text-muted">{data?.model_outlook.message ?? "…"}</p>
            <p className="mt-2 text-xs text-faint">
              Planned: LightGBM/XGBoost models per horizon (15m, 1h, EOD, next day, 5 days) with calibrated probabilities, walk-forward backtests and published
              hit-rates.
            </p>
          </Panel>
          <Panel title={<span className="inline-flex items-center gap-2"><Newspaper className="size-4 text-accent" /> News sentiment</span>}>
            <p className="text-sm text-muted">{data?.news.message ?? "…"}</p>
            <p className="mt-2 text-xs text-faint">
              Upstox market data does not include news. A licensed news feed plus NVIDIA NIM summaries (with original source links) are planned.
            </p>
          </Panel>
        </div>

        {data && (
          <p className="text-xs text-faint">
            Source: {data.source === "demo" ? "synthetic demo generator" : "Upstox Developer API"} · generated {fmtTime(data.generated_at, true)} IST ·{" "}
            {TIMEFRAME_LABEL[timeframe]} candles, forming candle excluded
          </p>
        )}
      </main>
      <Disclaimer />
    </>
  );
}
