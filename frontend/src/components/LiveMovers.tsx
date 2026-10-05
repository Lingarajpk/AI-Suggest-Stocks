"use client";

import Link from "next/link";
import useSWR from "swr";
import { ArrowDownRight, ArrowUpRight, Zap } from "lucide-react";
import { fetcher, type ApiError } from "@/lib/api";
import { fmtPct, fmtPrice } from "@/lib/format";
import type { MoverRow, Movers } from "@/lib/types";
import { Panel } from "./ui";

function List({ rows, empty, minutes }: { rows: MoverRow[]; empty: string; minutes?: boolean }) {
  if (!rows.length) return <p className="py-2 text-sm text-faint">{empty}</p>;
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => (
        <li key={r.symbol} className="flex items-center justify-between gap-2 text-sm">
          <Link href={`/stock/${r.symbol}`} className="font-medium hover:text-accent">
            {r.symbol}
          </Link>
          <span className="tabular text-xs text-muted">{fmtPrice(r.price)}</span>
          <span className={`tabular w-24 text-right text-xs font-semibold ${r.change_pct > 0 ? "text-bull" : r.change_pct < 0 ? "text-bear" : "text-muted"}`}>
            {fmtPct(r.change_pct)}
            {minutes && r.minutes != null && <span className="font-normal text-faint"> /{r.minutes}m</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function LiveMovers() {
  const { data, error } = useSWR<Movers, ApiError>("/api/movers", fetcher, { refreshInterval: 15_000, keepPreviousData: true });

  return (
    <Panel title="Live movers" action={<span className="text-xs text-muted">{data?.session === "open" ? "updates every 15s" : "market closed"}</span>}>
      {error ? (
        <p className="text-sm text-faint">{error.status === 401 ? "Connect Upstox to see live movers." : `Unavailable: ${error.message}`}</p>
      ) : (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
          <div>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-bull">
              <ArrowUpRight className="size-3.5" /> GOING UP TODAY
            </h3>
            <List rows={data?.gainers ?? []} empty="No stock is up today." />
          </div>
          <div>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-bear">
              <ArrowDownRight className="size-3.5" /> GOING DOWN TODAY
            </h3>
            <List rows={data?.losers ?? []} empty="No stock is down today." />
          </div>
          <div>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-accent">
              <Zap className="size-3.5" /> MOVING FAST (LAST {data?.fast_window_minutes ?? 15} MIN)
            </h3>
            <List
              rows={(data?.fast ?? []).filter((r) => Math.abs(r.change_pct) >= 0.1)}
              empty={data?.session === "open" ? "Collecting prices… shows after a few minutes of trading." : "Only during market hours."}
              minutes
            />
          </div>
        </div>
      )}
      <p className="mt-3 text-[11px] text-faint">Actual price changes from Upstox quotes, not predictions.</p>
    </Panel>
  );
}
