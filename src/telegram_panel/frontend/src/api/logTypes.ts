export interface LogEvent {
  schema_version: number;
  event_id: string;
  sequence: number;
  timestamp: string;
  pid: number;
  run_id: string;
  logger: string;
  level: string;
  category: string;
  message: string;
  elapsed: string;
  duration_ms: number | null;
  service?: string | null;
  exchange?: string | null;
  market_type?: string | null;
  symbol?: string | null;
  instance_id?: string | null;
  strategy?: string | null;
  profile?: string | null;
  request_id?: string | null;
  order_id?: string | null;
  position_id?: string | null;
  cycle_id?: string | null;
  source?: string | null;
}

export interface LogEventsResponse {
  events: LogEvent[];
  next_cursor: string | null;
  live_cursor: string | null;
  has_more: boolean;
  watermark: number;
}

export interface LogSourcesResponse {
  categories: string[];
  levels: string[];
  sources: string[];
  exchanges: string[];
  symbols: string[];
  instances: string[];
  legacy_sources: { source: string; filename: string }[];
  watermark: number;
}

export interface LogTailResponse {
  source: string;
  lines: string[];
}

export interface LogFilters {
  level?: string;
  category?: string;
  exchange?: string;
  symbol?: string;
  instance?: string;
  search?: string;
  order?: string;
  request?: string;
  source?: string;
  time_from?: string;
  time_to?: string;
  cursor?: string;
  limit?: number;
}
