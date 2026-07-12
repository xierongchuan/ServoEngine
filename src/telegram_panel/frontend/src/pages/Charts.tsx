import { useEffect, useState, useCallback, useRef } from 'react';
import { getChartData, getDashboard, getActiveTrades } from '../api/client';
import type { ChartData } from '../api/types';
import { InteractiveChart } from '../components/InteractiveChart';
import { Spinner } from '../components/Spinner';
import { Tabs } from '../components/ui/Tabs';
import { StatusDot } from '../components/ui/StatusDot';

export function Charts({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState('');
  const [selectedRange, setSelectedRange] = useState('AUTO');
  const [chartData, setChartData] = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const fsContainerRef = useRef<HTMLDivElement>(null);
  const [symbolsWithPositions, setSymbolsWithPositions] = useState<Set<string>>(new Set());


  useEffect(() => {
    // Use dashboard API which reads from new config system (active.json)
    getDashboard().then((dash) => {
      const all = dash.symbols || [];
      setSymbols(all);
      if (all.length > 0) {
        setSelectedSymbol((prev) => prev || all[0]);
      }
    }).catch(() => {});

    // Fetch active trades to get symbols with positions
    getActiveTrades().then((trades) => {
      const tradesArray = Array.isArray(trades) ? trades : Object.values(trades);
      const symbolsSet = new Set((tradesArray as any[]).map((t: any) => t.symbol));
      setSymbolsWithPositions(symbolsSet);
    }).catch(() => {});
  }, []);

  const fetchData = useCallback(async (showLoading = true) => {
    if (!selectedSymbol) return;
    if (showLoading) {
      setLoading(true);
    }
    setError(null);
    try {
      const data = await getChartData(selectedSymbol, selectedRange);
      setChartData(data);
      if (selectedRange === 'AUTO' && data.range) {
        setSelectedRange(data.range);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load chart');
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }, [selectedSymbol, selectedRange]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    setSelectedRange('AUTO');
  }, [selectedSymbol]);

  useEffect(() => {
    const unsub1 = subscribe('trade_update', () => fetchData(true));

    // Обработка data_update - только для текущего символа, без спиннера
    const handleDataUpdate = (eventData: Record<string, unknown>) => {
      const changedFile = eventData.path as string | undefined;
      if (!changedFile) return;

      // Извлекаем символ из имени файла (например, BTCUSDT.json -> BTC-USDT)
      const fileSymbol = changedFile.replace('.json', '').replace('_', '-');

      // Проверяем: это текущий выбранный символ?
      if (fileSymbol === selectedSymbol) {
        fetchData(false); // false = без спиннера загрузки
      }
    };

    const unsub2 = subscribe('data_update', handleDataUpdate);
    return () => { unsub1(); unsub2(); };
  }, [subscribe, fetchData, selectedSymbol]);

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
      {!fullscreen && <span className="text-lg font-semibold text-tg-text">Charts</span>}

      {/* Symbol selector */}
      {symbols.length > 0 && (
        <Tabs
          value={selectedSymbol}
          onChange={setSelectedSymbol}
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

      {/* Range selector */}
      {chartData?.available_ranges && (
        <Tabs
          value={selectedRange}
          onChange={setSelectedRange}
          options={chartData.available_ranges.map((r) => ({ value: r, label: r }))}
        />
      )}

      {loading && (
        <div className="flex items-center justify-center h-64">
          <Spinner size={32} />
        </div>
      )}

      {error && !loading && (
        <div className="text-center py-12 text-red-400 text-sm">{error}</div>
      )}

      {!loading && !error && chartData && (
        <>
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
              data={chartData}
              fullscreen={fullscreen}
              onToggleFullscreen={toggleFullscreen}
            />
          </div>

          {!fullscreen && (
            <div className="text-xs text-tg-hint text-center">
              {chartData.candles.length} candles | {chartData.interval} | {chartData.range}
            </div>
          )}
        </>
      )}
    </div>
  );
}
