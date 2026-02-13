import type { Trade } from '@/api/types';

function formatDuration(openTime: string): string {
  const diff = Date.now() - new Date(openTime).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

export function TradeCard({ trade }: { trade: Trade }) {
  const isLong = trade.side === 'LONG';
  const pnl = trade.last_pnl;
  const pnlPositive = pnl >= 0;

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
        <span
          className={`text-sm font-semibold ${
            pnlPositive ? 'text-green-400' : 'text-red-400'
          }`}
        >
          {pnlPositive ? '+' : ''}{pnl.toFixed(2)}%
        </span>
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
      </div>
    </div>
  );
}
