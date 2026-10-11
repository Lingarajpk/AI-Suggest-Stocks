export type Timeframe = "1m" | "5m" | "15m" | "1h" | "1d";
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
  patterns?: PatternBlock;
  strategies?: StrategyBlock | null;
}

export type StrategyKey = "trend_momentum" | "liquidity_sweep" | "fvg" | "ifvg" | "amd" | "breakout_prob" | "supertrend_flip" | "macd_divergence";

export interface StrategyState {
  key: StrategyKey;
  name: string;
  kind: string;
  description: string;
  state: "long" | "short" | "none" | "n/a";
  detail: string | null;
  last_signal: { time: string; side: "buy" | "sell"; price: number; bars_ago: number; detail: string | null } | null;
}

export interface CandleHit {
  key: string;
  name: string;
  time: string;
  direction: "up" | "down";
  price: number;
}

export interface FvgZone {
  kind: "fvg" | "ifvg";
  direction: "up" | "down";
  low: number;
  high: number;
  start_time: string;
  since_time: string;
}

export interface MarkerEvent {
  time: string;
  direction: "up" | "down";
  bars_ago: number;
}

export interface StrategyBlock {
  strategies: StrategyState[];
  breakout: {
    last_candle: "green" | "red";
    levels: { atr_mult: number; up_level: number | null; down_level: number | null; p_up_pct: number | null; p_down_pct: number | null }[];
    note: string;
  };
  candles: CandleHit[];
  volume_profile: {
    window: string;
    poc: number | null;
    vah: number | null;
    val: number | null;
    price_vs_value: string | null;
    delta_20_pct: number;
    cvd_divergence: MarkerEvent | null;
    note: string;
  };
  macd: { last_cross: MarkerEvent | null; last_zero_cross: MarkerEvent | null; last_divergence: MarkerEvent | null };
  fvg_zones?: FvgZone[];
  markers?: { time: string; key: StrategyKey; name: string; side: "buy" | "sell"; price: number }[];
}

export interface StrategyStatsRow {
  key: string;
  group: "strategy" | "candlestick";
  name: string;
  kind: string;
  description: string;
  signals: number;
  buy_signals: number;
  sell_signals: number;
  hit_rate: number | null;
  base_rate: number | null;
  edge_pts: number | null;
  avg_move_pct: number | null;
  trades: number;
  trade_win_rate: number | null;
  avg_trade_pct: number | null;
}

export interface StrategyStatsResult {
  timeframe: Timeframe;
  source: "upstox" | "demo";
  generated_at: string;
  instruments: number;
  unavailable: string[];
  horizon_bars: number;
  horizon_label: string;
  rows: StrategyStatsRow[];
  method: string;
}

export type PatternStatus = "forming" | "breakout" | "target_hit" | "stopped" | "expired" | "failed" | "expired_unbroken" | "target_at_breakout";

export interface PricePoint {
  time: string;
  price: number;
}

export interface PatternInstance {
  key: string;
  name: string;
  variant: string | null;
  bias: "bullish" | "bearish" | "either";
  kind: "reversal" | "continuation";
  stars: number;
  rule: string;
  status: PatternStatus;
  direction: "up" | "down" | null;
  start_time: string;
  end_time: string;
  detected_time: string;
  points: PricePoint[];
  lines: [PricePoint, PricePoint][];
  trigger_up?: number | null;
  trigger_down?: number | null;
  projection?: Partial<Record<"up" | "down", { target: number; stop: number }>>;
  bars_left?: number;
  breakout_time?: string;
  breakout_level?: number;
  entry_price?: number;
  target?: number;
  stop?: number;
  volume_confirmed?: boolean | null;
  pnl_pct?: number | null;
  exit_time?: string | null;
  exit_price?: number | null;
  bars_since_breakout?: number;
}

export interface PatternStats {
  detected: number;
  resolved: number;
  failed_to_break: number;
  wins: number;
  losses: number;
  expired: number;
  success_rate: number | null;
  profitable_rate: number | null;
  avg_pnl_pct: number | null;
  volume_confirmed_n: number;
  volume_confirmed_success_rate: number | null;
}

export interface PatternBlock {
  current: PatternInstance[];
  recent?: PatternInstance[];
  stock_stats?: Record<string, PatternStats>;
}

export interface PatternStatsRow extends PatternStats {
  key: string;
  name: string;
  bias: PatternInstance["bias"];
  kind: PatternInstance["kind"];
  stars: number;
  rule: string;
}

export interface PatternStatsResult {
  timeframe: Timeframe;
  source: "upstox" | "demo";
  generated_at: string;
  instruments: number;
  unavailable: string[];
  patterns: PatternStatsRow[];
  method: string;
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
  news?: { enabled: boolean; source: string; scored_by: "nvidia" | "keywords" };
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
  /** Supertrend only: +1 up-trend, -1 down-trend. */
  dir?: number;
}

export interface StockDetail extends Analysis {
  instrument: Instrument;
  timeframe: Timeframe;
  source: "upstox" | "demo";
  quote: Quote | null;
  generated_at: string;
  model_outlook: Outlook;
  candles: CandleData[];
  forming_candle?: CandleData | null;
  series: Record<string, Point[]>;
  oscillators: Record<string, Point[]>;
}

