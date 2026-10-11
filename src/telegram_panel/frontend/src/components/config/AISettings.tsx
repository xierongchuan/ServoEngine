import { useEffect, useId, useRef, useState } from 'react';
import type { AIProvider, AISettings as Settings, ProviderStatus, AIProbeResponse, DeepSeekThinking } from '../../api/aiTypes';
import { aiErrorMessage, getAIModels, getAISettings, probeAIProvider, saveAISettings } from '../../api/ai';
import { AI_PROVIDERS, defaultAISettings, defaultDeepSeekThinking, fromAIDraft, sameAIProfile, toAIDraft, validateAIDraft, effectiveAIOverride, type AIDraft } from './aiForm';

const control = 'tg-control w-full min-w-0 min-h-11 rounded-lg px-3 py-2 text-base focus-visible:outline focus-visible:outline-2 focus-visible:outline-tg-button';
const button = 'min-h-11 cursor-pointer touch-manipulation rounded-lg px-3 py-2 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-tg-button disabled:opacity-50 disabled:cursor-not-allowed active:opacity-80';
const panel = 'min-w-0 rounded-xl bg-tg-section-bg p-4 space-y-4';
type Operation = 'load' | 'save' | 'probe' | 'models';
type Request = { kind: Operation; controller: AbortController };

