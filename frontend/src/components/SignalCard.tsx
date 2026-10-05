"use client";

import Link from "next/link";
import { useState } from "react";
import { ChevronDown, Star } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { TIMEFRAME_LABEL } from "@/lib/api";
import { changeColor, fmtPct, fmtPrice, fmtTime } from "@/lib/format";
import type { Quote, ScanRow, Timeframe } from "@/lib/types";
import { FreshnessBadge, ScoreBar, SignalBadge } from "./ui";

export function SignalCard({
  row,
  quote,
  timeframe,
  watched,
  onToggleWatch,
}: {
  row: ScanRow;
  quote: Quote | null;
  timeframe: Timeframe;
  watched: boolean;
  onToggleWatch: () => void;
}) {
  const [open, setOpen] = useState(false);
  const sig = row.analysis?.signal;
  const sc = sig?.scenario;
  const evidence = [...(sig?.evidence ?? [])].filter((e) => e.weight > 0).sort((a, b) => Math.abs(b.score * b.weight) - Math.abs(a.score * a.weight));

  return (
    <motion.article layout className="flex flex-col rounded-xl border border-line bg-panel-2/60 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/stock/${row.instrument.symbol}`} className="font-semibold hover:text-accent">
            {row.instrument.symbol}
          </Link>
          <span className="ml-2 text-xs text-faint">{row.instrument.exchange}</span>
          <p className="truncate text-xs text-muted">{row.instrument.name}</p>
        </div>
        <button onClick={onToggleWatch} aria-label="Toggle watchlist" className="text-faint hover:text-warn">
          <Star className={`size-4 ${watched ? "fill-warn text-warn" : ""}`} />
        </button>
      </div>

      <div className="mt-3 flex items-baseline justify-between">
        <span className="tabular text-xl font-semibold">{fmtPrice(quote?.last_price ?? row.analysis?.last_close)}</span>
        <span className={`tabular text-sm ${changeColor(quote?.change_pct)}`}>{fmtPct(quote?.change_pct)}</span>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <SignalBadge signal={sig} />
        <ScoreBar score={sig?.score} />
      </div>
      <p className="mt-2 text-xs text-muted">
        Horizon: {TIMEFRAME_LABEL[timeframe]} candles · rule-based technical score
      </p>

      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
        <dt className="text-muted">Outcome probability</dt>
        <dd className="text-right text-faint" title="Shown only after ML models pass out-of-sample validation">
          Pending model
        </dd>
        {sc ? (
          <>
            <dt className="text-muted">Entry zone</dt>
            <dd className="tabular text-right">
              {fmtPrice(sc.entry_low)}–{fmtPrice(sc.entry_high)}
            </dd>
            <dt className="text-muted">Target · Stop</dt>
            <dd className="tabular text-right">
              <span className="text-bull">{fmtPrice(sc.target)}</span> · <span className="text-bear">{fmtPrice(sc.stop_loss)}</span>
            </dd>
            <dt className="text-muted">Risk / reward</dt>
            <dd className="tabular text-right">1 : {sc.risk_reward.toFixed(2)}</dd>
          </>
        ) : (
          <>
            <dt className="text-muted">Scenario</dt>
            <dd className="text-right text-faint">None (no trade)</dd>
          </>
        )}
      </dl>
      {sc && <p className="mt-1 text-[11px] text-faint">ATR-based scenario, not a guarantee or forecast.</p>}

      <button onClick={() => setOpen((o) => !o)} className="mt-3 flex items-center gap-1 text-xs font-medium text-accent">
        Why this signal?
        <ChevronDown className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.ul
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="mt-2 space-y-1 overflow-hidden text-xs"
          >
            {sig?.no_trade_reasons.map((r) => (
              <li key={r} className="text-warn">
                • {r}
              </li>
            ))}
            {evidence.map((e) => (
              <li key={e.factor} className="flex justify-between gap-2">
                <span className="text-muted">{e.factor}</span>
                <span className={`tabular text-right ${e.score > 0 ? "text-bull" : e.score < 0 ? "text-bear" : "text-muted"}`}>{e.detail}</span>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>

      <div className="mt-auto flex items-center justify-between pt-3 text-[11px] text-faint">
        <FreshnessBadge freshness={quote?.freshness} />
        <span>Candle {fmtTime(sig?.last_candle_time, true)}</span>
      </div>
    </motion.article>
  );
}
