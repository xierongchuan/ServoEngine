# CLAUDE.md

## Project

AI-powered cryptocurrency futures trading bot for BingX exchange. Combines technical analysis (EMA, RSI, ATR, Bollinger Bands, MACD, SEB, S/R levels) with LLM-based decision making via OpenRouter. Python 3.12+.

## Commands

All operations use **podman containers** — never run Python commands directly on host.

```bash
# Start trading bot (interactive)
./scripts/run_trading_bot.sh

# Generate chart manually (inside container/venv)
python3 src/core/plotter.py 2H    # last 2 hours
python3 src/core/plotter.py 1D    # last 1 day

# Monitor logs (interactive menu)
./scripts/monitor_logs.sh

# Telegram Panel
./scripts/start_panel.sh [ngrok|tunnel|prod]  # start with mode selection
./scripts/stop_panel.sh                        # stop panel container
./scripts/tunnel.sh start|stop|status|restart  # manage Cloudflare tunnel

# Container management (raw podman-compose)
podman-compose up --build -d   # start only
podman-compose down            # stop only
podman-compose logs -f         # logs
```

**Tests** (run inside container, pytest not in main requirements):

```bash
podman run --rm -v .:/app:Z -w /app python:3.12-slim \
  sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/ -x -q"
```

## Strategy Styles

The bot supports multiple trading strategies. Each strategy is configured in `config/strategies/<strategy>.json` and has a corresponding prompt template in `src/prompts/strategies/`. Available strategies vary based on the active configuration.

**Core strategies:**
- **SCALP** — High-frequency 1m scalping with dual-loop engine (fast 1.5s + slow 45s), trailing stops, breakeven.
- **AISCALP** — 1m day trading with multi-timeframe analysis (1H HTF), session awareness.
- **SWING** — Multi-day 1h swinging with 24h minimum hold, milestone exits.
- **GRID** — Limit order grid trading with inventory management.
- **HYBRID** — 5m swing trading with deterministic signal scoring + AI confirmation.
- **MACDX** — Fully deterministic MACD crossover strategy (no AI).

**Variants** (different AI integration modes): Some strategies have VETO (AI veto only) or REGIME (regime-adaptive) variants available in the prompt templates.

Strategy-specific parameters (weights, thresholds, loops) are defined in their respective JSON configs. The active strategy and symbol-to-profile mapping is set in `config/active.json`.

## Architecture

**Multiprocessing supervisor-worker pattern:** Each trading symbol runs in its own isolated process with an independent event loop. The main process (`run.py` → `src/main.py`) spawns the Chart Worker and WebSocket Provider.

```
run.py → src/main.py (spawns processes)
  ├── Worker per symbol (src/core/process_worker.py) — infinite loop orchestrator
  ├── Chart Worker (src/core/chart_worker.py) — parallel PNG generation via ProcessPoolExecutor
  └── WebSocket Provider (src/exchanges/ws_data_provider.py) — optional real-time kline cache
```

### Strategy-specific Pipelines

The `process_worker` selects the pipeline based on the active strategy:

- **SCALP**: Delegates to `ScalpEngine` (dual-loop: fast 1.5s + slow 45s)
- **HYBRID**: Conditional AI veto logic (auto-approve high-quality signals, AI review for borderline)
- **AISCALP**: Always goes through AI with multi-timeframe analysis
- **GRID**: Delegates to `GridWorker` (limit order grid)
- **MACDX**: Fully deterministic, no AI
- Other strategies use linear pipeline: Collector → Analyzer → SignalGenerator → RiskManager → [AI optional] → Executor → Monitor

### Core Modules (src/core/)

Key components:
- **process_worker.py** — orchestrates the per-symbol loop, hot-reloads config every 30s, handles disabled symbols, funding rate logging
- **collector.py** — fetches OHLCV candles from BingX API, optionally news; AISCALP also fetches HTF candles
- **analyzer.py** — calculates technical indicators (EMA, RSI, MACD, ATR, BB, SEB, S/R levels), detects market context, provides smart sampling for AI
- **lightweight_analyzer.py** — fast indicator calculations for SCALP fast loop
- **session.py** — trading session management (ASIAN/EUROPEAN/US) with overlap bonuses and dead zone penalties
- **regime.py** — `MarketRegimeDetector` singleton; classifies market into TRENDING/RANGING/VOLATILE/TRANSITIONAL
- **risk_manager.py** — dynamic SL/TP calculation (ATR+S/R), position sizing with streak and regime factors, risk validation
- **trade_tracker.py** — persists trades (`active_trades.json`, `trade_history.json`), syncs with exchange, tracks entry context
- **decision_journal.py** — AI decision history per symbol, cooldown logic, trade plan persistence
- **performance.py** — `PerformanceTracker` singleton; tracks performance metrics, provides calibration suggestions every 50 cycles

