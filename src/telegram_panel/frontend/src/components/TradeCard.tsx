import type { Trade } from '@/api/types';
import { disableSymbol, enableSymbol, closePosition } from '@/api/client';
import { calcRoePct, formatDollar, formatPct } from '@/utils/pnl';
import { useState } from 'react';

function formatDuration(openTime: string): string {
  const diff = Date.now() - new Date(openTime).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

export function TradeCard({ trade, disabledSymbols = [], onUpdate }: {
  trade: Trade;
  disabledSymbols?: string[];
  onUpdate?: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const isLong = trade.side === 'LONG';
  const grossPnl = trade.last_pnl ?? 0;
  const netPnl = trade.net_pnl ?? grossPnl;
  const fees = trade.estimated_total_fees ?? 0;
  const isPositive = netPnl >= 0;
  const netRoe = calcRoePct(trade, netPnl);

  // Check if this symbol is disabled (compare both formats)
  const normalizedSymbol = trade.symbol.replace('-', '').toUpperCase();
  const isDisabled = disabledSymbols.some(s => s.replace('-', '').toUpperCase() === normalizedSymbol);

  const handleStop = async () => {
    if (loading) return;
    setLoading(true);
    try {
      await disableSymbol(trade.symbol);
      onUpdate?.();
    } catch (e) {
      console.error('Failed to disable symbol:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleStart = async () => {
    if (loading) return;
    setLoading(true);
    try {
      await enableSymbol(trade.symbol);
      onUpdate?.();
    } catch (e) {
      console.error('Failed to enable symbol:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleClose = async () => {
    if (loading) return;
    if (!confirm(`Close ${trade.symbol} ${trade.side} position?`)) return;
    setLoading(true);
    try {
      await closePosition(trade.symbol);
      onUpdate?.();
    } catch (e: any) {
      alert(`Failed to close ${trade.symbol}: ${e?.message || 'Unknown error'}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-tg-section-bg rounded-xl p-3.5 border border-white/5">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-tg-text">{trade.symbol}</span>
          <span
            className={`text-xs font-medium px-2 py-0.5 rounded-full ${
              isLong
                ? 'bg-green-500/15 text-green-400'
                : 'bg-red-500/15 text-red-400'
            }`}
          >
            {trade.side}
          </span>
          <span className="text-xs text-tg-hint bg-tg-bg px-1.5 py-0.5 rounded">
            {trade.leverage}x
          </span>
        </div>
        <div className="flex flex-col items-end">
          <span
            className={`text-sm font-semibold ${
              isPositive ? 'text-green-400' : 'text-red-400'
            }`}
          >
            {formatDollar(netPnl)}
          </span>
          {netRoe !== null && (
            <span
              className={`text-[11px] ${
                isPositive ? 'text-green-400/70' : 'text-red-400/70'
              }`}
            >
              {formatPct(netRoe)}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <div className="flex justify-between">
          <span className="text-tg-hint">Entry</span>
          <span className="text-tg-text">{trade.entry_price}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-tg-hint">Current</span>
          <span className="text-tg-text">{trade.current_price}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-tg-hint">Amount</span>
          <span className="text-tg-text">{trade.amount}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-tg-hint">Duration</span>
          <span className="text-tg-text">{formatDuration(trade.open_time)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-tg-hint">Gross</span>
          <span className={`${grossPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {formatDollar(grossPnl)}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-tg-hint">Fees</span>
          <span className="text-orange-400">-${fees.toFixed(2)}</span>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex gap-2 mt-3 pt-3 border-t border-white/5">
        {isDisabled ? (
          <button
            onClick={handleStart}
            disabled={loading}
            className="flex-1 text-xs py-2 px-3 rounded-lg bg-green-500/20 text-green-400 hover:bg-green-500/30 disabled:opacity-50 transition-colors"
          >
            ▶️ Start
          </button>
        ) : (
          <button
            onClick={handleStop}
            disabled={loading}
            className="flex-1 text-xs py-2 px-3 rounded-lg bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30 disabled:opacity-50 transition-colors"
          >
            ⏹️ Stop
          </button>
        )}
        <button
          onClick={handleClose}
          disabled={loading}
          className="flex-1 text-xs py-2 px-3 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 disabled:opacity-50 transition-colors"
        >
          ❌ Close
        </button>
      </div>
    </div>
  );
}
