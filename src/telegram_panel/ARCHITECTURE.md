# Telegram Mini App -- Control Panel Architecture

## System Overview

```
+---------------------+       +---------------------------+       +-------------------+
|   Telegram Client   | <---> |     Telegram Bot API      | <---> |   Telegram Bot    |
|   (User's phone)    |       |   (api.telegram.org)      |       | (python-telegram- |
|                     |       +---------------------------+       |  bot v20+)        |
|  +-Mini App------+  |                                           |                   |
|  | React SPA     |  | -- HTTPS --> +-------------------+        | /start, /status   |
|  | (WebApp)      |  |             |   FastAPI Backend  | <------| InlineKeyboard    |
|  +---------------+  |             |                    |        +-------------------+
+---------------------+             |  REST API          |
                                    |  WebSocket         |
                                    |  Static files      |
                                    +--------+-----------+
                                             |
                                    +--------v-----------+
                                    |  Trading Bot Data  |
                                    |  (read-only)       |
                                    |                    |
                                    | data/              |
                                    |   active_trades.json
                                    |   trade_history.json
                                    |   decision_journal.json
                                    |   steps.log        |
                                    |   logs/{SYMBOL}.log|
                                    | charts/            |
                                    |   {SYMBOL}.png     |
                                    | bot_config.json    |
                                    +--------------------+
```

The panel runs as a **separate service** alongside the trading bot. It reads the
bot's data files on disk (read-only for most operations) and exposes them through
a REST API and WebSocket. The React frontend is served as a Telegram Mini App
(WebApp) and communicates exclusively with the FastAPI backend.

---

## Components

### 1. Telegram Bot (`bot.py`)

Lightweight Telegram bot using `python-telegram-bot` v20+ (async).

**Responsibilities:**
- `/start` -- greets the admin, shows InlineKeyboard with "Open Panel" button
- `/status` -- quick text summary: active positions, last PnL, strategy style, mode (demo/real)
- `/balance` -- current BingX account balance
- `/chart` -- sends latest chart PNG inline
- `/trades` -- last 5 closed trades summary
- `/help` -- command reference
- Proactive notifications: watches file changes, sends alerts on new/closed trades and errors
- Mini App link via `WebAppInfo` button so Telegram opens the SPA in-app

**Design decisions:**
- Polling mode by default (not webhook) for simplicity in development. Webhook
  can be enabled via env var `TELEGRAM_WEBHOOK_URL` for production.
- Runs inside the same FastAPI process as a background async task (started in
  FastAPI lifespan). The bot is lightweight (few commands, low traffic), so a
  separate process is unnecessary.
- Auth: every handler checks `update.effective_user.id == TELEGRAM_ADMIN_ID`.

### 2. FastAPI Backend (`backend/`)

Python 3.12+ async web server (uvicorn).

**Responsibilities:**
- Serve the built React frontend (static files from `frontend/dist/`)
- REST API for reading bot state (trades, config, logs, charts, journal)
- WebSocket endpoint for pushing real-time file change notifications
- Telegram `initData` HMAC authentication middleware
- File watcher (watchfiles) detecting changes to data files and broadcasting
  updates via WebSocket

### 3. React Frontend (`frontend/`)

Vite + React 18 + TypeScript + TailwindCSS single-page application.

**Responsibilities:**
- Telegram WebApp SDK integration (`@twa-dev/sdk`) for theme, haptics, back button
- Six tabs: Dashboard, Charts, Trades, Logs, Settings, AI Journal
- WebSocket connection for live updates (auto-reconnect with exponential backoff)
- Responsive mobile-first layout (Telegram Mini App viewport)
- Theme: Telegram CSS variables, dark mode support

### 4. File Watcher (`backend/services/file_watcher.py`)

Uses `watchfiles` (Rust-based, low CPU) to monitor the `data/` and `charts/`
directories via OS-level inotify. When a file changes, it publishes a typed
event to all connected WebSocket clients so the frontend can refresh the
relevant section without polling.

---

## Existing Bot Data Structures

Understanding these structures is critical for the panel's data reader layer.

### `data/active_trades.json` -- Object keyed by symbol
```json
{
  "BTCUSDT": {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry_price": 98245.12,
    "amount": 0.001,
    "leverage": 10,
    "open_time": "2026-02-13T10:30:00",
    "status": "OPEN",
    "last_pnl": 12.5,
    "current_price": 98500.0,
    "max_pnl": 25.3,
    "min_pnl": -5.0,
    "pnl_history": []
  }
}
```

