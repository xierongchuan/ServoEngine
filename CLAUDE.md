# CLAUDE.md

## Project

AI-powered cryptocurrency futures trading bot for BingX exchange. Combines technical analysis (SMA, EMA, RSI, ATR, Bollinger Bands, MACD, SEB) with LLM-based decision making via OpenRouter. Python 3.12+.

## Commands

```bash
# Run the trading bot
python3 run.py

# Run tests (pytest not in main requirements — install in container)
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/ -x -q"

# Run a single test
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/test_bingx.py -x -q"

# Generate chart manually
python3 src/core/plotter.py 2H

# Telegram Panel (container)
podman-compose up --build -d          # start
podman-compose down                   # stop
podman logs openproducerbot_panel_1   # logs

# SSH tunnel to VPS for HTTPS (Telegram Mini App requires HTTPS)
./scripts/tunnel.sh start|stop|status|restart

# One-time VPS setup (nginx + self-signed SSL)
./scripts/setup_server.sh

# All-in-one start (tunnel + container)
./scripts/start.sh

# All-in-one stop
./scripts/stop.sh
```

## Architecture

**Multiprocessing supervisor-worker pattern.** Each trading symbol runs in its own process with an independent event loop.

```
run.py → src/main.py (spawns processes)
  ├── Worker per symbol (src/core/process_worker.py) — infinite loop:
  │   Collector → Analyzer → DecisionJournal → SignalGenerator → Predictor (LLM) → Executor → Monitor → sleep
  ├── Chart Worker (src/core/chart_worker.py) — parallel PNG generation via ProcessPoolExecutor
  └── WebSocket Provider (src/exchanges/ws_data_provider.py) — optional real-time kline cache
```

### Per-symbol cycle (e.g. HYBRID 5m, 60s loop)

```
1. collector.process_symbol()         — fetch OHLCV candles + news → data/prices/{SYMBOL}.json
2. analyzer.analyze_symbol_with_position() — indicators, trends, market context, signal scoring
3. trade_tracker.sync_position()      — detect new/closed/updated positions
4. decision_journal.get_context()     — previous AI decisions for prompt context
5. signal_generator.generate()        — deterministic signal (HYBRID only)
6. predict.main()                     — build prompt → call LLM → parse JSON response
7. executor.main()                    — validate R/R, calculate size, place orders
8. monitor.main()                     — trailing stops, PnL tracking
9. sleep(loop_interval)               — style-dependent (5s SCALP, 60s INTRADAY, 4h SWING)
```

### Pipeline modules (src/core/)
- **collector.py** — fetches OHLCV candles from BingX API, optionally news
- **analyzer.py** — calculates indicators (EMA, RSI, MACD, ATR, BB, SEB, S/R levels), detects market context (trend, volume), smart sampling for AI context
- **signal_generator.py** — deterministic scoring system for HYBRID mode (max score 11, min for signal: 4). Weights: EMA(2), RSI(2), S/R(2), MACD(2), Momentum(1), BB(1), Volume(1)
- **predict.py** — builds prompt via PromptBuilder, calls LLM (OpenRouter), parses JSON (action, confidence, stop_loss, take_profit, reason). Retry: 3 attempts, exponential backoff
- **executor.py** — validates R/R ratio (`MIN_RISK_REWARD_RATIO`), calculates position size from balance %, places orders with SL/TP. Safety margin 95% of balance
- **monitor.py** — tracks open positions, trailing stops (activate at 15%+ PnL), emergency close at -20%
- **trade_tracker.py** — persists trade history to `data/active_trades.json` and `data/trade_history.json`, detects manual closes
- **decision_journal.py** — AI decision history per symbol (`data/decision_journal.json`), cooldown logic for SWING
- **plotter.py** — matplotlib chart generation (candlestick + SMA/RSI/MACD/BB overlays, S/R levels, position markers)
- **chart_worker.py** — dedicated process, updates charts every N seconds with optional parallel processing
- **process_worker.py** — per-symbol infinite loop orchestrator
- **grid_worker.py** / **grid_executor.py** — grid trading strategy (limit order grid with inventory management)

