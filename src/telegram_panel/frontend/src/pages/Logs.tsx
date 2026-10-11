import { useEffect, useRef, useState } from 'react';
import type { LogEvent, LogFilters, LogSources } from '../api/types';
import { getLogEvents, getLogSources, LogAPIError } from '../api/logs';
import { LogViewer } from '../components/LogViewer';

const FILTER_KEYS: (keyof LogFilters)[] = ['level', 'category', 'exchange', 'symbol', 'instance', 'search', 'order', 'request', 'source', 'time_from', 'time_to'];
const CATEGORIES = ['runtime', 'exchange', 'market_data', 'strategy', 'llm', 'risk', 'order', 'trade', 'config', 'panel'];
const CONTROL = 'tg-control rounded-lg px-3 py-2 min-h-[44px] text-sm focus-visible:ring-2 focus-visible:ring-tg-button';
const BUTTON = 'rounded-lg px-3 py-2 min-h-[44px] text-sm bg-tg-section-bg text-tg-text hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-tg-button disabled:opacity-50';

function readFilters(): LogFilters {
  const query = new URLSearchParams(window.location.search);
  return Object.fromEntries(FILTER_KEYS.map((key) => [key, (query.get(`log_${key}`) || '').slice(0, 256)]));
}

function saveFilters(filters: LogFilters) {
  const url = new URL(window.location.href);
  // Изменяем только allowlisted log_*; credentials транспорта не копируем в фильтры.
  for (const key of FILTER_KEYS) {
    if (filters[key]) url.searchParams.set(`log_${key}`, filters[key]!);
    else url.searchParams.delete(`log_${key}`);
  }
  window.history.replaceState(null, '', url);
}

function mergeEvents(old: LogEvent[], added: LogEvent[]) {
  const byId = new Map(old.map((event) => [event.event_id, event]));
  for (const event of added) byId.set(event.event_id, event);
  return [...byId.values()].sort((a, b) => a.sequence - b.sequence).slice(-2000);
}

function SelectFilter({ name, label, values, draft, change }: {
  name: keyof LogFilters; label: string; values: string[]; draft: LogFilters;
  change: (key: keyof LogFilters, value: string) => void;
}) {
  const selected = draft[name] || '';
  const options = [...new Set([...values, ...(selected ? [selected] : [])])];
  return <label className="flex flex-col gap-1 text-xs text-tg-hint">
    {label}
    <select className={CONTROL} value={selected} onChange={(e) => change(name, e.target.value)}>
      <option value="">Все</option>
      {options.map((value) => <option key={value} value={value}>{value}</option>)}
    </select>
  </label>;
}

