# AGENTS.md (Architect Mode)

This file provides architectural guidance for this repository.

**⚠️ CRITICAL: All operations (running, testing, building) MUST use podman containers. Never run Python or npm commands directly on host.**

## Non-Obvious Architecture Rules

- **Process isolation** - Each trading symbol runs in its own process with independent event loop; multiprocessing.Manager for shared state
- **Singleton per worker** - `MarketRegimeDetector` and `PerformanceTracker` are singletons per process; not thread-safe across processes
- **Class-level caching** - `BingXClient` has class-level `_positions_cache` (5s TTL) and `_balance_cache` (10s TTL) shared across instances
- **WebSocket optional** - `ws_data_provider.py` provides real-time kline cache but is optional; uses multiprocessing.Manager proxy for cross-process access
- **Prompt system** - Modular prompt builder with strategy-specific templates; AI responses parsed as JSON
- **Retry architecture** - BingX: 3 retries (1s→2s→4s); LLM: exponential backoff; WebSocket: auto-reconnect (1s→60s max)
- **Strategy modes** - HYBRID uses deterministic signal scoring + AI confirmation; HYBRID_VETO uses AI veto only (APPROVE/REJECT English-only)
