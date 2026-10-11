"use client";

import Link from "next/link";
import { ExternalLink, Newspaper } from "lucide-react";
import { useNewsFeed, useStockNews } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { NewsItem } from "@/lib/types";
import { Panel } from "./ui";

function ago(iso: string) {
  const mins = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000));
  if (mins < 60) return `${mins}m ago`;
  const h = Math.round(mins / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

function SentimentChip({ s }: { s: number }) {
  const cls = s > 0.15 ? "bg-bull/15 text-bull" : s < -0.15 ? "bg-bear/15 text-bear" : "bg-muted/10 text-muted";
  return <span className={`tabular shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold ${cls}`}>{s > 0 ? "+" : ""}{s.toFixed(1)}</span>;
}

const IMPACT_CLS = { high: "text-warn", medium: "text-muted", low: "text-faint" };

function Headline({ it, showSymbol }: { it: NewsItem; showSymbol?: boolean }) {
  return (
    <li className="flex gap-2 border-b border-line/60 py-2 last:border-0">
      <SentimentChip s={it.sentiment} />
      <div className="min-w-0 flex-1">
        <a href={it.link} target="_blank" rel="noopener noreferrer" className="group text-sm leading-snug hover:text-accent">
          {showSymbol && it.symbol && <span className="mr-1 font-semibold">{it.symbol}:</span>}
          {it.title}
          <ExternalLink className="ml-1 inline size-3 opacity-0 group-hover:opacity-60" />
        </a>
        <p className="mt-0.5 text-[11px] text-faint">
          {it.publisher ?? "News"} · {ago(it.published)} · <span className={IMPACT_CLS[it.impact]}>{it.impact} impact</span>
          {it.reason && <span title={`scored by ${it.scored_by}`}> · {it.reason}</span>}
        </p>
      </div>
    </li>
  );
}

const VERDICT_CLS = { BUY: "text-bull border-bull/40 bg-bull/10", SELL: "text-bear border-bear/40 bg-bear/10", HOLD: "text-warn border-warn/40 bg-warn/10" };

/** Stock page sidebar: live headlines and the probability after the news. Separate from the pattern/technical UI. */
export function NewsSidebar({ symbol }: { symbol: string }) {
  const { data, error } = useStockNews(symbol);
  const adj = data?.adjusted;
  const tech = data?.technical;
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Newspaper className="size-4 text-accent" /> Live news
          <span className="relative ml-1 flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-bull opacity-60" />
            <span className="relative inline-flex size-2 rounded-full bg-bull" />
          </span>
        </span>
      }
      action={<span className="text-[11px] text-faint">{data?.last_fetch ? `fetched ${fmtTime(data.last_fetch)} IST` : "fetching…"}</span>}
    >
      {error ? (
        <p className="text-sm text-bear">{error.message}</p>
      ) : !data ? (
        <p className="text-sm text-faint">Loading news…</p>
      ) : (
        <>
          <div className={`rounded-lg border p-3 ${adj ? VERDICT_CLS[adj.verdict] : "border-line"}`}>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider">After the news</span>
              {adj && <span className="text-lg font-bold">{adj.verdict === "BUY" ? "▲ BUY" : adj.verdict === "SELL" ? "▼ SELL" : "◆ HOLD"}</span>}
            </div>
            {adj && tech?.up_pct != null ? (
              <>
                <div className="mt-2 flex items-end justify-between text-ink">
                  <span>
                    <span className="block text-[11px] text-muted">Up</span>
                    <span className="tabular text-2xl font-semibold text-bull">{adj.up_pct.toFixed(1)}%</span>
                  </span>
                  <span className="text-right">
                    <span className="block text-[11px] text-muted">Down</span>
                    <span className="tabular text-2xl font-semibold text-bear">{adj.down_pct.toFixed(1)}%</span>
                  </span>
                </div>
                <div className="mt-1.5 flex h-2 overflow-hidden rounded-full bg-bear/60">
                  <div className="h-full bg-bull" style={{ width: `${adj.up_pct}%` }} />
                </div>
                <p className="mt-2 text-xs text-muted">
                  Technical {tech.up_pct.toFixed(1)}% → after news {adj.up_pct.toFixed(1)}%{" "}
                  <b className={adj.shift_pts > 0 ? "text-bull" : adj.shift_pts < 0 ? "text-bear" : "text-muted"}>
                    ({adj.shift_pts > 0 ? "+" : ""}
                    {adj.shift_pts.toFixed(1)} pts)
                  </b>{" "}
                  over {tech.horizon_label}. News mood: <b className="text-ink">{data.summary.label}</b> ({data.summary.score > 0 ? "+" : ""}
                  {data.summary.score.toFixed(2)}).
                </p>
              </>
            ) : (
              <p className="mt-1 text-xs text-muted">{tech?.message ?? "Technical probability not available yet."}</p>
            )}
            <p className="mt-1.5 text-[11px] text-faint">
              {data.learning.message} News can move the technical probability by at most ±{data.max_shift_pts} points.
            </p>
          </div>

          {data.items.length ? (
            <ul className="mt-2 max-h-[520px] overflow-y-auto pr-1">
              {data.items.map((it) => (
                <Headline key={it.id} it={it} />
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-faint">
              {data.enabled ? `No headlines about ${symbol} in the last ${data.lookback_hours} hours.` : "Live news is turned off (NEWS_ENABLED=false)."}
            </p>
          )}
          <p className="mt-2 text-[11px] text-faint">
            Source: {data.source}
            {data.items[0] ? `, sentiment by ${data.items[0].scored_by === "nvidia" ? "NVIDIA model" : "finance keyword list"}` : ""}. Headlines link
            to the original publisher. Not investment advice.
            {data.error && <span className="block text-warn">Last fetch problem: {data.error}</span>}
          </p>
        </>
      )}
    </Panel>
  );
}

/** Dashboard: latest headlines across all stocks. */
export function MarketNews() {
  const { data } = useNewsFeed(15);
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Newspaper className="size-4 text-accent" /> Live news
        </span>
      }
      action={<span className="text-[11px] text-faint">{data?.last_fetch ? `${fmtTime(data.last_fetch)} IST` : "fetching…"}</span>}
    >
      {data?.items.length ? (
        <ul className="max-h-[460px] overflow-y-auto pr-1">
          {data.items.map((it) => (
            <Headline key={it.id} it={it} showSymbol />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-faint">{data && !data.enabled ? "Live news is turned off." : "Waiting for the first news fetch…"}</p>
      )}
      <p className="mt-2 text-[11px] text-faint">
        Open a stock for its news-adjusted probability.{" "}
        <Link href="/stock/RELIANCE" className="hover:text-accent">
          Example →
        </Link>
      </p>
    </Panel>
  );
}
