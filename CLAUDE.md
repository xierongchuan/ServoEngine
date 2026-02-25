# CLAUDE.md

## Project

AI-powered cryptocurrency futures trading bot for BingX exchange. Combines technical analysis (EMA, RSI, ATR, Bollinger Bands, MACD, SEB, S/R levels) with LLM-based decision making via OpenRouter. Python 3.12+.

## Commands

```bash
# Run the trading bot (in container via podman)
./scripts/run_trading_bot.sh

# Run the bot directly (inside container or venv)
python3 run.py

# Run tests (pytest not in main requirements — install in container)
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/ -x -q"

# Run a single test
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/test_bingx.py -x -q"

# Generate chart manually
python3 src/core/plotter.py 2H

# Monitor logs
./scripts/monitor_logs.sh

# Telegram Panel
./scripts/start_panel.sh [ngrok|tunnel|prod]   # start (interactive mode selector)
./scripts/stop_panel.sh                        # stop (container + ngrok/tunnel)
podman-compose up --build -d                   # start container only
podman-compose down                            # stop container only
podman-compose logs -f                         # logs

# HTTPS tunnel for Telegram Mini App (SSH + cloudflared)
./scripts/tunnel.sh start|stop|status|restart
```

## Architecture

**Multiprocessing supervisor-worker pattern.** Each trading symbol runs in its own process with an independent event loop.

```
run.py → src/main.py (spawns processes)
  ├── Worker per symbol (src/core/process_worker.py) — infinite loop:
  │   HYBRID: Collector → Analyzer → TradeTracker → DecisionJournal → SignalGenerator →
  │           RegimeDetector → RiskManager → [AI Veto conditional] → Executor → Monitor → sleep
  │   INTRADAY: Collector → Analyzer → HTF Analysis → Session → TradeTracker →
  │             DecisionJournal → SignalGenerator → RegimeDetector → RiskManager → AI → Executor → Monitor → sleep
  │   SCALP: Delegates to ScalpEngine (dual-loop: fast 1.5s + slow 45s)
  │   GRID: Delegates to GridWorker (limit order grid)
  │   SWING: Collector → Analyzer → TradeTracker → DecisionJournal → AI → Executor → Monitor → sleep
  ├── Chart Worker (src/core/chart_worker.py) — parallel PNG generation via ProcessPoolExecutor
  └── WebSocket Provider (src/exchanges/ws_data_provider.py) — optional real-time kline cache
```

### Per-symbol cycle (e.g. HYBRID 5m, 60s loop)

```
 1. collector.process_symbol()          — fetch OHLCV candles + news → data/prices/{SYMBOL}.json
 2. analyzer.analyze_symbol_with_position() — indicators, trends, market context, signal scoring
 3. trade_tracker.sync_position()       — detect new/closed/updated positions
 4. decision_journal.get_context()      — previous AI decisions for prompt context
 5. signal_generator.generate()         — deterministic signal scoring (HYBRID/INTRADAY)
 6. regime.detect()                     — classify market regime (TRENDING/RANGING/VOLATILE/TRANSITIONAL)
 7. risk_manager.calculate_dynamic_sl_tp() — ATR+S/R based SL/TP with regime adjustments
 8. risk_manager.calculate_position_size() — dynamic sizing based on quality/regime/streak
 9. predict.main()                      — build prompt → call LLM → parse JSON (conditional: high-quality signals auto-execute, borderline/conflicting go through AI veto)
10. executor.main()                     — calculate size, place orders with SL/TP
11. monitor.main()                      — log open positions, PnL tracking
12. performance.check_calibration()     — periodic calibration suggestions (every 50 cycles)
13. sleep(loop_interval)                — style-dependent (1.5s SCALP, 60s INTRADAY, 4h SWING)
```

