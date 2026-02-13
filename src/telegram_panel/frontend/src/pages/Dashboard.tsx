import { useEffect, useState, useCallback } from 'react';
import { getDashboard, getActiveTrades, getTradeStats } from '../api/client';
import type { Trade, TradeStats } from '../api/types';
import { StatsCard } from '../components/StatsCard';
import { TradeCard } from '../components/TradeCard';
import { Spinner } from '../components/Spinner';

interface DashboardState {
  strategy: string;
  symbols: string[];
  activeTradesCount: number;
}

export function Dashboard({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [dashboard, setDashboard] = useState<DashboardState | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [dash, active, tradeStats] = await Promise.all([
        getDashboard(),
        getActiveTrades(),
        getTradeStats(),
      ]);
      setDashboard({
        strategy: dash.strategy || (dash as any).strategy_style || 'N/A',
        symbols: dash.symbols || [],
        activeTradesCount: (dash as any).active_trades_count ?? Object.keys(active).length,
      });
      // Convert active trades object to array
      const tradesArray = Array.isArray(active) ? active : Object.values(active);
      setTrades(tradesArray as Trade[]);
      setStats(tradeStats);
    } catch (err) {
      console.error('Dashboard fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    return subscribe('trade_update', () => {
      fetchData();
    });
  }, [subscribe, fetchData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Strategy badge */}
      <div className="flex items-center gap-3">
        <span className="text-lg font-semibold text-tg-text">Dashboard</span>
        {dashboard?.strategy && (
          <span className="px-2.5 py-1 bg-tg-button/20 text-tg-button text-xs font-medium rounded-full">
            {dashboard.strategy}
          </span>
        )}
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-2.5">
        <StatsCard
          label="Active Positions"
          value={dashboard?.activeTradesCount ?? 0}
          trend={trades.length > 0 ? 'up' : 'neutral'}
        />
        <StatsCard
          label="Total P&L"
          value={stats ? `$${stats.total_pnl.toFixed(2)}` : '$0.00'}
          trend={stats && stats.total_pnl > 0 ? 'up' : stats && stats.total_pnl < 0 ? 'down' : 'neutral'}
        />
        <StatsCard
          label="Win Rate"
          value={stats ? `${stats.win_rate}%` : '0%'}
          trend={stats && stats.win_rate > 50 ? 'up' : 'neutral'}
        />
        <StatsCard
          label="Total Trades"
          value={stats?.total_trades ?? 0}
        />
      </div>

      {/* Symbols */}
      {dashboard?.symbols && dashboard.symbols.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {dashboard.symbols.map((s) => (
            <span key={s} className="text-xs bg-tg-section-bg px-2 py-1 rounded text-tg-text/70">
              {s}
            </span>
          ))}
        </div>
      )}

      {/* Active trades */}
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-tg-hint">Active Positions</span>
        {trades.length === 0 ? (
          <div className="text-center py-8 text-tg-hint text-sm">No active positions</div>
        ) : (
          trades.map((trade) => <TradeCard key={trade.symbol} trade={trade} />)
        )}
      </div>
    </div>
  );
}
