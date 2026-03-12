import type { DashboardData, Trade, TradeStats, ChartFile, ChartData, JournalEntry, JournalStats } from './types';

const BASE_URL = import.meta.env.VITE_API_URL || window.location.origin;

function getInitData(): string {
  return window.Telegram?.WebApp?.initData || '';
}

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': getInitData(),
      ...options?.headers,
    },
  });
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error('Откройте панель через Telegram Mini App');
    }
    if (res.status === 403) {
      throw new Error('У вас нет доступа к этой панели');
    }
    // Try to extract error detail from response body
    let detail = '';
    try {
      const body = await res.json();
      if (body.detail) {
        detail = `: ${body.detail}`;
        if (body.type) detail += ` (${body.type})`;
      }
    } catch {
      // Response body is not JSON
    }
    throw new Error(`API error: ${res.status}${detail}`);
  }
  return res.json();
}

export function getDashboard() {
  return fetchAPI<DashboardData>('/api/dashboard');
}

export function getActiveTrades() {
  return fetchAPI<Trade[]>('/api/trades/active');
}

export function getTradeHistory(limit = 50, offset = 0) {
  return fetchAPI<Trade[]>(`/api/trades/history?limit=${limit}&offset=${offset}`);
}

export function getTradeStats() {
  return fetchAPI<TradeStats>('/api/trades/stats');
}

export function getChartsList() {
  return fetchAPI<ChartFile[]>('/api/charts/list');
}

export function getChartData(symbol: string, range: string = '1D') {
  return fetchAPI<ChartData>(
    `/api/chart-data/${encodeURIComponent(symbol)}?range=${encodeURIComponent(range)}`
  );
}

export function getChartUrl(filename: string) {
  const auth = encodeURIComponent(getInitData());
  return `${BASE_URL}/api/charts/${encodeURIComponent(filename)}?auth=${auth}`;
}

export function getSystemLogs(lines = 200) {
  return fetchAPI<string[]>(`/api/logs/system?lines=${lines}`);
}

export function getSymbolLogs(symbol: string, lines = 200) {
  return fetchAPI<string[]>(`/api/logs/${encodeURIComponent(symbol)}?lines=${lines}`);
}

// ============================================================================
// Legacy Config API
// ============================================================================

export function getConfig() {
  return fetchAPI<Record<string, unknown>>('/api/config');
}

