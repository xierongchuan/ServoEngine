import type { DashboardData, Trade, TradeStats, ChartFile, JournalEntry } from './types';

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
    throw new Error(`API error: ${res.status} ${res.statusText}`);
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

export function getConfig() {
  return fetchAPI<Record<string, unknown>>('/api/config');
}

export function updateConfig(data: Record<string, unknown>) {
  return fetchAPI<{ status: string }>('/api/config', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function getJournal(symbol?: string) {
  const path = symbol ? `/api/journal/${encodeURIComponent(symbol)}` : '/api/journal';
  return fetchAPI<JournalEntry[]>(path);
}