### Exchange layer (src/exchanges/)
- **exchange_client.py** — abstract base class: `get_balance()`, `get_kline_data()`, `get_positions()`, `place_order()`, `close_position()`, `get_ticker()`, `cancel_all_orders()`
- **bingx_client.py** — BingX perpetual futures (`/openApi/swap/v2/`). HMAC-SHA256 signing. Supports demo (VST) and real modes. Caching: positions 5s, balance 10s. Retry: 3 attempts (1s→2s→4s). Normalized keys: `snapshotTimeUTC`, `openPrice`, `closePrice`, `highPrice`, `lowPrice`, `volume`
- **exchange_factory.py** — factory returning BingXClient based on `EXCHANGE` env var
- **ws_data_provider.py** — WebSocket real-time kline cache (deque, 600 candles/symbol). Multiprocessing Manager proxy for cross-process access. Auto-reconnect with exponential backoff

### Prompt system (src/prompts/)
- **builder.py** — `PromptBuilder.build(style, ctx)` assembles modular prompt from blocks + strategy section
- **blocks/** — text templates: `role.txt`, `principles.txt`, `context_table.txt`, `decision_history.txt`, `market_analysis.txt`, `response_format.txt`, `risk_table.txt`, `position_management.txt`, `special_situations.txt`, `candle_history.txt`
- **strategies/** — `BaseStrategy` ABC with implementations: `ScalpStrategy` (1m), `IntradayStrategy` (5m), `SwingStrategy` (1h), `GridStrategy` (1m), `HybridStrategy` (5m deterministic+AI)

### Telegram Panel (src/telegram_panel/)
Management UI — does NOT affect trading bot functionality. Runs in a separate container.

- **run_panel.py** — entrypoint: launches FastAPI + Telegram bot in daemon thread (uses low-level asyncio to avoid signal handler issues in non-main thread)
- **bot.py** — `TelegramPanelBot` with commands: `/start`, `/status`, `/trades`, `/chart`, `/logs`, `/config`, `/help`. Admin-only via `TELEGRAM_ADMIN_ID`. `TradingNotifier` watches `active_trades.json` for trade open/close alerts
- **backend/app.py** — FastAPI with lifespan, CORS for Telegram WebApp, WebSocket at `/ws`, serves React static from `frontend/dist/`
- **backend/routes/** — `dashboard.py`, `trades.py`, `charts.py`, `logs.py`, `config_routes.py`, `journal.py`
- **backend/services/** — `auth.py` (Telegram HMAC initData verification), `data_reader.py` (read bot data files), `file_watcher.py` (watchfiles: monitors `data/`, `charts/`, broadcasts via WebSocket)
- **frontend/** — Vite + React 18 + TypeScript + TailwindCSS + `@twa-dev/sdk`. Pages: Dashboard, Charts, Trades, Logs, Journal, Settings. Hooks: `useTelegram()`, `useWebSocket()`
- **Dockerfile** — multi-stage: Node 20 builds React, Python 3.12 runs FastAPI+bot

### Utilities (src/utils/)
- **logger.py** — `setup_symbol_logger(symbol)`, `StageTimer` context manager, writes to `data/steps.log`, `data/trades.log`, `data/logs/{SYMBOL}.log`
- **helpers.py** — utility functions
- **news_api.py** — news fetching (when `ENABLE_NEWS=true`)
- **cleanup_cache.py** — old file cleanup

## Configuration

**Loading order:** hardcoded defaults (`src/config.py`) → `bot_config.json` → `.env` / env vars → dynamic `STRATEGY_STYLE` preset overrides

### Strategy styles

| Style | Timeframe | Loop | Leverage | ATR SL/TP | Notes |
|-------|-----------|------|----------|-----------|-------|
| SCALP | 1m | 5s | 15x | 1.5/2.0 | Fast entries/exits |
| INTRADAY | 5m | 60s | 10x | 2.0/3.0 | Day trading |
| SWING | 1h | 4h | 5x | 3.0/6.0 | Multi-day, 24h min hold, 6h cooldown |
| GRID | 1m | 5s | 5x | — | Limit order grid, inventory management |
| HYBRID | 5m | 60s | 10x | 1.5/3.0 | Deterministic signals + AI confirmation (default) |

### Key env vars (.env)
- `MODE` — `demo` (VST Futures) or `real`
- `EXCHANGE` — `bingx`
- `OPENROUTER_API_KEY` — AI API key
- `BINGX_API_KEY`, `BINGX_SECRET_KEY` — exchange credentials
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID`, `TELEGRAM_PANEL_URL` — panel
- `PANEL_PORT` — default 8080
- `VPS_HOST`, `VPS_USER`, `VPS_PORT`, `VPS_SSH_KEY` — tunnel config
- `ENABLE_NEWS` — `true`/`false`

### Key bot_config.json sections
- `EXCHANGE_SYMBOLS` — active trading pairs per exchange
- `STRATEGY_STYLE` — active style (SCALP/INTRADAY/SWING/GRID/HYBRID)
- `POSITION_SIZE_PERCENT` — % of balance per trade (default 10)
- `MIN_RISK_REWARD_RATIO` — R/R validation threshold (default 1.2)
- `AI_SETTINGS` — model, temperature (0.3), max_tokens (4096), retry count (3)
- `STYLE_PRESETS` — per-style overrides
- `HYBRID_SETTINGS` — signal scoring rules and weights
- `GRID_SETTINGS` — grid levels, spacing, order size
- `MOMENTUM_STRATEGY` — ATR multipliers, volume thresholds
- `TECHNICAL_ANALYSIS` — indicator parameters (sr_window, ema_periods, seb_length, etc.)
- `CHART_RANGES` — data fetch params per time window (candle count, interval, ai_context_candles)

## Data files (data/)

| File | Format | Purpose |
|------|--------|---------|
| `active_trades.json` | `{symbol: {side, entry_price, leverage, last_pnl, ...}}` | Open positions |
| `trade_history.json` | `[{symbol, side, entry_price, close_time, last_pnl, ...}]` | Closed trades |
| `decision_journal.json` | `{symbol: {entries: [...], trade_plan: {...}}}` | AI decision history |
| `prices/{SYMBOL}.json` | Candle data arrays | Fetched OHLCV |
| `steps.log` | Text log | System events |
| `trades.log` | Text log | Trade executions |
| `logs/{SYMBOL}.log` | Text log | Per-symbol logs |

## Development

**All development tools and commands MUST run inside podman containers.** Do not install dependencies or run tests directly on the host system.

Base container command pattern:
```bash
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "<commands>"
```

### Design patterns used
- **Factory** — `exchange_factory.py`
- **Strategy** — `BaseStrategy` + Scalp/Intraday/Swing/Grid/Hybrid implementations
- **Supervisor-Worker** — main process spawns per-symbol worker processes
- **Template Method** — `PromptBuilder` assembles blocks from strategy templates
- **Observer** — `FileWatcher` monitors data changes, broadcasts via WebSocket
- **Adapter** — `BingXClient` adapts BingX API to `ExchangeClient` interface

### Error handling
- **LLM API:** 3 retries, exponential backoff (2^(n+1)s), retryable: 429/500/502/503/504
- **BingX API:** 3 retries (1s→2s→4s), retryable: ConnectionError, Timeout
- **WebSocket:** auto-reconnect with exponential backoff (1s→60s max)
- **Worker cycle:** try/catch per cycle, log error, sleep 5s, continue loop

## Language

Codebase comments and log messages are in Russian. Commit messages and code identifiers are in English. Commit style: `feat:`, `fix:`, `test:`, `chore:`, `refactor:`.
