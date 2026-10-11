"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesMarkersPluginApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { CandleData, ClosedTrade, PatternInstance, Point, Scenario, SignalMarker, StrategyBlock, StrategyKey } from "@/lib/types";

const IST_OFFSET = 5.5 * 3600;
// Lightweight Charts renders timestamps as UTC; shift so the axis reads in IST.
const t = (iso: string) => (Date.parse(iso) / 1000 + IST_OFFSET) as UTCTimestamp;

const C = {
  pattern: "#e879f9", bull: "#22c55e", bear: "#f05252", muted: "#8494a7", line: "#1d2735", ema20: "#5aa9ff", ema50: "#f5a524",
  ema200: "#c084fc", bb: "#4d5b6d", vwap: "#2dd4bf", strategy: "#38bdf8", candle: "#fb923c", profile: "#fde047",
};

// Short chart labels for strategy markers (breakout probability flips too often to mark).
const STRATEGY_TAG: Partial<Record<StrategyKey, string>> = {
  trend_momentum: "T+M", liquidity_sweep: "Sweep", fvg: "FVG", ifvg: "IFVG", amd: "AMD", supertrend_flip: "ST", macd_divergence: "Div",
};

interface Props {
  candles: CandleData[];
  series: Record<string, Point[]>;
  oscillators: Record<string, Point[]>;
  support?: number | null;
  resistance?: number | null;
  intraday: boolean;
  showBands: boolean;
  markers: SignalMarker[];
  exits: ClosedTrade[];
  showMarkers: boolean;
  scenario: Scenario | null;
  /** Live patterns are outlined; breakouts (live and recent) get a marker. */
  patterns: PatternInstance[];
  recentPatterns: PatternInstance[];
  showPatterns: boolean;
  /** Strategy points, candlesticks, FVG zones and volume-profile levels. */
  strategies: StrategyBlock | null | undefined;
  showStrategies: boolean;
  showSupertrend: boolean;
  showVwapBands: boolean;
  /** Still-forming candle, redrawn on every live quote. */
  liveBar: CandleData | null;
}

