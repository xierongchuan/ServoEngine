export interface ExecutionRow {
  source: 'execution' | 'spot';
  account: string;
  owner: string | null;
  symbol: string | null;
  operation: string;
  state: string;
  order_id: string | null;
  updated: number | null;
  reason: string | null;
  quantity: string | number | null;
  terminal?: boolean | number | null;
  position_id?: string;
}

export interface ExecutionState {
  status: 'ok' | 'attention' | 'not_initialized';
  pending: ExecutionRow[];
  blocks: ExecutionRow[];
  ownership: ExecutionRow[];
  counts: { pending: number; blocks: number; ownership: number };
  sources: { execution: boolean; spot: boolean };
  truncated: { pending: boolean; blocks: boolean; ownership: boolean };
  checked_at: number;
  limit: number;
}
