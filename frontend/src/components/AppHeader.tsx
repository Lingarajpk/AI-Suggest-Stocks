"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Activity, Globe, LayoutGrid, Moon, Search, Star, Sun, Zap } from "lucide-react";
import { useSearch, type ApiError } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { setTheme, useTheme } from "@/lib/theme";
import type { Alert, Health } from "@/lib/types";
import type { StreamStatus } from "@/lib/useQuoteStream";
import { AlertToast, AlertsBell } from "./Alerts";
import { ToolsToggle } from "./DayTraderTools";

const SESSION_LABEL = {
  open: "Market open",
  pre_open: "Pre-open",
  closed: "Market closed",
} as const;

const NAV = [
  { href: "/", label: "Intraday", icon: Zap },
  { href: "/forex", label: "Forex", icon: Globe },
  { href: "/watchlist", label: "Watchlist", icon: Star },
  { href: "/dashboard", label: "Dashboard", icon: LayoutGrid },
] as const;

const slug = (s: string) => s.replace(/\s+/g, "");

export function AppHeader({
  health,
  healthError,
  stream,
  lastUpdate,
  alerts,
  latestAlert,
  session,
}: {
  health?: Health;
  healthError?: ApiError;
  stream: StreamStatus;
  lastUpdate: string | null;
  alerts: Alert[];
  latestAlert: Alert | null;
  /** Pages for another market (forex: 09:00-17:00) pass their own session instead of the stock market's. */
  session?: { state: Health["session"]; label: string; holiday?: string | null };
}) {
  const pathname = usePathname();
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  return (
    <>
      <AlertToast alert={latestAlert} />
      <header className="sticky top-0 z-20 border-b border-line bg-panel/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-4 sm:gap-6 sm:px-6">
          <Link href="/" className="flex shrink-0 items-center gap-2">
            <span className="grid size-8 place-items-center rounded-xl bg-accent text-white">
              <Activity className="size-4" />
            </span>
            <span className="text-[15px] font-bold tracking-tight">AI Stock</span>
          </Link>
          <nav className="hidden h-full items-stretch gap-1 md:flex">
            {NAV.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                className={`flex items-center border-b-2 px-3 text-sm font-medium ${
                  active(href) ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"
                }`}
              >
                {label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            <HeaderSearch />
            <StatusDot health={health} healthError={healthError} stream={stream} lastUpdate={lastUpdate} session={session} />
            <AlertsBell alerts={alerts} health={health} />
            <ToolsToggle />
            <ThemeToggle />
          </div>
        </div>
      </header>
      {/* Phone: tab bar at the bottom, like a trading app */}
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-panel/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <div className="grid grid-cols-4">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={`flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium ${active(href) ? "text-accent" : "text-muted"}`}
            >
              <Icon className="size-5" />
              {label}
            </Link>
          ))}
        </div>
      </nav>
    </>
  );
}

