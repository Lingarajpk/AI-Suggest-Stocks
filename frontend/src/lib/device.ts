"use client";

const KEY = "ai-stock:device";

/** A random ID for this browser, so the server can keep this device's watchlist and notifications
 * apart from everyone else's. No account needed; clearing site data creates a new one. */
export function deviceId(): string {
  try {
    let id = localStorage.getItem(KEY);
    if (!id) {
      id = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9]/g, "").padEnd(24, "0").slice(0, 40);
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}