### `data/trade_history.json` -- Array of closed trades
```json
[
  {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry_price": 97000.0,
    "amount": 0.001,
    "leverage": 10,
    "open_time": "2026-02-12T08:00:00",
    "status": "CLOSED",
    "close_time": "2026-02-12T14:30:00",
    "reason": "MANUAL_OR_TP_SL",
    "last_pnl": 45.2,
    "max_pnl": 50.0,
    "min_pnl": -10.0
  }
]
```

### `data/decision_journal.json` -- Object keyed by symbol
```json
{
  "BTCUSDT": {
    "entries": [
      {
        "time": "10:30:00",
        "action": "buy",
        "confidence": 0.78,
        "price": 98000.0,
        "sl": 97500.0,
        "tp": 99000.0,
        "pnl": "+0.50%",
        "reason": "EMA cross + RSI oversold"
      }
    ],
    "trade_plan": {
      "action": "buy",
      "entry_price": 98000.0,
      "planned_sl": 97500.0,
      "planned_tp": 99000.0,
      "reason": "Strong bullish signal",
      "confidence": 0.78,
      "time": "2026-02-13 10:30:00"
    },
    "last_close_time": "2026-02-12 14:30:00"
  }
}
```

### `charts/{SYMBOL}.png` -- Chart naming convention
The plotter generates one PNG per symbol at path `charts/{SYMBOL}.png`
(e.g., `charts/BTCUSDT.png`). Symbols containing `/` are converted via
`symbol.replace('/', '_')` (see `src/utils/helpers.py:get_filename`).
Charts are overwritten on every update cycle.

### `bot_config.json` -- Full configuration
Contains all trading parameters: symbols, strategy style, AI settings,
chart ranges, technical analysis parameters, etc. The full schema is
documented in the config file itself (320+ lines). Key sections:
- `EXCHANGE_SYMBOLS` -- symbols per exchange
- `STRATEGY_STYLE` -- current strategy (`SCALP`/`INTRADAY`/`SWING`/`GRID`/`HYBRID`)
- `STYLE_PRESETS` -- parameters per strategy
- `AI_SETTINGS` -- LLM model, temperature, etc.
- `MOMENTUM_STRATEGY` -- ATR-based SL/TP settings
- `HYBRID_SETTINGS` -- deterministic signal rules

### Log files
- `data/steps.log` -- system-wide events (plain text, one line per event)
- `data/logs/{SYMBOL}.log` -- per-symbol logs (e.g., `data/logs/BTCUSDT.log`)

---

## API Endpoints

All endpoints are prefixed with `/api`. Authentication is required on every
request (see Security Model).

### Dashboard / Status

| Method | Path               | Description                                         |
|--------|--------------------|-----------------------------------------------------|
| GET    | `/api/dashboard`   | Aggregated: balance, active trades, strategy, uptime |

### Trades

| Method | Path                      | Description                                |
|--------|---------------------------|--------------------------------------------|
| GET    | `/api/trades/active`      | Active positions from `active_trades.json` |
| GET    | `/api/trades/history`     | Closed trades from `trade_history.json` (paginated: `?limit=50&offset=0`) |
| GET    | `/api/trades/stats`       | Aggregated: win rate, total PnL, avg hold time, trade count |

### Charts

| Method | Path                       | Description                             |
|--------|----------------------------|-----------------------------------------|
| GET    | `/api/charts/list`         | List available chart PNGs with metadata |
| GET    | `/api/charts/{filename}`   | Serve chart PNG by filename             |

### Logs

| Method | Path                      | Description                                        |
|--------|---------------------------|----------------------------------------------------|
| GET    | `/api/logs/system`        | Tail of `data/steps.log` (`?lines=100`, default 100) |
| GET    | `/api/logs/{symbol}`      | Tail of `data/logs/{SYMBOL}.log` (`?lines=100`)     |

### AI Journal

| Method | Path                        | Description                            |
|--------|-----------------------------|----------------------------------------|
| GET    | `/api/journal`              | Full decision journal (all symbols)    |
| GET    | `/api/journal/{symbol}`     | Journal entries + trade plan for symbol |

### Configuration

| Method | Path                | Description                                          |
|--------|---------------------|------------------------------------------------------|
| GET    | `/api/config`       | Current `bot_config.json` (sanitized, no secrets)    |
| PUT    | `/api/config`       | Replace config (validated against schema whitelist)   |

### WebSocket

| Path       | Description                                                   |
|------------|---------------------------------------------------------------|
| `/ws`      | WebSocket endpoint for real-time updates (auth via query param) |

---

## WebSocket Events

All WebSocket messages use JSON format:

```json
{
  "type": "<event_type>",
  "data": { ... },
  "ts": "2026-02-13T12:00:00Z"
}
```

