"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ArrowDownRight, ArrowUpRight, Clock, Maximize2, Zap } from "lucide-react";
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
import { FreshnessBadge, Panel } from "./ui";

const VERDICT: Record<IntradayVerdict, { label: string; text: string; ring: string; chip: string }> = {
  BUY: { label: "▲ BUY", text: "text-bull", ring: "border-bull/60 from-bull/15", chip: "bg-bull text-bg" },
  SELL: { label: "▼ SELL", text: "text-bear", ring: "border-bear/60 from-bear/15", chip: "bg-bear text-bg" },
  WAIT: { label: "◆ WAIT", text: "text-warn", ring: "border-line from-warn/5", chip: "bg-warn/15 text-warn" },
  "NO TRADE": { label: "NO TRADE", text: "text-muted", ring: "border-line from-muted/5", chip: "bg-muted/15 text-muted" },
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
    <div className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2/60 px-3 py-2">
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
          <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-bear/60">
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
  const liveBar = useMemo(
    () => (card.chart ? buildLiveBar("5m", card.chart.forming_candle, card.chart.candles.at(-1), quote) : null),
    [card.chart, quote],
  );
  if (card.error) {
    return (
      <section className="rounded-xl border border-line bg-panel p-4 text-sm">
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
    <section className={`flex min-w-0 flex-col rounded-xl border-2 bg-linear-to-b to-panel p-4 ${v.ring}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold tracking-tight">{sym}</p>
          <p className="tabular text-2xl font-semibold">{fmtPrice(price)}</p>
          <p className={`tabular text-xs ${changeColor(quote?.change)}`}>
            {fmtSigned(quote?.change)} ({fmtPct(quote?.change_pct)})
          </p>
        </div>
        <div className="text-right">
          <span className={`inline-block rounded-lg px-3 py-1.5 text-xl font-bold tracking-tight ${v.chip}`}>{v.label}</span>
          <p className="mt-1 flex items-center justify-end gap-1 text-[11px] text-faint">
            <Clock className="size-3" />
            {card.since_known && card.since ? `since ${fmtTime(card.since)}` : `as of ${fmtTime(card.last_candle_time)}`}
          </p>
          <div className="mt-0.5 flex justify-end">
            <FreshnessBadge freshness={quote?.freshness} />
          </div>
        </div>
      </div>

      <p className={`mt-2 text-sm ${v.text}`}>{card.reason}</p>

      <div className="mt-3 flex gap-2">
        <Leg leg={card.entry} label="5-min entry" />
        <Leg leg={card.trend} label="15-min trend" />
      </div>

      {lv ? (
        <dl className="mt-3 grid grid-cols-4 gap-2 text-center text-xs">
          <div className="rounded-md bg-panel-2/70 px-1 py-1.5">
            <dt className="text-[10px] text-muted">Entry</dt>
            <dd className="tabular">{fmtPrice((lv.entry_low + lv.entry_high) / 2)}</dd>
          </div>
          <div className="rounded-md bg-bull/10 px-1 py-1.5">
            <dt className="text-[10px] text-muted">Target</dt>
            <dd className="tabular text-bull">{fmtPrice(lv.target)}</dd>
          </div>
          <div className="rounded-md bg-bear/10 px-1 py-1.5">
            <dt className="text-[10px] text-muted">Stop</dt>
            <dd className="tabular text-bear">{fmtPrice(lv.stop)}</dd>
          </div>
          <div className="rounded-md bg-panel-2/70 px-1 py-1.5">
            <dt className="text-[10px] text-muted">Risk:Reward</dt>
            <dd className="tabular">1 : {lv.risk_reward.toFixed(1)}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-3 rounded-md bg-panel-2/60 px-3 py-2 text-xs text-muted">No entry, target or stop: wait for both timeframes to agree.</p>
      )}

      <div className="mt-3 -mx-1">
        {card.chart ? <IntradayChart card={card} liveBar={liveBar} /> : <div className="h-[260px] animate-pulse rounded-lg bg-panel-2" />}
      </div>

      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
        <dt className="text-muted">Session VWAP{card.instrument.kind === "index" ? " (avg price)" : ""}</dt>
        <dd className="tabular text-right">
          {fmtPrice(card.session_vwap)} {vwapSide && <span className={vwapSide === "above" ? "text-bull" : "text-bear"}>· price {vwapSide}</span>}
        </dd>
        <dt className="text-muted">Supertrend (5-min)</dt>
        <dd className={`text-right ${card.supertrend.dir === 1 ? "text-bull" : card.supertrend.dir === -1 ? "text-bear" : ""}`}>
          {card.supertrend.dir === 1 ? "up" : card.supertrend.dir === -1 ? "down" : "—"} at {fmtPrice(card.supertrend.line)}
        </dd>
        <dt className="text-muted">Support / resistance</dt>
        <dd className="tabular text-right">
          {fmtPrice(card.support)} / {fmtPrice(card.resistance)}
        </dd>
        <dt className="text-muted">ATR (5-min)</dt>
        <dd className="tabular text-right">{fmtPrice(card.atr)}</dd>
      </dl>

      {(card.strategies_long.length > 0 || card.strategies_short.length > 0) && (
        <div className="mt-2 flex flex-wrap gap-1">
          {card.strategies_long.map((s) => (
            <span key={`l-${s}`} className="rounded bg-bull/10 px-1.5 py-0.5 text-[10px] text-bull">▲ {s}</span>
          ))}
          {card.strategies_short.map((s) => (
            <span key={`s-${s}`} className="rounded bg-bear/10 px-1.5 py-0.5 text-[10px] text-bear">▼ {s}</span>
          ))}
        </div>
      )}

      <p className="mt-2 text-[11px] text-faint">
        {hit != null && calls ? (
          <>
            On unseen data, 5-min {card.verdict} calls were right <b className="text-ink">{hit}%</b> of {calls.toLocaleString()} (price rose{" "}
            {rel.base_up_pct}% of the time anyway).
          </>
        ) : rel.skill_pct != null ? (
          <>5-min model skill vs guessing on unseen data: {rel.skill_pct > 0 ? "+" : ""}{rel.skill_pct}%.</>
        ) : null}
        {card.risks.length > 0 && <span className="block text-warn">⚠ {card.risks[0]}</span>}
      </p>

      <Link href={`/stock/${slug(sym)}?tf=5m`} className="mt-auto inline-flex items-center gap-1 pt-3 text-xs text-accent hover:underline">
        <Maximize2 className="size-3" /> Full chart, strategies & news
      </Link>
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
          <table className="w-full min-w-[520px] text-xs">
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
                  <tr key={r.instrument.symbol} className="border-b border-line/60">
                    <td className="py-1.5 pr-2">
                      <Link href={`/stock/${r.instrument.symbol}?tf=5m`} className="font-medium hover:text-accent">
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

export function IntradayDesk() {
  const health = useHealth();
  const stream = useQuoteStream();
  const { data, error, isLoading } = useIntraday();
  const note = data ? SESSION_NOTE[data.session] : null;

  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-4 py-5 sm:px-6">
        <StatusNotices health={health.data} healthError={health.error} streamError={stream.error} />

        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
              <Zap className="size-5 text-accent" /> Intraday desk
            </h1>
            <p className="text-xs text-muted">5-minute entry timing, confirmed by the 15-minute trend · indices first</p>
          </div>
          <p className="text-xs text-faint">{data ? `Updated ${fmtTime(data.generated_at)} IST · refreshes every 20 s` : "Loading…"}</p>
        </div>

        {note && <p className="rounded-lg border border-warn/30 bg-warn/5 px-4 py-2 text-xs text-warn">{note}</p>}
        {error && error.status !== 401 && (
          <div className="rounded-xl border border-bear/30 bg-bear/5 px-4 py-3 text-sm text-bear">Intraday desk failed: {error.message}</div>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {isLoading && !data
            ? [0, 1, 2].map((i) => <div key={i} className="h-[640px] animate-pulse rounded-xl border border-line bg-panel" />)
            : data?.indices.map((card) => (
                <IndexCard key={card.instrument.instrument_key} card={card} quote={stream.quotes[card.instrument.symbol] ?? card.quote} />
              ))}
        </div>
        {data && data.unavailable_indices.length > 0 && (
          <p className="text-xs text-warn">Not available from the data provider: {data.unavailable_indices.join(", ")}.</p>
        )}

        {data?.stocks && (
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <StockCalls title="Stocks · intraday BUY" rows={data.stocks.buy} side="BUY" quotes={stream.quotes} />
            <StockCalls title="Stocks · intraday SELL" rows={data.stocks.sell} side="SELL" quotes={stream.quotes} />
          </div>
        )}
        {data?.stocks && (
          <p className="text-xs text-faint">
            {data.stocks.waiting} of {data.stocks.scanned} stocks are on WAIT (timeframes disagree or no clear direction).
          </p>
        )}

        <MarketNews />

        {data && (
          <p className="text-xs text-faint">
            <b className="text-muted">How the call is made:</b> {data.method} Source: {data.source === "demo" ? "synthetic demo data" : "Upstox"}.
          </p>
        )}
      </main>
      <Disclaimer />
    </>
  );
}
