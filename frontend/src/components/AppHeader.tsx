"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, AlertTriangle, PlugZap, Radio } from "lucide-react";
import type { ApiError } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { Alert, Health } from "@/lib/types";
import type { StreamStatus } from "@/lib/useQuoteStream";
import { AlertToast, AlertsBell } from "./Alerts";

const SESSION_LABEL = {
  open: "Market open",
  pre_open: "Pre-open",
  closed: "Market closed",
} as const;

export function AppHeader({
  health,
  healthError,
  stream,
  lastUpdate,
  alerts,
  latestAlert,
}: {
  health?: Health;
  healthError?: ApiError;
  stream: StreamStatus;
  lastUpdate: string | null;
  alerts: Alert[];
  latestAlert: Alert | null;
}) {
  const pathname = usePathname();
  return (
    <>
      <AlertToast alert={latestAlert} />
      <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <Link href="/" className="flex items-center gap-2">
            <span className="grid size-8 place-items-center rounded-lg bg-accent/15 text-accent">
              <Activity className="size-4" />
            </span>
            <span className="font-semibold tracking-tight">AI Stock</span>
            <span className="hidden text-xs text-muted lg:inline">
              Market Intelligence · NSE/BSE
            </span>
          </Link>
          <nav className="flex rounded-lg border border-line bg-panel-2 p-0.5 text-xs font-medium">
            {[
              ["/", "Intraday"],
              ["/dashboard", "Dashboard"],
            ].map(([href, label]) => (
              <Link
                key={href}
                href={href}
                className={`rounded-md px-3 py-1 ${pathname === href ? "bg-accent/15 text-accent" : "text-muted hover:text-ink"}`}
              >
                {label}
              </Link>
            ))}
          </nav>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {healthError ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-bear/10 px-3 py-1 text-bear ring-1 ring-bear/30">
                <AlertTriangle className="size-3.5" /> Backend offline
              </span>
            ) : health ? (
              <>
                <span
                  className={`rounded-full px-3 py-1 font-semibold ring-1 ${
                    health.data_mode === "demo"
                      ? "bg-warn/10 text-warn ring-warn/30"
                      : "bg-accent/10 text-accent ring-accent/30"
                  }`}
                >
                  {health.data_mode === "demo" ? "DEMO DATA" : "UPSTOX"}
                </span>
                {health.data_mode === "upstox" && (
                  <span
                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 ring-1 ${
                      health.upstox?.authenticated
                        ? "text-bull ring-bull/30"
                        : "text-warn ring-warn/30"
                    }`}
                  >
                    <PlugZap className="size-3.5" />
                    {health.upstox?.authenticated
                      ? "Connected"
                      : "Not connected"}
                  </span>
                )}
                <span className="rounded-full px-3 py-1 text-muted ring-1 ring-line">
                  {SESSION_LABEL[health.session]}
                </span>
              </>
            ) : null}
            <AlertsBell alerts={alerts} health={health} />
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 ring-1 ring-line ${stream === "open" ? "text-ink" : "text-warn"}`}
              title="Backend → browser WebSocket"
            >
              <Radio
                className={`size-3.5 ${stream === "open" ? "text-bull" : ""}`}
              />
              {stream === "open"
                ? `Updated ${fmtTime(lastUpdate)}`
                : stream === "connecting"
                  ? "Connecting…"
                  : "Reconnecting…"}
            </span>
          </div>
        </div>
      </header>
    </>
  );
}
