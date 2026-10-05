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
    return (
      <Notice tone="warn" icon={<KeyRound className="size-4" />} title="Connect your Upstox account">
        {health.upstox_configured ? (
          <>
            {loginError && (
              <span className="mb-2 block rounded-md border border-bear/30 bg-bear/5 px-3 py-2 text-bear">
                Upstox refused the last login: {loginError}
              </span>
            )}
            Upstox access tokens expire daily (03:30 IST). Log in to authorise the backend; the token stays on the server.
            {health.upstox?.invalid_reason && <> Last token: {health.upstox.invalid_reason.replaceAll("_", " ")}.</>}{" "}
            <a href="/api/auth/upstox/login" className="ml-1 inline-block rounded-md bg-accent px-3 py-1 font-semibold text-bg hover:bg-accent/90">
              Log in with Upstox
            </a>
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
  if (streamError) {
    return (
      <Notice tone="warn" icon={<ServerOff className="size-4" />} title="Quote updates interrupted">
        {streamError}
      </Notice>
    );
  }
  return null;
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
  const cls = tone === "warn" ? "border-warn/30 bg-warn/5 text-warn" : "border-bear/30 bg-bear/5 text-bear";
  return (
    <div className={`flex gap-3 rounded-xl border px-4 py-3 text-sm ${cls}`}>
      <span className="mt-0.5">{icon}</span>
      <div>
        <p className="font-semibold">{title}</p>
        <p className="mt-0.5 text-muted">{children}</p>
      </div>
    </div>
  );
}
