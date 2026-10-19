"use client";

import { useEffect, useState } from "react";
import { FlaskConical, KeyRound, ServerOff } from "lucide-react";
import type { ApiError } from "@/lib/api";
import type { Health } from "@/lib/types";

/** Persistent notices about where data comes from. Never hidden in demo mode. */
export function StatusNotices({ health, healthError, streamError }: { health?: Health; healthError?: ApiError; streamError?: string | null }) {
  const loginError = useLoginError();
  if (healthError) {
    return (
      <Notice tone="bear" icon={<ServerOff className="size-4" />} title="Backend not reachable">
        Start the FastAPI server (<code className="text-ink">uvicorn app.main:app --reload</code> in <code className="text-ink">backend/</code>). No data is shown
        until it responds.
      </Notice>
    );
  }
  if (!health) return null;
  if (health.data_mode === "demo") {
    return (
      <Notice tone="warn" icon={<FlaskConical className="size-4" />} title="Demo mode — synthetic data">
        Prices, candles and signals on this page are generated for UI development and are <strong>not market data</strong>. Set{" "}
        <code className="text-ink">DATA_MODE=upstox</code> in <code className="text-ink">backend/.env</code> to use the Upstox API.
      </Notice>
    );
  }
  if (!health.upstox?.authenticated) {
    const up = health.upstox;
    return (
      <Notice tone="warn" icon={<KeyRound className="size-4" />} title={up?.approval_pending ? "Waiting for the owner to approve in the Upstox app…" : "Live data is offline"}>
        {health.upstox_configured ? (
          <>
            {loginError && (
              <span className="mb-2 block rounded-md border border-bear/30 bg-bear/5 px-3 py-2 text-bear">
                Upstox refused the last login: {loginError}
              </span>
            )}
            The site&apos;s Upstox connection expires every day at 03:30 IST and only the site owner can renew it. Visitors don&apos;t need to log in;
            the page goes live by itself once it is renewed.
            {up?.invalid_reason && <> Last connection: {up.invalid_reason.replaceAll("_", " ")}.</>}
            <OwnerConnect protectedByKey={!!up?.owner_protected} />
          </>
        ) : (
          <>
            Set <code className="text-ink">UPSTOX_API_KEY</code> and <code className="text-ink">UPSTOX_API_SECRET</code> in <code className="text-ink">backend/.env</code>, then
            restart the backend.
          </>
        )}
      </Notice>
    );
  }
  // A "not connected" error from before the owner logged in is stale once health says connected.
  if (streamError && !/not connected|reconnect/i.test(streamError)) {
    return (
      <Notice tone="warn" icon={<ServerOff className="size-4" />} title="Quote updates interrupted">
        {streamError}
      </Notice>
    );
  }
  return null;
}

const KEY_STORE = "aistock.ownerKey";

function readKey(): string {
  try {
    return window.localStorage.getItem(KEY_STORE) ?? "";
  } catch {
    return "";
  }
}

/** Owner-only reconnect: an approval request to the Upstox app (no OTP page), or the classic login. */
function OwnerConnect({ protectedByKey }: { protectedByKey: boolean }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    const saved = readKey();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of a device-local preference
    if (saved) setKey(saved);
  }, []);

  const keep = () => {
    try {
      if (remember && key) window.localStorage.setItem(KEY_STORE, key);
      else window.localStorage.removeItem(KEY_STORE);
    } catch {
      /* storage unavailable: nothing to remember */
    }
  };

  const requestApproval = async () => {
    keep();
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch(`/api/auth/upstox/request?key=${encodeURIComponent(key)}`, { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (res.ok) setMsg({ ok: true, text: "Request sent. Open the Upstox app and approve it; this page goes live by itself." });
      else setMsg({ ok: false, text: res.status === 403 ? "Wrong owner key." : (body.detail ?? body.message ?? `Failed (HTTP ${res.status})`) });
    } catch {
      setMsg({ ok: false, text: "Could not reach the server. Try again in a minute." });
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="ml-1 text-xs font-semibold text-accent underline-offset-2 hover:underline">
        I&apos;m the owner — reconnect
      </button>
    );
  }
  return (
    <span className="mt-2 block space-y-2 rounded-lg border border-line bg-panel p-3 text-xs text-muted">
      {protectedByKey && (
        <span className="flex flex-wrap items-center gap-2">
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Owner key"
            autoComplete="current-password"
            className="min-w-0 flex-1 rounded-md border border-line bg-panel-2 px-2 py-1.5 text-ink outline-none focus:border-accent"
          />
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-accent" /> Remember on this device
          </label>
        </span>
      )}
      <span className="flex flex-wrap gap-2">
        <button
          onClick={requestApproval}
          disabled={busy || (protectedByKey && !key)}
          className="rounded-md bg-accent px-3 py-1.5 font-semibold text-white hover:bg-accent/90 disabled:opacity-50"
        >
          {busy ? "Sending…" : "Send approval to my Upstox app"}
        </button>
        <a
          href={`/api/auth/upstox/login${key ? `?key=${encodeURIComponent(key)}` : ""}`}
          onClick={keep}
          className="rounded-md border border-line px-3 py-1.5 font-semibold text-ink hover:border-accent"
        >
          Log in with OTP instead
        </a>
      </span>
      {msg && <span className={`block ${msg.ok ? "text-bull" : "text-bear"}`}>{msg.text}</span>}
      {!protectedByKey && (
        <span className="block text-warn">ADMIN_KEY is not set on the backend, so anyone can replace the connection. Set it to lock this to you.</span>
      )}
    </span>
  );
}

/** Reads ?upstox=error&message=… set by the backend after a failed OAuth callback. */
function useLoginError() {
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("upstox") === "error") {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time read of the redirect URL
      setMessage(params.get("message") ?? "Unknown error");
    }
  }, []);
  return message;
}

function Notice({ tone, icon, title, children }: { tone: "warn" | "bear"; icon: React.ReactNode; title: string; children: React.ReactNode }) {
  const cls = tone === "warn" ? "border-warn/25 bg-warn/8 text-warn" : "border-bear/25 bg-bear/8 text-bear";
  return (
    <div className={`flex gap-3 rounded-2xl border px-4 py-3 text-sm ${cls}`}>
      <span className="mt-0.5">{icon}</span>
      <div>
        <p className="font-semibold">{title}</p>
        <p className="mt-0.5 text-muted">{children}</p>
      </div>
    </div>
  );
}
