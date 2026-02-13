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
  avg_duration: string;
}

export interface ChartFile {
  filename: string;
  symbol: string;
  modified: string;
}

export interface LogLine {
  line: string;
  level: string;
  timestamp: string;
}

export interface JournalEntry {
  timestamp: string;
  prediction: string;
  current_price: number;
  current_pnl: number;
  confidence?: number;
  reasoning?: string;
  symbol?: string;
}

export interface WSEvent {
  type: string;
  data: Record<string, unknown>;
}

export type TabId = 'dashboard' | 'charts' | 'trades' | 'logs' | 'settings' | 'journal';
