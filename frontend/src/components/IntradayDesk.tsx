"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Calculator, Clock, Maximize2, Zap } from "lucide-react";
import { useHealth, useIntraday } from "@/lib/api";
import { changeColor, fmtPct, fmtPrice, fmtSigned, fmtTime } from "@/lib/format";
import { buildLiveBar } from "@/lib/liveBar";
import type { IntradayCard, IntradayLeg, IntradayVerdict, Quote } from "@/lib/types";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { AppHeader } from "./AppHeader";
import { Disclaimer } from "./Disclaimer";
import { IntradayChart } from "./IntradayChart";
import { MarketNews } from "./NewsPanel";
import { StatusNotices } from "./StatusNotices";
import { TradeCalculator } from "./TradeCalculator";
import { FreshnessBadge, HowItWorks, Panel } from "./ui";

const VERDICT: Record<IntradayVerdict, { label: string; text: string; bar: string; chip: string }> = {
  BUY: { label: "▲ BUY", text: "text-bull", bar: "bg-bull", chip: "bg-bull text-white" },
  SELL: { label: "▼ SELL", text: "text-bear", bar: "bg-bear", chip: "bg-bear text-white" },
  WAIT: { label: "WAIT", text: "text-warn", bar: "bg-warn", chip: "bg-warn/15 text-warn" },
  "NO TRADE": { label: "NO TRADE", text: "text-muted", bar: "bg-line", chip: "bg-panel-2 text-muted" },
};

const SESSION_NOTE = {
  open: null,
  pre_open: "Pre-open: calls use the last completed candles and go live at 09:15.",
  closed: "Market closed: these are the calls from the last completed candles, not live.",
} as const;

/** Route slug for an index (the API accepts "NIFTY50", "BANKNIFTY", "SENSEX"). */
const slug = (symbol: string) => symbol.replace(/\s+/g, "");

function Leg({ leg, label }: { leg: IntradayLeg; label: string }) {
  const up = leg.up_pct;
  const tone = leg.lean > 0 ? "text-bull" : leg.lean < 0 ? "text-bear" : "text-muted";
  return (
    <div className="min-w-0 flex-1 rounded-xl bg-panel-2 px-3 py-2.5">
      <p className="flex items-center justify-between text-[11px] text-muted">
        <span>{label}</span>
        <span className={tone}>{leg.lean > 0 ? "▲ up" : leg.lean < 0 ? "▼ down" : "— flat"}</span>
      </p>
      {up != null ? (
        <>
          <p className="tabular text-lg font-semibold">
            <span className="text-bull">{up.toFixed(0)}%</span>
            <span className="text-faint"> / </span>
            <span className="text-bear">{(100 - up).toFixed(0)}%</span>
          </p>
          <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-bear/50">
            <div className="h-full bg-bull" style={{ width: `${up}%` }} />
          </div>
          <p className="mt-1 text-[10px] text-faint">up / down over {leg.horizon_label} · model</p>
        </>
      ) : (
        <p className="mt-0.5 text-xs text-muted">
          Rule score <b className={tone}>{leg.score == null ? "—" : `${leg.score > 0 ? "+" : ""}${leg.score.toFixed(0)}`}</b>
          <span className="block text-[10px] text-faint">model had no edge on this timeframe</span>
        </p>
      )}
    </div>
  );
}

