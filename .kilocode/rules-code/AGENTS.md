# AGENTS.md (Code Mode)

This file provides coding-specific guidance for this repository.

**⚠️ CRITICAL: All operations (running, testing, building) MUST use podman containers. Never run Python or npm commands directly on host.**

## Non-Obvious Coding Rules

- **Singleton initialization** - `MarketRegimeDetector` and `PerformanceTracker` must be initialized once per worker process; they are singletons
- **Config loading** - Environment variables override .env file which overrides hardcoded defaults in `src/config.py`
- **BingXClient caching** - Class-level `_positions_cache` (5s TTL) and `_balance_cache` (10s TTL) are shared across instances; reset in tests with `reset_class_cache()`
- **Retry patterns** - BingX API uses 3 retries (1s→2s→4s), LLM uses exponential backoff; always wrap API calls in retry logic
- **File path resolution** - Always use `os.path.join(os.path.dirname(os.path.dirname(__file__)), 'file')` for project-relative paths
- **Symbol format** - Exchange symbols must use hyphen format "BTC-USDT", never "BTCUSDT" or "BTC_USDT"
- **Error handling** - Log with emoji prefixes (✅, ❌, ⚠️) before raising or continuing
