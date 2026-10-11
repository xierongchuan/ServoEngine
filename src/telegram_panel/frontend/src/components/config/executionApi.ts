export interface TradingSettings {
  account_id: string;
  exchange: 'bingx' | 'mexc';
  market_type: 'perpetual' | 'spot';
  execution_mode: 'read_only' | 'demo' | 'live';
  margin_mode: 'isolated';
  position_mode: 'hedge' | 'one_way';
  settle_asset: 'USDT';
  grid_mode: 'inventory' | 'hedged';
  spot: {
    quote_budget_usdt: number;
    allow_local_protection: boolean;
  };
  protection: {
    trigger_price: 'last' | 'mark';
    failure_policy: 'close' | 'pause';
    verification_timeout_seconds: number;
    recovery_attempts: number;
  };
  market_data: {
    max_age_seconds: number;
    reconcile_interval_seconds: number;
    history_limit: number;
  };
}

// Общий fetchAPI не экспортирован; модуль локален редактору конфигурации.
async function executionRequest<T>(options?: RequestInit): Promise<T> {
  const initData = window.Telegram?.WebApp?.initData || '';
  const hash = window.location.hash.substring(1);
  const hashParams = new URLSearchParams(hash.includes('?') ? hash.split('?')[1] : hash);
  const token = hashParams.get('token') || new URLSearchParams(window.location.search).get('token') || '';
  const response = await fetch(`${import.meta.env.VITE_API_URL || window.location.origin}/api/config/execution`, {
    cache: 'no-store',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token && !initData ? { 'X-Web-Token': token } : { 'X-Telegram-Init-Data': initData }),
    },
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error(token && !initData
      ? 'Ссылка истекла или отозвана. Получите новую через /weblink.'
      : 'Сессия Telegram истекла. Откройте панель заново через бота.');
    if (response.status === 403) throw new Error('У вас нет доступа к настройкам исполнения');
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Не удалось сохранить настройки (${response.status})`);
  }
  return response.json();
}

export function getExecutionSettings() {
  return executionRequest<TradingSettings>();
}

export function updateExecutionSettings(settings: TradingSettings) {
  return executionRequest<{ status: string; execution: TradingSettings; needs_restart: boolean }>({
    method: 'PUT', body: JSON.stringify(settings),
  });
}