function IndexCard({ card, quote }: { card: IntradayCard; quote: Quote | null }) {
  const sym = card.instrument.symbol;
  const [calc, setCalc] = useState(false);
  const liveBar = useMemo(
    () => (card.chart ? buildLiveBar("5m", card.chart.forming_candle, card.chart.candles.at(-1), quote) : null),
    [card.chart, quote],
  );
  if (card.error) {
    return (
      <section className="rounded-2xl border border-line bg-panel p-5 text-sm">
        <p className="font-semibold">{sym}</p>
        <p className="mt-1 text-bear">{card.error}</p>
      </section>
    );
  }
  const v = VERDICT[card.verdict];
  const price = quote?.last_price ?? card.price;
  const lv = card.levels;
  const vwapSide = price != null && card.session_vwap != null ? (price >= card.session_vwap ? "above" : "below") : null;
  const rel = card.reliability;
  const hit = card.verdict === "BUY" ? rel.buy_hit_pct : card.verdict === "SELL" ? rel.sell_hit_pct : null;
  const calls = card.verdict === "BUY" ? rel.buy_calls : card.verdict === "SELL" ? rel.sell_calls : null;
  return (
    <section className="relative flex min-w-0 flex-col overflow-hidden rounded-2xl border border-line bg-panel p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
      <span className={`absolute inset-x-0 top-0 h-1 ${v.bar}`} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/stock/${slug(sym)}?tf=5m`} className="text-sm font-semibold text-muted hover:text-accent">
            {sym}
          </Link>
          <p className="tabular text-[26px] leading-tight font-bold tracking-tight">{fmtPrice(price)}</p>
          <p className={`tabular text-sm font-semibold ${changeColor(quote?.change)}`}>
            {fmtSigned(quote?.change)} ({fmtPct(quote?.change_pct)})
          </p>
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

      {lv ? (
        <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
          <div className="rounded-xl bg-panel-2 px-1 py-2">
            <dt className="text-[11px] text-muted">Entry</dt>
            <dd className="tabular text-sm font-semibold">{fmtPrice((lv.entry_low + lv.entry_high) / 2)}</dd>
          </div>
          <div className="rounded-xl bg-bull/10 px-1 py-2">
            <dt className="text-[11px] text-muted">Target</dt>
            <dd className="tabular text-sm font-semibold text-bull">{fmtPrice(lv.target)}</dd>
          </div>
          <div className="rounded-xl bg-bear/10 px-1 py-2">
            <dt className="text-[11px] text-muted">Stop-loss</dt>
            <dd className="tabular text-sm font-semibold text-bear">{fmtPrice(lv.stop)}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-3 rounded-xl bg-panel-2 px-3.5 py-2.5 text-sm text-muted">No entry yet: wait for both timeframes to agree.</p>
      )}
      {card.action && card.action.label !== card.verdict && (
        <p className={`mt-2 rounded-xl px-3.5 py-2.5 text-sm font-semibold ${card.action.tone === "warn" ? "bg-warn/10 text-warn" : card.action.tone === "bull" ? "bg-bull/10 text-bull" : "bg-bear/10 text-bear"}`}>
          {card.action.label}: {card.action.text}
        </p>
      )}
      {calc && lv && (card.verdict === "BUY" || card.verdict === "SELL") && (
        <div className="mt-3">
          <TradeCalculator
            setup={{ symbol: sym, side: card.verdict, entry: +((lv.entry_low + lv.entry_high) / 2).toFixed(2), stop: lv.stop, target: lv.target, source: "this intraday call" }}
            onClose={() => setCalc(false)}
          />
        </div>
      )}

      <div className="mt-3 -mx-2">
        {card.chart ? <IntradayChart card={card} liveBar={liveBar} height={300} /> : <div className="h-[300px] animate-pulse rounded-xl bg-panel-2" />}
      </div>

      <HowItWorks label="More details">
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
          <dt>Session VWAP{card.instrument.kind === "index" ? " (avg price)" : ""}</dt>
          <dd className="tabular text-right text-ink">
            {fmtPrice(card.session_vwap)} {vwapSide && <span className={vwapSide === "above" ? "text-bull" : "text-bear"}>· price {vwapSide}</span>}
          </dd>
          <dt>Supertrend (5-min)</dt>
          <dd className={`text-right ${card.supertrend.dir === 1 ? "text-bull" : card.supertrend.dir === -1 ? "text-bear" : ""}`}>
            {card.supertrend.dir === 1 ? "up" : card.supertrend.dir === -1 ? "down" : "—"} at {fmtPrice(card.supertrend.line)}
          </dd>
          <dt>Support / resistance</dt>
          <dd className="tabular text-right text-ink">
            {fmtPrice(card.support)} / {fmtPrice(card.resistance)}
          </dd>
          <dt>ATR (5-min) · Risk:Reward</dt>
          <dd className="tabular text-right text-ink">
            {fmtPrice(card.atr)} · {lv ? `1 : ${lv.risk_reward.toFixed(1)}` : "—"}
          </dd>
        </dl>
        {(card.strategies_long.length > 0 || card.strategies_short.length > 0) && (
          <div className="mt-2 flex flex-wrap gap-1">
            {card.strategies_long.map((s) => (
              <span key={`l-${s}`} className="rounded-full bg-bull/10 px-2 py-0.5 text-[11px] text-bull">▲ {s}</span>
            ))}
            {card.strategies_short.map((s) => (
              <span key={`s-${s}`} className="rounded-full bg-bear/10 px-2 py-0.5 text-[11px] text-bear">▼ {s}</span>
            ))}
          </div>
        )}
        <p className="mt-2">
          {hit != null && calls ? (
            <>
              On unseen data, 5-min {card.verdict} calls were right <b className="text-ink">{hit}%</b> of {calls.toLocaleString()} (price rose{" "}
              {rel.base_up_pct}% of the time anyway).
            </>
          ) : rel.skill_pct != null ? (
            <>5-min model skill vs guessing on unseen data: {rel.skill_pct > 0 ? "+" : ""}{rel.skill_pct}%.</>
          ) : null}
        </p>
      </HowItWorks>
      {card.risks.length > 0 && <p className="mt-2 text-xs text-warn">⚠ {card.risks[0]}</p>}

      <div className="mt-auto flex gap-2 pt-4">
        {lv && (
          <button onClick={() => setCalc((x) => !x)} className="inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-full border border-line text-sm font-semibold hover:border-accent/50">
            <Calculator className="size-4" /> {calc ? "Hide calculator" : "Calculator"}
          </button>
        )}
        <Link href={`/stock/${slug(sym)}?tf=5m`} className="inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-full bg-accent text-sm font-semibold text-white hover:opacity-90">
          <Maximize2 className="size-4" /> Open chart
        </Link>
      </div>
    </section>
  );
}

function StockCalls({ title, rows, side, quotes }: { title: string; rows: IntradayCard[]; side: "BUY" | "SELL"; quotes: Record<string, Quote> }) {
  const Icon = side === "BUY" ? ArrowUpRight : ArrowDownRight;
  return (
    <Panel
      title={
        <span className={`inline-flex items-center gap-2 ${side === "BUY" ? "text-bull" : "text-bear"}`}>
          <Icon className="size-4" /> {title}
        </span>
      }
      action={<span className="text-xs text-muted">{rows.length}</span>}
    >
      {rows.length === 0 ? (
        <p className="text-sm text-faint">No stock has 5-min and 15-min agreeing on a {side} right now.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-sm">
            <thead className="text-left text-muted">
              <tr className="border-b border-line">
                <th className="py-1.5 pr-2 font-medium">Stock</th>
                <th className="py-1.5 pr-2 text-right font-medium">Price</th>
                <th className="py-1.5 pr-2 text-right font-medium">Day</th>
                <th className="py-1.5 pr-2 text-right font-medium" title="5-min model: chance price is higher in 1 hour">5m up</th>
                <th className="py-1.5 pr-2 text-right font-medium" title="15-min model: chance price is higher in 2 hours">15m up</th>
                <th className="py-1.5 pr-2 text-right font-medium">Target</th>
                <th className="py-1.5 pr-2 text-right font-medium">Stop</th>
                <th className="py-1.5 text-right font-medium">Since</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const q = quotes[r.instrument.symbol] ?? r.quote;
                return (
                  <tr key={r.instrument.symbol} className="border-b border-line/60 last:border-0 hover:bg-panel-2/60">
                    <td className="py-1.5 pr-2">
                      <Link href={`/stock/${r.instrument.symbol}?tf=5m`} className="font-semibold hover:text-accent">
                        {r.instrument.symbol}
                      </Link>
                    </td>
                    <td className="tabular py-1.5 pr-2 text-right">{fmtPrice(q?.last_price ?? r.price)}</td>
                    <td className={`tabular py-1.5 pr-2 text-right ${changeColor(q?.change_pct)}`}>{fmtPct(q?.change_pct)}</td>
                    <td className="tabular py-1.5 pr-2 text-right">{r.entry.up_pct == null ? `score ${r.entry.score}` : `${r.entry.up_pct.toFixed(0)}%`}</td>
                    <td className="tabular py-1.5 pr-2 text-right">{r.trend.up_pct == null ? `score ${r.trend.score}` : `${r.trend.up_pct.toFixed(0)}%`}</td>
                    <td className="tabular py-1.5 pr-2 text-right text-bull">{fmtPrice(r.levels?.target)}</td>
                    <td className="tabular py-1.5 pr-2 text-right text-bear">{fmtPrice(r.levels?.stop)}</td>
                    <td className="py-1.5 text-right text-faint">{r.since_known && r.since ? fmtTime(r.since) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** Shown instead of a blank page when the desk can't load. */
function DeskUnavailable({ status, code, message }: { status: number; code: string; message: string }) {
  const offline = status === 401;
  const waking = code === "backend_waking" || code === "backend_unreachable";
  return (
    <div className="flex flex-col items-center gap-2 rounded-2xl border border-line bg-panel px-6 py-14 text-center">
      <p className="text-lg font-semibold">
        {offline ? "Live data is offline" : waking ? "Waking up the data server…" : "The intraday desk could not load"}
      </p>
      <p className="max-w-xl text-sm text-muted">
        {offline
          ? "The Upstox connection expires every day at 03:30 IST and the site owner hasn't renewed it yet. This page will fill in automatically as soon as it is."
          : waking
            ? "On free hosting the server sleeps when nobody uses it and takes 30–60 seconds to start. This page retries on its own."
            : message}
      </p>
      {!offline && <span className="mt-2 size-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />}
    </div>
  );
}

export function IntradayDesk() {
  const health = useHealth();
  const stream = useQuoteStream();
  const { data, error } = useIntraday();
  const note = data ? SESSION_NOTE[data.session] : null;

  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-4 py-5 sm:px-6">
        <StatusNotices health={health.data} healthError={health.error} streamError={stream.error} />

        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
              <Zap className="size-5 text-accent" /> Intraday
            </h1>
            <p className="text-sm text-muted">Index calls from 5-minute timing confirmed by the 15-minute trend</p>
          </div>
          <p className="text-xs text-faint">{data ? `Updated ${fmtTime(data.generated_at)} IST` : "Loading…"}</p>
        </div>

        {note && <p className="rounded-xl bg-warn/10 px-4 py-2.5 text-sm text-warn">{note}</p>}

        {!data && error ? (
          <DeskUnavailable status={error.status} code={error.code} message={error.message} />
        ) : (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2 xl:grid-cols-3">
            {!data
              ? [0, 1, 2].map((i) => (
                  <div key={i} className="space-y-3 rounded-2xl border border-line bg-panel p-5">
                    <p className="text-sm font-semibold text-muted">{["NIFTY 50", "BANK NIFTY", "SENSEX"][i]}</p>
                    <div className="h-8 w-40 animate-pulse rounded-lg bg-panel-2" />
                    <div className="h-16 animate-pulse rounded-xl bg-panel-2" />
                    <div className="h-[300px] animate-pulse rounded-xl bg-panel-2" />
                  </div>
                ))
              : data.indices.length === 0
                ? <p className="text-sm text-faint">The data provider returned no index data.</p>
                : data.indices.map((card) => (
                    <IndexCard key={card.instrument.instrument_key} card={card} quote={stream.quotes[card.instrument.symbol] ?? card.quote} />
                  ))}
          </div>
        )}
        {data && error && <p className="text-xs text-warn">Last refresh failed ({error.message}); showing the previous calls.</p>}
        {data && data.unavailable_indices.length > 0 && (
          <p className="text-xs text-warn">Not available from the data provider: {data.unavailable_indices.join(", ")}.</p>
        )}

        {data?.stocks && (
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <StockCalls title="Stocks to BUY" rows={data.stocks.buy} side="BUY" quotes={stream.quotes} />
            <StockCalls title="Stocks to SELL" rows={data.stocks.sell} side="SELL" quotes={stream.quotes} />
          </div>
        )}
        {data?.stocks && (
          <p className="text-xs text-faint">
            {data.stocks.waiting} of {data.stocks.scanned} stocks are on WAIT (timeframes disagree or no clear direction).
          </p>
        )}

        <MarketNews />

        {data && (
          <HowItWorks label="How the calls are made">
            {data.method} Source: {data.source === "demo" ? "synthetic demo data" : "Upstox"}.
          </HowItWorks>
        )}
      </main>
      <Disclaimer />
    </>
  );
}
