"use client";

import { useRef, useState } from "react";
import { Check, Gavel, Loader2, MessagesSquare, ShieldCheck, TrendingDown, TrendingUp } from "lucide-react";
import type { Timeframe } from "@/lib/types";
import { HowItWorks, Panel } from "./ui";

type Step = "facts" | "bull" | "bear" | "judge" | "risk" | "done" | "error";

interface DebateEvent {
  step: Step;
  points?: string[];
  strongest?: string;
  rebuttal?: string;
  rating?: string;
  confidence?: string;
  thesis?: string;
  entry?: number | null;
  stop?: number | null;
  target?: number | null;
  horizon?: string;
  change_my_mind?: string;
  level_issues?: string[];
  verdict?: string;
  notes?: string[];
  size_hint?: string;
  unverified_numbers?: string[];
  message?: string;
  cached?: boolean;
  model?: string;
  generated_at?: string;
  facts?: { data_source?: string; past_calls_on_this_chart?: { when: string; rating: string; move_since_pct: number; worked: boolean | null }[] };
}

const STEPS: { key: Step; label: string }[] = [
  { key: "facts", label: "Facts" },
  { key: "bull", label: "Bull" },
  { key: "bear", label: "Bear" },
  { key: "judge", label: "Judge" },
  { key: "risk", label: "Risk check" },
];

const RATING_CLS: Record<string, string> = {
  "STRONG BUY": "bg-bull text-white",
  BUY: "bg-bull/15 text-bull",
  HOLD: "bg-panel-2 text-ink",
  SELL: "bg-bear/15 text-bear",
  "STRONG SELL": "bg-bear text-white",
  REVIEW: "bg-warn/15 text-warn",
};

const RISK_CLS: Record<string, string> = { approve: "text-bull", reduce: "text-warn", reject: "text-bear" };

function Unverified({ nums }: { nums?: string[] }) {
  if (!nums?.length) return null;
  return <p className="mt-1 text-[10px] text-warn">Numbers not found in the data: {nums.join(", ")} — treat them with care.</p>;
}