### Pipeline modules (src/core/)
- **process_worker.py** — per-symbol infinite loop orchestrator. Hot-reload config every 30s. Dynamic sleep (position_check_interval vs base_interval with ±20% jitter). Disabled symbols check, funding rate logging
- **collector.py** — fetches OHLCV candles from BingX API, optionally news. For INTRADAY: also fetches HTF (1H) candles
- **analyzer.py** — calculates indicators (EMA, RSI, MACD, ATR, BB, SEB, S/R levels), detects market context (trend, volume), smart sampling for AI context. `analyze_htf()` for multi-timeframe analysis (INTRADAY)
- **signal_generator.py** — deterministic scoring system for HYBRID mode (max base score 10, regime-adaptive min: default 5). Weights: EMA(2), RSI(2), S/R(2), MACD(1), Momentum(1), BB(1), Volume(1). Tiered system (Tier 1: direction, Tier 2: confirmation), interaction bonuses/penalties, conflict friction. `should_close_position()` for exit signals
- **intraday_signal.py** — INTRADAY multi-timeframe signal generator. HTF trend weight (3), session awareness, counter-HTF penalty (-3). Uses `INTRADAY_SETTINGS` config
- **scalp_engine.py** — dedicated SCALP strategy engine with dual-loop (fast 1.5s signal detection + slow 45s regime/veto). Trailing stops, breakeven, time-based exits
- **scalp_signal.py** — SCALP signal generator with OB imbalance, VWAP, momentum scoring
- **scalp_performance.py** — SCALP-specific performance tracking, streak management, rate limiting
- **session.py** — trading session management (ASIAN 0-8 UTC, EUROPEAN 7-15, US 13-21). Overlap bonuses, dead zone penalties (21-23 UTC)
- **lightweight_analyzer.py** — fast indicator calculations for scalp fast loop
- **regime.py** — `MarketRegimeDetector` (singleton). Classifies market into 4 regimes: TRENDING, RANGING, VOLATILE, TRANSITIONAL. Uses EMA spread, BB width percentiles, ATR ratio. Returns regime-specific parameters (min_score, SL/TP multipliers, position_size_factor)
- **risk_manager.py** — `calculate_dynamic_sl_tp()` (ATR+S/R with regime/quality adjustments), `calculate_position_size()` (dynamic sizing with regime/quality/streak factors, bounds 3-20%), `validate_risk_parameters()` (fee-adjusted R/R and risk% validation)
- **performance.py** — `PerformanceTracker` (singleton). Tracks win rate, avg PnL, hold time, streaks by regime/score. Provides `should_adjust_thresholds()` for calibration. Saves to `data/calibration_suggestions.json`
- **predict.py** — builds prompt via PromptBuilder, calls LLM (OpenRouter), parses JSON (action, confidence, stop_loss, take_profit, reason). Retry: 3 attempts, exponential backoff. Smart filter skips low-volume neutral markets. `validate_prediction()` auto-adjusts low R/R
- **executor.py** — calculates position size from balance %, places orders with SL/TP. Accepts dynamic `size_pct` from risk_manager. Balance overflow protection at 95%. Fee reserve deduction. Sets SL/TP separately after order placement
- **monitor.py** — logs open positions age and PnL per symbol with fee estimates
- **trade_tracker.py** — persists trade history to `data/active_trades.json` and `data/trade_history.json`, detects manual closes. `set_entry_context()` saves regime/score/quality at entry for performance analysis. `force_sync_all()` for startup sync. Atomic file operations with fcntl.flock
- **decision_journal.py** — AI decision history per symbol (`data/decision_journal.json`), cooldown logic for SWING, trade plan persistence, position age tracking
- **plotter.py** — matplotlib chart generation (candlestick + SMA/RSI/SEB overlays, S/R levels, position markers, SL/TP lines). Full-dataset indicator warmup then slice to display range
- **chart_worker.py** — dedicated process, updates charts every N seconds with parallel processing via ProcessPoolExecutor
- **grid_worker.py** / **grid_executor.py** — grid trading strategy (limit order grid with inventory management, ADX-based pause during strong trends, emergency stop-loss)

### Exchange layer (src/exchanges/)
- **exchange_client.py** — abstract base class: `check_prerequisites()`, `get_balance()`, `get_kline_data()`, `get_positions()`, `place_order()`, `close_position()`, `get_order_book()`, `get_ticker()`, `cancel_all_orders()`, `get_commission_rate()`, `get_funding_rate()`
- **bingx_client.py** — BingX perpetual futures (`/openApi/swap/v2/`). HMAC-SHA256 signing. Supports demo (VST) and real modes. Class-level caching: positions 5s, balance 10s, commission 1h, funding 5min. Retry: 3 attempts (1s→2s→4s). WebSocket cache fallback for klines
- **exchange_factory.py** — factory returning BingXClient based on `EXCHANGE` env var
- **ws_data_provider.py** — WebSocket real-time kline cache (deque, 600 candles/symbol). Multiprocessing Manager proxy for cross-process access. Auto-reconnect with exponential backoff (1s→60s). REST backfill on startup

