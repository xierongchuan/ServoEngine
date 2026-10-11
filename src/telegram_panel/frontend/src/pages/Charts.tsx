import { useEffect, useState, useCallback, useRef } from 'react';
import { APIError, AuthenticationError, getChartData, getChartSources, getDashboard } from '../api/client';
import { chartStreamKey, type ChartSnapshot, type ChartStream } from '../api/chartTypes';
import { InteractiveChart } from '../components/InteractiveChart';
import { Spinner } from '../components/Spinner';
import { Tabs } from '../components/ui/Tabs';
import { StatusDot } from '../components/ui/StatusDot';

export function Charts({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState('');
  const [selectedRange, setSelectedRange] = useState('AUTO');
  const [chartData, setChartData] = useState<ChartSnapshot | null>(null);
  const [selectedSource, setSelectedSource] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [waitingForData, setWaitingForData] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const fsContainerRef = useRef<HTMLDivElement>(null);
  const [symbolsWithPositions, setSymbolsWithPositions] = useState<Set<string>>(new Set());


  useEffect(() => {
    // Use dashboard API which reads from new config system (active.json)
    getDashboard().then((dash) => {
      const all = dash.symbols || [];
      setSymbols(all);
      setSymbolsWithPositions(new Set((dash.active_trades || []).map(trade => trade.symbol)));
      if (all.length > 0) setSelectedSymbol((prev) => prev || all[0]);
      else { setLoading(false); setError('Нет настроенных символов для графика.'); }
    }).catch((err) => {
      setLoading(false);
      setError(err instanceof Error ? err.message : 'Не удалось загрузить список символов');
    });
  }, []);

  useEffect(() => {
    if (!selectedSymbol) return;
    let disposed = false;
    let inFlight = false;
    let pending = false;
    let authFailed = false;
    let controller: AbortController | null = null;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let queued: ReturnType<typeof setTimeout> | undefined;
    let lastStarted = 0;
    let sources: ChartStream[] | null = null;
    setLoading(true);
    setChartData(null);
    setError(null);
    setWaitingForData(false);

    const refresh = async () => {
      if (disposed || authFailed) return;
      if (inFlight) { pending = true; return; }
      if (Date.now() - lastStarted < 1000) {
        if (!queued) queued = setTimeout(() => { queued = undefined; void refresh(); }, 1000);
        return;
      }
      inFlight = true;
      lastStarted = Date.now();
      controller = new AbortController();
      timeout = setTimeout(() => controller?.abort(), 10000);
      try {
        if (sources === null) sources = await getChartSources(selectedSymbol, controller.signal);
        const stream = sources.find(source => chartStreamKey(source) === selectedSource) || sources[0];
        const data = await getChartData(selectedSymbol, selectedRange, controller.signal, stream);
        if (!disposed) {
          setChartData(data);
          setError(null);
          setWaitingForData(false);
          setSymbolsWithPositions(previous => {
            const next = new Set(previous);
            if (data.position) next.add(selectedSymbol); else next.delete(selectedSymbol);
            return next;
          });
        }
      } catch (err) {
        if (!disposed) {
          authFailed = err instanceof AuthenticationError;
          const unavailable = err instanceof APIError && err.status === 404;
          setWaitingForData(unavailable);
          // После запуска runtime могут появиться новые источники; пустой каталог нельзя кэшировать навсегда.
          if (unavailable) sources = null;
          setError(unavailable
            ? 'Данные графика ещё не появились. Проверьте запуск общего runtime в Настройках и дождитесь загрузки свечей. Обновление выполняется автоматически; read_only тоже собирает данные.'
            : err instanceof Error && err.name !== 'AbortError' ? err.message : 'Обновление графика не завершено. Повторим автоматически.');
        }
      } finally {
        clearTimeout(timeout);
        inFlight = false;
        if (!disposed) {
          setLoading(false);
          if (pending) { pending = false; void refresh(); }
        }
      }
    };
    void refresh();
    // REST остаётся источником полного снимка даже при потере WS или пропущенном событии.
    const polling = setInterval(() => { if (!document.hidden) void refresh(); }, 15000);
    const onVisible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    const unsubTrade = subscribe('trade_update', () => { void refresh(); });
    const unsubData = subscribe('data_update', event => {
      const normalize = (value: string) => value.replace(/[^a-z0-9]/gi, '').toUpperCase();
      const changed = typeof event.symbol === 'string' ? event.symbol
        : typeof event.path === 'string' ? event.path.split(/[\\/]/).pop()?.replace(/\.json$/, '') : '';
      if (changed && normalize(changed) === normalize(selectedSymbol)) void refresh();
    });
    return () => {
      disposed = true;
      controller?.abort();
      clearTimeout(timeout);
      clearTimeout(queued);
      clearInterval(polling);
      document.removeEventListener('visibilitychange', onVisible);
      unsubTrade();
      unsubData();
    };
  }, [selectedSymbol, selectedRange, selectedSource, subscribe]);

  // Слушаем выход из fullscreen через браузерное событие (Escape / жест)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreen(false);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  const toggleFullscreen = useCallback(() => {
    setFullscreen((prev) => !prev);
  }, []);

  return (
    <div
      ref={fsContainerRef}
      className={`flex flex-col gap-3 ${
        fullscreen
          ? 'fixed inset-0 z-40 bg-tg-bg p-3 pb-20 md:pb-3 md:pl-[5.5rem] w-full h-[100dvh]'
          : 'p-4'
      }`}
    >
      {!fullscreen && <span className="text-lg font-semibold text-tg-text">Графики</span>}

      {/* Symbol selector */}
      {symbols.length > 0 && (
        <Tabs
          value={selectedSymbol}
          onChange={(symbol) => { setSelectedRange('AUTO'); setSelectedSource(''); setSelectedSymbol(symbol); }}
          options={symbols.map((sym) => ({
            value: sym,
            label: (
              <>
                <StatusDot active={symbolsWithPositions.has(sym)} />
                {sym}
              </>
            ),
          }))}
        />
      )}

      {chartData && chartData.available_sources.length > 0 && (
        <Tabs value={chartData.source.stream ? chartStreamKey(chartData.source.stream) : selectedSource}
          onChange={(source) => { setSelectedSource(source); setSelectedRange('AUTO'); }}
          options={chartData.available_sources.map(source => ({ value: chartStreamKey(source),
            label: `${source.exchange} · ${source.market_type} · ${source.interval}` }))} />
      )}

      {/* Выбор диапазона */}
      {chartData?.available_ranges && (
        <Tabs
          value={selectedRange === 'AUTO' ? chartData.range : selectedRange}
          onChange={setSelectedRange}
          options={chartData.available_ranges.map((r) => ({ value: r, label: r }))}
        />
      )}

      {loading && (
        <div role="status" aria-label="Загрузка графика" className="flex items-center justify-center h-64">
          <Spinner size={32} />
        </div>
      )}

      {error && !loading && (
        <div role={waitingForData ? 'status' : 'alert'} className={`text-center py-3 text-sm leading-relaxed ${waitingForData ? 'text-tg-hint' : 'text-red-400'}`}>{error}{chartData && ' Показан последний успешно загруженный снимок.'}</div>
      )}

      {!loading && chartData && (
        <>
          {(chartData.source.stale || chartData.source.ready === false) && (
            <div role="status" className="text-amber-400 text-sm">Источник не готов или данные устарели. Показан последний доступный снимок.</div>
          )}
          {chartData.position && (
            <div className={`text-xs px-2 py-1 rounded inline-flex self-start ${
              chartData.position.side === 'LONG'
                ? 'bg-green-500/20 text-green-400'
                : 'bg-red-500/20 text-red-400'
            }`}>
              {chartData.position.side} @ {chartData.position.entry_price} | PnL: ${chartData.position.pnl.toFixed(2)}
            </div>
          )}

          {/* В fullscreen: relative + flex-1 = высота от flex-родителя, НЕ от содержимого.
              InteractiveChart внутри использует absolute inset-0 чтобы заполнить. */}
          <div className={fullscreen ? 'relative flex-1 min-h-0' : ''}>
            <InteractiveChart
              key={`${chartData.symbol}:${chartData.source.stream ? chartStreamKey(chartData.source.stream) : 'legacy'}`}
              data={chartData}
              fullscreen={fullscreen}
              onToggleFullscreen={toggleFullscreen}
            />
          </div>

          {!fullscreen && (
            <div className="text-xs text-tg-hint text-center">
              {chartData.candles.length} свечей | {chartData.interval} | {chartData.range}
                            {' | '}{chartData.source.stream ? `${chartData.source.stream.exchange} · ${chartData.source.stream.market_type}` : 'Legacy-файл, интервал вычислен'}
                            {chartData.source.snapshot_updated_at && ` | Обновлено: ${new Date(chartData.source.snapshot_updated_at * 1000).toLocaleTimeString()}`}
            </div>
          )}
        </>
      )}
    </div>
  );
}
