"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { CandleData, IntradayCard } from "@/lib/types";

const IST_OFFSET = 5.5 * 3600;
const t = (iso: string) => (Date.parse(iso) / 1000 + IST_OFFSET) as UTCTimestamp;
const C = { bull: "#22c55e", bear: "#f05252", muted: "#8494a7", line: "#1d2735", vwap: "#2dd4bf", ema: "#5aa9ff", entry: "#5aa9ff" };

/** Compact 5-minute chart for an intraday card: candles, session VWAP (±1σ), Supertrend, signal arrows, levels. */
export function IntradayChart({ card, liveBar, height = 260 }: { card: IntradayCard; liveBar: CandleData | null; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const s = useRef<Record<string, ISeriesApi<"Candlestick" | "Line">>>({});
  const markerApi = useRef<ISeriesMarkersPluginApi<UTCTimestamp> | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const fitted = useRef(false);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: C.muted, fontSize: 10 },
      grid: { vertLines: { color: "#111925" }, horzLines: { color: "#111925" } },
      rightPriceScale: { borderColor: C.line },
      timeScale: { borderColor: C.line, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const line = (color: string, width: 1 | 2 = 1, style = LineStyle.Solid) =>
      c.addSeries(LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    s.current.candles = c.addSeries(CandlestickSeries, {
      upColor: C.bull, downColor: C.bear, borderVisible: false, wickUpColor: C.bull, wickDownColor: C.bear,
    });
    s.current.vwap = line(C.vwap, 2);
    s.current.vwap_u1 = line(C.vwap, 1, LineStyle.Dotted);
    s.current.vwap_l1 = line(C.vwap, 1, LineStyle.Dotted);
    s.current.ema20 = line(C.ema);
    s.current.st_up = line(C.bull, 1, LineStyle.Dashed);
    s.current.st_down = line(C.bear, 1, LineStyle.Dashed);
    markerApi.current = createSeriesMarkers(s.current.candles as ISeriesApi<"Candlestick", UTCTimestamp>, []);
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      s.current = {};
      markerApi.current = null;
      lines.current = [];
    };
  }, []);

  const data = card.chart;
  useEffect(() => {
    const ser = s.current;
    if (!chart.current || !ser.candles || !data) return;
    ser.candles.setData(data.candles.map((k) => ({ time: t(k.time), open: k.open, high: k.high, low: k.low, close: k.close })));
    for (const k of ["vwap", "vwap_u1", "vwap_l1", "ema20"]) {
      ser[k].setData((data.series[k] ?? []).map((p) => ({ time: t(p.time), value: p.value })));
    }
    const st = data.series.supertrend ?? [];
    for (const [k, d] of [["st_up", 1], ["st_down", -1]] as const) {
      ser[k].setData(st.map((p) => (p.dir === d ? { time: t(p.time), value: p.value } : { time: t(p.time) })));
    }
    markerApi.current?.setMarkers(
      data.markers.map((m) => ({
        time: t(m.time),
        position: m.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
        shape: m.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
        color: m.side === "buy" ? C.bull : C.bear,
        text: m.side === "buy" ? "BUY" : "SELL",
      })),
    );
    const cs = ser.candles as ISeriesApi<"Candlestick">;
    lines.current.forEach((pl) => cs.removePriceLine(pl));
    lines.current = [];
    const lv = card.levels;
    if (lv) {
      const add = (price: number, color: string, title: string, style = LineStyle.Solid) =>
        lines.current.push(cs.createPriceLine({ price, color, lineStyle: style, lineWidth: 1, title }));
      add(lv.target, C.bull, "Target");
      add(lv.stop, C.bear, "Stop");
      add((lv.entry_low + lv.entry_high) / 2, C.entry, "Entry", LineStyle.Dotted);
    }
    if (!fitted.current && data.candles.length) {
      chart.current.timeScale().fitContent(); // once, so later refreshes keep the user's zoom
      fitted.current = true;
    }
  }, [data, card.levels]);

  useEffect(() => {
    if (!liveBar || !s.current.candles) return;
    s.current.candles.update({ time: t(liveBar.time), open: liveBar.open, high: liveBar.high, low: liveBar.low, close: liveBar.close });
  }, [liveBar, data]);

  return <div ref={el} style={{ height }} className="w-full" />;
}