export function updateConfig(data: Record<string, unknown>) {
  return fetchAPI<{ status: string; changes?: { hot_reloadable: string[]; restart_required: string[] }; needs_restart?: boolean }>('/api/config', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function validateConfig(data: Record<string, unknown>) {
  return fetchAPI<{ valid: boolean; errors: string[]; changes: { hot_reloadable: string[]; restart_required: string[] } }>('/api/config/validate', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function getConfigMeta() {
  return fetchAPI<{ hot_reloadable: string[]; restart_required: string[]; use_new_system?: boolean }>('/api/config/meta');
}

// ============================================================================
// New Config System API
// ============================================================================

export interface ConfigSystemInfo {
  use_new_system: boolean;
  config_dir: string | null;
  config_dir_exists: boolean;
  active_json_exists: boolean;
  strategies_dir_exists: boolean;
  strategy_files: string[];
  legacy_strategies: string[];
  available_strategies: string[];
  legacy_config_path: string;
}

export interface ActiveConfig {
  strategy: string;
  symbols: Record<string, string[]>;
  symbol_profiles: Record<string, string>;
  disabled_symbols: string[];
}

export interface TradingConfig {
  position?: { size_percent?: number; min_trade_amount_usdt?: number };
  risk?: {
    min_confidence_threshold?: number;
    min_risk_reward_ratio?: number;
    take_profit_percent?: number;
    stop_loss_percent?: number;
  };
  features?: Record<string, boolean>;
  [key: string]: unknown;
}

export interface StrategyInfo {
  name: string;
  description: string;
  preset: {
    timeframe?: string;
    leverage?: number;
    loop_interval?: number;
    atr_sl_mult?: number;
    atr_tp_mult?: number;
    [key: string]: unknown;
  };
  has_ai: boolean;
}

export interface StrategiesResponse {
  strategies: Record<string, StrategyInfo>;
  available: string[];
}

export interface ProfileInfo {
  name: string;
  description: string;
  inherits: string | null;
  preset?: Record<string, unknown>;
  position?: Record<string, unknown>;
  signal_rules?: Record<string, unknown>;
}

export interface ProfilesResponse {
  profiles: Record<string, ProfileInfo>;
  available: string[];
}

export interface SymbolProfilesResponse {
  symbol_profiles: Record<string, string>;
  symbols: string[];
  disabled_symbols: string[];
}

export function getConfigSystemInfo() {
  return fetchAPI<ConfigSystemInfo>('/api/config/system');
}

export function getActiveConfig() {
  return fetchAPI<ActiveConfig>('/api/config/active');
}

export function updateActiveConfig(data: Partial<ActiveConfig>) {
  return fetchAPI<{ status: string; config: ActiveConfig }>('/api/config/active', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function getTradingConfig() {
  return fetchAPI<TradingConfig>('/api/config/trading');
}

export function updateTradingConfig(data: Partial<TradingConfig>) {
  return fetchAPI<{ status: string; config: TradingConfig }>('/api/config/trading', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function getStrategies() {
  return fetchAPI<StrategiesResponse>('/api/config/strategies');
}

export function getStrategy(name: string) {
  return fetchAPI<Record<string, unknown>>(`/api/config/strategies/${encodeURIComponent(name)}`);
}

export function updateStrategy(name: string, data: Record<string, unknown>) {
  return fetchAPI<{ status: string; strategy: Record<string, unknown> }>(`/api/config/strategies/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function getProfiles() {
  return fetchAPI<ProfilesResponse>('/api/config/profiles');
}

export function getProfile(name: string) {
  return fetchAPI<Record<string, unknown>>(`/api/config/profiles/${encodeURIComponent(name)}`);
}

export function updateProfile(name: string, data: Record<string, unknown>) {
  return fetchAPI<{ status: string; profile: Record<string, unknown> }>(`/api/config/profiles/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function deleteProfile(name: string) {
  return fetchAPI<{ status: string }>(`/api/config/profiles/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
}

export function getSymbolProfiles() {
  return fetchAPI<SymbolProfilesResponse>('/api/config/symbol-profiles');
}

export function setSymbolProfile(symbol: string, profile: string) {
  return fetchAPI<{ status: string; symbol: string; profile: string }>(`/api/config/symbol-profiles/${encodeURIComponent(symbol)}`, {
    method: 'PUT',
    body: JSON.stringify({ profile }),
  });
}

// ============================================================================
// Journal API
// ============================================================================

export function getJournal(symbol?: string) {
  const path = symbol ? `/api/journal/${encodeURIComponent(symbol)}` : '/api/journal';
  return fetchAPI<JournalEntry[]>(path);
}

export function getJournalStats() {
  return fetchAPI<JournalStats>('/api/journal/stats');
}

// ============================================================================
// Trades Management API
// ============================================================================

export function syncPositions() {
  return fetchAPI<{ status: string; removed: number; removed_symbols?: string[]; remaining: number }>('/api/trades/sync', {
    method: 'POST',
  });
}

export function disableSymbol(symbol: string) {
  return fetchAPI<{ status: string }>(`/api/trades/disable/${encodeURIComponent(symbol)}`, {
    method: 'POST',
  });
}

export function enableSymbol(symbol: string) {
  return fetchAPI<{ status: string }>(`/api/trades/enable/${encodeURIComponent(symbol)}`, {
    method: 'POST',
  });
}

export function closePosition(symbol: string) {
  return fetchAPI<{ status: string }>(`/api/trades/close/${encodeURIComponent(symbol)}`, {
    method: 'POST',
  });
}

export function getDisabledSymbols() {
  return fetchAPI<{ disabled_symbols: string[] }>('/api/trades/disabled');
}
