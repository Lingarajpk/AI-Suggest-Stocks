"use client";

import Link from "next/link";
import { TrendingDown, TrendingUp } from "lucide-react";
import { useToday } from "@/lib/api";
import { changeColor, fmtPct, fmtPrice, fmtTime } from "@/lib/format";
import type { Quote, TodayItem } from "@/lib/types";
import { EdgeBadge } from "./TrackRecord";
import { Panel, ScoreBar, SkeletonRows } from "./ui";

function Row({ item, quote, side }: { item: TodayItem; quote: Quote | null; side: "up" | "down" }) {
  const q = quote ?? item.quote;
  const sc = item.scenario;
  const tr = item.track_record;
  return (
    <li className="rounded-lg border border-line bg-panel-2/50 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/stock/${item.instrument.symbol}`} className="font-semibold hover:text-accent">
            {item.instrument.symbol}
          </Link>
          <span className="ml-2 text-xs text-faint">{item.instrument.sector}</span>
          <p className="text-xs text-muted">{item.daily_signal} (daily)</p>
        </div>
        <div className="text-right">
          <p className="tabular font-semibold">{fmtPrice(q?.last_price ?? item.last_close)}</p>
          <p className={`tabular text-xs ${changeColor(q?.change_pct)}`}>{fmtPct(q?.change_pct)} today</p>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="flex items-center gap-2 text-muted">
          Strength <ScoreBar score={item.combined_score} />
        </span>
        <span className="text-faint">
          daily {item.daily_score > 0 ? "+" : ""}
          {item.daily_score.toFixed(0)} · today {item.intraday_score == null ? "—" : `${item.intraday_score > 0 ? "+" : ""}${item.intraday_score.toFixed(0)}`}
        </span>
      </div>

      {sc && (
        <p className="tabular mt-2 text-xs">
          <span className="text-muted">{side === "up" ? "Buy zone" : "Sell zone"} </span>
          {fmtPrice(sc.entry_low)}–{fmtPrice(sc.entry_high)}
          <span className="text-muted"> · Target </span>
          <span className="text-bull">{fmtPrice(sc.target)}</span>
          <span className="text-muted"> · Stop </span>
          <span className="text-bear">{fmtPrice(sc.stop_loss)}</span>
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted">
        <EdgeBadge winRate={tr.win_rate} baseRate={tr.base_rate_pct} count={tr.count} />
        {tr.count > 0 && (
          <span>
            Past {side === "up" ? "BUY" : "SELL"} signals on {item.instrument.symbol}: right {tr.win_rate}% of {tr.count} (random {tr.base_rate_pct}%) over{" "}
            {tr.horizon_label}
          </span>
        )}
      </div>
    </li>
  );
}

export function TodayOutlook({ quotes }: { quotes: Record<string, Quote> }) {
  const { data, error, isLoading } = useToday();

  const header = (
    <span className="flex flex-wrap items-baseline gap-x-2">
      Today&apos;s outlook
      <span className="text-xs font-normal text-muted">
        {data?.session === "open" ? "live session" : "market closed — based on the last session"}
        {data && ` · updated ${fmtTime(data.generated_at)}`}
      </span>
    </span>
  );

  const allWeak = data && [...data.upside, ...data.downside].every((i) => {
    const t = i.track_record;
    return t.win_rate == null || t.base_rate_pct == null || t.count < 20 || t.win_rate - t.base_rate_pct < 5;
  });

  return (
    <Panel title={header}>
      {error ? (
        <p className="text-sm text-faint">{error.status === 401 ? "Connect Upstox to see today's outlook." : `Unavailable: ${error.message}`}</p>
      ) : isLoading || !data ? (
        <SkeletonRows rows={4} />
      ) : (
        <>
          {allWeak && (data.upside.length > 0 || data.downside.length > 0) && (
            <p className="mb-3 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-xs text-warn">
              None of these setups has clearly beaten random in its own past signals. Treat them as a watchlist of current technical conditions, not as
              predictions.
            </p>
          )}
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
            <div>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-bull">
                <TrendingUp className="size-4" /> Upside setups
              </h3>
              {data.upside.length ? (
                <ul className="space-y-2">
                  {data.upside.map((i) => (
                    <Row key={i.instrument.symbol} item={i} quote={quotes[i.instrument.symbol] ?? null} side="up" />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-faint">No stock has a bullish daily signal confirmed by today&apos;s momentum.</p>
              )}
            </div>
            <div>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-bear">
                <TrendingDown className="size-4" /> Downside setups
              </h3>
              {data.downside.length ? (
                <ul className="space-y-2">
                  {data.downside.map((i) => (
                    <Row key={i.instrument.symbol} item={i} quote={quotes[i.instrument.symbol] ?? null} side="down" />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-faint">No stock has a bearish daily signal confirmed by today&apos;s momentum.</p>
              )}
            </div>
          </div>
          <p className="mt-3 text-[11px] text-faint">
            {data.method} Out of {data.considered} stocks scanned. Not investment advice.
          </p>
        </>
      )}
    </Panel>
  );
}