function dateInput(value?: string) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function Logs({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [filters, setFilters] = useState<LogFilters>(readFilters);
  const [draft, setDraft] = useState<LogFilters>(filters);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [metadata, setMetadata] = useState<LogSources | null>(null);
  const [paused, setPaused] = useState(false);
  const [follow, setFollow] = useState(true);
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [hasHistory, setHasHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [updated, setUpdated] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const refreshRef = useRef<() => void>(() => {});
  const historyRef = useRef<() => void>(() => {});
  const key = JSON.stringify(filters);

  useEffect(() => {
    const pop = () => { const next = readFilters(); setFilters(next); setDraft(next); };
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    let initialized = false;
    let liveBusy = false;
    let historyBusy = false;
    let queued = false;
    let blocked = false;
    let liveCursor: string | null = null;
    let historyCursor: string | null = null;
    const query: LogFilters = JSON.parse(key);
    setEvents([]); setLoading(true); setHasHistory(false); setError(null); setUpdated(null);
    setHistoryLoading(false);

    const failed = (err: unknown) => {
      if (!cancelled && !(err instanceof DOMException && err.name === 'AbortError')) {
        if (err instanceof LogAPIError && [401, 403, 409].includes(err.status)) blocked = true;
        setError(err instanceof Error ? err.message : 'Ошибка чтения журнала. Повторите запрос.');
      }
    };
    const markUpdated = () => { setUpdated(new Date().toLocaleTimeString('ru-RU')); setError(null); };
    const sources = async () => {
      try {
        const data = await getLogSources(controller.signal);
        if (!cancelled) { setMetadata(data); setMetadataError(null); }
      } catch {
        if (!cancelled) setMetadataError('Не удалось обновить список источников. Можно задать фильтры и повторить обновление.');
      }
    };
    const refresh = async () => {
      if (cancelled || blocked || pausedRef.current || !initialized) return;
      if (liveBusy) { queued = true; return; }
      liveBusy = true;
      try {
        let more = true;
        // До 1000 событий за проход; остальные добираем следующим проходом.
        for (let pageNumber = 0; more && pageNumber < 10; pageNumber++) {
          const page = await getLogEvents(query, liveCursor, controller.signal);
          if (cancelled || pausedRef.current) return;
          setEvents((old) => mergeEvents(old, page.events));
          if (!liveCursor && page.next_cursor) { historyCursor = page.next_cursor; setHasHistory(true); }
          liveCursor = page.live_cursor;
          more = page.has_more && !page.next_cursor;
          markUpdated();
        }
        if (more) queued = true;
      } catch (err) { queued = false; failed(err); }
      finally {
        liveBusy = false;
        if (queued && !cancelled && !pausedRef.current) {
          queued = false;
          // Yield браузеру между catch-up страницами.
          scheduleRefresh();
        }
      }
    };
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleRefresh = () => {
      if (refreshTimer === null) refreshTimer = setTimeout(() => { refreshTimer = null; void refresh(); }, 150);
    };
    const history = async () => {
      if (cancelled || blocked || historyBusy || !historyCursor) return;
      historyBusy = true; setHistoryLoading(true);
      try {
        const page = await getLogEvents(query, historyCursor, controller.signal);
        if (cancelled) return;
        setEvents((old) => mergeEvents(old, page.events));
        historyCursor = page.next_cursor;
        setHasHistory(Boolean(historyCursor));
        setError(null);
      } catch (err) { failed(err); }
      finally { historyBusy = false; if (!cancelled) setHistoryLoading(false); }
    };
    refreshRef.current = scheduleRefresh;
    historyRef.current = () => { void history(); };

    void sources();
    void getLogEvents(query, null, controller.signal).then((page) => {
      if (cancelled) return;
      setEvents(page.events); liveCursor = page.live_cursor; historyCursor = page.next_cursor;
      setHasHistory(Boolean(historyCursor)); initialized = true; markUpdated();
      if (!pausedRef.current) scheduleRefresh();
    }).catch(failed).finally(() => { if (!cancelled) setLoading(false); });

    const unsubscribers = ['log_events', 'log_line', 'log_symbol'].map((type) => subscribe(type, scheduleRefresh));
    const poll = setInterval(scheduleRefresh, 5000);
    const metadataPoll = setInterval(() => { void sources(); }, 30000);
    const resume = () => { if (!document.hidden) scheduleRefresh(); };
    document.addEventListener('visibilitychange', resume);
    return () => {
      cancelled = true; controller.abort();
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      clearInterval(poll); clearInterval(metadataPoll);
      unsubscribers.forEach((unsubscribe) => unsubscribe());
      document.removeEventListener('visibilitychange', resume);
      refreshRef.current = () => {}; historyRef.current = () => {};
    };
  }, [key, reload, subscribe]);

  useEffect(() => { if (!paused) refreshRef.current(); }, [paused]);
  const change = (name: keyof LogFilters, value: string) => setDraft((old) => ({ ...old, [name]: value }));
  const apply = (next: LogFilters) => { saveFilters(next); setDraft(next); setFilters(next); };

  return <div className="flex flex-col gap-3 p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h1 className="text-lg font-semibold text-tg-text">Журнал событий</h1>
      <div className="flex flex-wrap gap-2">
        <button className={BUTTON} aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? 'Продолжить live' : 'Пауза live'}</button>
        <button className={BUTTON} aria-pressed={follow} onClick={() => setFollow(!follow)}>Следовать за хвостом: {follow ? 'да' : 'нет'}</button>
      </div>
    </div>
    <div className="flex flex-wrap gap-2" aria-label="Быстрые фильтры">
      <button className={BUTTON} onClick={() => apply({})}>Все события</button>
      <button className={BUTTON} onClick={() => apply({ ...filters, level: 'ERROR,CRITICAL', category: '' })}>Ошибки</button>
      <button className={BUTTON} onClick={() => apply({ ...filters, level: '', category: 'order,trade' })}>Ордеры и сделки</button>
      <button className={BUTTON} onClick={() => apply({ ...filters, level: '', category: 'exchange,market_data' })}>Биржа и данные</button>
    </div>
    <form className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" onSubmit={(event) => { event.preventDefault(); apply(draft); }}>
      <SelectFilter name="level" label="Уровень" values={metadata?.levels || ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']} draft={draft} change={change} />
      <SelectFilter name="category" label="Категория" values={metadata?.categories || CATEGORIES} draft={draft} change={change} />
      <SelectFilter name="source" label="Источник" values={metadata?.sources || []} draft={draft} change={change} />
      <SelectFilter name="exchange" label="Биржа" values={metadata?.exchanges || []} draft={draft} change={change} />
      <SelectFilter name="symbol" label="Символ" values={metadata?.symbols || []} draft={draft} change={change} />
      <SelectFilter name="instance" label="Экземпляр стратегии" values={metadata?.instances || []} draft={draft} change={change} />
      {(['search', 'order', 'request'] as const).map((name) => <label key={name} className="flex flex-col gap-1 text-xs text-tg-hint">
        {{ search: 'Текст сообщения', order: 'Order ID', request: 'Request ID' }[name]}
        <input className={CONTROL} name={`log_${name}`} autoComplete="off" spellCheck={false} maxLength={256} value={draft[name] || ''} onChange={(e) => change(name, e.target.value)} />
      </label>)}
      {(['time_from', 'time_to'] as const).map((name) => <label key={name} className="flex flex-col gap-1 text-xs text-tg-hint">
        {name === 'time_from' ? 'С (местное время)' : 'По (местное время)'}
        <input className={CONTROL} type="datetime-local" name={`log_${name}`} value={dateInput(draft[name])} onChange={(e) => change(name, e.target.value ? new Date(e.target.value).toISOString() : '')} />
      </label>)}
      <button className={BUTTON} type="submit">Применить фильтры</button>
    </form>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p role="status" aria-live="polite" className="text-xs text-tg-hint">{loading ? 'Загрузка…' : `${paused ? 'Live на паузе' : 'Live с проверкой каждые 5 с'} · ${events.length} событий${updated ? ` · обновлено ${updated}` : ''}`}</p>
      <button className={BUTTON} onClick={() => setReload((old) => old + 1)}>Обновить</button>
    </div>
    {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    {metadataError && <p role="status" className="text-sm text-amber-400">{metadataError}</p>}
    <button className={BUTTON} disabled={loading || historyLoading || !hasHistory || events.length >= 2000} onClick={() => historyRef.current()}>{historyLoading ? 'Загрузка истории…' : hasHistory ? 'Загрузить более старые' : 'Начало доступной истории'}</button>
    {events.length >= 2000 && <p className="text-xs text-tg-hint">На экране максимум 2000 событий. Сузьте период или другие фильтры для просмотра истории.</p>}
    {!loading && <LogViewer events={events} follow={follow} onFollowChange={setFollow} />}
    {Boolean(metadata?.legacy_sources.length) && <p className="text-xs text-tg-hint">Старые текстовые журналы сохранены и доступны через совместимые API. Они не смешиваются с новыми событиями, чтобы не создавать дубли.</p>}
  </div>;
}
