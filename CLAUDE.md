# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AI-powered cryptocurrency futures trading bot for BingX exchange. Combines technical analysis (SMA, EMA, RSI, ATR, Bollinger Bands) with LLM-based decision making via OpenRouter. Written in Python 3.12+.

## Commands

```bash
# Run the bot
python3 run.py

# Run tests (no pytest in requirements — install in container)
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/ -x -q"

# Run a single test
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/test_bingx.py -x -q"

# Generate chart manually
python3 src/core/plotter.py 2H
```

## Architecture

**Multiprocessing supervisor-worker pattern.** Each trading symbol runs in its own process with an independent event loop.

```
run.py → src/main.py (spawns processes)
  ├── Worker per symbol (src/core/process_worker.py) — infinite loop:
  │   Collector → Analyzer → Predictor (LLM) → Executor → Monitor → sleep
  └── Chart Worker (src/core/chart_worker.py) — parallel PNG generation
```

### Pipeline modules (src/core/)
- **collector.py** — fetches OHLCV candles from BingX API
- **analyzer.py** — calculates indicators, detects market context, smart sampling for AI context
- **predict.py** — builds prompt, calls LLM, parses JSON trading signal
- **executor.py** — validates R/R ratio, calculates position size from balance %, places orders with SL/TP
- **monitor.py** — tracks open positions, trailing stops
- **trade_tracker.py** — persists trade history to JSON files in data/

### Exchange layer (src/exchanges/)
- **exchange_client.py** — abstract base class defining the interface
- **bingx_client.py** — BingX perpetual futures implementation (normalizes data to unified format with keys: snapshotTimeUTC, openPrice, closePrice, highPrice, lowPrice, volume)
- **exchange_factory.py** — factory returning BingXClient based on EXCHANGE env var

### Configuration hierarchy
`bot_config.json` → `.env` / env vars → hardcoded defaults in `src/config.py`

Key config concepts:
- **STRATEGY_STYLE** (`SCALP`/`INTRADAY`/`SWING`) auto-configures timeframe, chart range, loop interval, leverage, ATR multipliers via STYLE_PRESETS
- **CHART_RANGES** defines data fetch parameters per time window (candle count, interval, AI context size)
- **MOMENTUM_STRATEGY** controls ATR-based SL/TP and volume filters
- Config auto-adjustment logic in config.py dynamically overrides DEFAULT_CHART_RANGE, DEFAULT_PLOTTER_RANGE, and Smart Sampling step based on active style

### Logging
- `data/steps.log` — system events
- `data/logs/{SYMBOL}.log` — per-symbol logs (setup via `setup_symbol_logger()` in each worker)
- `data/trades.log` — trade execution records

## Development

**All development tools and commands MUST run inside podman containers.** Do not install dependencies or run tests directly on the host system.

Base container command pattern:
```bash
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "<commands>"
```

## Language

Codebase comments and log messages are in Russian. Commit messages and code identifiers are in English. Commit style: `feat:`, `fix:`, `test:`, `chore:`, `refactor:`.
