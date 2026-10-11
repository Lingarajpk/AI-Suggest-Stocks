"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { TIMEFRAME_LABEL, useHealth, useScanner } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Timeframe } from "@/lib/types";
import { useQuoteStream } from "@/lib/useQuoteStream";
import { useWatchlist } from "@/lib/watchlist";
import { AppHeader } from "./AppHeader";
import { Disclaimer } from "./Disclaimer";
import { IndexStrip, SectorStrength, StatTiles } from "./MarketOverview";
import { LiveMovers } from "./LiveMovers";
import { ActivePatterns } from "./PatternsPanel";
import { MarketNews } from "./NewsPanel";
import { ScannerTable } from "./ScannerTable";
import { SignalCard } from "./SignalCard";
import { StatusNotices } from "./StatusNotices";
import { TodayOutlook } from "./TodayOutlook";
import { Panel, SkeletonRows } from "./ui";

const TIMEFRAMES: Timeframe[] = ["1d", "1h", "15m", "5m"];

export function Dashboard() {
  const [timeframe, setTimeframe] = useState<Timeframe>("1d");
  const health = useHealth();
  const stream = useQuoteStream();
  const scan = useScanner(timeframe);
  const watch = useWatchlist();

  const latest = useMemo(
    () =>
      (scan.data?.rows ?? [])
        .filter((r) => r.analysis?.signal.status === "ok" && r.analysis.signal.signal !== "Neutral")
        .sort((a, b) => Math.abs(b.analysis!.signal.score!) - Math.abs(a.analysis!.signal.score!))
        .slice(0, 6),
    [scan.data],
  );

  const scanAuthError = scan.error?.status === 401;

  return (
    <>
      <AppHeader health={health.data} healthError={health.error} stream={stream.status} lastUpdate={stream.lastUpdate} alerts={stream.alerts} latestAlert={stream.latestAlert} />
      <main className="mx-auto w-full min-w-0 max-w-[1400px] space-y-5 px-4 py-5 sm:px-6">
        <StatusNotices health={health.data} healthError={health.error} streamError={stream.error} />

        <IndexStrip quotes={stream.quotes} />

        <LiveMovers />

        <TodayOutlook quotes={stream.quotes} />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex rounded-lg border border-line bg-panel p-1">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  tf === timeframe ? "bg-accent/15 text-accent" : "text-muted hover:text-ink"
                }`}
              >
                {TIMEFRAME_LABEL[tf]}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3 text-xs text-muted">
            {scan.data && <span>Scan generated {fmtTime(scan.data.generated_at)} IST</span>}
            <button onClick={() => scan.mutate()} className="inline-flex items-center gap-1 hover:text-ink" aria-label="Refresh scan">
              <RefreshCw className={`size-3.5 ${scan.isValidating ? "animate-spin" : ""}`} /> Refresh
            </button>
          </div>
        </div>

        <StatTiles scan={scan.data} />

        {scan.error && !scanAuthError && (
          <div className="rounded-xl border border-bear/30 bg-bear/5 px-4 py-3 text-sm text-bear">Scan failed: {scan.error.message}</div>
        )}
        {scan.data?.quotes_error && (
          <div className="rounded-xl border border-warn/30 bg-warn/5 px-4 py-3 text-sm text-warn">Quotes unavailable: {scan.data.quotes_error}</div>
        )}
        {!!scan.data?.unresolved_symbols.length && (
          <p className="text-xs text-warn">Could not resolve instrument keys for: {scan.data.unresolved_symbols.join(", ")}</p>
        )}

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Panel title="Strongest signals" action={<span className="text-xs text-muted">{TIMEFRAME_LABEL[timeframe]} · excludes no-trade</span>}>
            {scan.isLoading ? (
              <SkeletonRows rows={4} />
            ) : latest.length ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {latest.map((r) => (
                  <SignalCard
                    key={r.instrument.symbol}
                    row={r}
                    quote={stream.quotes[r.instrument.symbol] ?? r.quote}
                    timeframe={timeframe}
                    watched={watch.has(r.instrument.symbol)}
                    onToggleWatch={() => watch.toggle(r.instrument.symbol)}
                  />
                ))}
              </div>
            ) : (
              <p className="py-6 text-center text-sm text-faint">
                {scanAuthError ? "Connect Upstox to scan instruments." : "No instruments currently pass the evidence and data-quality checks."}
              </p>
            )}
          </Panel>
          <div className="space-y-5">
            <SectorStrength scan={scan.data} />
            <ActivePatterns rows={scan.data?.rows ?? []} timeframe={timeframe} />
            <MarketNews />
            <Panel title="Watchlist" action={<span className="text-xs text-faint">stored in this browser</span>}>
              {watch.list.length ? (
                <ul className="space-y-1.5 text-sm">
                  {watch.list.map((s) => {
                    const q = stream.quotes[s];
                    const row = scan.data?.rows.find((r) => r.instrument.symbol === s);
                    return (
                      <li key={s} className="flex items-center justify-between gap-2">
                        <Link href={`/stock/${s}`} className="font-medium hover:text-accent">
                          {s}
                        </Link>
                        <span className="tabular text-xs text-muted">{q ? q.last_price.toFixed(2) : "—"}</span>
                        <span className="text-xs">{row?.analysis?.signal.status === "no_trade" ? "No trade" : (row?.analysis?.signal.signal ?? "—")}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-sm text-faint">Star a stock to add it here.</p>
              )}
            </Panel>
          </div>
        </div>

        <Panel title={`Scanner · ${TIMEFRAME_LABEL[timeframe]} candles`}>
          {scan.isLoading ? (
            <SkeletonRows rows={8} />
          ) : (
            <ScannerTable rows={scan.data?.rows ?? []} quotes={stream.quotes} watchlist={watch.list} onToggleWatch={watch.toggle} />
          )}
        </Panel>
      </main>
      <Disclaimer />
    </>
  );
}
