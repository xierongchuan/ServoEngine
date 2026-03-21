export interface Trade {
  symbol: string;
  side: 'LONG' | 'SHORT';
  entry_price: number;
  amount: number;
  leverage: number;
  open_time: string;
  status: 'open' | 'closed';
  last_pnl: number;
  current_price: number;
  max_pnl: number;
  min_pnl: number;
  close_time?: string;
  reason?: string;
  net_pnl?: number;
  estimated_total_fees?: number;
  estimated_entry_fee?: number;
  fee_rate_used?: number;
}

export interface DashboardData {
  active_trades: Trade[];
  strategy: string;
  symbols: string[];
  config_summary: Record<string, string | number>;
}

export interface TradeStats {
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  total_net_pnl: number;
  total_fees: number;
  avg_duration: string;
}

export interface ChartFile {
  filename: string;
  modified: number;
  size: number;
}

export interface LogLine {
  line: string;
  level: string;
  timestamp: string;
}

export interface IndicatorStatus {
  name: string;
  weight: number;
  ok: boolean;
  value: string;
  detail: string;
}

export interface JournalEntry {
  time?: string;
  action?: string;
  confidence?: number;
  score?: number;
  confirmations?: number;
  price?: number;
  sl?: number;
  tp?: number;
  pnl?: string;
  reason?: string;
  indicators_status?: IndicatorStatus[];
}

export interface JournalSymbolStats {
  entry_count: number;
  action_distribution: { buy: number; sell: number; hold: number; close: number };
  avg_confidence: number;
  last_action_time: string | null;
  has_active_plan: boolean;
  in_cooldown: boolean;
  cooldown_remaining_hours: number;
  position_age_hours: number | null;
  last_close_time: string | null;
}

export interface JournalStats {
  total_entries: number;
  active_plans_count: number;
  avg_confidence: number;
  symbols: Record<string, JournalSymbolStats>;
}

export interface WSEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface CandleData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SeriesPoint {
  time: number;
  value: number;
}

export interface PositionData {
  side: 'LONG' | 'SHORT';
  entry_price: number;
  sl: number;
  tp: number;
  pnl: number;
  leverage: number;
}

export interface ChartData {
  symbol: string;
  range: string;
  interval: string;
  candles: CandleData[];
  indicators: Record<string, SeriesPoint[]>;
  position: PositionData | null;
  available_ranges: string[];
}

export type TabId = 'dashboard' | 'charts' | 'trades' | 'logs' | 'settings' | 'journal';