function Bubble({ ev, side }: { ev: DebateEvent; side: "bull" | "bear" }) {
  const bull = side === "bull";
  return (
    <div className={`flex ${bull ? "justify-start" : "justify-end"}`}>
      <div className={`max-w-[92%] rounded-2xl px-4 py-3 text-sm sm:max-w-[80%] ${bull ? "rounded-tl-sm bg-bull/10" : "rounded-tr-sm bg-bear/10"}`}>
        <p className={`mb-1 flex items-center gap-1.5 text-xs font-semibold ${bull ? "text-bull" : "text-bear"}`}>
          {bull ? <TrendingUp className="size-3.5" /> : <TrendingDown className="size-3.5" />} {bull ? "Bull" : "Bear"}
        </p>
        <ul className="list-disc space-y-0.5 pl-4 text-ink/90">
          {(ev.points ?? []).map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
        {ev.rebuttal && <p className="mt-1.5 text-xs text-muted">Rebuttal: {ev.rebuttal}</p>}
        {ev.strongest && <p className="mt-1.5 text-xs font-medium">Strongest point: {ev.strongest}</p>}
        <Unverified nums={ev.unverified_numbers} />
      </div>
    </div>
  );
}

/** Bull vs bear on NVIDIA NIM over this chart's computed facts, then a judge and a risk check. */
export function AiDebate({ symbol, timeframe, enabled, dp = 2 }: { symbol: string; timeframe: Timeframe; enabled: boolean; dp?: number }) {
  const [events, setEvents] = useState<DebateEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const by = (s: Step) => events.find((e) => e.step === s);
  const done = new Set(events.map((e) => e.step));
  const current = running ? STEPS.find((s) => !done.has(s.key))?.key : null;
  const fmt = (v: number | null | undefined) => (v == null ? "—" : `₹${v.toLocaleString("en-IN", { maximumFractionDigits: dp, minimumFractionDigits: dp })}`);

  const start = async () => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setEvents([]);
    setError(null);
    setRunning(true);
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/debate?timeframe=${timeframe}`, { method: "POST", signal: ctrl.signal });
      if (!res.ok || !res.body) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.message ?? `HTTP ${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done: end } = await reader.read();
        if (end) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const ev = JSON.parse(line.slice(6)) as DebateEvent;
          if (ev.step === "error") setError(ev.message ?? "Debate failed");
          else setEvents((xs) => [...xs, ev]);
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const facts = by("facts")?.facts;
  const bull = by("bull"), bear = by("bear"), judge = by("judge"), risk = by("risk"), fin = by("done");
  const past = facts?.past_calls_on_this_chart ?? [];

  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <MessagesSquare className="size-4 text-accent" /> AI debate
        </span>
      }
      action={
        <button
          onClick={start}
          disabled={!enabled || running}
          className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {running && <Loader2 className="size-4 animate-spin" />}
          {running ? "Debating…" : events.length ? "Run again" : "Start debate"}
        </button>
      }
    >
      {!enabled ? (
        <p className="text-sm text-faint">The AI debate needs an NVIDIA NIM API key on the backend.</p>
      ) : !events.length && !running && !error ? (
        <p className="text-sm text-muted">
          A bull and a bear argue this chart using only the app&apos;s own numbers, then a judge rates it and a risk manager checks the plan. About
          four AI calls; it runs only when you press the button.
        </p>
      ) : null}

      {(running || events.length > 0) && (
        <ol className="mb-4 flex flex-wrap items-center gap-2 text-xs">
          {STEPS.map((s, i) => {
            const ok = done.has(s.key);
            const now = current === s.key;
            return (
              <li key={s.key} className="flex items-center gap-2">
                <span
                  className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-medium ${ok ? "bg-bull/10 text-bull" : now ? "bg-accent/10 text-accent" : "bg-panel-2 text-faint"}`}
                >
                  {ok ? <Check className="size-3" /> : now ? <Loader2 className="size-3 animate-spin" /> : null}
                  {s.label}
                </span>
                {i < STEPS.length - 1 && <span className="text-faint">→</span>}
              </li>
            );
          })}
        </ol>
      )}

      {error && <p className="mb-3 rounded-xl bg-bear/10 px-3 py-2 text-sm text-bear">{error}</p>}

      <div className="space-y-3">
        {bull && <Bubble ev={bull} side="bull" />}
        {bear && <Bubble ev={bear} side="bear" />}
      </div>

      {judge && (
        <div className="mt-4 rounded-2xl border border-line p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="flex items-center gap-2 text-sm font-semibold">
              <Gavel className="size-4 text-accent" /> Judge
            </p>
            <span className="flex items-center gap-2">
              <span className={`rounded-full px-3 py-1 text-sm font-bold ${RATING_CLS[judge.rating ?? "REVIEW"] ?? RATING_CLS.REVIEW}`}>{judge.rating}</span>
              {judge.confidence && <span className="text-xs text-muted">{judge.confidence} confidence</span>}
            </span>
          </div>
          {judge.rating === "REVIEW" && <p className="mt-1 text-xs text-warn">The AI did not give one of the five ratings, so this needs your own review.</p>}
          {judge.thesis && <p className="mt-2 text-sm text-ink/90">{judge.thesis}</p>}
          {judge.rating !== "HOLD" && judge.rating !== "REVIEW" && (
            <div className="mt-3 grid grid-cols-3 gap-2 text-center">
              {(
                [
                  ["Entry", judge.entry, "text-ink"],
                  ["Stop", judge.stop, "text-bear"],
                  ["Target", judge.target, "text-bull"],
                ] as const
              ).map(([k, v, c]) => (
                <div key={k} className="rounded-xl bg-panel-2 py-2">
                  <p className="text-[10px] text-muted">{k}</p>
                  <p className={`tabular text-sm font-semibold ${c}`}>{fmt(v)}</p>
                </div>
              ))}
            </div>
          )}
          {judge.level_issues?.length ? (
            <p className="mt-2 text-[11px] text-warn">
              The AI&apos;s own levels failed checks ({judge.level_issues.join("; ")}), so the rule-based levels are shown instead.
            </p>
          ) : null}
          <p className="mt-2 text-xs text-muted">
            {judge.horizon && <>Horizon: {judge.horizon}. </>}
            {judge.change_my_mind && <>Would change its mind if: {judge.change_my_mind}</>}
          </p>
          <Unverified nums={judge.unverified_numbers} />
        </div>
      )}

      {risk && (
        <div className="mt-3 rounded-2xl bg-panel-2 p-4 text-sm">
          <p className="flex items-center gap-2 font-semibold">
            <ShieldCheck className="size-4 text-accent" /> Risk check:{" "}
            <span className={`capitalize ${RISK_CLS[risk.verdict ?? ""] ?? "text-ink"}`}>{risk.verdict ?? "—"}</span>
            {risk.size_hint && <span className="text-xs font-normal text-muted">· size {risk.size_hint}</span>}
          </p>
          <ul className="mt-1 list-disc pl-5 text-xs text-muted">
            {(risk.notes ?? []).map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
          <Unverified nums={risk.unverified_numbers} />
        </div>
      )}

      {past.length > 0 && (
        <div className="mt-3 text-xs">
          <p className="font-medium text-muted">Earlier AI calls on this chart</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {past.map((p) => (
              <li
                key={p.when}
                className={`rounded-md px-2 py-0.5 ${p.worked === true ? "bg-bull/10 text-bull" : p.worked === false ? "bg-bear/10 text-bear" : "bg-panel-2 text-muted"}`}
              >
                {p.rating} {new Date(p.when).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} · {p.move_since_pct > 0 ? "+" : ""}
                {p.move_since_pct}% since
              </li>
            ))}
          </ul>
        </div>
      )}

      {fin && (
        <p className="mt-3 text-[11px] text-faint">
          {fin.cached ? "From the last 30 minutes (same candle). " : ""}
          {fin.model} · {facts?.data_source === "demo" ? "synthetic demo data · " : ""}AI opinion on computed facts, not investment advice.
        </p>
      )}
      <HowItWorks>
        The app first gathers this chart&apos;s numbers: the signal, the outlook model and its reliability grade, regime, active strategies with
        their evidence and trade-filter odds, the market board and earlier AI calls with how they turned out. Each AI role may use only those
        numbers; any number it writes that isn&apos;t in them is flagged. HOLD is the expected answer when evidence is weak. The judge&apos;s levels must
        sit on the right sides of the entry and within 0.3–4 ATR, otherwise the rule-based levels are shown. Inspired by TradingAgents.
      </HowItWorks>
    </Panel>
  );
}
