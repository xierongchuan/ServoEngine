import { useEffect, useState, useCallback } from 'react';
import { getActiveTrades, getTradeHistory, getTradeStats, getDisabledSymbols, syncPositions } from '../api/client';
import type { Trade, TradeStats } from '../api/types';
import { TradeCard } from '../components/TradeCard';
import { StatsCard } from '../components/StatsCard';
import { Spinner } from '../components/Spinner';
import { calcRoePct, formatDollar, formatPct } from '../utils/pnl';

type SubTab = 'active' | 'history';

export function Trades({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [subTab, setSubTab] = useState<SubTab>('active');
  const [activeTrades, setActiveTrades] = useState<Trade[]>([]);
  const [history, setHistory] = useState<Trade[]>([]);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [disabledSymbols, setDisabledSymbols] = useState<string[]>([]);
  const [syncing, setSyncing] = useState(false);

  const fetchActive = useCallback(async () => {
    try {
      const data = await getActiveTrades();
      const arr = Array.isArray(data) ? data : Object.values(data);
      setActiveTrades(arr as Trade[]);
    } catch {}
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const data = await getTradeHistory(50, 0);
      setHistory((data as any).trades || []);
      setHistoryTotal((data as any).total || 0);
    } catch {}
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const data = await getTradeStats();
      setStats(data);
    } catch {}
  }, []);

  const fetchDisabled = useCallback(async () => {
    try {
      const data = await getDisabledSymbols();
      setDisabledSymbols(data.disabled_symbols || []);
    } catch {}
  }, []);

  const handleUpdate = useCallback(() => {
    fetchActive();
    fetchDisabled();
  }, [fetchActive, fetchDisabled]);

  const handleSync = useCallback(async () => {
    if (syncing) return;
    setSyncing(true);
    try {
      await syncPositions();
      await Promise.all([fetchActive(), fetchHistory(), fetchStats()]);
    } catch (e) {
      console.error('Sync failed:', e);
    } finally {
      setSyncing(false);
    }
  }, [syncing, fetchActive, fetchHistory, fetchStats]);

  useEffect(() => {
    Promise.all([fetchActive(), fetchHistory(), fetchStats(), fetchDisabled()]).finally(() => setLoading(false));
  }, [fetchActive, fetchHistory, fetchStats, fetchDisabled]);

  useEffect(() => {
    const unsub1 = subscribe('trade_update', () => fetchActive());
    const unsub2 = subscribe('trade_closed', () => {
      fetchActive();
      fetchHistory();
      fetchStats();
    });
    const unsub3 = subscribe('config_changed', () => fetchDisabled());
    return () => { unsub1(); unsub2(); unsub3(); };
  }, [subscribe, fetchActive, fetchHistory, fetchStats, fetchDisabled]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold text-tg-text">Trades</span>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="text-xs px-3 py-1.5 rounded-lg bg-tg-button/20 text-tg-button hover:bg-tg-button/30 disabled:opacity-50 transition-colors"
        >
          {syncing ? '⏳ Syncing…' : '🔄 Sync'}
        </button>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 gap-2">
          <StatsCard label="Net P&L" value={`$${(stats.total_net_pnl ?? 0).toFixed(2)}`} trend={(stats.total_net_pnl ?? 0) > 0 ? 'up' : (stats.total_net_pnl ?? 0) < 0 ? 'down' : 'neutral'} />
          <StatsCard label="Gross P&L" value={`$${(stats.total_pnl ?? 0).toFixed(2)}`} trend={(stats.total_pnl ?? 0) > 0 ? 'up' : (stats.total_pnl ?? 0) < 0 ? 'down' : 'neutral'} />
          <StatsCard label="Win Rate" value={`${stats.win_rate ?? 0}%`} trend={(stats.win_rate ?? 0) > 50 ? 'up' : 'neutral'} />
          <StatsCard label="Trades" value={stats.total_trades} />
        </div>
      )}

      {/* Sub-tabs */}
      <div className="flex gap-2">
        <button
          onClick={() => setSubTab('active')}
          className={`text-sm px-4 py-1.5 rounded-lg transition-colors ${
            subTab === 'active' ? 'bg-tg-button text-white' : 'bg-tg-section-bg text-tg-hint'
          }`}
        >
          Active ({activeTrades.length})
        </button>
        <button
          onClick={() => setSubTab('history')}
          className={`text-sm px-4 py-1.5 rounded-lg transition-colors ${
            subTab === 'history' ? 'bg-tg-button text-white' : 'bg-tg-section-bg text-tg-hint'
          }`}
        >
          History ({historyTotal})
        </button>
      </div>

      {/* Content */}
      <div className="flex flex-col gap-2">
        {subTab === 'active' ? (
          activeTrades.length === 0 ? (
            <div className="text-center py-8 text-tg-hint text-sm">No active positions</div>
          ) : (
            activeTrades.map((t) => (
              <TradeCard
                key={t.symbol}
                trade={t}
                disabledSymbols={disabledSymbols}
                onUpdate={handleUpdate}
              />
            ))
          )
        ) : (
          history.length === 0 ? (
            <div className="text-center py-8 text-tg-hint text-sm">No trade history</div>
          ) : (
            history.map((t, i) => {
              const net = t.net_pnl ?? t.last_pnl ?? 0;
              const gross = t.last_pnl ?? 0;
              const hasFees = t.net_pnl != null;
              const roe = calcRoePct(t, net);
              return (
                <div key={i} className="bg-tg-section-bg rounded-xl p-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-tg-text">{t.symbol}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      t.side === 'LONG' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                    }`}>
                      {t.side}
                    </span>
                  </div>
                  <div className="flex flex-col items-end">
                    <div className="flex items-center gap-1.5">
                      <span className={`text-sm font-semibold ${
                        net >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {formatDollar(net)}
                      </span>
                      {roe !== null && (
                        <span className={`text-xs ${
                          net >= 0 ? 'text-green-400/70' : 'text-red-400/70'
                        }`}>
                          {formatPct(roe)}
                        </span>
                      )}
                    </div>
                    {hasFees && (
                      <span className="text-[10px] text-tg-hint">
                        gross: {formatDollar(gross)}
                      </span>
                    )}
                    <span className="text-xs text-tg-hint">
                      {t.close_time ? new Date(t.close_time).toLocaleDateString() : ''}
                    </span>
                  </div>
                </div>
              );
            })
          )
        )}
      </div>
    </div>
  );
}
