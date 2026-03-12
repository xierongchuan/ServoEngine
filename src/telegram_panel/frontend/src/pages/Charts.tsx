import { useEffect, useState, useCallback, useRef } from 'react';
import { getChartData, getDashboard, getActiveTrades } from '../api/client';
import type { ChartData } from '../api/types';
import { InteractiveChart } from '../components/InteractiveChart';
import { Spinner } from '../components/Spinner';

export function Charts({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState('');
  const [selectedRange, setSelectedRange] = useState('1D');
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

  const fetchData = useCallback(async () => {
    if (!selectedSymbol) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getChartData(selectedSymbol, selectedRange);
      setChartData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load chart');
    } finally {
      setLoading(false);
    }
  }, [selectedSymbol, selectedRange]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    const unsub1 = subscribe('trade_update', () => fetchData());
    const unsub2 = subscribe('data_update', () => fetchData());
    return () => { unsub1(); unsub2(); };
  }, [subscribe, fetchData]);

  // Слушаем выход из fullscreen через браузерное событие (Escape / жест)
  useEffect(() => {
    const onFsChange = () => {
      if (!document.fullscreenElement) {
        setFullscreen(false);
      }
    };
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (!fullscreen) {
      // Входим в настоящий полноэкранный режим
      const el = fsContainerRef.current;
      if (el?.requestFullscreen) {
        el.requestFullscreen().then(() => setFullscreen(true)).catch(() => {
          // Fallback если API недоступен — просто ставим state
          setFullscreen(true);
        });
      } else {
        setFullscreen(true);
      }
    } else {
      // Выходим
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      }
      setFullscreen(false);
    }
  }, [fullscreen]);

  return (
    <div
      ref={fsContainerRef}
      className={`flex flex-col gap-3 ${
        fullscreen
          ? 'bg-tg-bg overflow-auto p-3 w-full h-full'
          : 'p-4'
      }`}
    >
      {!fullscreen && <span className="text-lg font-semibold text-tg-text">Charts</span>}

      {/* Symbol selector */}
      {symbols.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {symbols.map((sym) => (
            <button
              key={sym}
              onClick={() => setSelectedSymbol(sym)}
              className={`text-xs px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1.5 ${
                selectedSymbol === sym
                  ? 'bg-tg-button text-white'
                  : 'bg-tg-section-bg text-tg-hint'
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${symbolsWithPositions.has(sym) ? 'bg-green-400' : 'bg-gray-500'}`} />
              {sym}
            </button>
          ))}
        </div>
      )}

      {/* Range selector */}
      {chartData?.available_ranges && (
        <div className="flex flex-wrap gap-1">
          {chartData.available_ranges.map((r) => (
            <button
              key={r}
              onClick={() => setSelectedRange(r)}
              className={`text-[10px] px-2 py-1 rounded transition-colors ${
                selectedRange === r
                  ? 'bg-tg-button text-white'
                  : 'bg-tg-section-bg text-tg-hint'
              }`}
            >
              {r}
            </button>
          ))}
        </div>
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

          <InteractiveChart
            data={chartData}
            fullscreen={fullscreen}
            onToggleFullscreen={toggleFullscreen}
          />

          <div className="text-xs text-tg-hint text-center">
            {chartData.candles.length} candles | {chartData.interval} | {chartData.range}
          </div>
        </>
      )}
    </div>
  );
}