export function PriceChart({
  candles, series, oscillators, support, resistance, intraday, showBands, markers, exits, showMarkers, scenario, patterns, recentPatterns,
  showPatterns, strategies, showStrategies, showSupertrend, showVwapBands, liveBar,
}: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const s = useRef<Record<string, ISeriesApi<"Candlestick" | "Histogram" | "Line">>>({});
  const priceLines = useRef<IPriceLine[]>([]);
  const markerApi = useRef<ISeriesMarkersPluginApi<UTCTimestamp> | null>(null);
  const patternSeries = useRef<ISeriesApi<"Line">[]>([]);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: C.muted,
        fontSize: 11,
        panes: { separatorColor: C.line, separatorHoverColor: "#26364a" },
      },
      grid: { vertLines: { color: "#111925" }, horzLines: { color: "#111925" } },
      rightPriceScale: { borderColor: C.line },
      timeScale: { borderColor: C.line, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const line = (color: string, pane = 0, width: 1 | 2 = 1) =>
      c.addSeries(LineSeries, { color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, pane);

    s.current.candles = c.addSeries(CandlestickSeries, {
      upColor: C.bull, downColor: C.bear, borderVisible: false, wickUpColor: C.bull, wickDownColor: C.bear,
    });
    s.current.volume = c.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    s.current.ema20 = line(C.ema20);
    s.current.ema50 = line(C.ema50);
    s.current.ema200 = line(C.ema200, 0, 2);
    s.current.bb_upper = line(C.bb);
    s.current.bb_lower = line(C.bb);
    s.current.vwap = line(C.vwap);
    const dashed = (color: string, style: LineStyle) => {
      const ls = line(color);
      ls.applyOptions({ lineStyle: style });
      return ls;
    };
    s.current.vwap_u1 = dashed(C.vwap, LineStyle.Dotted);
    s.current.vwap_l1 = dashed(C.vwap, LineStyle.Dotted);
    s.current.vwap_u2 = dashed(C.vwap, LineStyle.Dashed);
    s.current.vwap_l2 = dashed(C.vwap, LineStyle.Dashed);
    s.current.st_up = line(C.bull, 0, 2);
    s.current.st_down = line(C.bear, 0, 2);

    s.current.rsi14 = line("#a78bfa", 1, 2);
    s.current.rsi14.createPriceLine({ price: 70, color: C.bear, lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "" });
    s.current.rsi14.createPriceLine({ price: 30, color: C.bull, lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "" });

    s.current.macd_hist = c.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, 2);
    s.current.macd = line(C.ema20, 2);
    s.current.macd_signal = line(C.ema50, 2);

    markerApi.current = createSeriesMarkers(s.current.candles as ISeriesApi<"Candlestick", UTCTimestamp>, []);

    const panes = c.panes();
    panes[0]?.setStretchFactor(3);
    panes[1]?.setStretchFactor(1);
    panes[2]?.setStretchFactor(1);
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      s.current = {};
      priceLines.current = [];
      markerApi.current = null;
      patternSeries.current = [];
    };
  }, []);

  useEffect(() => {
    const ser = s.current;
    if (!chart.current || !ser.candles) return;
    ser.candles.setData(candles.map((k) => ({ time: t(k.time), open: k.open, high: k.high, low: k.low, close: k.close })));
    ser.volume.setData(
      candles.map((k) => ({ time: t(k.time), value: k.volume, color: k.close >= k.open ? "rgba(34,197,94,0.25)" : "rgba(240,82,82,0.25)" })),
    );
    const pts = (p?: Point[]) => (p ?? []).map((x) => ({ time: t(x.time), value: x.value }));
    for (const k of ["ema20", "ema50", "ema200", "bb_upper", "bb_lower", "vwap"]) ser[k].setData(pts(series[k]));
    ser.bb_upper.applyOptions({ visible: showBands });
    ser.bb_lower.applyOptions({ visible: showBands });
    ser.vwap.applyOptions({ visible: intraday });
    for (const k of ["vwap_u1", "vwap_l1", "vwap_u2", "vwap_l2"]) {
      ser[k].setData(pts(series[k]));
      ser[k].applyOptions({ visible: intraday && showVwapBands });
    }
    // Supertrend: one green series for up-trend stretches, one red for down-trend (gaps elsewhere).
    const st = series.supertrend ?? [];
    for (const [k, d] of [["st_up", 1], ["st_down", -1]] as const) {
      ser[k].setData(st.map((x) => (x.dir === d ? { time: t(x.time), value: x.value } : { time: t(x.time) })));
      ser[k].applyOptions({ visible: showSupertrend });
    }
    ser.rsi14.setData(pts(oscillators.rsi14));
    ser.macd.setData(pts(oscillators.macd));
    ser.macd_signal.setData(pts(oscillators.macd_signal));
    ser.macd_hist.setData(
      (oscillators.macd_hist ?? []).map((x) => ({ time: t(x.time), value: x.value, color: x.value >= 0 ? "rgba(34,197,94,0.5)" : "rgba(240,82,82,0.5)" })),
    );

    const cs = ser.candles as ISeriesApi<"Candlestick">;
    priceLines.current.forEach((pl) => cs.removePriceLine(pl));
    priceLines.current = [];
    if (support) priceLines.current.push(cs.createPriceLine({ price: support, color: C.bull, lineStyle: LineStyle.Dashed, lineWidth: 1, title: "S" }));
    if (resistance) priceLines.current.push(cs.createPriceLine({ price: resistance, color: C.bear, lineStyle: LineStyle.Dashed, lineWidth: 1, title: "R" }));
    if (scenario) {
      const add = (price: number, color: string, title: string, style = LineStyle.Solid) =>
        priceLines.current.push(cs.createPriceLine({ price, color, lineStyle: style, lineWidth: 1, title }));
      const entry = scenario.direction === "long" ? "Buy zone" : "Sell zone";
      add(scenario.entry_high, C.ema20, entry, LineStyle.Dotted);
      add(scenario.entry_low, C.ema20, "", LineStyle.Dotted);
      add(scenario.target, C.bull, "Target");
      add(scenario.stop_loss, C.bear, "Stop");
    }
    const vp = strategies?.volume_profile;
    if (showStrategies && vp) {
      for (const [price, title, style] of [[vp.poc, "POC", LineStyle.Solid], [vp.vah, "VAH", LineStyle.Dotted], [vp.val, "VAL", LineStyle.Dotted]] as const) {
        if (price != null) priceLines.current.push(cs.createPriceLine({ price, color: C.profile, lineStyle: style, lineWidth: 1, title }));
      }
    }

    const entryMarks = markers.map((m) => ({
      time: t(m.time),
      position: m.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
      shape: m.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
      color: m.side === "buy" ? C.bull : C.bear,
      text: m.side === "buy" ? "BUY" : "SELL",
    }));
    // Exit of a BUY = sell it (marked above the bar, pointing down); exit of a SELL = buy back.
    const exitMarks = exits.map((x) => ({
      time: t(x.exit_time),
      position: x.side === "buy" ? ("aboveBar" as const) : ("belowBar" as const),
      shape: x.side === "buy" ? ("arrowDown" as const) : ("arrowUp" as const),
      color: C.ema50,
      text: `${x.side === "buy" ? "EXIT" : "COVER"} ${x.pnl_pct > 0 ? "+" : ""}${x.pnl_pct.toFixed(1)}%`,
    }));
    // Pattern outlines: swing points joined up, plus the pattern's trendlines (each its own 2-point series).
    const c = chart.current;
    patternSeries.current.forEach((ps) => c.removeSeries(ps));
    patternSeries.current = [];
    const draw = (pts: { time: string; price: number }[], color: string, style: LineStyle) => {
      const data = pts.map((p) => ({ time: t(p.time), value: p.price }));
      if (data.length < 2 || data.some((d, i) => i > 0 && d.time <= data[i - 1].time)) return;
      const ps = c.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      ps.setData(data);
      patternSeries.current.push(ps);
    };
    // Open fair value gaps: top and bottom edge from where the gap formed to the latest candle.
    const lastTime = candles.at(-1)?.time;
    if (showStrategies && lastTime) {
      for (const z of strategies?.fvg_zones ?? []) {
        const color = z.direction === "up" ? C.bull : C.bear;
        const style = z.kind === "fvg" ? LineStyle.Solid : LineStyle.Dashed;
        draw([{ time: z.start_time, price: z.high }, { time: lastTime, price: z.high }], color, style);
        draw([{ time: z.start_time, price: z.low }, { time: lastTime, price: z.low }], color, style);
      }
    }
    if (showPatterns) {
      for (const p of patterns) {
        const color = p.direction === "up" || p.bias === "bullish" ? C.bull : p.direction === "down" || p.bias === "bearish" ? C.bear : C.pattern;
        draw(p.points, C.pattern, LineStyle.Dotted);
        p.lines.forEach((l) => draw(l, color, LineStyle.Dashed));
      }
    }
    const patternMarks = showPatterns
      ? [...patterns, ...recentPatterns]
          .filter((p) => p.breakout_time)
          .map((p) => ({
            time: t(p.breakout_time!),
            position: p.direction === "up" ? ("belowBar" as const) : ("aboveBar" as const),
            shape: "circle" as const,
            color: C.pattern,
            text: p.name,
          }))
      : [];
    const strategyMarks = showStrategies
      ? [
          ...(strategies?.markers ?? [])
            .filter((m) => STRATEGY_TAG[m.key])
            .map((m) => ({
              time: t(m.time),
              position: m.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
              shape: "square" as const,
              color: C.strategy,
              text: STRATEGY_TAG[m.key]!,
            })),
          ...(strategies?.candles ?? []).map((k) => ({
            time: t(k.time),
            position: k.direction === "up" ? ("belowBar" as const) : ("aboveBar" as const),
            shape: "circle" as const,
            color: C.candle,
            text: k.name,
          })),
        ]
      : [];
    markerApi.current?.setMarkers(
      [...(showMarkers ? [...entryMarks, ...exitMarks] : []), ...patternMarks, ...strategyMarks].sort((a, b) => a.time - b.time),
    );
  }, [
    candles, series, oscillators, support, resistance, intraday, showBands, markers, exits, showMarkers, scenario, patterns, recentPatterns,
    showPatterns, strategies, showStrategies, showSupertrend, showVwapBands,
  ]);

  // Runs after the data effect above, so the live candle sits on top of the latest setData.
  useEffect(() => {
    const ser = s.current;
    if (!liveBar || !ser.candles) return;
    const time = t(liveBar.time);
    ser.candles.update({ time, open: liveBar.open, high: liveBar.high, low: liveBar.low, close: liveBar.close });
    ser.volume.update({ time, value: liveBar.volume, color: liveBar.close >= liveBar.open ? "rgba(34,197,94,0.25)" : "rgba(240,82,82,0.25)" });
  }, [liveBar, candles]);

  return <div ref={el} className="h-[560px] w-full" />;
}

export const CHART_LEGEND = [
  { label: "EMA 20", color: C.ema20 },
  { label: "EMA 50", color: C.ema50 },
  { label: "EMA 200", color: C.ema200 },
  { label: "VWAP (intraday)", color: C.vwap },
  { label: "Bollinger 20,2", color: C.bb },
  { label: "▲ BUY / ▼ SELL signal points", color: C.bull },
  { label: "EXIT (sell the buy) / COVER (buy back the sell)", color: C.ema50 },
  { label: "Buy/Sell zone · Target · Stop (current setup)", color: C.ema20 },
  { label: "● Chart pattern outline / breakout", color: C.pattern },
  { label: "Supertrend 10,3 (green up / red down)", color: C.bull },
  { label: "VWAP ±1σ / ±2σ bands (intraday)", color: C.vwap },
  { label: "■ Strategy signal (T+M, Sweep, FVG, IFVG, AMD, ST flip, MACD Div) · FVG zones", color: C.strategy },
  { label: "● Candlestick pattern", color: C.candle },
  { label: "POC / value area (VAH–VAL)", color: C.profile },
];
