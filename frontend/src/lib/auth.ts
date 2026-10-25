"use client";

import useSWR from "swr";

export interface Me {
  user: string | null;
  login_required: boolean;
}

/** Only same-site paths are allowed as the page to return to after logging in. */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/";
}

/** Send the browser to the login page (the session is missing or expired), then back here. */
export function toLogin() {
  if (typeof window === "undefined" || window.location.pathname.startsWith("/login")) return;
  const here = window.location.pathname + window.location.search;
  window.location.href = `/login${here && here !== "/" ? `?next=${encodeURIComponent(here)}` : ""}`;
}

async function post(url: string, body?: unknown): Promise<Response> {
  return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
}

export async function login(username: string, password: string): Promise<Me> {
  const res = await post("/api/auth/login", { username, password });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if ([502, 503, 504].includes(res.status)) throw new Error("The server is waking up. Try again in a few seconds.");
    throw new Error(typeof data.detail === "string" ? data.detail : `Login failed (HTTP ${res.status})`);
  }
  return data as Me;
}

export async function logout() {
  await post("/api/auth/logout").catch(() => {});
  window.location.href = "/login";
}

export function useMe() {
  return useSWR<Me | null>("/api/auth/me", async (url: string) => {
    const res = await fetch(url, { cache: "no-store" });
    return res.ok ? ((await res.json()) as Me) : null;
  }, { revalidateOnFocus: false });
}

/** Short-lived token for the live-quotes websocket (null when the site has no login). */
export async function wsToken(): Promise<string | null> {
  try {
    const res = await fetch("/api/auth/ws-token", { cache: "no-store" });
    if (res.status === 401) toLogin();
    return res.ok ? ((await res.json()).token as string | null) : null;
  } catch {
    return null;
  }
}
