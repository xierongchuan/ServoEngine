import { useEffect, useId, useState } from 'react';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Spinner } from '../Spinner';
import { getExecutionSettings, updateExecutionSettings, type TradingSettings } from './executionApi';
import { executionStartWarning, validateExecutionForm, withExecutionDefaults } from './executionForm';

const inputClass = 'w-full min-h-[44px] rounded-lg bg-tg-bg text-tg-text px-3 py-2 text-base focus-visible:outline focus-visible:outline-2 focus-visible:outline-tg-button disabled:opacity-60';

function Choice<T extends string>({ label, value, options, onChange, error }: {
  label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (value: T) => void; error?: string;
}) {
  const id = useId();
  return (
    <label className="flex flex-col gap-2 text-sm text-tg-text">
      {label}
      <select className={inputClass} value={value} aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined} onChange={(event) => onChange(event.target.value as T)}>
        {options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}
      </select>
      {error && <span id={`${id}-error`} className="font-medium">Ошибка: {error}</span>}
    </label>
  );
}

function NumberField({ label, value, min, max, step = 1, onChange, error }: {
  label: string; value: number; min: number; max?: number; step?: number | 'any'; onChange: (value: number) => void; error?: string;
}) {
  const id = useId();
  return (
    <label className="flex flex-col gap-2 text-sm text-tg-text">
      {label}
      <input className={inputClass} type="number" required min={min} max={max} step={step}
        aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined}
        value={Number.isNaN(value) ? '' : value} onChange={(event) => onChange(event.target.valueAsNumber)} />
      {error && <span id={`${id}-error`} className="font-medium">Ошибка: {error}</span>}
    </label>
  );
}

