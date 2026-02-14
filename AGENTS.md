# AGENTS.md

This file provides guidance to agents when working with code in this repository.

**⚠️ CRITICAL: All operations (running, testing, building) MUST use podman containers. Never run Python or npm commands directly on host.**

## Build/Test Commands

```bash
# Run tests in container (pytest not in main requirements)
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/ -x -q"

# Run single test
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/test_bingx.py -x -q"

# Run bot directly
python3 run.py
```

## Non-Obvious Project Rules

- **Tests MUST run in container** - Do not run pytest directly on host; always use podman/docker container
- **Comments in Russian** - Code comments and log messages are in Russian; code identifiers in English
- **Config loading order** - `.env` → hardcoded defaults (`src/config.py`) → `bot_config.json` merge (environment variables take precedence over .env file)
- **Singleton pattern** - `MarketRegimeDetector` and `PerformanceTracker` are singletons per process (one instance per worker)
- **Class-level caching** - `BingXClient` has class-level caches: positions (5s TTL), balance (10s TTL)
- **Exchange symbols format** - Use "BTC-USDT" (with hyphen), not "BTCUSDT" or "BTC_USDT"
- **Retry logic** - BingX API: 3 retries (1s→2s→4s), LLM API: exponential backoff
- **File paths** - Use `os.path.join(os.path.dirname(os.path.dirname(__file__)), 'file')` for relative paths from project root
- **Strategy modes** - HYBRID uses deterministic scoring + AI confirmation; HYBRID_VETO uses AI veto only (APPROVE/REJECT English-only)

## Code Style

- **Indentation**: 4 spaces for Python, 2 spaces for JSON/YAML (per `.editorconfig`)
- **Imports**: Standard library first, then third-party, then local (`from src.core import ...`)
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Error handling**: Log errors with emoji prefixes (✅, ❌, ⚠️) then raise or continue
