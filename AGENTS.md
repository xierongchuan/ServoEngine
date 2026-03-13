# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Build/Test Commands

```bash
# Run tests in container (pytest not in main requirements)
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/ -x -q"

# Run single test
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/test_bingx.py -x -q"
```

## Non-Obvious Project Rules

- **Podman required** - All operations (running, testing, building) MUST use podman containers. Never run Python or npm commands directly on host
- **Comments in Russian** - Code comments and log messages are in Russian; code identifiers in English
- **Config loading order** - `.env` → hardcoded defaults (`src/config.py`) → `bot_config.json` merge (environment variables take precedence)
- **Singleton pattern** - `MarketRegimeDetector` and `PerformanceTracker` are singletons per process (one instance per worker)
- **Class-level caching** - `BingXClient` has class-level caches: positions (5s TTL), balance (10s TTL); reset with `reset_class_cache()` in tests
- **Exchange symbols format** - Use "BTC-USDT" (with hyphen), not "BTCUSDT" or "BTC_USDT"
- **Retry logic** - BingX API: 3 retries (1s→2s→4s), LLM: exponential backoff
- **File paths** - Use `os.path.join(os.path.dirname(os.path.dirname(__file__)), 'file')` for project-relative paths
- **Strategy modes** - HYBRID uses deterministic scoring + AI confirmation; HYBRID_VETO uses AI veto only (APPROVE/REJECT English-only)
- **Error handling** - Log with emoji prefixes (✅, ❌, ⚠️) before raising or continuing