export function ExecutionSettings() {
  const accountFieldId = useId();
  const localRiskId = useId();
  const [saved, setSaved] = useState<TradingSettings | null>(null);
  const [draft, setDraft] = useState<TradingSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const settings = withExecutionDefaults(await getExecutionSettings());
      setSaved(settings);
      setDraft(settings);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить исполнение');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  if (loading) return <div className="flex justify-center p-4" role="status" aria-label="Загрузка настроек исполнения"><Spinner /></div>;
  if (!draft || !saved) return <Card title="Исполнение">
    <p role="alert" className="text-sm text-tg-text">{error}</p>
    <Button variant="secondary" onClick={load}>Повторить загрузку</Button>
  </Card>;

  const changed = JSON.stringify(saved) !== JSON.stringify(draft);
  const fieldErrors = validateExecutionForm(draft);
  const startWarning = executionStartWarning(draft);
  const chooseMarket = (market: TradingSettings['market_type']) => {
    setDraft({ ...draft, market_type: market,
      position_mode: market === 'spot' ? 'one_way' : 'hedge',
      grid_mode: market === 'spot' ? 'inventory' : 'hedged',
      protection: { ...draft.protection, trigger_price: market === 'spot' ? 'last' : 'mark' },
    });
    setNotice(null);
  };
  const save = async () => {
    if (Object.keys(fieldErrors).length > 0) {
      setError('Исправьте отмеченные поля перед сохранением');
      if (Object.keys(fieldErrors).some((key) => key.includes('.'))) setAdvancedOpen(true);
      return;
    }
    if (draft.execution_mode === 'live' && (saved.execution_mode !== 'live' || saved.exchange !== draft.exchange || saved.market_type !== draft.market_type || saved.account_id !== draft.account_id)) {
      if (!confirm('Режим LIVE разрешает реальные операции с капиталом после явного запуска runtime. Сохранить настройки?')) return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await updateExecutionSettings(withExecutionDefaults(draft));
      const settings = withExecutionDefaults(result.execution);
      setSaved(settings);
      setDraft(settings);
      setNotice(result.needs_restart
        ? 'Сохранено. Применение — только при новом запуске runtime. Автоматического запуска, закрытия позиций или переключения счёта нет.'
        : 'Настройки исполнения не изменились.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось сохранить исполнение');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form noValidate className="flex flex-col gap-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <Card title="Биржа и исполнение" badge="Нужен новый запуск">
        <p className="text-sm text-tg-text leading-relaxed">
          Любое изменение исполнения, включая бюджет, защиту и рыночные данные, возможно только при остановленном runtime без открытого управляемого состояния.
          Недоступный или устаревший статус блокирует смену. Cross не поддерживается.
        </p>
        <fieldset disabled={saving} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <label className="flex flex-col gap-2 text-sm text-tg-text sm:col-span-2">
            Идентификатор аккаунта (не API-ключ)
            <input className={inputClass} type="text" value={draft.account_id} required maxLength={64}
              autoCapitalize="none" autoCorrect="off" spellCheck={false}
              aria-invalid={!!fieldErrors.account_id} aria-describedby={`${accountFieldId}-hint${fieldErrors.account_id ? ` ${accountFieldId}-error` : ''}`}
              onChange={(event) => setDraft({ ...draft, account_id: event.target.value })} />
            <span id={`${accountFieldId}-hint`} className="break-all">Namespace: {draft.account_id}:{draft.exchange}:{draft.execution_mode}. Ротация ключей не меняет этот идентификатор.</span>
            {fieldErrors.account_id && <span id={`${accountFieldId}-error`} className="font-medium">Ошибка: {fieldErrors.account_id}</span>}
          </label>
          <Choice label="Биржа" value={draft.exchange} error={fieldErrors.exchange} options={[[ 'bingx', 'BingX' ], [ 'mexc', 'MEXC' ]]}
            onChange={(exchange) => {
              setDraft({ ...draft, exchange, market_type: 'perpetual', position_mode: 'hedge', grid_mode: 'hedged',
                execution_mode: exchange === 'mexc' && draft.execution_mode === 'demo' ? 'read_only' : draft.execution_mode });
              setNotice(null);
            }} />
          <Choice label="Рынок" value={draft.market_type} error={fieldErrors.market_type}
            options={draft.exchange === 'mexc' ? [['perpetual', 'USDT-M Perpetual'], ['spot', 'Spot — WS + REST, локальная защита']] : [['perpetual', 'Perpetual']]}
            onChange={chooseMarket} />
          <Choice label="Режим исполнения" value={draft.execution_mode} error={fieldErrors.execution_mode}
            options={draft.exchange === 'mexc' ? [['read_only', 'Только чтение (production API)'], ['live', 'LIVE — реальные операции']] : [['read_only', 'Только чтение'], ['demo', 'DEMO — BingX VST'], ['live', 'LIVE — реальные операции']]}
            onChange={(execution_mode) => setDraft({ ...draft, execution_mode })} />
          <Choice label="Режим позиций" value={draft.position_mode} error={fieldErrors.position_mode}
            options={draft.market_type === 'spot' ? [['one_way', 'Односторонний (Spot)']] : [['hedge', 'Hedge'], ['one_way', 'One-way']]}
            onChange={(position_mode) => setDraft({ ...draft, position_mode, grid_mode: position_mode === 'one_way' ? 'inventory' : draft.grid_mode })} />
          <Choice label="Режим сетки" value={draft.grid_mode} error={fieldErrors.grid_mode}
            options={draft.market_type === 'spot' || draft.position_mode === 'one_way' ? [['inventory', 'Inventory']] : [['inventory', 'Inventory'], ['hedged', 'Hedged']]}
            onChange={(grid_mode) => setDraft({ ...draft, grid_mode })} />
          <div className="text-sm text-tg-text self-center">Маржа: Isolated · Расчёт: USDT</div>
        </fieldset>
        {draft.market_type === 'perpetual' && <p role="note" className="text-sm text-tg-text font-semibold leading-relaxed">Новые входы фьючерсного GRID отключены. GTC LIMIT: защита частичных исполнений и изменения объёма не доказана. Для BingX реализован отдельный путь: локальные уровни → одна MARKET-позиция с биржевыми SL/TP в исходном запросе, без локального SL; он ещё не прошёл полную VST-проверку и тоже заблокирован. Inventory/Hedged не снимают запрет. Сверка сохранённого состояния остаётся доступна.</p>}
        <p className="text-sm text-tg-text">{draft.exchange === 'mexc'
          ? 'У MEXC нет API sandbox. Старое значение demo нормализуется в read_only; реальные операции разрешены только в явно выбранном LIVE.'
          : 'DEMO использует отдельный BingX VST endpoint. Только явно выбранный LIVE разрешает реальные операции.'}</p>
        <p className="text-sm text-tg-text">{draft.execution_mode === 'read_only'
          ? 'Только публичные рыночные данные: без private precheck и без ордеров. Изменение режима требует остановки и нового запуска всего runtime.'
          : draft.execution_mode === 'demo'
            ? 'BingX VST: отдельный демо-счёт и endpoint, торговля не на реальном счёте.'
            : 'LIVE разрешает реальные операции. Запуск и сверка счёта выполняются отдельно; сохранение не запускает торговлю.'}</p>
        {draft.market_type === 'spot' && <p className="text-sm text-tg-text">MEXC Spot: публичный protobuf WebSocket и REST-восстановление; таймфреймы без WS обслуживаются точным REST polling. Spot не поддерживает hedge, плечо и mark-price защиту. LIVE требует отдельной проверки запуска; ручные операции на бирже не считаются собственными лотами бота.</p>}
        {draft.execution_mode === 'live' && <p className="text-sm text-tg-text font-semibold">Внимание: LIVE использует реальный счёт и может привести к потере капитала.</p>}
      </Card>
      {draft.market_type === 'spot' && <Card title="Spot: общий бюджет аккаунта">
        <p className="text-sm text-tg-text leading-relaxed">TOTAL, не бюджет каждого worker. Общий durable quote-pool атомарно выделяет свободные средства владельцу перед покупкой с учётом резервов остальных instances. Весь бюджет не выдаётся каждому процессу; ручные монеты не импортируются, изменение уже выделенного бюджета требует сверки ledger.</p>
        <fieldset disabled={saving} className="flex flex-col gap-4">
          <NumberField label="Общий выделенный бюджет аккаунта, USDT (0 — покупки отключены)"
            value={draft.spot.quote_budget_usdt} min={0} step="any" error={fieldErrors['spot.quote_budget_usdt']}
            onChange={(quote_budget_usdt) => setDraft({ ...draft, spot: { ...draft.spot, quote_budget_usdt } })} />
          <label className="flex items-start gap-3 min-h-[44px] py-2 text-sm text-tg-text cursor-pointer">
            <input type="checkbox" className="mt-1 h-5 w-5 shrink-0 accent-tg-button focus-visible:outline focus-visible:outline-2 focus-visible:outline-tg-button"
              checked={draft.spot.allow_local_protection} aria-invalid={!!fieldErrors['spot.allow_local_protection']}
              aria-describedby={`${localRiskId}-hint${fieldErrors['spot.allow_local_protection'] ? ` ${localRiskId}-error` : ''}`}
              onChange={(event) => setDraft({ ...draft, spot: { ...draft.spot, allow_local_protection: event.target.checked } })} />
            Я явно принимаю риск только локальной SL/TP для LIVE Spot
          </label>
          {fieldErrors['spot.allow_local_protection'] && <p id={`${localRiskId}-error`} className="text-sm text-tg-text font-medium">Ошибка: {fieldErrors['spot.allow_local_protection']}</p>}
        </fieldset>
        <p id={`${localRiskId}-hint`} className="text-sm text-tg-text font-semibold">Биржевых SL/TP нет. При остановке процесса, потере сети или недоступности API лоты остаются без защиты. Локальная регистрация SL — не подтверждение биржевого ордера.</p>
        {startWarning && <p role="status" className="text-sm text-tg-text">{startWarning}</p>}
      </Card>}
      <details open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)} className="rounded-2xl bg-tg-section-bg p-4">
        <summary className="cursor-pointer min-h-[44px] text-tg-text font-medium">Защита и рыночные данные</summary>
        <fieldset disabled={saving} className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-3">
          <Choice label="Цена срабатывания защиты" value={draft.protection.trigger_price} error={fieldErrors['protection.trigger_price']}
            options={draft.market_type === 'spot' ? [['last', 'Последняя цена']] : [['last', 'Последняя цена'], ['mark', 'Mark price']]}
            onChange={(trigger_price) => setDraft({ ...draft, protection: { ...draft.protection, trigger_price } })} />
          <Choice label="При отказе защиты" value={draft.protection.failure_policy} error={fieldErrors['protection.failure_policy']} options={[[ 'pause', 'Приостановить' ], [ 'close', 'Закрыть позицию' ]]}
            onChange={(failure_policy) => setDraft({ ...draft, protection: { ...draft.protection, failure_policy } })} />
          <NumberField label="Проверка защиты, секунд (1–300)" value={draft.protection.verification_timeout_seconds} error={fieldErrors['protection.verification_timeout_seconds']} min={1} max={300}
            onChange={(verification_timeout_seconds) => setDraft({ ...draft, protection: { ...draft.protection, verification_timeout_seconds } })} />
          <NumberField label="Попытки восстановления (0–10)" value={draft.protection.recovery_attempts} error={fieldErrors['protection.recovery_attempts']} min={0} max={10}
            onChange={(recovery_attempts) => setDraft({ ...draft, protection: { ...draft.protection, recovery_attempts } })} />
          <NumberField label="Максимальный возраст данных, секунд (1–300)" value={draft.market_data.max_age_seconds} error={fieldErrors['market_data.max_age_seconds']} min={1} max={300}
            onChange={(max_age_seconds) => setDraft({ ...draft, market_data: { ...draft.market_data, max_age_seconds } })} />
          <NumberField label="Интервал сверки, секунд (1–3600)" value={draft.market_data.reconcile_interval_seconds} error={fieldErrors['market_data.reconcile_interval_seconds']} min={1} max={3600}
            onChange={(reconcile_interval_seconds) => setDraft({ ...draft, market_data: { ...draft.market_data, reconcile_interval_seconds } })} />
          <NumberField label="История, свечей (50–2000)" value={draft.market_data.history_limit} error={fieldErrors['market_data.history_limit']} min={50} max={2000}
            onChange={(history_limit) => setDraft({ ...draft, market_data: { ...draft.market_data, history_limit } })} />
        </fieldset>
        <p className="text-sm text-tg-text mt-4">Весь execution фиксируется на процесс. Изменения этих параметров тоже требуют остановки и нового запуска; hot-reload их не применяет.</p>
      </details>
      {error && <p role="alert" className="text-sm text-tg-text font-medium">Ошибка: {error}</p>}
      {notice && <p role="status" className="text-sm text-tg-text">{notice}</p>}
      <div className="flex flex-wrap gap-3">
        <Button type="submit" disabled={saving || !changed} isLoading={saving}>Сохранить исполнение</Button>
        <Button type="button" variant="secondary" disabled={saving || !changed} onClick={() => { setDraft(saved); setError(null); setNotice(null); }}>Отменить изменения</Button>
      </div>
    </form>
  );
}
