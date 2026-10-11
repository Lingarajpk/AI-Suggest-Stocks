"use client";

import { Crosshair } from "lucide-react";
import { TIMEFRAME_LABEL, edgeVerdict, useStrategyStats } from "@/lib/api";
import { fmtPct, fmtPrice, fmtTime } from "@/lib/format";
import type { StrategyBlock, StrategyState, StrategyStatsRow, Timeframe } from "@/lib/types";
import { Panel } from "./ui";

const MIN_SAMPLES = 20;

const STATE_CLS: Record<StrategyState["state"], string> = {
  long: "bg-bull/15 text-bull",
  short: "bg-bear/15 text-bear",
  none: "bg-muted/10 text-muted",
  "n/a": "bg-muted/5 text-faint",
};
const STATE_LABEL: Record<StrategyState["state"], string> = { long: "▲ long", short: "▼ short", none: "—", "n/a": "n/a" };

const TONE = { bull: "text-bull", bear: "text-bear", warn: "text-warn", muted: "text-faint" };

/** Stock page: what each strategy says right now, with its pooled track record next to it. */
export function StrategiesPanel({ block, timeframe }: { block: StrategyBlock | null | undefined; timeframe: Timeframe }) {
  const { data: stats } = useStrategyStats(timeframe);
  const pooled = new Map((stats?.rows ?? []).map((r) => [r.key, r]));
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Crosshair className="size-4 text-accent" /> Strategies
        </span>
      }
      action={<span className="text-[11px] text-faint">{TIMEFRAME_LABEL[timeframe]}</span>}
    >
      {!block ? (
        <p className="text-sm text-faint">No strategy data.</p>
      ) : (
        <div className="space-y-4">
          <ul className="space-y-2">
            {block.strategies.map((s) => {
              const r = pooled.get(s.key);
              const ev = r ? edgeVerdict(r.hit_rate, r.base_rate, r.signals) : null;
              const ls = s.last_signal;
              return (
                <li key={s.key} className="border-b border-line/60 pb-2 last:border-0" title={s.description}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{s.name}</span>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold ${STATE_CLS[s.state]}`}>{STATE_LABEL[s.state]}</span>
                  </div>
                  {s.detail && <p className="mt-0.5 text-xs text-muted">{s.detail}</p>}
                  <p className="mt-0.5 text-[11px] text-faint">
                    {ls ? (
                      <>
                        Last: <span className={ls.side === "buy" ? "text-bull" : "text-bear"}>{ls.side.toUpperCase()}</span> at {fmtPrice(ls.price)}{" "}
                        {ls.bars_ago === 0 ? "on the latest candle" : `${ls.bars_ago} candles ago`}
                        {ls.detail && ` · ${ls.detail}`}
                      </>
                    ) : (
                      "No signal in this history."
                    )}
                  </p>
                  {ev && r && r.signals > 0 && (
                    <p className={`text-[11px] ${TONE[ev.tone]}`}>
                      All stocks: right {r.hit_rate}% of {r.signals} vs {r.base_rate}% random · {ev.label}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>

          <div>
            <p className="mb-1 text-xs font-medium text-muted">Breakout probability · next candle (after a {block.breakout.last_candle} candle)</p>
            <table className="w-full text-xs">
              <thead className="text-muted">
                <tr className="border-b border-line">
                  <th className="py-1 text-left font-medium">Move</th>
                  <th className="py-1 text-right font-medium">Up to</th>
                  <th className="py-1 text-right font-medium">Odds</th>
                  <th className="py-1 text-right font-medium">Down to</th>
                  <th className="py-1 text-right font-medium">Odds</th>
                </tr>
              </thead>
              <tbody>
                {block.breakout.levels.map((l) => (
                  <tr key={l.atr_mult} className="border-b border-line/60">
                    <td className="py-1">±{l.atr_mult} ATR</td>
                    <td className="tabular py-1 text-right">{fmtPrice(l.up_level)}</td>
                    <td className="tabular py-1 text-right text-bull">{l.p_up_pct == null ? "—" : `${l.p_up_pct}%`}</td>
                    <td className="tabular py-1 text-right">{fmtPrice(l.down_level)}</td>
                    <td className="tabular py-1 text-right text-bear">{l.p_down_pct == null ? "—" : `${l.p_down_pct}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-[11px] text-faint">{block.breakout.note}</p>
          </div>

          <div>
            <p className="mb-1 text-xs font-medium text-muted">Volume profile &amp; order flow (estimated) · {block.volume_profile.window}</p>
            <dl className="grid grid-cols-2 gap-y-0.5 text-xs">
              <dt className="text-muted">POC (busiest price)</dt>
              <dd className="tabular text-right">{fmtPrice(block.volume_profile.poc)}</dd>
              <dt className="text-muted">Value area (70%)</dt>
              <dd className="tabular text-right">
                {fmtPrice(block.volume_profile.val)} – {fmtPrice(block.volume_profile.vah)}
              </dd>
              <dt className="text-muted">Price is</dt>
              <dd className="text-right">{block.volume_profile.price_vs_value ?? "—"}</dd>
              <dt className="text-muted">Net delta, 20 candles</dt>
              <dd className={`tabular text-right ${block.volume_profile.delta_20_pct > 0 ? "text-bull" : block.volume_profile.delta_20_pct < 0 ? "text-bear" : ""}`}>
                {fmtPct(block.volume_profile.delta_20_pct)} of volume
              </dd>
              <dt className="text-muted">CVD divergence</dt>
              <dd className="text-right">
                {block.volume_profile.cvd_divergence ? (
                  <span className={block.volume_profile.cvd_divergence.direction === "up" ? "text-bull" : "text-bear"}>
                    {block.volume_profile.cvd_divergence.direction === "up" ? "bullish" : "bearish"}, {block.volume_profile.cvd_divergence.bars_ago} candles ago
                  </span>
                ) : (
                  "none"
                )}
              </dd>
            </dl>
            <p className="mt-1 text-[11px] text-faint">{block.volume_profile.note}</p>
          </div>

          <div>
            <p className="mb-1 text-xs font-medium text-muted">MACD events</p>
            <dl className="grid grid-cols-2 gap-y-0.5 text-xs">
              {(
                [
                  ["Signal-line cross", block.macd.last_cross],
                  ["Zero-line cross", block.macd.last_zero_cross],
                  ["Divergence", block.macd.last_divergence],
                ] as const
              ).map(([label, ev]) => (
                <div key={label} className="contents">
                  <dt className="text-muted">{label}</dt>
                  <dd className="text-right">
                    {ev ? (
                      <span className={ev.direction === "up" ? "text-bull" : "text-bear"}>
                        {ev.direction === "up" ? "bullish" : "bearish"}, {ev.bars_ago === 0 ? "latest candle" : `${ev.bars_ago} candles ago`}
                      </span>
                    ) : (
                      "—"
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          <div>
            <p className="mb-1 text-xs font-medium text-muted">Recent candlesticks</p>
            {block.candles.length ? (
              <ul className="space-y-0.5 text-xs">
                {block.candles
                  .slice(-6)
                  .reverse()
                  .map((c) => (
                    <li key={`${c.key}-${c.time}`} className="flex justify-between gap-2">
                      <span>
                        <span className={c.direction === "up" ? "text-bull" : "text-bear"}>{c.direction === "up" ? "▲" : "▼"}</span> {c.name}
                      </span>
                      <span className="text-faint">{fmtTime(c.time, timeframe === "1d")}</span>
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="text-xs text-faint">None in the recent candles.</p>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}

function StatsRows({ rows }: { rows: StrategyStatsRow[] }) {
  return (
    <>
      {rows.map((r) => {
        const few = r.signals < MIN_SAMPLES;
        const ev = edgeVerdict(r.hit_rate, r.base_rate, r.signals);
        return (
          <tr key={r.key} className={`border-b border-line/60 ${few ? "text-faint" : ""}`} title={r.description}>
            <td className="py-1 pr-2">{r.name}</td>
            <td className="tabular py-1 pr-2 text-right">
              {r.signals}
              <span className="text-faint">
                {" "}
                ({r.buy_signals}▲ {r.sell_signals}▼)
              </span>
            </td>
            <td className="tabular py-1 pr-2 text-right">{r.hit_rate == null ? "—" : `${r.hit_rate}%`}</td>
            <td className="tabular py-1 pr-2 text-right">{r.base_rate == null ? "—" : `${r.base_rate}%`}</td>
            <td className={`py-1 pr-2 text-right ${few ? "" : TONE[ev.tone]}`}>
              {r.edge_pts == null ? "—" : `${r.edge_pts > 0 ? "+" : ""}${r.edge_pts} pts`}
            </td>
            <td className="tabular py-1 pr-2 text-right">{r.trade_win_rate == null ? "—" : `${r.trade_win_rate}%`}</td>
            <td className={`tabular py-1 text-right ${(r.avg_trade_pct ?? 0) < 0 ? "text-bear" : ""}`}>{fmtPct(r.avg_trade_pct)}</td>
          </tr>
        );
      })}
    </>
  );
}

/** Full-width table: every strategy and candlestick measured on every stock. */
export function StrategyStatsTable({ timeframe }: { timeframe: Timeframe }) {
  const { data, error, isLoading } = useStrategyStats(timeframe);
  const strategies = (data?.rows ?? []).filter((r) => r.group === "strategy");
  const candles = (data?.rows ?? []).filter((r) => r.group === "candlestick");
  const head = (label: string) => (
    <tr className="border-b border-line text-left text-muted">
      <th className="py-1.5 pr-2 font-medium">{label}</th>
      <th className="py-1.5 pr-2 text-right font-medium">Signals</th>
      <th className="py-1.5 pr-2 text-right font-medium">Right</th>
      <th className="py-1.5 pr-2 text-right font-medium">Random</th>
      <th className="py-1.5 pr-2 text-right font-medium">Edge</th>
      <th className="py-1.5 pr-2 text-right font-medium">Trades won</th>
      <th className="py-1.5 text-right font-medium">Avg trade</th>
    </tr>
  );
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <Crosshair className="size-4 text-accent" /> Strategy &amp; candlestick track record · all stocks
        </span>
      }
      action={
        <span className="text-xs text-muted">
          {TIMEFRAME_LABEL[timeframe]} · {data ? `${data.instruments} stocks · judged over ${data.horizon_label}` : "…"}
        </span>
      }
    >
      {error ? (
        <p className="text-sm text-bear">{error.message}</p>
      ) : isLoading && !data ? (
        <p className="text-sm text-faint">Backtesting every strategy on every stock… (first run takes a few seconds)</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-xs">
            <thead>{head("Strategy")}</thead>
            <tbody>
              <StatsRows rows={strategies} />
            </tbody>
            <thead>{head("Candlestick")}</thead>
            <tbody>
              <StatsRows rows={candles} />
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-faint">
            {data?.method} Edge = right − random, in percentage points. Greyed rows have fewer than {MIN_SAMPLES} signals — too few to trust.
            {data?.source === "demo" && " Demo mode: these numbers come from synthetic prices, not the market."}
          </p>
        </div>
      )}
    </Panel>
  );
}