/** One dot instead of four status pills: green live, amber not live, red offline. Details on tap. */
function StatusDot({ health, healthError, stream, lastUpdate, session }: {
  health?: Health; healthError?: ApiError; stream: StreamStatus; lastUpdate: string | null; session?: { state: Health["session"]; label: string; holiday?: string | null };
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const demo = health?.data_mode === "demo";
  const connected = !!health?.upstox?.authenticated;
  const streaming = stream === "open" || stream === "polling";
  const market = session?.state ?? health?.session;
  const holiday = session ? session.holiday : health?.holiday;
  const [tone, label] = healthError
    ? (["bg-bear", "Offline"] as const)
    : !health
      ? (["bg-faint", "…"] as const)
      : demo
        ? (["bg-warn", "Demo"] as const)
        : !connected
          ? (["bg-warn", "Not connected"] as const)
          : market !== "open"
            ? (["bg-faint", "Closed"] as const)
            : streaming
              ? (["bg-bull", "Live"] as const)
              : (["bg-warn", "Reconnecting"] as const);

  const rows: [string, string][] = [
    ["Data", healthError ? "Backend offline" : demo ? "Demo data (not real prices)" : "Upstox live data"],
    ["Upstox", demo ? "—" : connected ? `Connected${health?.upstox?.connected_as ? ` as ${health.upstox.connected_as}` : ""}` : "Not connected"],
    [session?.label ?? "Market", market ? `${SESSION_LABEL[market]}${holiday ? ` · ${holiday}` : ""}` : "—"],
    ["Prices", streaming ? `Updated ${fmtTime(lastUpdate)}${stream === "polling" ? " (backup feed)" : ""}` : stream === "connecting" ? "Connecting…" : "Reconnecting…"],
  ];

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-9 items-center gap-2 rounded-full px-3 text-xs font-medium text-muted hover:bg-panel-2"
        aria-label={`Status: ${label}`}
      >
        <span className="relative flex size-2.5">
          {label === "Live" && <span className="absolute inline-flex size-full animate-ping rounded-full bg-bull opacity-60" />}
          <span className={`relative inline-flex size-2.5 rounded-full ${tone}`} />
        </span>
        <span className="hidden sm:inline">{label}</span>
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-64 rounded-2xl border border-line bg-panel p-4 text-sm shadow-xl">
          <dl className="space-y-2">
            {rows.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <dt className="text-muted">{k}</dt>
                <dd className="text-right font-medium text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

function ThemeToggle() {
  const theme = useTheme();
  const dark = theme === "dark";
  return (
    <button
      onClick={() => setTheme(dark ? "light" : "dark")}
      className="grid size-9 place-items-center rounded-full text-muted hover:bg-panel-2 hover:text-ink"
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Light theme" : "Dark theme"}
    >
      {dark ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
    </button>
  );
}

/** Stock and index search; Enter or a tap opens the stock page. */
function HeaderSearch() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const { data, isLoading } = useSearch(debounced);
  const results = debounced ? (data?.results ?? []).slice(0, 8) : [];

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const go = (symbol: string) => {
    setOpen(false);
    setQ("");
    router.push(`/stock/${encodeURIComponent(slug(symbol))}`);
  };

  return (
    <div ref={ref} className="relative">
      {open ? (
        <div className="flex h-9 w-[min(18rem,calc(100vw-9rem))] items-center gap-2 rounded-full border border-line bg-panel-2 px-3 focus-within:border-accent">
          <Search className="size-4 shrink-0 text-faint" />
          <input
            ref={input}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
              if (e.key === "Enter" && results[0]) go(results[0].symbol);
            }}
            placeholder="Search stocks & indices"
            className="w-full min-w-0 bg-transparent text-sm text-ink outline-none placeholder:text-faint"
          />
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="inline-flex h-9 items-center gap-2 rounded-full border border-line px-3 text-sm text-faint hover:border-accent/50 sm:w-56"
          aria-label="Search stocks"
        >
          <Search className="size-4" />
          <span className="hidden sm:inline">Search stocks & indices</span>
        </button>
      )}
      {open && debounced && (
        <div className="absolute right-0 z-30 mt-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-line bg-panel shadow-xl">
          {isLoading && !results.length ? (
            <p className="px-4 py-3 text-sm text-faint">Searching…</p>
          ) : results.length ? (
            results.map((r) => (
              <button
                key={`${r.exchange}-${r.symbol}`}
                onClick={() => go(r.symbol)}
                className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-panel-2"
              >
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-ink">{r.symbol}</span>
                  <span className="block truncate text-xs text-muted">{r.name}</span>
                </span>
                <span className="shrink-0 text-[11px] text-faint">{r.kind === "index" ? "Index" : r.exchange}</span>
              </button>
            ))
          ) : (
            <p className="px-4 py-3 text-sm text-faint">No matches</p>
          )}
        </div>
      )}
    </div>
  );
}
