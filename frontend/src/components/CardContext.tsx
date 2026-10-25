"use client";

import type { IntradayCard } from "@/lib/types";
import { RegimeChip } from "./StrategiesPanel";

/** One line under an intraday call: the 5-minute chart's regime and whether the daily trend agrees. */
export function CardContext({ card }: { card: IntradayCard }) {
  const d = card.daily;
  if (!card.regime && !d) return null;
  const daily =
    d == null ? null : d.agrees === true ? (
      <span className="text-bull">daily trend agrees</span>
    ) : d.agrees === false ? (
      <span className="font-semibold text-warn">against the daily trend</span>
    ) : (
      <span className="text-muted">daily trend {d.lean > 0 ? "up" : d.lean < 0 ? "down" : "flat"}</span>
    );
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
      <RegimeChip regime={card.regime} />
      {daily && (
        <span title={d ? `Daily rule score ${d.score > 0 ? "+" : ""}${d.score.toFixed(0)}` : undefined}>
          {card.regime && <span className="text-faint">· </span>}
          {daily}
        </span>
      )}
    </p>
  );
}