### Prompt system (src/prompts/)
- **builder.py** — `PromptBuilder.build(style, ctx)` assembles modular prompt from blocks + strategy section. Cached block loading. Separator: `\n\n---\n\n`
- **blocks/** — text templates: `role.txt`, `principles.txt`, `context_table.txt`, `decision_history.txt`, `market_analysis.txt`, `response_format.txt`, `risk_table.txt`, `position_management.txt`, `special_situations.txt`, `candle_history.txt`
- **strategies/** — `BaseStrategy` ABC with implementations: `ScalpStrategy`, `ScalpVetoStrategy`, `ScalpRegimeStrategy`, `IntradayStrategy`, `SwingStrategy`, `SwingVetoStrategy`, `GridStrategy`, `HybridStrategy`, `HybridVetoStrategy`. Registry in `__init__.py` STRATEGIES dict

### Telegram Panel (src/telegram_panel/)
Management UI — does NOT affect trading bot functionality. Runs in a separate container.

- **run_panel.py** — entrypoint: launches FastAPI + Telegram bot in daemon thread (uses low-level asyncio to avoid signal handler issues in non-main thread). Retry up to 5 times with exponential backoff
- **bot.py** — `TelegramPanelBot` with commands: `/start`, `/status`, `/trades`, `/chart`, `/logs`, `/config`, `/help`. Multi-user auth via `TELEGRAM_ALLOWED_IDS` (falls back to `TELEGRAM_ADMIN_ID`). `TradingNotifier` watches `active_trades.json` for trade open/close alerts. Mini App button integration
- **backend/app.py** — FastAPI with lifespan, CORS for Telegram WebApp, WebSocket at `/ws`, health check at `/api/health`, serves React static from `frontend/dist/`
- **backend/config.py** — panel-specific config (ports, tokens, paths, allowed IDs)
- **backend/ws.py** — `ConnectionManager` for WebSocket connection management and broadcast with stale connection cleanup
- **backend/routes/** — `dashboard.py`, `trades.py` (includes `/api/trades/stats`, disable/enable symbol endpoints), `charts.py`, `logs.py`, `config_routes.py`, `journal.py`
- **backend/services/** — `auth.py` (Telegram HMAC initData verification via `X-Telegram-Init-Data` header, user ID whitelist), `data_reader.py` (read bot data files), `file_watcher.py` (watchdog: monitors `data/`, `charts/`, broadcasts via WebSocket)
- **frontend/** — Vite + React 18 + TypeScript + TailwindCSS + `@twa-dev/sdk`. Pages: Dashboard, Charts, Trades, Logs, Journal, Settings. Hooks: `useTelegram()`, `useWebSocket()`. Utils: `pnl.ts` (ROE%, formatting)
- **Dockerfile** — multi-stage: Node 20 builds React, Python 3.12 runs FastAPI+bot

### Utilities (src/utils/)
- **logger.py** — `setup_symbol_logger(symbol)`, `StageTimer` context manager, `ElapsedFilter`, writes to `data/steps.log`, `data/trades.log`, `data/logs/{SYMBOL}.log`. UTC timestamps
- **helpers.py** — utility functions (`get_filename()`)
- **news_api.py** — news fetching from NewsAPI, Alpha Vantage, Finnhub (when `ENABLE_NEWS=true`). Sentiment analysis via TextBlob or keyword matching
- **cleanup_cache.py** — old file cleanup

## Configuration

**Loading order:** `.env` / env vars → hardcoded defaults (`src/config.py`) + `bot_config.json` deep merge → dynamic `STRATEGY_STYLE` preset overrides. Hot-reload: `bot_config.json` checked every 30s for modifications.

### Strategy styles

| Style | Timeframe | Loop | Leverage | ATR SL/TP | Notes |
|-------|-----------|------|----------|-----------|-------|
| SCALP | 1m | 1.5s | 15x | 1.0/3.0 | Dual-loop engine (fast 1.5s + slow 45s), trailing stops, time exits |
| INTRADAY | 5m | 60s | 10x | 2.0/3.0 | Multi-TF (5m+1H), session awareness, HTF trend alignment |
| SWING | 1h | 4h | 5x | 3.0/6.0 | Multi-day, 24h min hold, 6h cooldown, milestone-based exits |
| GRID | 1m | 5s | 5x | — | Limit order grid, inventory management, ADX-based pausing |
| HYBRID | 5m | 60s | 10x | 1.5/3.0 | Deterministic signals + AI confirmation (default) |

### Key env vars (.env)
- `MODE` — `demo` (VST Futures) or `real`
- `EXCHANGE` — `bingx`
- `OPENROUTER_API_KEY` — AI API key
- `BINGX_API_KEY`, `BINGX_SECRET_KEY` — exchange credentials
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID`, `TELEGRAM_PANEL_URL` — panel
- `TELEGRAM_ALLOWED_IDS` — comma-separated list of allowed Telegram user IDs (falls back to `TELEGRAM_ADMIN_ID`)
- `PANEL_PORT` — default 8080
- `VPS_HOST`, `VPS_USER`, `VPS_PORT`, `VPS_SSH_KEY` — tunnel config
- `ENABLE_NEWS` — `true`/`false`
- `NEWSAPI_KEY`, `ALPHAVANTAGE_KEY`, `FINNHUB_KEY` — news provider API keys (when `ENABLE_NEWS=true`)

### Key bot_config.json sections
- `EXCHANGE_SYMBOLS` — active trading pairs per exchange
- `DISABLED_SYMBOLS` — symbols blocked from new position entry
- `EXCHANGE_FEES` — per-exchange maker/taker fee percentages
- `STRATEGY_STYLE` — active style (SCALP/INTRADAY/SWING/GRID/HYBRID)
- `POSITION_SIZE_PERCENT` — % of balance per trade (default 10)
- `MIN_RISK_REWARD_RATIO` — R/R validation threshold (default 1.2)
- `MIN_CONFIDENCE_THRESHOLD` — minimum AI confidence to execute (default 0.55)
- `AI_SETTINGS` — model, temperature (0.3), max_tokens (4096), retry count (3), reasoning config, provider_routing, fallback_models
- `STYLE_PRESETS` — per-style overrides (timeframe, loop_interval, leverage, ATR multipliers, position_check_interval, min_hold_hours, cooldown_after_close_hours)
- `HYBRID_SETTINGS` — signal scoring rules, weights, interaction bonuses, AI filter config (auto_approve_quality, invoke_on_borderline)
- `INTRADAY_SETTINGS` — signal scoring (HTF trend weight 3), session config (ASIAN/EUROPEAN/US, dead zones), multi-timeframe config, pre-filter rules, AI filter, interaction rules (counter_htf_trend_penalty -3)
- `SCALP_SETTINGS` — signal rules (EMA 5/13, RSI 7, MACD 6/13/5, OB imbalance), SL/TP (trailing mode), breakeven, time exits, risk limits (max trades/hour, daily loss limit), dual loops, regime overrides, AI integration (regime + veto)
- `REGIME_SETTINGS` — market regime detection params, per-regime min_score/SL/TP/sizing factors
- `PERFORMANCE_TRACKING` — performance analysis (enabled, min_trades, win_rate_floor)
- `DYNAMIC_SIZING` — adaptive position sizing (min/max size 3-20%, quality/streak weights)
- `GRID_SETTINGS` — grid levels, spacing, order size, inventory limit, emergency stop
- `MOMENTUM_STRATEGY` — volume thresholds, momentum entry config
- `TECHNICAL_ANALYSIS` — indicator parameters (sr_window, ema_periods, seb_length, etc.)
- `CHART_RANGES` — data fetch params per time window (candle count, interval, ai_context_candles)
- `PLOTTER_RANGES` — chart rendering presets (12 time ranges from 15m to 14D)
- `SMART_SAMPLING` — AI context compression (recent_candles, history_step)
- `CHART_SETTINGS` — chart rendering config (update_interval, sma_periods, dpi)
- `POSITION_LIMITS` — max positions, precision, balance safety margin
- `DECISION_JOURNAL` — journal config (enabled, max_entries per style)
- `AGGRESSIVE_MODE` / `AGGRESSIVE_SETTINGS` — aggressive trading mode RSI thresholds
- `NEWS_SETTINGS` — news provider config (provider, max items, timeout)

## Data files (data/)

| File | Format | Purpose |
|------|--------|---------|
| `active_trades.json` | `{symbol: {dealId, side, entry_price, leverage, last_pnl, entry_regime, entry_score, ...}}` | Open positions |
| `trade_history.json` | `[{symbol, side, entry_price, close_time, last_pnl, net_pnl, ...}]` | Closed trades |
| `decision_journal.json` | `{symbol: {entries: [...], trade_plan: {...}, last_close_time: str}}` | AI decision history |
| `prices/{SYMBOL}.json` | Candle data arrays | Fetched OHLCV |
| `prices/{SYMBOL}_htf.json` | Candle data arrays | Higher-timeframe candles (INTRADAY) |
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
- **Strategy** — `BaseStrategy` + 9 implementations (Scalp/ScalpVeto/ScalpRegime/Intraday/Swing/SwingVeto/Grid/Hybrid/HybridVeto)
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
