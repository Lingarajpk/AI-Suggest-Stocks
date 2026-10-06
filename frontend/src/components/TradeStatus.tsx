"use client";

import { AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, Crosshair } from "lucide-react";
import { TIMEFRAME_LABEL } from "@/lib/api";
import { fmtPct, fmtPrice, fmtTime } from "@/lib/format";
import type { Quote, SignalHistory, Timeframe } from "@/lib/types";
import { Panel } from "./ui";

type Tone = "bull" | "bear" | "warn" | "muted";
const BOX: Record<Tone, string> = {
  bull: "border-bull/40 bg-bull/5 text-bull",
  bear: "border-bear/40 bg-bear/5 text-bear",
  warn: "border-warn/40 bg-warn/5 text-warn",
  muted: "border-line bg-panel-2/50 text-muted",
};

function Message({ tone, icon, title, children }: { tone: Tone; icon: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className={`rounded-lg border p-3 ${BOX[tone]}`}>
      <p className="flex items-center gap-2 text-sm font-semibold">
        {icon}
        {title}
      </p>
      {children && <div className="mt-1.5 space-y-1 text-xs text-ink/85">{children}</div>}
    </div>
  );
}

/** Follows the latest BUY/SELL on this chart: holding, turning against it, or exited. */
export function TradeStatus({ history, quote, timeframe }: { history: SignalHistory; quote: Quote | null; timeframe: Timeframe }) {
  const ot = history.open_trade;
  const ex = history.latest_exit;
  const stats = history.trades;

  let body: React.ReactNode;
  if (ot) {
    const long = ot.side === "buy";
    const d = long ? 1 : -1;
    const live = quote && (quote.freshness === "polled" || quote.freshness === "demo") ? quote.last_price : null;
    const price = live ?? ot.last_close;
    const pnl = d * (price / ot.entry_price - 1) * 100;
    const stopHit = long ? price <= ot.stop : price >= ot.stop;
    const targetHit = long ? price >= ot.target : price <= ot.target;
    const header = `${long ? "▲ BUY" : "▼ SELL"} active since ${fmtTime(ot.entry_time, true)} at ₹${fmtPrice(ot.entry_price)}`;
    const levels = (
      <p className="tabular">
        Now ₹{fmtPrice(price)}
        {live == null && " (last close)"} ·{" "}
        <span className={pnl >= 0 ? "text-bull" : "text-bear"}>
          {pnl >= 0 ? "+" : ""}
          {pnl.toFixed(2)}%
        </span>{" "}
        · Target ₹{fmtPrice(ot.target)} · Stop ₹{fmtPrice(ot.stop)}
      </p>
    );

    if (stopHit) {
      body = (
        <Message tone="bear" icon={<AlertTriangle className="size-4" />} title={`Stop-loss reached — ${long ? "sell your BUY" : "buy back your SELL"}`}>
          <p>{header}</p>
          {levels}
          <p className="text-faint">Live price check; the chart confirms when the candle closes.</p>
        </Message>
      );
    } else if (targetHit) {
      body = (
        <Message tone="bull" icon={<CheckCircle2 className="size-4" />} title={`Target reached — consider ${long ? "selling" : "buying back"} to book profit`}>
          <p>{header}</p>
          {levels}
        </Message>
      );
    } else if (ot.status === "weakening") {
      body = (
        <Message
          tone="warn"
          icon={long ? <ArrowDownRight className="size-4" /> : <ArrowUpRight className="size-4" />}
          title={long ? "Going down after BUY — consider selling your buy" : "Going up after SELL — consider buying back your sell"}
        >
          <p>{header}</p>
          {levels}
          <p>Why: {ot.warnings.join(", ")}.</p>
          <p className="text-faint">Full EXIT is marked when price closes through EMA20 with MACD flipped, or the stop/target is hit.</p>
        </Message>
      );
    } else {
      body = (
        <Message
          tone={long ? "bull" : "bear"}
          icon={long ? <ArrowUpRight className="size-4" /> : <ArrowDownRight className="size-4" />}
          title={long ? "Holding BUY — trend still up" : "Holding SELL — trend still down"}
        >
          <p>{header}</p>
          {levels}
        </Message>
      );
    }
  } else if (ex) {
    const long = ex.side === "buy";
    body = (
      <Message
        tone={ex.pnl_pct >= 0 ? "bull" : "bear"}
        icon={<Crosshair className="size-4" />}
        title={`${long ? "EXIT — BUY was sold" : "COVER — SELL was bought back"} at ₹${fmtPrice(ex.exit_price)}`}
      >
        <p>
          {fmtTime(ex.exit_time, true)}: {ex.reason_text}. Entry ₹{fmtPrice(ex.entry_price)} ({fmtTime(ex.entry_time, true)}) → result{" "}
          <span className={ex.pnl_pct >= 0 ? "text-bull" : "text-bear"}>{fmtPct(ex.pnl_pct)}</span> over {ex.bars_held} candles.
        </p>
        <p className="text-faint">No trade open. Waiting for the next ▲BUY or ▼SELL signal.</p>
      </Message>
    );
  } else {
    body = <Message tone="muted" icon={<Crosshair className="size-4" />} title="No signal trade yet on this chart" />;
  }

  return (
    <Panel title={`Signal trade · ${TIMEFRAME_LABEL[timeframe]}`}>
      {body}
      {stats && (stats.buy.count > 0 || stats.sell.count > 0) && (
        <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
          {(["buy", "sell"] as const).map((side) => {
            const st = stats[side];
            return (
              <div key={side} className="rounded-md border border-line px-2 py-1.5">
                <p className={`font-semibold ${side === "buy" ? "text-bull" : "text-bear"}`}>Past {side.toUpperCase()} trades</p>
                {st.count ? (
                  <p className="tabular text-muted">
                    {st.count} trades · {st.win_rate}% profitable · avg {fmtPct(st.avg_pnl_pct)}
                  </p>
                ) : (
                  <p className="text-faint">none</p>
                )}
              </div>
            );
          })}
          <p className="col-span-2 text-faint">{stats.rules} Not investment advice.</p>
        </div>
      )}
    </Panel>
  );
}
