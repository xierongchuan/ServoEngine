import type { ChartData } from './types';

export interface ChartStream {
  exchange: string;
  market_type: string;
  symbol: string;
  interval: string;
}

export const chartStreamKey = (stream: ChartStream) =>
  `${stream.exchange}:${stream.market_type}:${stream.symbol}:${stream.interval}`;

export interface ChartSnapshot extends ChartData {
  available_sources: ChartStream[];
  source: {
    kind: 'legacy_file' | 'market_snapshot';
    stream: ChartStream | null;
    interval_inferred: boolean;
    interval_seconds: number | null;
    snapshot_updated_at: number | null;
    last_candle_at: number;
    stale: boolean | null;
    ready: boolean | null;
    age_seconds: number | null;
    status: string | null;
    version: number | null;
  };
}
