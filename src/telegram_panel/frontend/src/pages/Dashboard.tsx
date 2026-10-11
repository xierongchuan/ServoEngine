import { useEffect, useState, useCallback } from 'react';
import { AuthenticationError, getDashboard, getActiveTrades, getTradeStats, getExecutionState } from '../api/client';
import type { ExecutionState } from '../api/executionTypes';
import type { Trade, TradeStats } from '../api/types';
import { StatsCard } from '../components/StatsCard';
import { TradeCard } from '../components/TradeCard';
import { Spinner } from '../components/Spinner';
import { Card } from '../components/ui/Card';
import { StatusDot } from '../components/ui/StatusDot';

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
  const [execution, setExecution] = useState<ExecutionState | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);

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
    let disposed = false;
    let inFlight = false;
    let authFailed = false;
    let controller: AbortController | null = null;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      if (disposed || inFlight || authFailed) return;
      inFlight = true;
      controller = new AbortController();
      timeout = setTimeout(() => controller?.abort(), 10000);
      try {
        const state = await getExecutionState(controller.signal);
        if (!disposed) { setExecution(state); setExecutionError(null); }
      } catch (error) {
        if (!disposed) {
          authFailed = error instanceof AuthenticationError;
          setExecutionError(error instanceof Error && error.name !== 'AbortError'
            ? error.message : 'Чтение журнала не завершено. Повторим автоматически.');
        }
      } finally {
        clearTimeout(timeout);
        inFlight = false;
      }
    };
    void refresh();
    // Для execution SQLite нет обязательного WS-события: polling читает новый durable snapshot.
    const polling = setInterval(() => { if (!document.hidden) void refresh(); }, 15000);
    const onVisible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      disposed = true;
      controller?.abort();
      clearTimeout(timeout);
      clearInterval(polling);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  // Extract symbols that have active positions
  const symbolsWithPositions = new Set(trades.map(t => t.symbol));

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
          label="Net P&L"
          value={stats ? `$${(stats.total_net_pnl ?? stats.total_pnl ?? 0).toFixed(2)}` : '$0.00'}
          trend={stats && (stats.total_net_pnl ?? stats.total_pnl ?? 0) > 0 ? 'up' : stats && (stats.total_net_pnl ?? stats.total_pnl ?? 0) < 0 ? 'down' : 'neutral'}
        />
        <StatsCard
          label="Win Rate"
          value={stats ? `${stats.win_rate ?? 0}%` : '0%'}
          trend={stats && (stats.win_rate ?? 0) > 50 ? 'up' : 'neutral'}
        />
        <StatsCard
          label="Total Trades"
          value={stats?.total_trades ?? 0}
        />
      </div>

      <Card title="Исполнение: журнал и паузы">
        <div role="status" aria-live="polite" aria-atomic="true" className="text-sm text-tg-text">
          {!execution && !executionError ? 'Загрузка журнала исполнения…'
            : execution?.status === 'not_initialized' ? 'Журналы ещё не созданы — состояние исполнения неизвестно.'
            : execution ? `Ожидающие команды: ${execution.counts.pending} · Блокировки: ${execution.counts.blocks}` : ''}
        </div>
        {executionError && <div role="alert" className="text-sm text-amber-400">
          {executionError}{execution && ' Показан последний прочитанный снимок, он может быть устаревшим.'}
        </div>}
        {execution && (execution.counts.pending > 0 || execution.counts.blocks > 0) && (
          <details className="text-sm text-tg-text">
            <summary className="cursor-pointer min-h-11 py-2">Ожидающие команды и причины пауз</summary>
            <ul className="flex flex-col gap-2 mt-2">
              {[...execution.blocks.slice(0, 5), ...execution.pending.slice(0, 5)].map((row, index) => (
                <li key={`${row.source}:${row.account}:${row.symbol}:${index}`} className="break-words">
                  <div>{row.source} · {row.symbol || 'весь аккаунт'} · {row.operation} · {row.state}</div>
                  <div className="text-tg-hint">Аккаунт: {row.account} · Владелец: {row.owner || '—'}</div>
                  {row.order_id && <div>Ордер: {row.order_id}</div>}
                  {row.quantity !== null && <div>Количество: {row.quantity}</div>}
                  {row.updated !== null && <div>Обновлено: {new Date(row.updated * 1000).toLocaleString()}</div>}
                  {row.reason && <div className="text-amber-400">{row.reason}</div>}
                </li>
              ))}
            </ul>
            {(execution.counts.pending > 5 || execution.counts.blocks > 5) &&
              <p className="text-tg-hint mt-2">Показаны первые 5 записей каждого типа.</p>}
          </details>
        )}
        <p className="text-xs text-tg-hint">
          При работающем торговом процессе сверка (reconcile) выполняется автоматически.
          Панель только читает журнал каждые 15 секунд: неоднозначные результаты и паузы
          могут требовать проверки оператором и здесь не снимаются.
        </p>
      </Card>

      {/* Symbols */}
      {dashboard?.symbols && dashboard.symbols.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {dashboard.symbols.map((s) => (
            <span key={s} className="text-xs bg-tg-section-bg px-2 py-1 rounded text-tg-text/70 flex items-center gap-1.5">
              <StatusDot active={symbolsWithPositions.has(s)} />
              {s}
            </span>
          ))}
        </div>
      )}

      {/* Active trades */}
      <Card title="Active Positions">
        {trades.length === 0 ? (
          <div className="text-center py-8 text-tg-hint text-sm">No active positions</div>
        ) : (
          trades.map((trade) => <TradeCard key={trade.symbol} trade={trade} onUpdate={fetchData} />)
        )}
      </Card>
    </div>
  );
}
