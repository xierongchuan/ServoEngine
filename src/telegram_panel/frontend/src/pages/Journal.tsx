import { useEffect, useState, useCallback } from 'react';
import { getJournal, getJournalStats, getDashboard } from '../api/client';
import type { JournalStats, JournalSymbolStats, IndicatorStatus } from '../api/types';
import { StatsCard } from '../components/StatsCard';
import { Spinner } from '../components/Spinner';
import { Tabs } from '../components/ui/Tabs';
import { StatusDot } from '../components/ui/StatusDot';

interface JournalEntryData {
  time?: string;
  action?: string;
  confidence?: number;
  score?: number;
  confirmations?: number;
  price?: number;
  sl?: number;
  tp?: number;
  pnl?: string;
  reason?: string;
  indicators_status?: IndicatorStatus[];
}

interface JournalData {
  entries: JournalEntryData[];
  trade_plan?: {
    action?: string;
    entry_price?: number;
    planned_sl?: number;
    planned_tp?: number;
    reason?: string;
    confidence?: number;
    score?: number;
    confirmations?: number;
    time?: string;
  };
  last_close_time?: string;
}

const ACTION_FILTERS = ['ALL', 'BUY', 'SELL', 'HOLD', 'CLOSE'] as const;

function actionBadgeClass(action?: string): string {
  switch (action) {
    case 'buy': return 'bg-green-500/20 text-green-400';
    case 'sell': return 'bg-red-500/20 text-red-400';
    case 'close': return 'bg-orange-500/20 text-orange-400';
    default: return 'bg-gray-500/20 text-gray-400';
  }
}

