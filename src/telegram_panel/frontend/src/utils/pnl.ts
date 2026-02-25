import type { Trade } from '@/api/types';

/** ROE% = pnl / margin * 100, where margin = entry * amount / leverage */
export function calcRoePct(trade: Trade, pnl: number): number | null {
  const { entry_price, amount, leverage } = trade;
  if (!entry_price || !amount || !leverage || leverage <= 0) return null;
  const margin = entry_price * amount / leverage;
  if (margin === 0) return null;
  return (pnl / margin) * 100;
}

/** "+$42.35" / "-$13.00" / "$0.00" */
export function formatDollar(value: number, decimals = 2): string {
  const abs = Math.abs(value);
  if (value > 0) return `+$${abs.toFixed(decimals)}`;
  if (value < 0) return `-$${abs.toFixed(decimals)}`;
  return `$${abs.toFixed(decimals)}`;
}

/** "+5.26%" / "-2.10%" / "0.00%" */
export function formatPct(value: number, decimals = 2): string {
  const abs = Math.abs(value);
  if (value > 0) return `+${abs.toFixed(decimals)}%`;
  if (value < 0) return `-${abs.toFixed(decimals)}%`;
  return `${abs.toFixed(decimals)}%`;
}
