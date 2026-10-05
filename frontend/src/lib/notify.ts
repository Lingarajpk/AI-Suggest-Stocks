"use client";

import { useCallback, useSyncExternalStore } from "react";
import type { Alert } from "./types";

// Per-browser preference: show desktop notifications + sound for live alerts.
const KEY = "ai-stock:notify";
const listeners = new Set<() => void>();

function readEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) === "on" && typeof Notification !== "undefined" && Notification.permission === "granted";
  } catch {
    return false;
  }
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function useNotifyPreference() {
  const enabled = useSyncExternalStore(subscribe, readEnabled, () => false);
  const supported = typeof window !== "undefined" && "Notification" in window;
  const denied = supported && Notification.permission === "denied";

  const enable = useCallback(async () => {
    if (!("Notification" in window)) return;
    const perm = await Notification.requestPermission();
    try {
      localStorage.setItem(KEY, perm === "granted" ? "on" : "off");
    } catch {
      /* storage unavailable */
    }
    if (perm === "granted") beep("up");
    listeners.forEach((l) => l());
  }, []);

  const disable = useCallback(() => {
    try {
      localStorage.setItem(KEY, "off");
    } catch {
      /* storage unavailable */
    }
    listeners.forEach((l) => l());
  }, []);

  return { enabled, supported, denied, enable, disable };
}

let audio: AudioContext | null = null;

/** Short two-tone chime: rising for up/buy, falling for down/sell. */
export function beep(direction: Alert["direction"]) {
  try {
    audio ??= new AudioContext();
    const tones = direction === "down" ? [880, 587] : [587, 880];
    tones.forEach((freq, i) => {
      const osc = audio!.createOscillator();
      const gain = audio!.createGain();
      osc.frequency.value = freq;
      const t = audio!.currentTime + i * 0.16;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
      osc.connect(gain).connect(audio!.destination);
      osc.start(t);
      osc.stop(t + 0.16);
    });
  } catch {
    /* audio unavailable */
  }
}

export function showNotification(alert: Alert) {
  if (!readEnabled()) return;
  try {
    const n = new Notification(alert.title, { body: alert.message, tag: `ai-stock-${alert.id}` });
    n.onclick = () => {
      window.focus();
      // Fired outside React (OS notification click), so the router isn't available here.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      if (alert.symbol) window.location.href = `/stock/${alert.symbol}`;
    };
    beep(alert.direction);
  } catch {
    /* notifications unavailable */
  }
}
