import type { DashboardData, Trade, TradeStats, ChartFile, ChartData, JournalEntry, JournalStats } from './types';

const BASE_URL = import.meta.env.VITE_API_URL || window.location.origin;

function getInitData(): string {
  // X-Telegram-Init-Data: авторизация через Telegram Mini App
  return window.Telegram?.WebApp?.initData || '';
}

function getWebToken(): string {
  // X-Web-Token: авторизация через ссылку /weblink (браузер)
  // DEBUG: log full URL info
  console.log('[API] URL debug:', {
    href: window.location.href,
    origin: window.location.origin,
    pathname: window.location.pathname,
    search: window.location.search,
    hash: window.location.hash,
  });

  // Проверяем сначала hash (#/auth?token=xxx), затем search (?token=xxx)
  const hash = window.location.hash;
  if (hash && hash.includes('token=')) {
    const hashParams = new URLSearchParams(hash.substring(1)); // убираем #
    const token = hashParams.get('token');
    console.log('[API] getWebToken from hash:', token ? token.substring(0,8)+'...' : null);
    return token || '';
  }
  const urlParams = new URLSearchParams(window.location.search);
  const token = urlParams.get('token');
  console.log('[API] getWebToken from search:', token ? token.substring(0,8)+'...' : null);
  return token || '';
}

