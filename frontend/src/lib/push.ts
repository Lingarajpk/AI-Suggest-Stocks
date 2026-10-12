"use client";

import { useCallback, useSyncExternalStore } from "react";
import { deviceId } from "./device";

// Web push: BUY/SELL notifications for this device's watchlist, even when the site is closed.
const KEY = "ai-stock:push";
const listeners = new Set<() => void>();

function readEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) === "on";
  } catch {
    return false;
  }
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function setFlag(on: boolean) {
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    /* storage unavailable */
  }
  listeners.forEach((l) => l());
}

export function pushEnabled(): boolean {
  return typeof window !== "undefined" && readEnabled();
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob((base64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

const isIos = () => typeof navigator !== "undefined" && /iphone|ipad|ipod/i.test(navigator.userAgent);
const isStandalone = () =>
  typeof window !== "undefined" &&
  (window.matchMedia?.("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true);

export function usePush() {
  const enabled = useSyncExternalStore(subscribe, readEnabled, () => false);
  const supported = typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const needsHomeScreen = typeof window !== "undefined" && isIos() && !isStandalone();

  /** Returns an error message, or null when push is on. */
  const enable = useCallback(async (): Promise<string | null> => {
    if (needsHomeScreen) return "On iPhone: tap Share → Add to Home Screen, open AI Stock from the Home Screen, then turn this on.";
    if (!supported) return "This browser can't receive push notifications.";
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return "Notifications were not allowed. Allow them in the browser's site settings.";
    try {
      const reg = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      const { public_key } = (await fetch("/api/push/key").then((r) => r.json())) as { public_key: string | null };
      if (!public_key) return "The server has no push key yet. Try again in a minute.";
      const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) }));
      const res = await fetch(`/api/watchlist/${deviceId()}/push`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription: sub.toJSON() }),
      });
      if (!res.ok) return `The server refused the subscription (HTTP ${res.status}).`;
      setFlag(true);
      return null;
    } catch (e) {
      return `Could not turn on notifications: ${e instanceof Error ? e.message : "unknown error"}`;
    }
  }, [supported, needsHomeScreen]);

  const disable = useCallback(async () => {
    try {
      const reg = await navigator.serviceWorker.getRegistration("/sw.js");
      await (await reg?.pushManager.getSubscription())?.unsubscribe();
      await fetch(`/api/watchlist/${deviceId()}/push`, { method: "DELETE" });
    } catch {
      /* best effort */
    }
    setFlag(false);
  }, []);

  return { enabled, supported, needsHomeScreen, enable, disable };
}