Strategy-specific signal generators:
- **signal_generator.py** — HYBRID deterministic scoring (tiered system, max 10 base + interactions)
- **aiscalp_signal.py** — AISCALP multi-timeframe scoring with HTF trend weight
- **scalp_engine.py** — SCALP dual-loop engine with trailing stops, breakeven, time exits
- **scalp_signal.py** — SCALP signal logic (OB imbalance, VWAP, momentum patterns)
- **scalp_performance.py** — SCALP performance tracking and rate limiting
- **macdx_signal.py** — MACDX MACD crossover with confirmations (no AI)
- **grid_worker.py** / **grid_executor.py** — GRID strategy with inventory management and ADX-based pausing

Support modules:
- **predict.py** — LLM integration via OpenRouter, prompt building, JSON parsing, 3 retries with exponential backoff
- **executor.py** — order placement with dynamic sizing and SL/TP
- **monitor.py** — position logging and PnL tracking
- **plotter.py** — chart generation (candlesticks + overlays, S/R, position markers)
- **chart_worker.py** — parallel chart updates

### Exchange layer (src/exchanges/)

- **exchange_client.py** — abstract base class defining the exchange interface
- **bingx_client.py** — BingX perpetual futures implementation (HMAC-SHA256 signing, demo/real modes). Class-level caching (positions 5s, balance 10s, commission 1h). Retry: 3 attempts (1s→2s→4s). WebSocket cache integration
- **exchange_factory.py** — factory returning the appropriate client
- **ws_data_provider.py** — WebSocket real-time kline cache (deque, 600 candles/symbol). Multiprocessing Manager proxy for cross-process sharing. Auto-reconnect with exponential backoff (1s→60s max). REST backfill on startup. Keepalive ping every 20s

