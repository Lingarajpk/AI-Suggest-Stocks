"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { changeColor, fmtPct, fmtPrice, fmtSigned } from "@/lib/format";
import type { Quote, ScanResult } from "@/lib/types";
import { FreshnessBadge, Panel } from "./ui";

const INDICES = ["NIFTY 50", "BANK NIFTY", "SENSEX"];

export function IndexStrip({ quotes }: { quotes: Record<string, Quote> }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {INDICES.map((name, i) => {
        const q = quotes[name];
        return (
          <motion.div
            key={name}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="rounded-xl border border-line bg-panel px-4 py-3"
          >
            <div className="flex items-center justify-between">
              <Link href={`/stock/${name.replace(/\s+/g, "")}?tf=5m`} className="text-xs font-medium tracking-wider text-muted hover:text-accent">
                {name} →
              </Link>
              <FreshnessBadge freshness={q?.freshness ?? "unavailable"} />
            </div>
            {q ? (
              <div className="mt-1 flex items-baseline gap-3">
                <span className="tabular text-2xl font-semibold">{fmtPrice(q.last_price)}</span>
                <span className={`tabular text-sm ${changeColor(q.change)}`}>
                  {fmtSigned(q.change)} ({fmtPct(q.change_pct)})
                </span>
              </div>
            ) : (
              <p className="mt-2 text-sm text-faint">Not available for this data source</p>
            )}
          </motion.div>
        );
      })}
    </div>
  );
}

export function StatTiles({ scan }: { scan?: ScanResult }) {
  const c = scan?.counts ?? {};
  const tiles = [
    { label: "Scanned", value: scan?.scanned, cls: "text-ink" },
    { label: "Bullish", value: scan ? (c["Strong Bullish"] ?? 0) + (c["Bullish"] ?? 0) : undefined, cls: "text-bull" },
    { label: "Neutral", value: c["Neutral"], cls: "text-muted" },
    { label: "Bearish", value: scan ? (c["Strong Bearish"] ?? 0) + (c["Bearish"] ?? 0) : undefined, cls: "text-bear" },
    { label: "No trade", value: c["no_trade"], cls: "text-warn", hint: "Weak evidence, stale or insufficient data" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-xl border border-line bg-panel px-4 py-3" title={t.hint}>
          <p className="text-xs text-muted">{t.label}</p>
          <p className={`tabular mt-1 text-2xl font-semibold ${t.cls}`}>{t.value ?? "—"}</p>
        </div>
      ))}
    </div>
  );
}

export function SectorStrength({ scan }: { scan?: ScanResult }) {
  return (
    <Panel title="Sector strength" action={<span className="text-xs text-muted">avg technical score</span>}>
      {!scan?.sector_strength.length ? (
        <p className="text-sm text-faint">No data yet</p>
      ) : (
        <ul className="space-y-2.5">
          {scan.sector_strength.map((s) => (
            <li key={s.sector} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 text-sm">
              <span className="truncate">
                {s.sector} <span className="text-xs text-faint">({s.count})</span>
              </span>
              <span className={`tabular text-xs ${s.avg_score > 0 ? "text-bull" : s.avg_score < 0 ? "text-bear" : "text-muted"}`}>
                {s.avg_score > 0 ? "+" : ""}
                {s.avg_score.toFixed(0)}
              </span>
              <div className="relative col-span-2 h-1.5 rounded-full bg-line">
                <div className="absolute top-0 left-1/2 h-full w-px bg-faint" />
                <div
                  className={`absolute top-0 h-full rounded-full ${s.avg_score >= 0 ? "bg-bull/80" : "bg-bear/80"}`}
                  style={
                    s.avg_score >= 0
                      ? { left: "50%", width: `${Math.min(100, s.avg_score) / 2}%` }
                      : { right: "50%", width: `${Math.min(100, -s.avg_score) / 2}%` }
                  }
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
