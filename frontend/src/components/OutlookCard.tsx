"use client";

import { Gauge } from "lucide-react";
import { TIMEFRAME_LABEL } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { Outlook, Timeframe, Verdict } from "@/lib/types";
import { Panel } from "./ui";

const VERDICT: Record<Verdict, { cls: string; ring: string; label: string }> = {
  BUY: { cls: "text-bull", ring: "border-bull/50 from-bull/15", label: "▲ BUY" },
  SELL: { cls: "text-bear", ring: "border-bear/50 from-bear/15", label: "▼ SELL" },
  HOLD: { cls: "text-warn", ring: "border-warn/40 from-warn/10", label: "◆ HOLD" },
  "NO EDGE": { cls: "text-muted", ring: "border-line from-muted/10", label: "NO EDGE" },
  "NO TRADE": { cls: "text-warn", ring: "border-warn/40 from-warn/10", label: "NO TRADE" },
};

/** Highlighted combined outlook: up/down probability from all strategies + BUY/SELL/HOLD. */
export function OutlookCard({ outlook, timeframe }: { outlook: Outlook | undefined; timeframe: Timeframe }) {
  if (!outlook || outlook.status !== "ok" || outlook.up_pct == null || !outlook.verdict || !outlook.test) {
    return (
      <section className="rounded-xl border border-line bg-panel px-5 py-4 text-sm text-muted">
        <span className="font-semibold text-ink">Combined outlook</span> · {outlook?.message ?? "Loading…"}
      </section>
    );
  }
  const v = VERDICT[outlook.verdict];
  const t = outlook.test;
  const up = outlook.up_pct;
  const drivers = (outlook.drivers ?? []).filter((d) => Math.abs(d.push) >= 0.01).slice(0, 6);
  const maxPush = Math.max(0.01, ...drivers.map((d) => Math.abs(d.push)));
  return (
    <section className={`rounded-xl border-2 bg-linear-to-br to-panel p-5 ${v.ring}`}>
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-[220px]">
          <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted">
            <Gauge className="size-4" /> Combined outlook · all strategies
          </p>
          <p className={`mt-2 text-4xl font-bold tracking-tight ${v.cls}`}>{v.label}</p>
          <p className="mt-1 text-xs text-muted">
            {TIMEFRAME_LABEL[timeframe]} candles · next {outlook.horizon_label}
          </p>
        </div>

        <div className="min-w-[260px] flex-1">
          <div className="flex items-end justify-between">
            <div>
              <p className="text-xs text-muted">Up probability</p>
              <p className="tabular text-3xl font-semibold text-bull">{up.toFixed(1)}%</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-muted">Down probability</p>
              <p className="tabular text-3xl font-semibold text-bear">{outlook.down_pct!.toFixed(1)}%</p>
            </div>
          </div>
          <div className="relative mt-2 flex h-3 overflow-hidden rounded-full bg-bear/60">
            <div className="h-full bg-bull" style={{ width: `${up}%` }} />
            {outlook.thresholds && (
              <>
                <span className="absolute inset-y-0 w-px bg-ink/70" style={{ left: `${outlook.thresholds.sell_at_pct}%` }} />
                <span className="absolute inset-y-0 w-px bg-ink/70" style={{ left: `${outlook.thresholds.buy_at_pct}%` }} />
              </>
            )}
          </div>
          <p className="mt-1 text-[11px] text-faint">
            Lines mark the SELL ≤ {outlook.thresholds?.sell_at_pct}% and BUY ≥ {outlook.thresholds?.buy_at_pct}% thresholds.
          </p>
          <p className="mt-2 text-sm">{outlook.reason}</p>
        </div>

        <div className="min-w-[240px] flex-1">
          <p className="mb-1.5 text-xs font-medium text-muted">What is pushing it</p>
          <ul className="space-y-1">
            {drivers.map((d) => (
              <li key={d.feature} className="flex items-center gap-2 text-xs">
                <span className="w-40 shrink-0 truncate text-muted">{d.label}</span>
                <span className="relative h-2 flex-1 rounded bg-panel-2">
                  <span
                    className={`absolute inset-y-0 rounded ${d.push > 0 ? "left-1/2 bg-bull" : "right-1/2 bg-bear"}`}
                    style={{ width: `${(Math.abs(d.push) / maxPush) * 50}%` }}
                  />
                  <span className="absolute inset-y-0 left-1/2 w-px bg-line" />
                </span>
                <span className={`w-8 text-right ${d.push > 0 ? "text-bull" : "text-bear"}`}>{d.push > 0 ? "up" : "down"}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-4 border-t border-line pt-3 text-xs text-muted">
        <b className="text-ink">How reliable:</b> tested on {t.samples.toLocaleString()} unseen candles ({fmtDate(t.test_period[0])} – {fmtDate(t.test_period[1])}).{" "}
        {t.buy_hit_pct != null && (
          <>
            BUY calls were right <b className="text-bull">{t.buy_hit_pct}%</b> of {t.buy_calls}
            {t.sell_hit_pct != null ? ", " : ". "}
          </>
        )}
        {t.sell_hit_pct != null && (
          <>
            SELL calls <b className="text-bear">{t.sell_hit_pct}%</b> of {t.sell_calls}.{" "}
          </>
        )}
        Price simply rose {t.base_up_pct}% of the time. Skill vs. guessing: <b className={t.skill_pct > 0 ? "text-bull" : "text-bear"}>{t.skill_pct > 0 ? "+" : ""}{t.skill_pct}%</b>.
        <span className="block text-faint">A probability from past behaviour, not a promise. Before brokerage, taxes and slippage. Not investment advice.</span>
      </p>
    </section>
  );
}

/** Calibration table: did "60% up" calls really go up ~60% of the time on unseen data? */
export function OutlookReliability({ outlook }: { outlook: Outlook | undefined }) {
  const t = outlook?.test;
  return (
    <Panel title={<span className="inline-flex items-center gap-2"><Gauge className="size-4 text-accent" /> Model test (unseen data)</span>}>
      {!t ? (
        <p className="text-sm text-muted">{outlook?.message ?? "…"}</p>
      ) : (
        <>
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr className="border-b border-line">
                <th className="py-1 font-medium">Model said up</th>
                <th className="py-1 text-right font-medium">Candles</th>
                <th className="py-1 text-right font-medium">Actually went up</th>
              </tr>
            </thead>
            <tbody>
              {t.calibration.map((b) => (
                <tr key={b.range} className="border-b border-line/60">
                  <td className="py-1">{b.range}</td>
                  <td className="tabular py-1 text-right">{b.n}</td>
                  <td className="tabular py-1 text-right">{b.actual_up_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-faint">
            Trained on {t.train_samples.toLocaleString()} candles ({fmtDate(t.train_period[0])} – {fmtDate(t.train_period[1])}), tested on the
            {" "}{t.samples.toLocaleString()} that came after. Accuracy {t.accuracy_pct}% · Brier {t.brier} vs {t.brier_baseline} for guessing.
          </p>
          {t.features_used && (
            <div className="mt-3 border-t border-line pt-2 text-xs">
              <p className="font-medium text-muted">Strategies the model uses</p>
              <p className="mt-0.5">
                {t.features_used.join(" · ")}
                {t.features_added?.length ? (
                  <span className="text-bull">
                    {" "}
                    (added because they helped: {t.features_added.map((f) => f.feature).join(", ")})
                  </span>
                ) : null}
              </p>
              {t.features_dropped?.length ? (
                <p className="mt-1 text-faint">
                  Left out — did not improve predictions on a held-out slice of the training data: {t.features_dropped.join(", ")}.
                </p>
              ) : null}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
