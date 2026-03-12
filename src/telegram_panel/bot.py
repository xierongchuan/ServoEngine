"""Telegram bot for OpenProducerBot trading panel.

Provides command handlers for monitoring trades, viewing charts/logs,
and an inline button to launch the Telegram Mini App.
"""

import json
import logging
import os
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID: int = int(os.environ.get("TELEGRAM_ADMIN_ID", "0"))
PANEL_URL: str = os.environ.get("TELEGRAM_PANEL_URL", "")

# Разрешённые пользователи (из TELEGRAM_ALLOWED_IDS, фолбек на ADMIN_ID)
_raw_ids = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
ALLOWED_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()}
if not ALLOWED_IDS and ADMIN_ID:
    ALLOWED_IDS = {ADMIN_ID}


def get_project_root() -> Path:
    """Resolve project root relative to this file (2 levels up from telegram_panel/)."""
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT: Path = get_project_root()
DATA_DIR: Path = Path(os.environ.get("PANEL_DATA_DIR", str(PROJECT_ROOT / "data")))
CHARTS_DIR: Path = Path(os.environ.get("PANEL_CHARTS_DIR", str(PROJECT_ROOT / "charts")))
CONFIG_PATH: Path = Path(os.environ.get("PANEL_CONFIG_PATH", str(PROJECT_ROOT / "config" / "active.json")))

ACTIVE_TRADES_PATH: Path = DATA_DIR / "active_trades.json"
TRADE_HISTORY_PATH: Path = DATA_DIR / "trade_history.json"
STEPS_LOG_PATH: Path = DATA_DIR / "steps.log"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_json(path: Path) -> dict | list | None:
    """Safely read and parse a JSON file. Returns None on any error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_log_tail(path: Path, lines: int = 20) -> list[str]:
    """Read the last *lines* lines from a log file."""
    try:
        with open(path, "rb") as f:
            # Seek from end to find enough newlines
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            # Read up to 64KB from the end -- enough for 20 lines
            chunk = min(size, 65536)
            f.seek(-chunk, 2)
            data = f.read().decode("utf-8", errors="replace")
        all_lines = data.splitlines()
        return all_lines[-lines:]
    except Exception:
        return []


def find_latest_chart(symbol: str) -> Path | None:
    """Find the most recently modified chart PNG matching *symbol*."""
    if not CHARTS_DIR.is_dir():
        return None
    matches = sorted(
        CHARTS_DIR.glob(f"{symbol}*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def calc_roe_pct(trade: dict) -> float | None:
    """Calculate ROE% (Return on Equity) for a trade. Returns None if data insufficient."""
    entry = trade.get("entry_price")
    amount = trade.get("amount")
    leverage = trade.get("leverage")
    pnl = trade.get("net_pnl") or trade.get("last_pnl")
    if not all(isinstance(v, (int, float)) for v in [entry, amount, leverage, pnl]):
        return None
    if entry <= 0 or amount <= 0 or leverage <= 0:
        return None
    margin = entry * amount / leverage
    return (pnl / margin) * 100 if margin else None


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------

def allowed_users_only(func):
    """Allow only users from the TELEGRAM_ALLOWED_IDS list."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user is None or update.effective_user.id not in ALLOWED_IDS:
            if update.message:
                await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        return await func(update, context)

    return wrapper


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