export function AISettings() {
  const prefix = useId();
  const [saved, setSaved] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<AIDraft | null>(null);
  const [statuses, setStatuses] = useState<ProviderStatus[]>([]);
  const [busy, setBusy] = useState<Operation | null>('load');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [probe, setProbe] = useState<AIProbeResponse | null>(null);
  const [models, setModels] = useState<{ provider: AIProvider; ids: string[] } | null>(null);
  const [modelsNotice, setModelsNotice] = useState('');
  const [expanded, setExpanded] = useState<Partial<Record<AIProvider, boolean>>>({});
  const [advanced, setAdvanced] = useState(false);
  const [deepseekAdvanced, setDeepseekAdvanced] = useState(false);
  const [overridesOpen, setOverridesOpen] = useState(false);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(0);
  const summary = useRef<HTMLDivElement>(null);
  const request = useRef<Request | null>(null);
  const mounted = useRef(false);

  const start = (kind: Operation): Request | null => {
    if (!mounted.current || request.current) return null;
    const next = { kind, controller: new AbortController() };
    request.current = next;
    setBusy(kind);
    setError('');
    return next;
  };
  const current = (item: Request) => mounted.current && request.current === item && !item.controller.signal.aborted;
  const finish = (item: Request) => {
    if (!current(item)) return;
    request.current = null;
    setBusy(null);
  };
  const load = async () => {
    const item = start('load');
    if (!item) return;
    try {
      const response = await getAISettings(item.controller.signal);
      if (!current(item)) return;
      setSaved(response.settings);
      setDraft(toAIDraft(response.settings));
      setStatuses(response.providers);
      setTouched({}); setSubmitted(0); setProbe(null); setModels(null); setModelsNotice('');
    } catch (failure) {
      if (current(item)) setError(aiErrorMessage(failure));
    } finally { finish(item); }
  };

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      request.current?.controller.abort();
      request.current = null;
    };
  }, []);
  useEffect(() => { if (submitted) summary.current?.focus(); }, [submitted]);

  const invalidateReads = () => {
    if (request.current?.kind === 'probe' || request.current?.kind === 'models') {
      request.current.controller.abort();
      request.current = null;
      setBusy(null);
    }
    setProbe(null); setModels(null); setModelsNotice(''); setError('');
  };
  const edit = (update: (value: AIDraft) => AIDraft) => {
    if (request.current?.kind === 'save' || request.current?.kind === 'load') return;
    invalidateReads();
    setDraft(value => value ? update(value) : value);
  };

  if (!draft || !saved) return (
    <section className={panel} aria-label="Настройки ИИ" aria-busy={busy === 'load'}>
      {busy === 'load' ? <p role="status" className="text-tg-hint">Загрузка настроек ИИ…</p> : <>
        <p role="alert" className="text-tg-destructive">{error}</p>
        <button type="button" className={`${button} bg-tg-button text-tg-button-text`} onClick={() => void load()}>Повторить загрузку</button>
      </>}
    </section>
  );

  const errors = validateAIDraft(draft);
  const changed = JSON.stringify(fromAIDraft(draft)) !== JSON.stringify(saved);
  const locked = busy === 'save' || busy === 'load';
  const profileSaved = sameAIProfile(saved, draft, draft.provider);
  const activeStatus = statuses.find(status => status.id === draft.provider);
  const missingKey = activeStatus?.requires_api_key && !activeStatus.has_api_key;
  const canCheck = profileSaved && !!activeStatus && !missingKey && !busy;
  const deepseekThinking = draft.providers.deepseek.thinking ?? defaultDeepSeekThinking();
  const thinkingActive = draft.provider === 'deepseek' && deepseekThinking.enabled;
  const id = (key: string) => `${prefix}-${key}`;
  const visibleError = (key: string) => (submitted || touched[key]) ? errors[key] : undefined;
  const fieldProps = (key: string) => ({
    id: id(key), 'aria-invalid': !!visibleError(key),
    'aria-describedby': `${id(key)}-hint${visibleError(key) ? ` ${id(key)}-error` : ''}`,
    onBlur: () => setTouched(value => ({ ...value, [key]: true })),
  });
  const field = (key: string, label: string, value: string | number, onChange: (value: string) => void,
    hint = '', type = 'text', step?: number, list?: string) => (
    <div className="min-w-0 space-y-1.5" key={key}>
      <label htmlFor={id(key)} className="block text-sm font-medium text-tg-text">{label}</label>
      <input {...fieldProps(key)} className={control} type={type} step={step} list={list}
        value={typeof value === 'number' && !Number.isFinite(value) ? '' : value}
        onChange={event => onChange(event.target.value)} autoComplete="off" spellCheck={false} />
      <p id={`${id(key)}-hint`} className="text-xs leading-relaxed text-tg-hint">{hint}</p>
      {visibleError(key) && <p id={`${id(key)}-error`} className="text-sm text-tg-destructive">{visibleError(key)}</p>}
    </div>
  );
  const numeric = (key: 'temperature' | 'max_tokens' | 'request_timeout' | 'retry_count' | 'retry_backoff_base', label: string, hint: string, step = 1) =>
    field(key, label, draft[key], value => edit(previous => ({ ...previous, [key]: value === '' ? NaN : Number(value) })), hint, 'number', step);
  const toggle = (label: string, checked: boolean, onChange: (value: boolean) => void, hint: string, key?: string) => (
    <label className="flex min-h-11 items-start gap-3 py-2 text-sm text-tg-text">
      <input {...(key ? fieldProps(key) : {})} type="checkbox" className="mt-1 h-5 w-5 shrink-0 accent-tg-button focus-visible:outline focus-visible:outline-2 focus-visible:outline-tg-button" checked={checked} onChange={event => onChange(event.target.checked)} />
      <span>{label}<span id={key ? `${id(key)}-hint` : undefined} className="mt-1 block text-xs leading-relaxed text-tg-hint">{hint}</span>
        {key && visibleError(key) && <span id={`${id(key)}-error`} className="mt-1 block text-sm text-tg-destructive">{visibleError(key)}</span>}
      </span>
    </label>
  );

  const updateDeepSeekThinking = (patch: Partial<DeepSeekThinking>) => edit(previous => ({ ...previous,
    providers: { ...previous.providers, deepseek: { ...previous.providers.deepseek,
      thinking: { ...(previous.providers.deepseek.thinking ?? defaultDeepSeekThinking()), ...patch },
    } },
  }));

  const save = async () => {
    if (request.current) return;
    if (Object.keys(errors).length) {
      setSubmitted(value => value + 1); setAdvanced(true); setOverridesOpen(true);
      if (Object.keys(errors).some(key => key.startsWith('providers.deepseek.thinking.'))) setDeepseekAdvanced(true);
      setExpanded({ openrouter: true, deepseek: true, lmstudio: true });
      return;
    }
    if (!changed) return;
    const item = start('save');
    if (!item) return;
    try {
      const response = await saveAISettings(fromAIDraft(draft), item.controller.signal);
      if (!current(item)) return;
      setSaved(response.settings); setDraft(toAIDraft(response.settings));
      setStatuses(values => values.map(status => ({ ...status,
        configured: !!response.settings.providers[status.id].model && (status.has_api_key || !status.requires_api_key),
      })));
      setTouched({}); setSubmitted(0); setProbe(null); setModels(null); setModelsNotice('');
      setNotice(response.needs_restart
        ? 'Сохранено. Для применения нужен контролируемый перезапуск через вкладку Runtime. Проверьте активные позиции: панель не запускает и не перезапускает процессы автоматически.'
        : 'Настройки ИИ сохранены.');
    } catch (failure) {
      if (current(item)) setError(aiErrorMessage(failure));
    } finally { finish(item); }
  };
  const check = async (kind: 'probe' | 'models') => {
    if (!canCheck) return;
    const item = start(kind);
    if (!item) return;
    setProbe(null); setModelsNotice('');
    try {
      if (kind === 'probe') {
        const response = await probeAIProvider(draft.provider, item.controller.signal);
        if (current(item) && response.provider === draft.provider) setProbe(response);
      } else {
        const response = await getAIModels(draft.provider, item.controller.signal);
        if (current(item) && response.provider === draft.provider) {
          const ids = [...new Set(response.models.map(model => model.id))].sort();
          setModels({ provider: draft.provider, ids });
          setModelsNotice(ids.length ? `Получено моделей: ${ids.length}. Выберите подсказку или введите ID вручную.` : 'Список пуст. Загрузите модель на сервере или введите ID вручную.');
        }
      }
    } catch (failure) {
      if (current(item)) {
        setError(aiErrorMessage(failure));
        if (kind === 'models') setModelsNotice('Автообнаружение недоступно. ID модели можно ввести вручную.');
      }
    } finally { finish(item); }
  };

  return (
    <form className="min-w-0 space-y-4 text-tg-text" noValidate aria-label="Настройки ИИ" aria-busy={!!busy}
      onSubmit={event => { event.preventDefault(); void save(); }}>
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">Провайдер и модель</h2>
        <p className="text-sm leading-relaxed text-tg-hint">Три независимых профиля. Переключение не стирает настройки остальных.</p>
      </div>
      {!!submitted && !!Object.keys(errors).length && <div ref={summary} tabIndex={-1} role="alert" className={`${panel} ring-1 ring-tg-destructive focus:outline-tg-destructive`}>
        <p className="font-medium">Проверьте поля перед сохранением</p>
        <ul className="space-y-2 text-sm">{Object.entries(errors).map(([key, message]) => <li key={key}>
          <a className="text-tg-link underline break-words" href={`#${id(key)}`}>{key}: {message}</a>
        </li>)}</ul>
      </div>}
      {error && <div role="alert" className={`${panel} text-sm text-tg-destructive`}>
        <p>{error}</p>
        <button type="button" className={`${button} text-tg-text underline`} disabled={!!busy}
          onClick={() => { if (!changed || window.confirm('Загрузить сохранённые настройки и отменить черновик?')) void load(); }}>Загрузить заново</button>
      </div>}
      {notice && <p role="status" className={`${panel} text-sm leading-relaxed border-l-2 border-tg-button`}>{notice}</p>}

      <fieldset disabled={locked} className="min-w-0 space-y-4">
        <legend className="sr-only">Профили провайдеров</legend>
        <div role="group" aria-label="Активный провайдер" className="grid grid-cols-3 gap-2">
          {AI_PROVIDERS.map(provider => <button key={provider.id} type="button" aria-pressed={draft.provider === provider.id}
            className={`${button} px-1 ${draft.provider === provider.id ? 'bg-tg-button text-tg-button-text' : 'bg-tg-section-bg text-tg-text'}`}
            onClick={() => edit(value => ({ ...value, provider: provider.id }))}>{provider.label}</button>)}
        </div>
        {AI_PROVIDERS.map(provider => {
          const selected = draft.provider === provider.id;
          const open = selected || !!expanded[provider.id];
          const status = statuses.find(value => value.id === provider.id);
          const persisted = sameAIProfile(saved, draft, provider.id, true);
          const updateProfile = (key: 'base_url' | 'model', value: string) => edit(previous => ({ ...previous,
            providers: { ...previous.providers, [provider.id]: { ...previous.providers[provider.id], [key]: value } },
          }));
          return <section key={provider.id} className={`${panel} ${selected ? 'ring-1 ring-tg-button' : ''}`} aria-labelledby={id(`${provider.id}-heading`)}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <h3 id={id(`${provider.id}-heading`)} className="font-semibold">{provider.label}</h3>
                <p className="mt-1 text-xs text-tg-hint">{selected ? 'Выбран в черновике' : 'Неактивный профиль'} · {persisted ? 'Сохранён' : 'Есть изменения'}{saved.provider === provider.id ? ' · Текущий в конфиге' : ''}</p>
              </div>
              {!selected && <button type="button" className={`${button} shrink-0 text-tg-link`} aria-expanded={open} aria-controls={id(`${provider.id}-fields`)}
                onClick={() => setExpanded(value => ({ ...value, [provider.id]: !open }))}>{open ? 'Свернуть' : 'Изменить'}</button>}
            </div>
            {!open && <div className="space-y-1 text-xs text-tg-hint">
                          <p className="truncate font-mono" title={draft.providers[provider.id].base_url}>{draft.providers[provider.id].base_url}</p>
                          <p className="truncate font-mono" title={draft.providers[provider.id].model}>{draft.providers[provider.id].model || 'Модель не задана'}</p>
                          <p>{status ? (status.has_api_key ? 'Ключ задан' : status.requires_api_key ? 'Нет ключа в окружении' : 'Ключ необязателен') : 'Статус ключа неизвестен'}</p>
                        </div>}
            <div className="space-y-4" id={id(`${provider.id}-fields`)} hidden={!open}>
              <p className="text-xs leading-relaxed text-tg-hint">{provider.hint}</p>
              {field(`providers.${provider.id}.base_url`, 'Base URL', draft.providers[provider.id].base_url, value => updateProfile('base_url', value), provider.id === 'deepseek' ? 'Официальный адрес: https://api.deepseek.com. Ключи и пароли в URL запрещены.' : 'Адрес API с /v1. Ключи и пароли в URL запрещены.', 'url')}
              {field(`providers.${provider.id}.model`, 'ID модели', draft.providers[provider.id].model, value => updateProfile('model', value), provider.id === 'deepseek' ? 'Например, deepseek-flash или deepseek-v4-pro. ID можно ввести вручную.' : 'Можно ввести вручную; каталог моделей — только подсказка.', 'text', undefined, models?.provider === provider.id ? id('models') : undefined)}
                            {provider.id === 'deepseek' && <div className="space-y-2 border-t border-tg-hint/20 pt-2">
                              <button type="button" className={`${button} flex w-full items-center justify-between gap-2 px-0 text-left`}
                                aria-expanded={deepseekAdvanced} aria-controls={id('deepseek-thinking')}
                                onClick={() => setDeepseekAdvanced(value => !value)}>
                                <span>Thinking DeepSeek · {deepseekThinking.enabled ? 'включён' : 'выключен'}</span>
                                <span aria-hidden="true" className="text-tg-hint">{deepseekAdvanced ? '−' : '+'}</span>
                              </button>
                              <div id={id('deepseek-thinking')} hidden={!deepseekAdvanced} className="space-y-3">
                                {toggle('Включить thinking', deepseekThinking.enabled, enabled => updateDeepSeekThinking({ enabled }),
                                  'По умолчанию выключено: при коротком бюджете VETO рассуждения могут не оставить токенов для итогового ответа.', 'providers.deepseek.thinking.enabled')}
                                <div hidden={!deepseekThinking.enabled && !visibleError('providers.deepseek.thinking.effort')} className="space-y-1.5">
                                  <label htmlFor={id('providers.deepseek.thinking.effort')} className="block text-sm font-medium">Уровень thinking</label>
                                  <select {...fieldProps('providers.deepseek.thinking.effort')} className={control} value={deepseekThinking.effort}
                                    onChange={event => updateDeepSeekThinking({ effort: event.target.value as DeepSeekThinking['effort'] })}>
                                    <option value="low">Низкий · low</option><option value="high">Высокий · high</option><option value="max">Максимальный · max</option>
                                  </select>
                                  <p id={`${id('providers.deepseek.thinking.effort')}-hint`} className="text-xs text-tg-hint">Поддержка выбранного уровня зависит от модели.</p>
                                  {visibleError('providers.deepseek.thinking.effort') && <p id={`${id('providers.deepseek.thinking.effort')}-error`} className="text-sm text-tg-destructive">{visibleError('providers.deepseek.thinking.effort')}</p>}
                                </div>
                                {deepseekThinking.enabled && <p className="text-xs leading-relaxed text-tg-hint">При thinking DeepSeek игнорирует температуру. Лимит токенов включает рассуждения и итоговый ответ.</p>}
                                <p className="text-xs leading-relaxed text-tg-hint">VETO и REGIME используют thinking профиля DeepSeek, если их итоговый провайдер — DeepSeek. Это независимо от reasoning OpenRouter.</p>
                              </div>
                            </div>}
              <div className="rounded-lg bg-tg-bg p-3 text-xs leading-relaxed space-y-1">
                <p>{status ? (status.requires_api_key ? (status.has_api_key ? 'Ключ задан в окружении' : 'Ключ отсутствует в окружении') : (status.has_api_key ? 'Необязательный ключ задан' : 'Ключ необязателен, не задан')) : 'Статус ключа неизвестен'}</p>
                <p className="break-all font-mono text-tg-hint">{provider.env}</p>
                <p className="text-tg-hint">{status ? (status.configured ? 'Сохранённый профиль настроен; доступность проверяется отдельно.' : 'Сохранённый профиль ещё не настроен.') : 'Сервер не сообщил статус профиля.'} {status?.hint}</p>
                <p className="text-tg-hint">Ключи меняются только в окружении контейнера и никогда не вводятся в панели.</p>
              </div>
            </div>
          </section>;
        })}
      </fieldset>
      {models && <datalist id={id('models')}>{models.ids.map(model => <option key={model} value={model} />)}</datalist>}

      <section className={panel} aria-label="Проверка сохранённого профиля">
        <div className="flex flex-wrap gap-2">
          <button type="button" className={`${button} bg-tg-bg`} disabled={!canCheck} onClick={() => void check('probe')}>{busy === 'probe' ? 'Проверка…' : 'Проверить соединение'}</button>
          <button type="button" className={`${button} bg-tg-bg`} disabled={!canCheck} onClick={() => void check('models')}>{busy === 'models' ? 'Загрузка моделей…' : 'Обновить модели'}</button>
        </div>
        <p className="text-xs leading-relaxed text-tg-hint">Проверяется сохранённый профиль {AI_PROVIDERS.find(value => value.id === draft.provider)?.label}. Backend выполняет только GET к API провайдера: без отправки промпта и платной генерации. Это не проверка качества ответа модели.</p>
        {!profileSaved && <p className="text-sm text-tg-hint">Сначала сохраните Base URL и ID модели этого профиля или отмените изменения.</p>}
        {missingKey && <p className="text-sm text-tg-hint">Для проверки задайте {AI_PROVIDERS.find(value => value.id === draft.provider)?.env} в окружении и обновите панель.</p>}
        {probe && <p role="status" className={`text-sm break-words ${probe.ok ? 'text-tg-text' : 'text-tg-destructive'}`}>
          {probe.ok ? 'Соединение доступно' : 'Проверка не пройдена'} · {Math.round(probe.latency_ms)} мс. {probe.message}{probe.model ? ` Модель: ${probe.model}.` : ''}
        </p>}
        {modelsNotice && <p role="status" className="text-sm text-tg-hint">{modelsNotice}</p>}
        {(busy === 'models' || busy === 'probe') && <p role="status" className="text-sm text-tg-hint">Ожидаем ответ сохранённого профиля…</p>}
      </section>

      <fieldset disabled={locked} className={panel}>
        <legend className="sr-only">Дополнительные параметры ИИ</legend>
        <button type="button" className={`${button} flex w-full items-center justify-between gap-2 px-0 text-left`} aria-expanded={advanced} aria-controls={id('advanced')}
          onClick={() => setAdvanced(value => !value)}><span className="font-semibold">Расширенные параметры</span><span aria-hidden="true" className="text-tg-hint">{advanced ? '−' : '+'}</span></button>
        <div hidden={!advanced} id={id('advanced')} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {numeric('temperature', 'Температура', thinkingActive ? 'Игнорируется DeepSeek при включённом thinking; значение сохранится для остальных режимов.' : '0–2. Некоторые модели поддерживают только фиксированное значение.', 0.1)}
            {numeric('max_tokens', 'Лимит токенов ответа', thinkingActive ? 'При thinking DeepSeek включает рассуждения и итоговый ответ.' : 'В пределах контекста выбранной модели.')}
            {numeric('request_timeout', 'Таймаут запроса, с', 'Время ожидания одной попытки.', 0.5)}
            {numeric('retry_count', 'Повторные попытки', '0 — без повторов. Повторы увеличивают общее время ожидания.')}
            {numeric('retry_backoff_base', 'База задержки повторов, с', 'Для экспоненциального увеличения задержки.', 0.5)}
          </div>
          <div className="border-t border-tg-hint/20 pt-3 space-y-2">
            <h3 className="text-sm font-semibold">OpenRouter: рассуждения и маршрутизация</h3>
            {draft.provider !== 'openrouter' && <p className="text-xs leading-relaxed text-tg-hint">Эти параметры сохранены для OpenRouter, но не применяются к текущему провайдеру. Thinking DeepSeek настраивается в его карточке профиля; для LM Studio поддержка зависит от модели и сервера.</p>}
            <>
              {toggle('Включить рассуждения', draft.reasoning.enabled, value => edit(previous => ({ ...previous, reasoning: { ...previous.reasoning, enabled: value } })), 'Только для моделей, поддерживающих reasoning в OpenRouter.')}
              <div hidden={!draft.reasoning.enabled} className="space-y-2">
                <label htmlFor={id('reasoning.effort')} className="block text-sm">Уровень рассуждений</label>
                <select {...fieldProps('reasoning.effort')} className={control} value={draft.reasoning.effort} onChange={event => edit(previous => ({ ...previous, reasoning: { ...previous.reasoning, effort: event.target.value as Settings['reasoning']['effort'] } }))}>
                  <option value="none">Без рассуждений</option><option value="minimal">Минимальный</option>
                                    <option value="low">Низкий</option><option value="medium">Средний</option><option value="high">Высокий</option>
                                    <option value="xhigh">Очень высокий</option><option value="max">Максимальный</option>
                </select>
                <p id={`${id('reasoning.effort')}-hint`} className="text-xs text-tg-hint">Поддержка зависит от модели.</p>
                {visibleError('reasoning.effort') && <p id={`${id('reasoning.effort')}-error`} className="text-sm text-tg-destructive">{visibleError('reasoning.effort')}</p>}
                {toggle('Не возвращать текст рассуждений', draft.reasoning.exclude, value => edit(previous => ({ ...previous, reasoning: { ...previous.reasoning, exclude: value } })), 'Не отключает внутренние рассуждения и не гарантирует снижения стоимости.')}
              </div>
              {toggle('Разрешить резервные маршруты', draft.provider_routing.allow_fallbacks, value => edit(previous => ({ ...previous, provider_routing: { allow_fallbacks: value } })), 'OpenRouter может выбрать другого поставщика той же модели.')}
              <label htmlFor={id('fallback_models')} className="block text-sm">Резервные модели</label>
              <textarea {...fieldProps('fallback_models')} className={`${control} font-mono`} rows={3} value={draft.fallback_models}
                onChange={event => edit(previous => ({ ...previous, fallback_models: event.target.value }))} />
              <p id={`${id('fallback_models')}-hint`} className="text-xs text-tg-hint">До 20 ID, по одному на строку, в порядке приоритета. Пусто — без резервных моделей. Маршрутизация может менять стоимость и обработчика данных.</p>
              {visibleError('fallback_models') && <p id={`${id('fallback_models')}-error`} className="text-sm text-tg-destructive">{visibleError('fallback_models')}</p>}
            </>
          </div>
        </div>
      </fieldset>

      <fieldset disabled={locked} className={panel}>
        <legend className="sr-only">Переопределения VETO и REGIME</legend>
        <button type="button" className={`${button} flex w-full items-center justify-between gap-2 px-0 text-left`} aria-expanded={overridesOpen} aria-controls={id('overrides')}
          onClick={() => setOverridesOpen(value => !value)}><span className="font-semibold">VETO / REGIME</span><span aria-hidden="true" className="text-tg-hint">{overridesOpen ? '−' : '+'}</span></button>
        <div hidden={!overridesOpen} id={id('overrides')} className="space-y-5">
          <p className="text-xs leading-relaxed text-tg-hint">Не включает эти этапы стратегии. По умолчанию они наследуют основной провайдер и его модель; провайдер и модель можно переопределить независимо.</p>
          {(['veto', 'regime'] as const).map(key => {
            const value = draft.overrides[key];
            const effective = effectiveAIOverride(fromAIDraft(draft), value);
            const update = (patch: Partial<typeof value>) => edit(previous => ({ ...previous, overrides: { ...previous.overrides, [key]: { ...previous.overrides[key], ...patch } } }));
            const path = `overrides.${key}`;
            return <section key={key} className="space-y-3 border-t border-tg-hint/20 pt-4">
              <h3 className="text-sm font-semibold">{key.toUpperCase()}</h3>
              <label htmlFor={id(`${path}.provider`)} className="block text-sm">Провайдер</label>
              <select {...fieldProps(`${path}.provider`)} className={control} value={value.provider ?? ''} onChange={event => update({ provider: event.target.value ? event.target.value as AIProvider : null })}>
                <option value="">Наследовать основной</option>{AI_PROVIDERS.map(provider => <option key={provider.id} value={provider.id}>{provider.label}</option>)}
              </select>
              <p id={`${id(`${path}.provider`)}-hint`} className="text-xs text-tg-hint">Base URL и ключ берутся из профиля выбранного провайдера.</p>
              {visibleError(`${path}.provider`) && <p id={`${id(`${path}.provider`)}-error`} className="text-sm text-tg-destructive">{visibleError(`${path}.provider`)}</p>}
              {field(`${path}.model`, 'ID модели (необязательно)', value.model ?? '', model => update({ model: model || null }), `Пусто — модель профиля ${effective.provider}: ${draft.providers[effective.provider]?.model || 'не задана'}.`)}
              <div className="grid gap-4 sm:grid-cols-2">
                {field(`${path}.temperature`, 'Температура', value.temperature, number => update({ temperature: number === '' ? NaN : Number(number) }), '0–2.', 'number', 0.1)}
                {field(`${path}.max_tokens`, 'Лимит токенов', value.max_tokens, number => update({ max_tokens: number === '' ? NaN : Number(number) }), 'Лимит ответа этого этапа.', 'number', 1)}
              </div>
              {effective.provider === 'deepseek' && <p className="text-xs leading-relaxed text-tg-hint">Thinking берётся из профиля DeepSeek: {deepseekThinking.enabled ? 'включён; температура игнорируется, лимит включает рассуждения и итоговый ответ. Для короткого бюджета VETO проверьте, достаточно ли токенов.' : 'выключен.'}</p>}
              <p className="break-words text-xs text-tg-hint">Итог: {effective.provider} / <span className="font-mono">{effective.model || 'модель не задана'}</span></p>
            </section>;
          })}
        </div>
      </fieldset>

      <div className={`${panel} space-y-3`}>
        <p className="text-xs leading-relaxed text-tg-hint">{changed ? 'Есть несохранённые изменения.' : 'Черновик совпадает с сохранёнными настройками.'} Сохранение не запускает торговлю. Применение — после контролируемого перезапуска.</p>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={`${button} grow bg-tg-button text-tg-button-text`} disabled={!changed || !!busy}>{busy === 'save' ? 'Сохранение…' : 'Сохранить настройки ИИ'}</button>
          <button type="button" className={`${button} bg-tg-bg`} disabled={!changed || locked} onClick={() => { invalidateReads(); setDraft(toAIDraft(saved)); setTouched({}); setSubmitted(0); }}>Отменить</button>
          <button type="button" className={`${button} text-tg-hint`} disabled={locked} onClick={() => { invalidateReads(); setDraft(toAIDraft(defaultAISettings())); setTouched({}); setSubmitted(0); }}>Сбросить черновик</button>
        </div>
        <p className="text-xs text-tg-hint">Сброс возвращает значения по умолчанию только в черновике. Для отправки на сервер нажмите «Сохранить».</p>
      </div>
    </form>
  );
}