function isWebTokenMode(): boolean {
  // If there's a token in URL and no Telegram initData
  const token = getWebToken();
  const initData = getInitData();
  const result = !!token && !initData;
  console.log('[API] isWebTokenMode:', {
    hasToken: !!token,
    tokenPreview: token ? token.substring(0,8)+'...' : null,
    hasInitData: !!initData,
    result
  });
  return result;
}

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getWebToken(); // X-Web-Token
  const initData = getInitData(); // X-Telegram-Init-Data
  const useToken = isWebTokenMode();

  // Если useToken=true: отправляем X-Web-Token (браузер через /weblink)
  // Если useToken=false: отправляем X-Telegram-Init-Data (Telegram Mini App)

  // DEBUG: log auth details
  console.log('[API] fetchAPI:', path, { token: token ? token.substring(0,8)+'...' : null, initData: initData ? 'present' : 'none', useToken });

  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(useToken
        ? { 'X-Web-Token': token || '' }
        : { 'X-Telegram-Init-Data': initData }
      ),
      ...options?.headers,
    },
  });
  if (!res.ok) {
    if (res.status === 401) {
      if (useToken) {
        throw new Error('Ссылка истекла или недействительна. Используйте /weblink для новой ссылки.');
      }
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
  const token = getWebToken();
  const initData = getInitData();
  const useToken = isWebTokenMode();

  const auth = useToken ? token : initData;
  const authParam = useToken ? 'token' : 'auth';
  return `${BASE_URL}/api/charts/${encodeURIComponent(filename)}?${authParam}=${encodeURIComponent(auth)}`;
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
  strategy_instances?: StrategyInstance[];
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

export interface BaseConfig {
  ai?: {
    provider?: string;
    model?: string;
    temperature?: number;
    max_tokens?: number;
    reasoning?: { enabled: boolean; effort: string; exclude: boolean };
    [key: string]: unknown;
  };
  exchange?: { fees?: Record<string, { maker: number; taker: number }> };
  [key: string]: unknown;
}

export interface StrategyInfo {
  name: string;
  description: string;
  preset: {
    timeframe?: string;
    leverage?: number;
    loop_interval?: number;
    sl_percent?: number;
    tp_percent?: number;
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
  [key: string]: unknown;  // Allow any keys for dynamic profile structure
}

export interface ProfilesResponse {
  profiles: Record<string, ProfileInfo>;
  available: string[];
  profile_strategies?: Record<string, string | null>;
  compatible_by_strategy?: Record<string, string[]>;
}

export interface StrategyInstance {
  id: string;
  symbol: string;
  strategy: string;
  profile: string;
  enabled: boolean;
}

export interface SymbolProfilesResponse {
  symbol_profiles: Record<string, string>;
  instance_profiles?: Record<string, string>;
  strategy_instances?: StrategyInstance[];
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

export function getBaseConfig() {
  return fetchAPI<BaseConfig>('/api/config/base');
}

export function updateBaseConfig(data: Partial<BaseConfig>) {
  return fetchAPI<{ status: string; config: BaseConfig }>('/api/config/base', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function addSymbol(symbol: string, exchange: string = 'bingx') {
  return fetchAPI<{ status: string; symbols: Record<string, string[]> }>('/api/config/active/symbol', {
    method: 'POST',
    body: JSON.stringify({ symbol, exchange }),
  });
}

export function removeSymbol(symbol: string, exchange: string = 'bingx') {
  return fetchAPI<{ status: string; symbols: Record<string, string[]> }>(`/api/config/active/symbol/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}`, {
    method: 'DELETE',
  });
}

export function getStrategyInstances() {
  return fetchAPI<{ strategy_instances: StrategyInstance[]; available: string[] }>('/api/config/strategy-instances');
}

export function createStrategyInstance(data: Partial<StrategyInstance>) {
  return fetchAPI<{ status: string; instance: StrategyInstance; config: ActiveConfig }>('/api/config/strategy-instances', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateStrategyInstance(instanceId: string, data: Partial<StrategyInstance>) {
  return fetchAPI<{ status: string; instances: StrategyInstance[]; config: ActiveConfig }>(`/api/config/strategy-instances/${encodeURIComponent(instanceId)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function deleteStrategyInstance(instanceId: string) {
  return fetchAPI<{ status: string; instances: StrategyInstance[]; config: ActiveConfig }>(`/api/config/strategy-instances/${encodeURIComponent(instanceId)}`, {
    method: 'DELETE',
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

export function setSymbolProfile(symbol: string, profile: string, instanceId?: string) {
  return fetchAPI<{ status: string; symbol: string; profile: string }>(`/api/config/symbol-profiles/${encodeURIComponent(symbol)}`, {
    method: 'PUT',
    body: JSON.stringify({ profile, instance_id: instanceId }),
  });
}

export interface ProfileUsageResponse {
  profile: string;
  symbols: string[];
  instances?: { id: string; symbol: string; strategy: string; enabled: boolean }[];
  isUsed: boolean;
  usageCount: number;
}

export function getProfileUsage(profileName: string) {
  return fetchAPI<ProfileUsageResponse>(`/api/config/profiles/${encodeURIComponent(profileName)}/usage`);
}

export interface CloneProfileResponse {
  status: string;
  profile: string;
  source: string;
}

export function cloneProfile(sourceName: string, newName: string) {
  return fetchAPI<CloneProfileResponse>(`/api/config/profiles/${encodeURIComponent(sourceName)}/clone`, {
    method: 'POST',
    body: JSON.stringify({ new_name: newName }),
  });
}

export interface ProfileSchema {
  schemas: Record<string, Record<string, string[]>>;
  default: Record<string, string[]>;
}

export function getProfileSchema() {
  return fetchAPI<ProfileSchema>('/api/config/profiles/schema');
}

export interface AutoCreateProfileRequest {
  name?: string;
  settings: Record<string, unknown>;
  strategy: string;
  switch_from_default?: boolean;
}

export interface AutoCreateProfileResponse {
  status: string;
  profile: string;
  switchedSymbols: string[];
  previouslyUsingDefault: boolean;
  isUpdate: boolean;
}

export function autoCreateProfile(data: AutoCreateProfileRequest) {
  return fetchAPI<AutoCreateProfileResponse>('/api/config/profiles/auto-create', {
    method: 'POST',
    body: JSON.stringify(data),
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

export function closePosition(symbol: string) {
  return fetchAPI<{ status: string }>(`/api/trades/close/${encodeURIComponent(symbol)}`, {
    method: 'POST',
  });
}

// ============================================================================
// Runtime Management API
// ============================================================================

export interface RuntimeStatus {
  state: 'running' | 'stopped' | 'starting' | 'stopping' | 'restarting' | 'crashed' | 'unavailable' | string;
  control_enabled: boolean;
  supervisor_pid: number | null;
  runtime_pid: number | null;
  started_at: string | null;
  stopped_at: string | null;
  last_exit_code: number | null;
  last_error: string | null;
  last_command_id: string | null;
  last_command_action: string | null;
  last_command_at: string | null;
  updated_at: string | null;
  updated_at_ts: number | null;
  stale: boolean;
  command_path: string;
  status_path: string;
}

export interface RuntimeCommandResponse {
  status: 'queued';
  command: {
    id: string;
    action: 'start' | 'stop' | 'restart';
    requested_by: string;
    requested_at: string;
    requested_at_ts: number;
  };
  runtime_status: RuntimeStatus;
}

export interface SymbolRuntimeCommandResponse {
  status: 'queued';
  command: {
    id: string;
    action: 'start' | 'stop' | 'restart';
    symbol?: string;
    instance_id?: string;
    instance_ids?: string[];
    requested_by: string;
    requested_at: string;
    requested_at_ts: number;
    reason?: string;
  };
}

export function getRuntimeStatus() {
  return fetchAPI<RuntimeStatus>('/api/runtime/status');
}

export function startRuntime() {
  return fetchAPI<RuntimeCommandResponse>('/api/runtime/start', {
    method: 'POST',
  });
}

export function stopRuntime() {
  return fetchAPI<RuntimeCommandResponse>('/api/runtime/stop', {
    method: 'POST',
  });
}

export function restartRuntime() {
  return fetchAPI<RuntimeCommandResponse>('/api/runtime/restart', {
    method: 'POST',
  });
}

export function commandSymbolRuntime(
  action: 'start' | 'stop' | 'restart',
  data: { symbol?: string; instance_id?: string; instance_ids?: string[]; reason?: string }
) {
  return fetchAPI<SymbolRuntimeCommandResponse>(`/api/runtime/symbol/${action}`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function restartSymbolRuntime(instanceId: string, symbol?: string, reason = 'manual_restart') {
  return commandSymbolRuntime('restart', { instance_id: instanceId, symbol, reason });
}

export function startSymbolRuntime(instanceId: string, symbol?: string, reason = 'manual_start') {
  return commandSymbolRuntime('start', { instance_id: instanceId, symbol, reason });
}

export function stopSymbolRuntime(instanceId: string, symbol?: string, reason = 'manual_stop') {
  return commandSymbolRuntime('stop', { instance_id: instanceId, symbol, reason });
}

export function restartSymbolRuntimeBatch(instanceIds: string[], reason = 'batch_restart') {
  return commandSymbolRuntime('restart', { instance_ids: instanceIds, reason });
}