export type Verdict = "BUY" | "SELL" | "HOLD" | "NO EDGE" | "NO TRADE";

export interface OutlookTest {
  samples: number;
  train_samples: number;
  train_period: [string, string];
  test_period: [string, string];
  base_up_pct: number;
  brier: number;
  brier_baseline: number;
  skill_pct: number;
  accuracy_pct: number;
  buy_calls: number;
  buy_hit_pct: number | null;
  sell_calls: number;
  sell_hit_pct: number | null;
  calibration: { range: string; n: number; predicted_up_pct: number; actual_up_pct: number }[];
  features_used?: string[];
  features_added?: { feature: string; brier_gain: number }[];
  features_dropped?: string[];
}

export interface Outlook {
  status: "ok" | "not_available";
  message: string;
  horizon_bars?: number;
  horizon_label?: string;
  verdict?: Verdict;
  reason?: string;
  up_pct?: number;
  down_pct?: number;
  drivers?: { feature: string; label: string; value: number; push: number }[];
  thresholds?: { buy_at_pct: number; sell_at_pct: number };
  test?: OutlookTest;
  method?: string;
}

export interface NewsItem {
  id: string;
  symbol?: string;
  title: string;
  publisher: string | null;
  link: string;
  published: string;
  sentiment: number;
  impact: "low" | "medium" | "high";
  reason: string | null;
  scored_by: "nvidia" | "keywords";
}

export interface NewsFeed {
  source: string;
  last_fetch: string | null;
  error: string | null;
  enabled: boolean;
  items: NewsItem[];
}

export interface StockNews extends NewsFeed {
  symbol: string;
  price_source: "upstox" | "demo";
  lookback_hours: number;
  summary: { score: number; weight: number; label: string };
  technical: { status: string; up_pct?: number; down_pct?: number; verdict?: Verdict; horizon_label?: string; message?: string };
  adjusted: { up_pct: number; down_pct: number; shift_pts: number; verdict: "BUY" | "SELL" | "HOLD" } | null;
  learning: { status: "demo" | "default" | "learned"; samples: number; min_samples: number; beta: number; message: string; direction_hit_pct?: number | null };
  max_shift_pts: number;
  method: string;
}

export type IntradayVerdict = "BUY" | "SELL" | "WAIT" | "NO TRADE";

export interface IntradayLeg {
  timeframe: Timeframe;
  lean: -1 | 0 | 1;
  basis: "model" | "rules" | "none";
  up_pct: number | null;
  score: number | null;
  signal: SignalLabel | null;
  horizon_label: string | null;
}

export interface IntradayCard {
  instrument: Instrument;
  quote: Quote | null;
  price: number | null;
  verdict: IntradayVerdict;
  reason: string;
  since: string | null;
  since_known: boolean;
  last_candle_time: string | null;
  entry: IntradayLeg;
  trend: IntradayLeg;
  levels: { entry_low: number; entry_high: number; target: number; stop: number; risk_reward: number; basis: string } | null;
  session_vwap: number | null;
  supertrend: { dir: number | null; line: number | null };
  atr: number | null;
  support: number | null;
  resistance: number | null;
  strategies_long: string[];
  strategies_short: string[];
  reliability: {
    buy_hit_pct: number | null;
    buy_calls: number | null;
    sell_hit_pct: number | null;
    sell_calls: number | null;
    skill_pct: number | null;
    base_up_pct: number | null;
  };
  risks: string[];
  chart?: {
    candles: CandleData[];
    forming_candle: CandleData | null;
    series: Record<string, Point[]>;
    markers: SignalMarker[];
  };
  error?: string;
}

export interface IntradayDesk {
  generated_at: string;
  source: "upstox" | "demo";
  session: "open" | "closed" | "pre_open";
  entry_timeframe: Timeframe;
  trend_timeframe: Timeframe;
  indices: IntradayCard[];
  unavailable_indices: string[];
  method: string;
  stocks?: { buy: IntradayCard[]; sell: IntradayCard[]; waiting: number; scanned: number };
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
  trades?: { buy: TradeStats; sell: TradeStats; rules: string };
  open_trade?: OpenTrade | null;
  latest_exit?: ClosedTrade | null;
  exits?: ClosedTrade[];
}

export interface TradeStats {
  count: number;
  wins: number;
  win_rate: number | null;
  avg_pnl_pct: number | null;
  total_pnl_pct: number | null;
}

interface TradeBase {
  side: "buy" | "sell";
  entry_time: string;
  entry_price: number;
  stop: number;
  target: number;
  bars_held: number;
  pnl_pct: number;
}

export interface ClosedTrade extends TradeBase {
  exit_time: string;
  exit_price: number;
  reason: "stop" | "target" | "reversal" | "opposite";
  reason_text: string;
}

export interface OpenTrade extends TradeBase {
  last_close: number;
  status: "holding" | "weakening";
  warnings: string[];
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
  kind: "signal" | "level" | "fast" | "auth" | "exit" | "weak" | "pattern" | "strategy" | "intraday" | "test" | "news";
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