### Prompt system (src/prompts/)
- **builder.py** — `PromptBuilder.build(style, ctx)` assembles modular prompt from blocks + strategy section. Cached block loading. Separator: `\n\n---\n\n`
- **blocks/** — text templates: `role.txt`, `principles.txt`, `context_table.txt`, `decision_history.txt`, `market_analysis.txt`, `response_format.txt`, `risk_table.txt`, `position_management.txt`, `special_situations.txt`, `candle_history.txt`
- **strategies/** — `BaseStrategy` ABC with implementations: `ScalpStrategy`, `ScalpVetoStrategy`, `ScalpRegimeStrategy`, `AiScalpStrategy`, `SwingStrategy`, `SwingVetoStrategy`, `GridStrategy`, `HybridStrategy`, `HybridVetoStrategy`. Registry in `__init__.py` STRATEGIES dict

### Telegram Panel (src/telegram_panel/)

Management UI that runs in a separate container and does NOT affect trading bot functionality.

**Components:**
- **run_panel.py** — entrypoint that launches FastAPI + Telegram bot in a daemon thread with 5 retries on startup
- **bot.py** — Telegram bot with commands: `/start` (welcome + Mini App button), `/status`, `/trades`, `/chart`, `/logs`, `/config`, `/help`, plus admin commands (`/weblink`, `/reload`, `/stop`, `/resume`, `/close`). Multi-user auth via `TELEGRAM_ALLOWED_IDS`. `TradingNotifier` emits alerts on trade open/close.
- **backend/app.py** — FastAPI app with lifespan, CORS for WebApp, global exception handler
- **backend/config.py** — panel paths and configuration
- **backend/ws.py** — WebSocket `ConnectionManager` for real-time broadcasts
- **backend/routes/** — API routers: `dashboard`, `trades` (includes `/api/trades/stats`, symbol enable/disable), `charts`, `logs`, `config_routes`, `journal`
- **backend/services/** — `auth` (Telegram HMAC initData verification + web-token support), `data_reader`, `file_watcher` (watchdog on `data/`, `charts/`, broadcasts changes)
- **frontend/** — React 18 + TypeScript + TailwindCSS + `@twa-dev/sdk`. Pages: Dashboard, Charts, Trades, Logs, Journal, Settings
- **Dockerfile** — multi-stage: Node builds React, Python runs FastAPI+bot

**Authentication:** Supports both Telegram Mini App initData (HMAC verification) and web-token scheme for direct browser access.

**WebSocket:** Real-time updates when data files change (`/ws` endpoint).

### Utilities (src/utils/)
- **logger.py** — `setup_symbol_logger(symbol)`, `StageTimer` context manager, `ElapsedFilter`, writes to `data/steps.log`, `data/trades.log`, `data/logs/{SYMBOL}.log`. UTC timestamps
- **helpers.py** — utility functions (`get_filename()`)
- **news_api.py** — news fetching from NewsAPI, Alpha Vantage, Finnhub (when `ENABLE_NEWS=true`). Sentiment analysis via TextBlob or keyword matching
- **cleanup_cache.py** — old file cleanup

## Configuration System

**Modular configuration with deep inheritance:**

```
config/
  base.json           # Infrastructure (exchange fees, AI, charts, TA params) — rarely changed
  trading.json        # Trading params (position, risk, features, regime, sizing)
  strategies/         # Per-strategy configs (preset, signal_rules, AI filter, exit_rules)
    scalp.json, aiscalp.json, swing.json, grid.json, hybrid.json, macdx.json
  profiles/           # Per-symbol overrides (inherits from base strategy)
    default.json, btc_aggressive.json, eth_conservative.json
  active.json         # Runtime: active strategy + symbols + profile mapping + disabled_symbols
```

**Loading order:** `.env` → hardcoded defaults (`src/config.py`) → `config/` files deep merge → strategy preset overrides. Hot-reload: `config/active.json` and `config/trading.json` checked every 30s for modifications.

**Profile inheritance:** Profiles support `_inherits` field to inherit from another profile, and `_strategy` for strategy-specific validation.

**Config loader:** `src/config_loader.py` handles merging, inheritance, and symbol-specific resolution.

### Strategy Configurations

All trading strategies are configured in `config/strategies/`. Each strategy JSON defines:
- `preset`: timeframe, loop intervals, leverage, ATR multipliers
- `signal_rules`: indicator weights, thresholds, scoring parameters
- `ai_filter`: AI integration settings (enabled, confidence thresholds, auto-approve quality)
- `exit_rules`: position management rules (trailing, breakeven, time exits)

The active strategy is set in `config/active.json`. Symbol-specific overrides can be applied via profiles in `config/profiles/`.

Strategy-specific prompt templates are in `src/prompts/strategies/`. The `STRATEGIES` dict in `__init__.py` lists all available prompt strategies (including variants like VETO and REGIME).

### Key env vars (.env)

- `MODE` — `demo` (BingX VST Futures) or `real`
- `EXCHANGE` — exchange name (currently only `bingx`)
- `OPENROUTER_API_KEY` — AI API key for OpenRouter
- `BINGX_API_KEY`, `BINGX_SECRET_KEY` — exchange credentials
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID`, `TELEGRAM_PANEL_URL` — Telegram Panel
- `TELEGRAM_ALLOWED_IDS` — comma-separated user IDs (falls back to `TELEGRAM_ADMIN_ID`)
- `PANEL_PORT` — default `8080`
- `VPS_HOST`, `VPS_USER`, `VPS_PORT`, `VPS_SSH_KEY` — tunnel configuration for Cloudflare
- `ENABLE_NEWS` — `true`/`false` to enable news fetching
- `NEWSAPI_KEY`, `ALPHAVANTAGE_KEY`, `FINNHUB_KEY` — news provider API keys

## Data files (data/)

| File | Format | Purpose |
|------|--------|---------|
| `active_trades.json` | `{symbol: {dealId, side, entry_price, leverage, last_pnl, entry_regime, entry_score, ...}}` | Open positions |
| `trade_history.json` | `[{symbol, side, entry_price, close_time, last_pnl, net_pnl, ...}]` | Closed trades |
| `decision_journal.json` | `{symbol: {entries: [...], trade_plan: {...}, last_close_time: str}}` | AI decision history |
| `prices/{SYMBOL}.json` | Candle data arrays | Fetched OHLCV |
| `prices/{SYMBOL}_htf.json` | Candle data arrays | Higher-timeframe candles (AISCALP) |
| `steps.log` | Text log | System events |
| `trades.log` | Text log | Trade executions |
| `logs/{SYMBOL}.log` | Text log | Per-symbol logs |
| `calibration_suggestions.json` | `{regime: {suggestion, confidence, auto_apply, ...}}` | PerformanceTracker calibration output |
| `news/{SYMBOL}.json` | News data arrays | Fetched news per symbol |

## Development

**All development tools and commands MUST run inside podman containers.** Do not install dependencies or run tests directly on the host system.

Base container command pattern:
```bash
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "<commands>"
```

### Dependencies
Runtime: `requests`, `pandas`, `matplotlib`, `textblob`, `newspaper3k`, `lxml_html_clean`, `nltk`, `websocket-client`
Dev/test: `pytest` (installed in container at test time)

### Design patterns used
- **Factory** — `exchange_factory.py`
- **Strategy** — `BaseStrategy` + 9 implementations (Scalp/ScalpVeto/ScalpRegime/AiScalp/Swing/SwingVeto/Grid/Hybrid/HybridVeto)
- **Singleton** — `MarketRegimeDetector`, `PerformanceTracker`, `WebSocketDataProvider` (one instance per process)
- **Supervisor-Worker** — main process spawns per-symbol worker processes
- **Template Method** — `PromptBuilder` assembles blocks from strategy templates
- **Observer** — `FileWatcher` monitors data changes, broadcasts via WebSocket
- **Adapter** — `BingXClient` adapts BingX API to `ExchangeClient` interface

### Error handling
- **LLM API:** 3 retries, exponential backoff (2^(n+1)s), retryable: 429/500/502/503/504
- **BingX API:** 3 retries (1s→2s→4s), retryable: ConnectionError, Timeout
- **WebSocket:** auto-reconnect with exponential backoff (1s→60s max)
- **Worker cycle:** try/catch per cycle, log error, sleep 5s, continue loop
- **Telegram bot:** 5 retries with exponential backoff on startup

## Language

Codebase comments and log messages are in Russian. Commit messages and code identifiers are in English. Commit style: `feat:`, `fix:`, `test:`, `chore:`, `refactor:`.
