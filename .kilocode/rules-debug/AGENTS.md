# AGENTS.md (Debug Mode)

This file provides debugging-specific guidance for this repository.

**⚠️ CRITICAL: All operations (running, testing, building) MUST use podman containers. Never run Python or npm commands directly on host.**

## Non-Obvious Debug Rules

- **Test isolation** - Use `reset_class_cache()` fixture in tests to clear BingXClient caches between tests
- **Cache behavior** - BingXClient caches positions (5s) and balance (10s); stale cache can cause misleading debug output
- **Worker process logs** - Check `data/logs/{SYMBOL}.log` for per-symbol worker process logs
- **Trade tracking files** - `data/active_trades.json` and `data/trade_history.json` contain position state
- **Retry failures** - Check `data/steps.log` for retry attempts and exponential backoff timing
- **Strategy-specific behavior** - HYBRID vs HYBRID_VETO differ significantly; check `config/active.json` for active strategy