### Server -> Client Events

| Event Type         | Trigger                          | Data Payload                                 |
|--------------------|----------------------------------|----------------------------------------------|
| `trade_update`     | `active_trades.json` changed     | Full active trades object                    |
| `trade_closed`     | `trade_history.json` changed     | `{ "symbol": "BTCUSDT", "pnl": 12.5, ... }` |
| `journal_update`   | `decision_journal.json` changed  | Updated journal for changed symbol           |
| `chart_update`     | `charts/{SYMBOL}.png` changed    | `{ "symbol": "BTCUSDT", "filename": "BTCUSDT.png" }` |
| `log_line`         | `steps.log` appended             | `{ "source": "system", "line": "..." }`      |
| `log_symbol`       | `logs/{SYMBOL}.log` appended     | `{ "source": "BTCUSDT", "line": "..." }`     |
| `config_changed`   | `bot_config.json` changed        | Updated config object                        |
| `ping`             | Every 30s keepalive              | `{}`                                         |

### Client -> Server Events

| Event Type         | Description                                          |
|--------------------|------------------------------------------------------|
| `subscribe`        | `{ "channels": ["trades", "charts", "logs", ...] }`  |
| `pong`             | Response to server ping                              |

---

## File Structure

```
src/telegram_panel/
|-- ARCHITECTURE.md          # This document
|-- run_panel.py             # Development entry point
|-- requirements.txt         # Python dependencies
|-- Dockerfile               # Multi-stage build (frontend + backend)
|-- docker-compose.yml       # Deployment orchestration
|-- .env.example             # Environment variable template
|-- __init__.py
|
|-- backend/
|   |-- __init__.py
|   |-- app.py               # FastAPI app factory, lifespan, CORS, static mount
|   |-- config.py            # Panel-specific settings (env vars, paths)
|   |
|   |-- routes/
|   |   |-- __init__.py
|   |   |-- dashboard.py     # GET /api/dashboard
|   |   |-- trades.py        # GET /api/trades/*
|   |   |-- charts.py        # GET /api/charts/*
|   |   |-- logs.py          # GET /api/logs/*
|   |   |-- config.py        # GET/PUT /api/config
|   |   |-- journal.py       # GET /api/journal/*
|   |
|   |-- services/
|   |   |-- __init__.py
|   |   |-- auth.py          # Telegram initData HMAC validation
|   |   |-- data_reader.py   # Read/parse bot data files (JSON, logs)
|   |   |-- file_watcher.py  # watchfiles-based directory observer
|   |
|   |-- ws.py                # WebSocket connection manager + broadcast
|
|-- bot.py                   # Telegram bot (commands + notifications)
|
|-- frontend/
|   |-- index.html
|   |-- package.json
|   |-- vite.config.ts
|   |-- tailwind.config.js
|   |-- tsconfig.json
|   |-- postcss.config.js
|   |
|   |-- src/
|   |   |-- main.tsx          # Entry point, Telegram WebApp SDK init
|   |   |-- App.tsx           # Root component with tab router
|   |   |-- vite-env.d.ts
|   |   |
|   |   |-- api/
|   |   |   |-- client.ts         # Fetch wrapper with initData auth header
|   |   |   |-- types.ts          # TypeScript interfaces for API responses
|   |   |
|   |   |-- hooks/
|   |   |   |-- useWebSocket.ts    # WebSocket connection + auto-reconnect
|   |   |   |-- useTelegram.ts     # Telegram WebApp SDK helper
|   |   |
|   |   |-- pages/
|   |   |   |-- Dashboard.tsx      # Overview: balance, active positions, PnL
|   |   |   |-- Charts.tsx         # Chart images with live refresh
|   |   |   |-- Trades.tsx         # Active + history table with stats
|   |   |   |-- Logs.tsx           # Live log viewer (system + per-symbol)
|   |   |   |-- Settings.tsx       # Config editor (strategy, thresholds)
|   |   |   |-- Journal.tsx        # AI decision journal viewer
|   |   |
|   |   |-- components/
|   |   |   |-- TabBar.tsx         # Bottom navigation tabs
|   |   |   |-- TradeCard.tsx      # Single trade display card
|   |   |   |-- StatsCard.tsx      # Metric card (balance, PnL, win rate)
|   |   |   |-- LogViewer.tsx      # Scrollable log output
|   |   |   |-- ChartViewer.tsx    # Chart image with auto-refresh
|   |   |   |-- Spinner.tsx        # Loading indicator
|   |   |
|   |   |-- styles/
|   |   |   |-- globals.css        # TailwindCSS imports + Telegram theme vars
```

