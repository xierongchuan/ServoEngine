import { useEffect, useState, useCallback } from 'react';
import { getSystemLogs, getSymbolLogs, getDashboard, getActiveTrades } from '../api/client';
import { LogViewer } from '../components/LogViewer';
import { Spinner } from '../components/Spinner';
import { Button } from '../components/ui/Button';
import { Tabs } from '../components/ui/Tabs';
import { StatusDot } from '../components/ui/StatusDot';

type LogSource = 'system' | string;

export function Logs({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [source, setSource] = useState<LogSource>('system');
  const [lines, setLines] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const [symbolsWithPositions, setSymbolsWithPositions] = useState<Set<string>>(new Set());

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

    // Fetch active trades to get symbols with positions
    const fetchPositions = () => {
      getActiveTrades().then((trades) => {
        const tradesArray = Array.isArray(trades) ? trades : Object.values(trades);
        const symbolsSet = new Set((tradesArray as any[]).map((t: any) => t.symbol));
        setSymbolsWithPositions(symbolsSet);
      }).catch(() => {});
    };
    fetchPositions();

    // Refresh positions every 10 seconds
    const interval = setInterval(fetchPositions, 10000);
    return () => clearInterval(interval);
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
    const unsub3 = subscribe('trade_update', () => {
      // Refresh positions when trades change
      getActiveTrades().then((trades) => {
        const tradesArray = Array.isArray(trades) ? trades : Object.values(trades);
        const symbolsSet = new Set((tradesArray as any[]).map((t: any) => t.symbol));
        setSymbolsWithPositions(symbolsSet);
      }).catch(() => {});
    });
    return () => { unsub1(); unsub2(); unsub3(); };
  }, [subscribe, source, fetchLogs]);

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold text-tg-text">Logs</span>
        <Button
          onClick={() => setAutoScroll(!autoScroll)}
          variant={autoScroll ? 'secondary' : 'ghost'}
          size="sm"
        >
          Auto-scroll {autoScroll ? 'ON' : 'OFF'}
        </Button>
      </div>

      {/* Source selector */}
      <Tabs
        value={source}
        onChange={setSource}
        options={[
          { value: 'system', label: 'System' },
          ...symbols.map((s) => ({
            value: s,
            label: (
              <>
                <StatusDot active={symbolsWithPositions.has(s)} />
                {s}
              </>
            ),
          })),
        ]}
      />

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