function formatDuration(timeStr: string): string {
  const diff = Date.now() - new Date(timeStr.replace(' ', 'T')).getTime();
  if (isNaN(diff) || diff < 0) return '';
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function calcRR(entry: number, sl: number, tp: number): string | null {
  const risk = Math.abs(entry - sl);
  const reward = Math.abs(tp - entry);
  if (risk === 0) return null;
  return (reward / risk).toFixed(1);
}

function PriceBar({ sl, entry, tp }: { sl: number; entry: number; tp: number }) {
  const isLong = tp > entry;
  const low = Math.min(sl, entry, tp);
  const high = Math.max(sl, entry, tp);
  const range = high - low || 1;
  const entryPct = ((entry - low) / range) * 100;

  return (
    <div className="relative h-1.5 bg-gray-700 rounded-full overflow-hidden mt-1">
      <div
        className="absolute h-full bg-red-500/40 rounded-full"
        style={{
          left: isLong ? '0%' : `${entryPct}%`,
          width: isLong ? `${entryPct}%` : `${100 - entryPct}%`,
        }}
      />
      <div
        className="absolute h-full bg-green-500/40 rounded-full"
        style={{
          left: isLong ? `${entryPct}%` : '0%',
          width: isLong ? `${100 - entryPct}%` : `${entryPct}%`,
        }}
      />
      <div
        className="absolute top-0 w-0.5 h-full bg-tg-text"
        style={{ left: `${entryPct}%` }}
      />
    </div>
  );
}

function pnlColor(pnl?: string): string {
  if (!pnl) return 'text-tg-text';
  if (pnl.startsWith('+')) return 'text-green-400';
  if (pnl.startsWith('-')) return 'text-red-400';
  return 'text-tg-text';
}

function IndicatorBar({ indicators }: { indicators: IndicatorStatus[] }) {
  if (!indicators || indicators.length === 0) return null;

  const okCount = indicators.filter(i => i.ok).length;
  const totalCount = indicators.length;

  return (
    <div className="mt-2 p-2 bg-gray-800/50 rounded-lg">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-tg-hint">Индикаторы</span>
        <span className="text-xs font-medium text-tg-text">
          {okCount}/{totalCount} подтверждены
        </span>
      </div>
      <div className="flex gap-1">
        {indicators.map((ind, idx) => (
          <div
            key={idx}
            className={`flex-1 h-1.5 rounded-full ${
              ind.ok ? 'bg-green-500' : 'bg-gray-600'
            }`}
            title={`${ind.name}: ${ind.detail}`}
          />
        ))}
      </div>
      <div className="mt-1.5 grid grid-cols-2 gap-1 text-[10px]">
        {indicators.map((ind, idx) => (
          <div key={idx} className="flex items-center gap-1">
            <span className={ind.ok ? 'text-green-400' : 'text-gray-500'}>
              {ind.ok ? '✓' : '✗'}
            </span>
            <span className="text-tg-hint truncate" title={ind.detail}>
              {ind.name}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Journal({ subscribe }: { subscribe: (type: string, cb: (data: Record<string, unknown>) => void) => () => void }) {
  const [journal, setJournal] = useState<Record<string, JournalData>>({});
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<JournalStats | null>(null);
  const [actionFilter, setActionFilter] = useState<string>('ALL');
  const [expandedEntry, setExpandedEntry] = useState<number | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [journalData, statsData] = await Promise.all([getJournal(), getJournalStats()]);
      if (journalData && typeof journalData === 'object') {
        setJournal(journalData as any);
        const keys = Object.keys(journalData);
        setSelected((prev) => (prev && keys.includes(prev)) ? prev : keys[0] || '');
      }
      setStats(statsData);
    } catch (err) {
      console.error('Journal fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getDashboard().then((d) => setSymbols(d.symbols || [])).catch(() => {});
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    return subscribe('journal_update', () => fetchData());
  }, [subscribe, fetchData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size={32} />
      </div>
    );
  }

  const allSymbols = [...new Set([...Object.keys(journal), ...symbols])];
  const data = journal[selected];
  const symbolStats: JournalSymbolStats | undefined = stats?.symbols?.[selected];

  const filteredEntries = (data?.entries || [])
    .slice()
    .reverse()
    .filter((e) => actionFilter === 'ALL' || e.action?.toUpperCase() === actionFilter);

  return (
    <div className="flex flex-col gap-4 p-4">
      <span className="text-lg font-semibold text-tg-text">AI Journal</span>

      {/* Summary stats */}
      {stats && (
        <div className="grid grid-cols-3 gap-2">
          <StatsCard label="Decisions" value={stats.total_entries} />
          <StatsCard
            label="Active Plans"
            value={stats.active_plans_count}
            trend={stats.active_plans_count > 0 ? 'up' : 'neutral'}
          />
          <StatsCard
            label="Avg Conf."
            value={`${(stats.avg_confidence * 100).toFixed(0)}%`}
          />
        </div>
      )}

      {/* Symbol selector */}
      <Tabs
        value={selected}
        onChange={(s) => { setSelected(s); setExpandedEntry(null); setActionFilter('ALL'); }}
        options={allSymbols.map((s) => ({
          value: s,
          label: (
            <>
              <StatusDot active={stats?.symbols?.[s]?.has_active_plan ?? false} />
              {s}
            </>
          ),
        }))}
      />

      {!data ? (
        <div className="flex flex-col items-center gap-2 py-12 text-tg-hint">
          <span className="text-sm">
            {selected ? `No journal entries for ${selected}` : 'Select a symbol to view AI decisions'}
          </span>
        </div>
      ) : (
        <>
          {/* Cooldown / last close indicator */}
          {symbolStats?.in_cooldown && (
            <div className="bg-blue-500/10 text-blue-400 text-xs rounded-lg px-3 py-2">
              Cooldown: {symbolStats.cooldown_remaining_hours.toFixed(1)}h remaining
            </div>
          )}
          {symbolStats?.last_close_time && !symbolStats?.in_cooldown && (
            <div className="text-xs text-tg-hint">
              Last close: {symbolStats.last_close_time}
            </div>
          )}

          {/* Trade plan */}
          {data.trade_plan && (
            <div className="bg-tg-section-bg rounded-xl p-3.5 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-tg-text">Active Trade Plan</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${actionBadgeClass(data.trade_plan.action)}`}>
                    {data.trade_plan.action?.toUpperCase() || 'N/A'}
                  </span>
                </div>
                {data.trade_plan.time && (
                  <span className="text-xs text-tg-hint">{formatDuration(data.trade_plan.time)}</span>
                )}
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs">
                <div><span className="text-tg-hint">Entry: </span><span className="text-tg-text">${data.trade_plan.entry_price}</span></div>
                <div><span className="text-tg-hint">Confidence: </span><span className="text-tg-text">{((data.trade_plan.confidence || 0) * 100).toFixed(0)}%</span></div>
                <div><span className="text-tg-hint">SL: </span><span className="text-red-400">${data.trade_plan.planned_sl}</span></div>
                <div><span className="text-tg-hint">TP: </span><span className="text-green-400">${data.trade_plan.planned_tp}</span></div>
                {data.trade_plan.score != null && data.trade_plan.score > 0 && (
                  <div><span className="text-tg-hint">Score: </span><span className="text-yellow-400">{data.trade_plan.score}</span></div>
                )}
                {data.trade_plan.confirmations != null && data.trade_plan.confirmations > 0 && (
                  <div><span className="text-tg-hint">Conf: </span><span className="text-blue-400">{data.trade_plan.confirmations}</span></div>
                )}
              </div>

              {data.trade_plan.entry_price && data.trade_plan.planned_sl && data.trade_plan.planned_tp && (
                <>
                  <div className="flex items-center gap-3 text-xs">
                    <span className="text-tg-hint">R/R: <span className="text-tg-text font-medium">{calcRR(data.trade_plan.entry_price, data.trade_plan.planned_sl, data.trade_plan.planned_tp)}</span></span>
                  </div>
                  <PriceBar sl={data.trade_plan.planned_sl} entry={data.trade_plan.entry_price} tp={data.trade_plan.planned_tp} />
                </>
              )}

              {data.trade_plan.reason && (
                <p className="text-xs text-tg-hint/80 mt-1">{data.trade_plan.reason}</p>
              )}
            </div>
          )}

          {/* Action filter + decisions header */}
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-tg-hint">
              Decisions ({filteredEntries.length})
            </span>
            <Tabs
              value={actionFilter}
              onChange={(f) => { setActionFilter(f); setExpandedEntry(null); }}
              options={ACTION_FILTERS.map(f => ({ value: f, label: f }))}
            />
          </div>

          {/* Decision entries */}
          <div className="flex flex-col gap-2">
            {filteredEntries.length === 0 ? (
              <div className="text-center py-6 text-tg-hint text-xs">
                No {actionFilter !== 'ALL' ? actionFilter.toLowerCase() : ''} decisions
              </div>
            ) : (
              filteredEntries.map((entry, i) => {
                const isExpanded = expandedEntry === i;
                return (
                  <div
                    key={i}
                    onClick={() => setExpandedEntry(isExpanded ? null : i)}
                    className="bg-tg-section-bg rounded-xl p-3 flex flex-col gap-1.5 cursor-pointer active:bg-tg-section-bg/80 transition-colors"
                  >
                    {/* Header */}
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${actionBadgeClass(entry.action)}`}>
                          {entry.action?.toUpperCase() || 'HOLD'}
                        </span>
                        {entry.score != null && (
                          <span className={`text-[10px] ${entry.score > 0 ? 'text-yellow-400' : 'text-gray-500'}`}>
                            Score: {entry.score}
                          </span>
                        )}
                        {entry.confirmations != null && (
                          <span className={`text-[10px] ${entry.confirmations > 0 ? 'text-blue-400' : 'text-gray-500'}`}>
                            Conf: {entry.confirmations}
                          </span>
                        )}
                        {entry.confidence != null && (
                          <div className="flex items-center gap-1">
                            <div className="w-8 h-1 bg-gray-700 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-tg-button rounded-full"
                                style={{ width: `${entry.confidence * 100}%` }}
                              />
                            </div>
                            <span className="text-[10px] text-tg-hint">{(entry.confidence * 100).toFixed(0)}%</span>
                          </div>
                        )}
                      </div>
                      <span className="text-xs text-tg-hint">{entry.time || ''}</span>
                    </div>

                    {/* Price + PnL */}
                    {entry.price != null && (
                      <div className="flex gap-4 text-xs">
                        <span className="text-tg-hint">Price: <span className="text-tg-text">${entry.price}</span></span>
                        {entry.pnl && entry.pnl !== '\u2014' && (
                          <span className="text-tg-hint">P&L: <span className={pnlColor(entry.pnl)}>{entry.pnl}</span></span>
                        )}
                      </div>
                    )}

                    {/* Reason */}
                    {entry.reason && (
                      <p className={`text-xs text-tg-hint/70 ${isExpanded ? '' : 'line-clamp-2'}`}>{entry.reason}</p>
                    )}

                    {/* Indicators status (если есть) */}
                    {isExpanded && entry.indicators_status && (
                      <IndicatorBar indicators={entry.indicators_status} />
                    )}

                    {/* Expanded details */}
                    {isExpanded && (entry.sl || entry.tp) && (
                      <div className="grid grid-cols-2 gap-2 text-xs mt-1 pt-1.5 border-t border-white/5">
                        {entry.sl != null && entry.sl !== 0 && (
                          <div><span className="text-tg-hint">SL: </span><span className="text-red-400">${entry.sl}</span></div>
                        )}
                        {entry.tp != null && entry.tp !== 0 && (
                          <div><span className="text-tg-hint">TP: </span><span className="text-green-400">${entry.tp}</span></div>
                        )}
                        {entry.sl && entry.tp && entry.price && entry.sl !== 0 && entry.tp !== 0 && (
                          <div><span className="text-tg-hint">R/R: </span><span className="text-tg-text">{calcRR(entry.price, entry.sl, entry.tp)}</span></div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </>
      )}
    </div>
  );
}
