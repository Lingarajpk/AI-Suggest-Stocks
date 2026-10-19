"use client";

import { useSyncExternalStore } from "react";

/** Light (default) or dark, saved per device. The <html data-theme> attribute drives the CSS colours. */
export type Theme = "light" | "dark";

const KEY = "ai-stock:theme";
const EVENT = "ai-stock:theme";

/** Inline script for <head>: applies the saved theme before the first paint (no flash). */
export const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("${KEY}");document.documentElement.dataset.theme=t==="dark"?"dark":"light"}catch(e){document.documentElement.dataset.theme="light"}})()`;

function current(): Theme {
  return typeof document !== "undefined" && document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function setTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* storage unavailable: still switches for this visit */
  }
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", t === "dark" ? "#0f1115" : "#ffffff");
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, current, () => "light");
}

/** Chart colours for each theme (lightweight-charts needs real colours, not CSS variables). */
export function chartPalette(theme: Theme) {
  return theme === "dark"
    ? {
        text: "#9aa3b0", line: "#2a2f39", grid: "#1b1f26", separatorHover: "#3a4150",
        bull: "#00c896", bear: "#ff6b4a", muted: "#9aa3b0",
        volUp: "rgba(0,200,150,0.28)", volDown: "rgba(255,107,74,0.28)",
        histUp: "rgba(0,200,150,0.55)", histDown: "rgba(255,107,74,0.55)",
        ema20: "#7b8cff", ema50: "#f5a524", ema200: "#c084fc", bb: "#5d6675", vwap: "#2dd4bf",
        strategy: "#38bdf8", candle: "#fb923c", profile: "#fde047", pattern: "#e879f9", rsi: "#a78bfa",
      }
    : {
        text: "#6b7280", line: "#e6e8ee", grid: "#f2f4f7", separatorHover: "#cfd4dc",
        bull: "#00b386", bear: "#eb5b3c", muted: "#6b7280",
        volUp: "rgba(0,179,134,0.22)", volDown: "rgba(235,91,60,0.22)",
        histUp: "rgba(0,179,134,0.5)", histDown: "rgba(235,91,60,0.5)",
        ema20: "#5367ff", ema50: "#e08a00", ema200: "#9333ea", bb: "#9aa3b0", vwap: "#0d9488",
        strategy: "#0284c7", candle: "#ea580c", profile: "#b7950b", pattern: "#c026d3", rsi: "#7c3aed",
      };
}

export type ChartPalette = ReturnType<typeof chartPalette>;