---

## Data Flow

### Startup Sequence

```
1. FastAPI app starts (app.py lifespan)
2. |-- Load panel config from env vars
3. |-- Start Telegram bot as async background task
4. |-- Start file watcher as async background task
5. |-- Mount static files (frontend/dist/) at /
6. |-- Begin accepting HTTP/WS connections
```

### REST Request Flow

```
Telegram Mini App
  |
  |--> GET /api/trades/active
  |      |
  |      |--> auth.py: validate Telegram initData HMAC
  |      |--> auth.py: check user.id == TELEGRAM_ADMIN_ID
  |      |--> data_reader.py: read data/active_trades.json
  |      |--> Return JSON response
  |
  |<-- { "BTCUSDT": { "side": "LONG", "entry_price": 98000, ... } }
```

### Real-time Update Flow (WebSocket)

```
Trading bot writes data/active_trades.json
  |
  |--> file_watcher detects inotify event
  |
  |--> ws.py: broadcast({
  |      "type": "trade_update",
  |      "data": <parsed active trades>,
  |      "ts": "..."
  |    })
  |
  |--> All connected WebSocket clients receive update
  |--> React Dashboard component re-renders with new data
```

### Config Update Flow

```
React Settings page
  |
  |--> PUT /api/config  { "STRATEGY_STYLE": "SWING", ... }
  |      |
  |      |--> auth.py: validate initData + admin check
  |      |--> Validate against editable keys whitelist
  |      |--> Read current bot_config.json
  |      |--> Merge validated changes
  |      |--> Write updated bot_config.json
  |      |--> Return 200 OK
  |
  |--> file_watcher detects bot_config.json change
  |--> WebSocket broadcasts config_changed event
  |
  NOTE: Trading bot reads config at import time (src/config.py).
  Config changes require bot restart to take effect. The panel
  does NOT restart the trading bot -- operator must do this manually.
```

### Telegram Bot Notification Flow

```
file_watcher detects trade_history.json changed
  |
  |--> Compare with previous state (new entry = trade closed)
  |--> bot.py: send message to TELEGRAM_ADMIN_ID
  |    "Trade closed: BTCUSDT LONG, PnL: +12.5 USDT"
```

---

## Security Model

### Authentication: Telegram initData Validation

Every API request must include the Telegram WebApp `initData` string in the
`Authorization` header:

```
Authorization: tma <initData>
```

The backend validates this using the standard Telegram HMAC-SHA256 scheme:

1. Parse the `initData` query string
2. Extract `hash` parameter
3. Build data-check-string from remaining sorted key=value pairs joined by `\n`
4. Compute `secret_key = HMAC-SHA256("WebAppData", BOT_TOKEN)`
5. Compute `HMAC-SHA256(secret_key, data_check_string)`
6. Compare computed hash with provided hash
7. Verify `auth_date` is within acceptable window (max 3600 seconds)

### Authorization: Admin-only Access

After validating `initData`, extract `user.id` and compare against the
`TELEGRAM_ADMIN_ID` environment variable. Only the configured admin user
can access any endpoint. All others receive `403 Forbidden`.

### WebSocket Authentication

WebSocket connections are authenticated on the upgrade request via query parameter:
```
ws://host:port/ws?initData=<encoded_initData>
```
Validated once on connect. Invalid connections are rejected immediately.

### Additional Security Measures

- **CORS**: Restricted to Telegram WebApp origins only
- **Config writes whitelist**: Only specific top-level keys can be modified
  via the API. Sensitive fields (API keys, exchange secrets) are never exposed
  or editable through the panel.
- **File path traversal prevention**: Symbol names in log/chart endpoints are
  validated against the configured symbols list from `bot_config.json`
  (`EXCHANGE_SYMBOLS`). Path components like `..` are rejected.
- **No secret exposure**: `.env` contents, API keys (`BINGX_API_KEY`,
  `OPENROUTER_API_KEY`, etc.) are never returned by any endpoint.
- **Rate limiting**: 60 requests/minute per connection (prevents accidental floods)

---

## Deployment

### Dockerfile (Multi-stage Build)

```dockerfile
# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + built frontend
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY bot.py ./bot.py
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
EXPOSE 8080
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

### docker-compose.yml

```yaml
services:
  panel:
    build:
      context: ./src/telegram_panel
      dockerfile: Dockerfile
    ports:
      - "${PANEL_PORT:-8080}:8080"
    volumes:
      # Mount bot data directory read-only
      - ../../data:/app/data:ro
      # Mount charts directory read-only
      - ../../charts:/app/charts:ro
      # Mount config read-write (for settings updates)
      - ../../bot_config.json:/app/bot_config.json:rw
    env_file: ../../.env
    environment:
      - PANEL_PORT=8080
      - PANEL_DATA_DIR=/app/data
      - PANEL_CHARTS_DIR=/app/charts
      - PANEL_CONFIG_PATH=/app/bot_config.json
    restart: unless-stopped
