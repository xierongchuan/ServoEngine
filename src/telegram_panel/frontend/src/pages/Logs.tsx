import { useEffect, useState, useCallback } from 'react';
import { getSystemLogs, getSymbolLogs, getDashboard } from '../api/client';
import { LogViewer } from '../components/LogViewer';
import { Spinner } from '../components/Spinner';

type LogSource = 'system' | string;

export function Logs({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [source, setSource] = useState<LogSource>('system');
  const [lines, setLines] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);

  const fetchLogs = useCallback(async (src: string) => {
    try {
      let data: string[];
      if (src === 'system') {
        data = await getSystemLogs(300);
      } else {
        data = await getSymbolLogs(src, 300);
      }
      // API may return { lines: [...] } or just [...]
      if (Array.isArray(data)) {
        setLines(data);
      } else if ((data as any).lines) {
        setLines((data as any).lines);
      }
    } catch (err) {
      console.error('Logs fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getDashboard().then((d) => {
      setSymbols(d.symbols || []);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchLogs(source);
  }, [source, fetchLogs]);

  useEffect(() => {
    const unsub1 = subscribe('log_line', () => {
      if (source === 'system') fetchLogs('system');
    });
    const unsub2 = subscribe('log_symbol', (data) => {
      if (source !== 'system' && (data as any).source === source) {
        fetchLogs(source);
      }
    });
    return () => { unsub1(); unsub2(); };
  }, [subscribe, source, fetchLogs]);

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold text-tg-text">Logs</span>
        <button
          onClick={() => setAutoScroll(!autoScroll)}
          className={`text-xs px-2 py-1 rounded ${
            autoScroll ? 'bg-tg-button/20 text-tg-button' : 'bg-tg-section-bg text-tg-hint'
          }`}
        >
          Auto-scroll {autoScroll ? 'ON' : 'OFF'}
        </button>
      </div>

      {/* Source selector */}
      <div className="flex flex-wrap gap-1.5">
        <button
          onClick={() => setSource('system')}
          className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
            source === 'system' ? 'bg-tg-button text-white' : 'bg-tg-section-bg text-tg-hint'
          }`}
        >
          System
        </button>
        {symbols.map((s) => (
          <button
            key={s}
            onClick={() => setSource(s)}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
              source === s ? 'bg-tg-button text-white' : 'bg-tg-section-bg text-tg-hint'
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-40">
          <Spinner size={32} />
        </div>
      ) : (
        <LogViewer lines={lines} autoScroll={autoScroll} />
      )}
    </div>
  );
}
