export type Timeframe = "15m" | "1h" | "1d";
export type Freshness = "polled" | "stale" | "market_closed" | "unavailable" | "demo";
export type SignalLabel = "Strong Bullish" | "Bullish" | "Neutral" | "Bearish" | "Strong Bearish";

export interface Instrument {
  symbol: string;
  name: string;
  exchange: string;
  segment: string;
  instrument_key: string;
  isin: string | null;
  sector: string | null;
  kind: "equity" | "index";
}

export interface Quote {
  instrument_key: string;
  symbol: string;
  last_price: number;
  change: number | null;
  change_pct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  prev_close: number | null;
  volume: number | null;
  last_trade_time: string | null;
  fetched_at: string;
  freshness: Freshness;
  source: "upstox" | "demo";
}

export interface Evidence {
  factor: string;
  weight: number;
  score: number;
  detail: string;
}

export interface Scenario {
  direction: "long" | "short";
  entry_low: number;
  entry_high: number;
  target: number;
  stop_loss: number;
  risk_reward: number;
  target_capped_by: string | null;
  basis: string;
}

export interface Signal {
  status: "ok" | "no_trade";
  signal: SignalLabel | null;
  score: number | null;
  no_trade_reasons: string[];
  evidence: Evidence[];
  risks: string[];
  scenario: Scenario | null;
  levels: { support?: number | null; resistance?: number | null; atr?: number | null };
  bars: number;
  last_candle_time?: string;
  method?: string;
}

export type Snapshot = Record<string, number | null>;

export interface Analysis {
  signal: Signal;
  snapshot: Snapshot;
  last_close?: number;
  history?: SignalHistory;
}

export interface ScanRow {
  instrument: Instrument;
  quote: Quote | null;
  analysis: Analysis | null;
  error: string | null;
}

export interface ScanResult {
  timeframe: Timeframe;
  generated_at: string;
  source: "upstox" | "demo";
  scanned: number;
  unresolved_symbols: string[];
  quotes_error: string | null;
  counts: Record<string, number>;
  sector_strength: { sector: string; avg_score: number; count: number }[];
  rows: ScanRow[];
}

export interface Health {
  status: string;
  data_mode: "demo" | "upstox";
  provider: string;
  upstox: { authenticated: boolean; source: string; expires_at: string | null; invalid_reason: string | null } | null;
  upstox_configured: boolean;
  cache: string;
  nvidia_configured: boolean;
  nvidia_model: string;
  session: "open" | "closed" | "pre_open";
  server_time: string;
  quote_poll_seconds: number;
  alerts: {
    enabled: boolean;
    telegram: boolean;
    move_levels: number[];
    fast_move_pct: number;
    fast_window_minutes: number;
    signal_timeframe: Timeframe;
  };
}

export interface CandleData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Point {
  time: string;
  value: number;
}

export interface StockDetail extends Analysis {
  instrument: Instrument;
  timeframe: Timeframe;
  source: "upstox" | "demo";
  quote: Quote | null;
  generated_at: string;
  model_outlook: { status: string; message: string };
  news: { status: string; message: string };
  candles: CandleData[];
  series: Record<string, Point[]>;
  oscillators: Record<string, Point[]>;
}

export interface Explanation {
  status: "ok";
  text: string;
  model: string;
  generated_at: string;
  unverified_numbers: string[];
  cached: boolean;
}

export interface SideStats {
  count: number;
  wins: number;
  win_rate: number | null;
  avg_return_pct: number | null;
}

export interface SignalMarker {
  time: string;
  side: "buy" | "sell";
  price: number;
  score: number;
  forward_return_pct: number | null;
  outcome: "win" | "loss" | null;
}

export interface SignalHistory {
  horizon_bars: number;
  horizon_label: string;
  buy: SideStats;
  sell: SideStats;
  base_rate_up_pct: number | null;
  period_start: string | null;
  period_end: string;
  notes: string;
  markers?: SignalMarker[];
}

export interface TrackRecord extends SideStats {
  horizon_label: string;
  base_rate_pct: number | null;
}

export interface TodayItem {
  instrument: Instrument;
  quote: Quote | null;
  last_close: number | null;
  daily_signal: SignalLabel;
  daily_score: number;
  intraday_score: number | null;
  combined_score: number;
  scenario: Scenario | null;
  risks: string[];
  track_record: TrackRecord;
}

export interface TodayResult {
  generated_at: string;
  source: "upstox" | "demo";
  session: "open" | "closed" | "pre_open";
  considered: number;
  upside: TodayItem[];
  downside: TodayItem[];
  method: string;
}

export interface Alert {
  id: number;
  time: string;
  kind: "signal" | "level" | "fast" | "auth";
  direction: "up" | "down" | "system";
  symbol: string | null;
  title: string;
  message: string;
  price: number | null;
  change_pct: number | null;
  source: "upstox" | "demo";
}

export interface MoverRow {
  symbol: string;
  name?: string;
  price: number;
  change_pct: number;
  minutes?: number;
}

export interface Movers {
  session: string;
  gainers: MoverRow[];
  losers: MoverRow[];
  fast: MoverRow[];
  fast_window_minutes: number;
}
