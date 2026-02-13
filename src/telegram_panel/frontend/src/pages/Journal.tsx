import { useEffect, useState, useCallback } from 'react';
import { getJournal, getDashboard } from '../api/client';
import { Spinner } from '../components/Spinner';

interface JournalData {
  entries: Array<{
    time?: string;
    action?: string;
    confidence?: number;
    price?: number;
    sl?: number;
    tp?: number;
    pnl?: string;
    reason?: string;
  }>;
  trade_plan?: {
    action?: string;
    entry_price?: number;
    planned_sl?: number;
    planned_tp?: number;
    reason?: string;
    confidence?: number;
    time?: string;
  };
}

export function Journal({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [journal, setJournal] = useState<Record<string, JournalData>>({});
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [loading, setLoading] = useState(true);

  const fetchJournal = useCallback(async () => {
    try {
      const data = await getJournal();
      if (data && typeof data === 'object') {
        setJournal(data as any);
        const keys = Object.keys(data);
        if (keys.length > 0 && !selected) {
          setSelected(keys[0]);
        }
      }
    } catch (err) {
      console.error('Journal fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    getDashboard().then((d) => setSymbols(d.symbols || [])).catch(() => {});
    fetchJournal();
  }, [fetchJournal]);

  useEffect(() => {
    return subscribe('journal_update', () => fetchJournal());
  }, [subscribe, fetchJournal]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  const allSymbols = [...new Set([...Object.keys(journal), ...symbols])];
  const data = journal[selected];

  return (
    <div className="flex flex-col gap-4 p-4">
      <span className="text-lg font-semibold text-tg-text">AI Journal</span>

      {/* Symbol selector */}
      <div className="flex flex-wrap gap-1.5">
        {allSymbols.map((s) => (
          <button
            key={s}
            onClick={() => setSelected(s)}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
              selected === s ? 'bg-tg-button text-white' : 'bg-tg-section-bg text-tg-hint'
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {!data ? (
        <div className="text-center py-8 text-tg-hint text-sm">
          {selected ? `No journal entries for ${selected}` : 'Select a symbol'}
        </div>
      ) : (
        <>
          {/* Trade plan */}
          {data.trade_plan && (
            <div className="bg-tg-section-bg rounded-xl p-3.5 flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-tg-text">Active Trade Plan</span>
                <span className={`text-xs px-1.5 py-0.5 rounded ${
                  data.trade_plan.action === 'buy'
                    ? 'bg-green-500/20 text-green-400'
                    : data.trade_plan.action === 'sell'
                    ? 'bg-red-500/20 text-red-400'
                    : 'bg-gray-500/20 text-gray-400'
                }`}>
                  {data.trade_plan.action?.toUpperCase() || 'N/A'}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div><span className="text-tg-hint">Entry: </span><span className="text-tg-text">${data.trade_plan.entry_price}</span></div>
                <div><span className="text-tg-hint">Confidence: </span><span className="text-tg-text">{((data.trade_plan.confidence || 0) * 100).toFixed(0)}%</span></div>
                <div><span className="text-tg-hint">SL: </span><span className="text-red-400">${data.trade_plan.planned_sl}</span></div>
                <div><span className="text-tg-hint">TP: </span><span className="text-green-400">${data.trade_plan.planned_tp}</span></div>
              </div>
              {data.trade_plan.reason && (
                <p className="text-xs text-tg-hint/80 mt-1">{data.trade_plan.reason}</p>
              )}
            </div>
          )}

          {/* Decision entries */}
          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-tg-hint">
              Decisions ({data.entries?.length || 0})
            </span>
            {(data.entries || []).slice().reverse().map((entry, i) => (
              <div key={i} className="bg-tg-section-bg rounded-xl p-3 flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                      entry.action === 'buy' ? 'bg-green-500/20 text-green-400'
                        : entry.action === 'sell' ? 'bg-red-500/20 text-red-400'
                        : 'bg-gray-500/20 text-gray-400'
                    }`}>
                      {entry.action?.toUpperCase() || 'HOLD'}
                    </span>
                    {entry.confidence && (
                      <span className="text-xs text-tg-hint">
                        {(entry.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-tg-hint">{entry.time || ''}</span>
                </div>
                {entry.price && (
                  <div className="flex gap-4 text-xs">
                    <span className="text-tg-hint">Price: <span className="text-tg-text">${entry.price}</span></span>
                    {entry.pnl && <span className="text-tg-hint">P&L: <span className="text-tg-text">{entry.pnl}</span></span>}
                  </div>
                )}
                {entry.reason && (
                  <p className="text-xs text-tg-hint/70 line-clamp-2">{entry.reason}</p>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
