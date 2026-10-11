import type { TradingSettings } from './executionApi';

export type ExecutionInput = Omit<Partial<TradingSettings>, 'protection' | 'market_data' | 'spot'> & {
  spot?: Partial<TradingSettings['spot']>;
  protection?: Partial<TradingSettings['protection']>;
  market_data?: Partial<TradingSettings['market_data']>;
};
export type ExecutionErrors = Record<string, string>;

export function withExecutionDefaults(input: ExecutionInput): TradingSettings {
  const spot = input.market_type === 'spot';
  const result: TradingSettings = {
    account_id: 'default', exchange: 'bingx', market_type: 'perpetual', execution_mode: 'read_only', margin_mode: 'isolated',
    position_mode: spot ? 'one_way' : 'hedge', settle_asset: 'USDT', grid_mode: spot ? 'inventory' : 'hedged',
    ...input,
    spot: { quote_budget_usdt: 0, allow_local_protection: false, ...input.spot },
    protection: { trigger_price: spot ? 'last' : 'mark', failure_policy: 'pause', verification_timeout_seconds: 10, recovery_attempts: 3, ...input.protection },
    market_data: { max_age_seconds: 30, reconcile_interval_seconds: 60, history_limit: 500, ...input.market_data },
  };
  if (result.exchange === 'mexc' && result.execution_mode === 'demo') result.execution_mode = 'read_only';
  return result;
}

export function validateExecutionForm(settings: TradingSettings): ExecutionErrors {
  const errors: ExecutionErrors = {};
  if (typeof settings.account_id !== 'string' || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(settings.account_id)) {
    errors.account_id = 'Введите 1–64 символа a-z, 0-9, _ или -; первый — буква или цифра';
  }
  if (typeof settings.spot.quote_budget_usdt !== 'number' || !Number.isFinite(settings.spot.quote_budget_usdt) || settings.spot.quote_budget_usdt < 0) {
    errors['spot.quote_budget_usdt'] = 'Введите неотрицательный конечный общий бюджет аккаунта';
  }
  if (typeof settings.spot.allow_local_protection !== 'boolean') errors['spot.allow_local_protection'] = 'Требуется явное согласие true или false';
  const choices: Record<string, readonly string[]> = {
    exchange: ['bingx', 'mexc'], market_type: ['perpetual', 'spot'], execution_mode: ['read_only', 'demo', 'live'],
    margin_mode: ['isolated'], position_mode: ['hedge', 'one_way'], settle_asset: ['USDT'], grid_mode: ['inventory', 'hedged'],
  };
  for (const [key, allowed] of Object.entries(choices)) {
    if (!allowed.includes(String(settings[key as keyof TradingSettings]))) errors[key] = `Допустимо: ${allowed.join(', ')}`;
  }
  if (settings.exchange === 'bingx' && settings.market_type !== 'perpetual') errors.market_type = 'BingX поддерживает только perpetual';
  if (settings.market_type === 'spot') {
    if (settings.position_mode !== 'one_way') errors.position_mode = 'Для Spot нужен односторонний режим';
    if (settings.grid_mode !== 'inventory') errors.grid_mode = 'Для Spot допустим только inventory';
    if (settings.protection.trigger_price !== 'last') errors['protection.trigger_price'] = 'Для Spot используется последняя цена';
  }
  if (settings.grid_mode === 'hedged' && settings.position_mode !== 'hedge') errors.grid_mode = 'Hedged требует режима позиций hedge';
  if (!['last', 'mark'].includes(settings.protection.trigger_price)) errors['protection.trigger_price'] = 'Допустимо last или mark';
  if (!['pause', 'close'].includes(settings.protection.failure_policy)) errors['protection.failure_policy'] = 'Допустимо pause или close';
  const numbers: [string, number, number, number][] = [
    ['protection.verification_timeout_seconds', settings.protection.verification_timeout_seconds, 1, 300],
    ['protection.recovery_attempts', settings.protection.recovery_attempts, 0, 10],
    ['market_data.max_age_seconds', settings.market_data.max_age_seconds, 1, 300],
    ['market_data.reconcile_interval_seconds', settings.market_data.reconcile_interval_seconds, 1, 3600],
    ['market_data.history_limit', settings.market_data.history_limit, 50, 2000],
  ];
  for (const [field, value, min, max] of numbers) {
    if (!Number.isInteger(value) || value < min || value > max) errors[field] = `Введите целое число от ${min} до ${max}`;
  }
  return errors;
}

export function executionStartWarning(settings: TradingSettings): string | null {
  if (settings.market_type !== 'spot' || settings.execution_mode !== 'live') return null;
  if (settings.spot.quote_budget_usdt <= 0) return 'Настройки можно сохранить, но LIVE Spot не запустится: задайте общий бюджет аккаунта больше 0 USDT.';
  if (!settings.spot.allow_local_protection) return 'Настройки можно сохранить, но LIVE Spot не запустится без явного принятия риска локальной защиты.';
  return null;
}