@allowed_users_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message with Mini App launcher."""
    text = (
        "OpenProducerBot Trading Panel\n\n"
        "Available commands:\n"
        "/status - Quick status overview\n"
        "/trades - Active positions\n"
        "/chart - Latest chart image\n"
        "/logs - Recent system logs\n"
        "/config - Configuration summary\n"
        "/help - All commands"
    )
    keyboard = []
    if PANEL_URL:
        keyboard.append(
            [InlineKeyboardButton("Open Panel", web_app=WebAppInfo(url=PANEL_URL))]
        )
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(text, reply_markup=reply_markup)  # type: ignore[union-attr]


@allowed_users_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick status: strategy, active positions count, symbols."""
    config = read_json(CONFIG_PATH) or {}
    active = read_json(ACTIVE_TRADES_PATH)
    if not isinstance(active, dict):
        active = {}

    strategy = config.get("STRATEGY_STYLE", "N/A")
    count = len(active)
    symbols = ", ".join(active.keys()) if active else "none"

    text = (
        f"Strategy: {strategy}\n"
        f"Active: {count} position{'s' if count != 1 else ''}\n"
        f"Symbols: {symbols}"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


@allowed_users_only
async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List active trades with details."""
    active = read_json(ACTIVE_TRADES_PATH)
    if not isinstance(active, dict) or not active:
        await update.message.reply_text("No active positions")  # type: ignore[union-attr]
        return

    lines: list[str] = []
    for symbol, trade in active.items():
        side = trade.get("side", "?")
        entry = trade.get("entry_price", "?")
        pnl = trade.get("last_pnl")
        net = trade.get("net_pnl")
        fees = trade.get("estimated_total_fees")
        leverage = trade.get("leverage", "?")
        pnl_str = f"${pnl:+.2f}" if isinstance(pnl, (int, float)) else "N/A"
        if isinstance(net, (int, float)):
            pnl_str += f" (net: ${net:+.2f})"
        roe = calc_roe_pct(trade)
        roe_str = f" | ROE: {roe:+.2f}%" if roe is not None else ""
        fees_str = f" | Fees: ${fees:.2f}" if isinstance(fees, (int, float)) else ""
        lines.append(f"{symbol} {side} @ {entry} | P&L: {pnl_str}{roe_str}{fees_str} | {leverage}x")

    await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]


@allowed_users_only
async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the latest chart PNG for a symbol."""
    # Determine symbol: from args or first symbol in config
    if context.args:
        symbol = context.args[0].upper()
    else:
        config = read_json(CONFIG_PATH) or {}
        exchange_symbols = config.get("EXCHANGE_SYMBOLS", {})
        # Flatten all symbol lists
        all_symbols: list[str] = []
        for syms in exchange_symbols.values():
            if isinstance(syms, list):
                all_symbols.extend(syms)
        symbol = all_symbols[0] if all_symbols else "BTCUSDT"

    chart_path = find_latest_chart(symbol)

    # Если график не найден — пробуем сгенерировать на лету
    if chart_path is None:
        try:
            from src.core.plotter import plot_symbol
            plot_symbol(symbol)
            chart_path = find_latest_chart(symbol)
        except Exception as e:
            logger.warning("On-demand chart generation failed for %s: %s", symbol, e)

    if chart_path is None:
        await update.message.reply_text(f"No charts available for {symbol}")  # type: ignore[union-attr]
        return

    mtime = datetime.fromtimestamp(chart_path.stat().st_mtime, tz=timezone.utc)
    caption = f"{symbol} | {mtime:%Y-%m-%d %H:%M UTC}"
    with open(chart_path, "rb") as photo:
        await update.message.reply_photo(photo=photo, caption=caption)  # type: ignore[union-attr]


@allowed_users_only
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send last 20 lines of steps.log."""
    lines = read_log_tail(STEPS_LOG_PATH, 20)
    if not lines:
        await update.message.reply_text("No logs available")  # type: ignore[union-attr]
        return

    text = "```\n" + "\n".join(lines) + "\n```"
    await update.message.reply_text(text, parse_mode="Markdown")  # type: ignore[union-attr]


@allowed_users_only
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current configuration summary."""
    config_dir = CONFIG_PATH.parent if CONFIG_PATH.name == "active.json" else CONFIG_PATH.parent / "config"

    # Read from new config system
    active = read_json(config_dir / "active.json") or {}
    trading = read_json(config_dir / "trading.json") or {}
    base = read_json(config_dir / "base.json") or {}

    strategy = active.get("strategy", "N/A")
    pos = trading.get("position", {})
    risk = trading.get("risk", {})
    ai = base.get("ai", {})

    pos_size = pos.get("size_percent", "N/A")
    confidence = risk.get("min_confidence_threshold", "N/A")
    model = ai.get("model", "N/A")

    # Get leverage from strategy preset
    leverage = "N/A"
    strat_path = config_dir / "strategies" / f"{strategy.lower()}.json"
    strat = read_json(strat_path) or {}
    leverage = strat.get("preset", {}).get("leverage", "N/A")

    # Symbols
    exchange_symbols = active.get("symbols", {})
    all_symbols: list[str] = []
    for syms in exchange_symbols.values():
        if isinstance(syms, list):
            all_symbols.extend(syms)
    symbols_str = ", ".join(all_symbols) if all_symbols else "none"

    text = (
        f"Strategy: {strategy}\n"
        f"Position size: {pos_size}%\n"
        f"Leverage: {leverage}x\n"
        f"Confidence threshold: {confidence}\n"
        f"AI model: {model}\n"
        f"Symbols: {symbols_str}"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


@allowed_users_only
async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force config reload by touching active.json."""
    try:
        CONFIG_PATH.touch()
        await update.message.reply_text(  # type: ignore[union-attr]
            "🔄 Config reload triggered.\n"
            "Workers will pick up changes within 30 seconds."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to trigger reload: {e}")  # type: ignore[union-attr]


@allowed_users_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all available commands."""
    text = (
        "/start  - Welcome message and Mini App launcher\n"
        "/status - Quick status overview\n"
        "/trades - List active positions\n"
        "/chart  - Send latest chart (optional: /chart ETHUSDT)\n"
        "/logs   - Last 20 lines of system log\n"
        "/config - Configuration summary\n"
        "/stop   - Stop trading for symbol (e.g., /stop BTCUSDT)\n"
        "/resume - Resume trading for symbol (e.g., /resume BTCUSDT)\n"
        "/close  - Close position by symbol (e.g., /close BTCUSDT)\n"
        "/reload - Force config reload for all workers\n"
        "/help   - Show this help message"
    )
    await update.message.reply_text(text)  # type: ignore[union-attr]


@allowed_users_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable trading for a symbol."""
    if not context.args:
        await update.message.reply_text("Usage: /stop <SYMBOL>\nExample: /stop BTCUSDT")  # type: ignore[union-attr]
        return

    symbol = context.args[0].upper().replace(" ", "")

    # Read current config
    config = read_json(CONFIG_PATH) or {}
    disabled = config.get("DISABLED_SYMBOLS", [])

    if symbol in disabled:
        await update.message.reply_text(f"⚠️ {symbol} is already disabled")  # type: ignore[union-attr]
        return

    disabled.append(symbol)
    config["DISABLED_SYMBOLS"] = disabled

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        await update.message.reply_text(f"⏹️ Trading disabled for {symbol}\nNew positions will not be opened.")  # type: ignore[union-attr]
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")  # type: ignore[union-attr]


@allowed_users_only
async def cmd_start_trading(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable trading for a symbol."""
    if not context.args:
        await update.message.reply_text("Usage: /start <SYMBOL>\nExample: /start BTCUSDT")  # type: ignore[union-attr]
        return

    symbol = context.args[0].upper().replace(" ", "")

    # Read current config
    config = read_json(CONFIG_PATH) or {}
    disabled = config.get("DISABLED_SYMBOLS", [])

    if symbol not in disabled:
        await update.message.reply_text(f"⚠️ {symbol} is already enabled")  # type: ignore[union-attr]
        return

    disabled.remove(symbol)
    config["DISABLED_SYMBOLS"] = disabled

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        await update.message.reply_text(f"▶️ Trading enabled for {symbol}")  # type: ignore[union-attr]
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")  # type: ignore[union-attr]


@allowed_users_only
async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close position for a symbol at market price."""
    if not context.args:
        await update.message.reply_text("Usage: /close <SYMBOL>\nExample: /close BTCUSDT")  # type: ignore[union-attr]
        return

    symbol = context.args[0].upper().replace(" ", "")

    # Read active trades
    active = read_json(ACTIVE_TRADES_PATH)
    if not isinstance(active, dict):
        active = {}

    if symbol not in active:
        await update.message.reply_text(f"ℹ️ No active position for {symbol}")  # type: ignore[union-attr]
        return

    trade = active[symbol]
    deal_id = trade.get("deal_id") or trade.get("dealId")

    if not deal_id:
        await update.message.reply_text(f"❌ Cannot find deal ID for {symbol}")  # type: ignore[union-attr]
        return

    # Try to close the position
    try:
        from src.exchanges.exchange_factory import get_exchange_client
        client = get_exchange_client()

        if hasattr(client, "close_position"):
            success = client.close_position(symbol, deal_id, 1.0)
            if success:
                await update.message.reply_text(f"✅ Position {symbol} closed successfully")  # type: ignore[union-attr]
            else:
                await update.message.reply_text(f"❌ Failed to close position {symbol}")  # type: ignore[union-attr]
        else:
            await update.message.reply_text(f"❌ Exchange client does not support close_position")  # type: ignore[union-attr]
    except Exception as e:
        await update.message.reply_text(f"❌ Error closing position: {str(e)}")  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Notification system
# ---------------------------------------------------------------------------

class TradingNotifier:
    """Watches active_trades.json for changes and sends notifications."""

    def __init__(self) -> None:
        self._last_mtime: float = 0.0
        self._last_symbols: set[str] = set()
        self._initialized: bool = False

    def _snapshot(self) -> tuple[float, dict[str, Any]]:
        """Return (mtime, trades_dict). Returns (0, {}) on error."""
        try:
            mtime = ACTIVE_TRADES_PATH.stat().st_mtime
        except OSError:
            return 0.0, {}
        trades = read_json(ACTIVE_TRADES_PATH)
        if not isinstance(trades, dict):
            trades = {}
        return mtime, trades

    async def check_for_updates(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Called periodically by job_queue. Detects new/closed trades."""
        mtime, trades = self._snapshot()

        # Skip if file hasn't changed
        if mtime == self._last_mtime:
            return

        self._last_mtime = mtime
        current_symbols = set(trades.keys())

        if not self._initialized:
            # First run: just capture state, don't send notifications
            self._last_symbols = current_symbols
            self._initialized = True
            return

        # Detect new trades
        opened = current_symbols - self._last_symbols
        for sym in opened:
            trade = trades.get(sym, {})
            side = trade.get("side", "?")
            entry = trade.get("entry_price", "?")
            leverage = trade.get("leverage", "?")
            message = f"New {side} position opened: {sym} @ ${entry} | {leverage}x"
            for user_id in ALLOWED_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=message,
                    )
                except Exception as e:
                    logger.error("Failed to send open notification to %s: %s", user_id, e)

        # Detect closed trades
        closed = self._last_symbols - current_symbols
        for sym in closed:
            # Try to find the close info from trade_history.json
            history = read_json(TRADE_HISTORY_PATH)
            pnl_str = "N/A"
            roe_str = ""
            fees_str = ""
            if isinstance(history, list):
                for entry in reversed(history):
                    if isinstance(entry, dict) and entry.get("symbol") == sym:
                        pnl = entry.get("last_pnl")
                        net = entry.get("net_pnl")
                        fees = entry.get("estimated_total_fees")
                        if isinstance(pnl, (int, float)):
                            pnl_str = f"${pnl:+.2f}"
                            if isinstance(net, (int, float)):
                                pnl_str += f" (net: ${net:+.2f})"
                        roe = calc_roe_pct(entry)
                        if roe is not None:
                            roe_str = f" | ROE: {roe:+.2f}%"
                        if isinstance(fees, (int, float)):
                            fees_str = f" | Fees: ${fees:.2f}"
                        break
            message = f"Position closed: {sym} | P&L: {pnl_str}{roe_str}{fees_str}"
            for user_id in ALLOWED_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=message,
                    )
                except Exception as e:
                    logger.error("Failed to send close notification to %s: %s", user_id, e)

        self._last_symbols = current_symbols

    def start_polling(self, app: Application) -> None:
        """Register the periodic check in the application's job queue."""
        if app.job_queue is None:
            logger.warning("job_queue is None -- notifications disabled")
            return
        app.job_queue.run_repeating(
            self.check_for_updates,
            interval=10,
            first=5,
            name="trade_notifier",
        )
        logger.info("Trade notifier started (10s interval)")


# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------

class TelegramPanelBot:
    """Wraps Application setup, handler registration, and notifier."""

    def __init__(self) -> None:
        if not BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        self.app: Application = Application.builder().token(BOT_TOKEN).build()
        self.notifier = TradingNotifier()

    def _register_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", cmd_start))
        self.app.add_handler(CommandHandler("status", cmd_status))
        self.app.add_handler(CommandHandler("trades", cmd_trades))
        self.app.add_handler(CommandHandler("chart", cmd_chart))
        self.app.add_handler(CommandHandler("logs", cmd_logs))
        self.app.add_handler(CommandHandler("config", cmd_config))
        self.app.add_handler(CommandHandler("stop", cmd_stop))
        self.app.add_handler(CommandHandler("resume", cmd_start_trading))
        self.app.add_handler(CommandHandler("close", cmd_close))
        self.app.add_handler(CommandHandler("reload", cmd_reload))
        self.app.add_handler(CommandHandler("help", cmd_help))

    def run(self) -> None:
        """Start the bot with long polling (blocking)."""
        self._register_handlers()
        self.notifier.start_polling(self.app)
        logger.info("Telegram bot starting (polling)...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

    def get_app(self) -> Application:
        """Return configured Application (for external startup, e.g. run_panel.py)."""
        self._register_handlers()
        self.notifier.start_polling(self.app)
        return self.app


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    bot = TelegramPanelBot()
    bot.run()


if __name__ == "__main__":
    main()
