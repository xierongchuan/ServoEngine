# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Build/Test Commands

```bash
# Run tests in container (pytest not in main requirements)
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q -r requirements.txt -r src/telegram_panel/requirements.txt pytest && python -m pytest tests/ -x -q"

# Run single test
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q -r requirements.txt -r src/telegram_panel/requirements.txt pytest && python -m pytest tests/test_bingx.py -x -q"
```

## Non-Obvious Project Rules

- **Podman required** - All operations (running, testing, building) MUST use podman containers. Never run Python or npm commands directly on host
- **Comments in Russian** - Code comments and log messages are in Russian; code identifiers in English
- **Configuration system** - New modular system in `config/` directory: `base.json` → `trading.json` → `strategies/*.json` → `profiles/*.json` with deep merge and hot-reload (30s). Legacy `bot_config.json` fallback still supported. See `src/config_loader.py`.
- **Singletons** - `MarketRegimeDetector`, `PerformanceTracker`, `WebSocketDataProvider` are singletons per process (one instance per worker). Do not share across processes.
- **Class-level caching** - `BingXClient` has class-level caches: positions (5s TTL), balance (10s TTL). Call `reset_class_cache()` in tests to clear.
- **Exchange symbols format** - Use "BTC-USDT" (with hyphen) for API calls; internal normalization handles variations.
- **Retry logic** - BingX API: 3 retries (1s→2s→4s). LLM (OpenRouter): 3 retries with exponential backoff (2^(n+1)s). Retryable errors: 429, 500, 502, 503, 504.
- **File paths** - Use `src.utils.helpers.get_filename(symbol)` for data file paths; avoid manual path construction.
- **Strategies** - Located in `config/strategies/` (JSON configs) and `src/prompts/strategies/` (prompt templates). The `STRATEGIES` dict in `__init__.py` lists all prompt strategies. Some strategies have VETO or REGIME variants.
- **Multiprocessing** - Each symbol runs in its own OS process. Use `multiprocessing.Manager` proxies for shared state (WebSocket cache). Do not share Python objects directly.
- **Config hot-reload** - `config/active.json` and `config/trading.json` are checked every 30 seconds by workers. Restart required for strategy JSON changes.
- **Telegram Panel** - Runs separately; does not affect trading bot. Uses dual auth: Telegram initData (Mini App) and web tokens (6h expiry).
- **Logging** - Use `src.utils.logger` module: `info()`, `warning()`, `error()`. Emoji prefixes (✅, ❌, ⚠️) are conventional. UTC timestamps.
- **Error handling** - Log before raising. Worker cycles have try/except: log error, sleep 5s, continue loop.
- **Общение на русском** - Все сообщения, ответы и взаимодействие с пользователем должны быть на русском языке
