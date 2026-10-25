"use client";

import { useEffect, useState } from "react";
import { Activity, Eye, EyeOff, LogIn } from "lucide-react";
import { login, safeNext } from "@/lib/auth";

export function LoginForm({ next }: { next: string | null }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const target = safeNext(next);

  // A server without a login (local development, or a backend not yet updated): go straight in.
  // The backend is what enforces the login, so this placeholder cookie only opens the page shell.
  useEffect(() => {
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => {
        if (r.status === 404) {
          document.cookie = `ais_session=open; path=/; max-age=${86400}; samesite=lax`;
          window.location.replace(target);
        }
        return r.ok ? r.json() : null;
      })
      .then(async (me) => {
        if (me?.login_required === false) {
          await login("", "");
          window.location.replace(target);
        } else if (me?.user) {
          window.location.replace(target);
        }
      })
      .catch(() => {});
  }, [target]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return setError("Enter your username and password.");
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      window.location.replace(target);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
      setBusy(false);
    }
  };

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <span className="grid size-12 place-items-center rounded-2xl bg-accent text-white">
            <Activity className="size-6" />
          </span>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-ink">AI Stock</h1>
            <p className="text-sm text-muted">Log in to see calls, charts and alerts.</p>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-line bg-panel p-5 shadow-sm">
          <label className="block">
            <span className="mb-1.5 block text-xs font-semibold text-muted">Username</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              autoFocus
              className="h-11 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm text-ink outline-none focus:border-accent"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-semibold text-muted">Password</span>
            <span className="flex h-11 items-center rounded-xl border border-line bg-panel-2 pr-1 focus-within:border-accent">
              <input
                type={show ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="h-full min-w-0 flex-1 bg-transparent px-3 text-sm text-ink outline-none"
              />
              <button
                type="button"
                onClick={() => setShow((s) => !s)}
                className="grid size-9 place-items-center rounded-lg text-faint hover:text-ink"
                aria-label={show ? "Hide password" : "Show password"}
              >
                {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </span>
          </label>
          {error && <p className="rounded-lg bg-bear/10 px-3 py-2 text-sm text-bear" role="alert">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-accent text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60"
          >
            <LogIn className="size-4" />
            {busy ? "Logging in…" : "Log in"}
          </button>
        </form>
        <p className="mt-4 text-center text-xs text-faint">You stay logged in on this device for 30 days.</p>
      </div>
    </main>
  );
}