```

### Environment Variables

| Variable               | Required | Description                                       |
|------------------------|----------|---------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`   | Yes      | Bot token from @BotFather                         |
| `TELEGRAM_ADMIN_ID`    | Yes      | Telegram user ID of the admin (integer)           |
| `TELEGRAM_PANEL_URL`   | Yes      | Public HTTPS URL where the panel is hosted        |
| `PANEL_PORT`           | No       | Port to listen on (default: 8080)                 |
| `PANEL_DATA_DIR`       | No       | Path to bot's `data/` directory (default: `/app/data`) |
| `PANEL_CHARTS_DIR`     | No       | Path to bot's `charts/` directory (default: `/app/charts`) |
| `PANEL_CONFIG_PATH`    | No       | Path to `bot_config.json` (default: `/app/bot_config.json`) |
| `BINGX_API_KEY`        | No       | For `/api/dashboard` balance (reuses bot's key)   |
| `BINGX_SECRET_KEY`     | No       | For `/api/dashboard` balance (reuses bot's key)   |
| `MODE`                 | No       | `demo` or `real` (affects BingX API endpoint)     |

### Deployment Notes

1. **Runs independently** from the trading bot. They share only the filesystem
   via Docker volume mounts (data/, charts/, bot_config.json).
2. **HTTPS required** for Telegram Mini Apps. Use a reverse proxy (nginx/caddy)
   with TLS or a tunnel (ngrok/cloudflared) for development.
3. **Telegram Bot Setup**: Create a new bot with @BotFather, then configure the
   Mini App button via `/setmenubutton` pointing to `${TELEGRAM_PANEL_URL}`.
4. **No database required**. All state is read from existing JSON files written
   by the trading bot.
5. **Development**: Use ngrok for HTTPS tunnel (`ngrok http 8080`), set
   `TELEGRAM_PANEL_URL` to the ngrok URL. The Vite dev server proxies API
   requests to the FastAPI backend.

---

## Key Design Decisions

1. **Read-only data access (except config)**: The panel only reads data files
   written by the trading bot. The sole exception is `bot_config.json` which
   can be updated via the Settings page. This avoids race conditions and file
   locking issues with the bot's multiprocessing architecture (one process per
   symbol, each writing independently via `TradeTracker._save_json`).

2. **watchfiles over polling**: The `watchfiles` library uses OS-level file
   notifications (inotify on Linux) with a Rust backend. This is far more
   efficient than periodic HTTP polling and provides sub-second latency for
   updates.

3. **No shared database**: The trading bot writes plain JSON files
   (`trade_history.json`, `active_trades.json`, `decision_journal.json`).
   Rather than introducing a database, the panel reads these files directly.
   This keeps the trading bot completely unchanged and the panel fully decoupled.

4. **Telegram bot as async task inside FastAPI**: Running the bot in the same
   process as the API server avoids a second container. The bot is lightweight
   (few commands, single admin user). Both share the `data_reader` service.

5. **Static file serving**: The built React app is served by FastAPI directly
   via `StaticFiles` mount. For high-traffic production use, a reverse proxy
   can serve static files instead.

6. **Chart cache-busting**: Charts are served as static PNGs. The frontend
   appends a timestamp query parameter (`?t=<unix_ms>`) and refreshes when a
   `chart_update` WebSocket event arrives.

7. **Balance caching**: The `/api/dashboard` endpoint calls the BingX API for
   balance and caches the result for 30 seconds to avoid rate limiting.

---

## Technology Stack

| Layer         | Technology                        | Version   |
|---------------|-----------------------------------|-----------|
| Backend       | Python + FastAPI + Uvicorn        | 3.12+     |
| Telegram Bot  | python-telegram-bot               | 20+       |
| File Watching | watchfiles                        | latest    |
| Frontend      | React + TypeScript + Vite         | React 18  |
| Styling       | TailwindCSS                       | 3.x       |
| Telegram SDK  | @twa-dev/sdk                      | latest    |
| Container     | Docker (multi-stage)              | -         |
| Orchestration | docker-compose                    | -         |

### Python Dependencies (requirements.txt)

```
fastapi>=0.110
uvicorn[standard]>=0.29
python-telegram-bot>=20.0
watchfiles>=0.21
httpx>=0.27
pydantic>=2.0
```
