import json
import os
import time
from datetime import datetime
from src.config import DATA_DIR
from src.utils.logger import info, warning, log_trade

HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.json")
ACTIVE_TRADES_FILE = os.path.join(DATA_DIR, "active_trades.json")

class TradeTracker:
    def __init__(self):
        self._ensure_files()
        self.active_trades = self._load_json(ACTIVE_TRADES_FILE)

    def _ensure_files(self):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'w') as f:
                json.dump([], f)
        if not os.path.exists(ACTIVE_TRADES_FILE):
            with open(ACTIVE_TRADES_FILE, 'w') as f:
                json.dump({}, f)

    def _load_json(self, filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception:
            return {} if filepath == ACTIVE_TRADES_FILE else []

    def _save_json(self, filepath, data):
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=4, default=str)
        except Exception as e:
            warning(f"Failed to save {filepath}: {e}")

    def sync_position(self, symbol, real_position):
        """
        Synchronizes the internal state with the real position from the exchange.
        Handles detecting new trades and closed trades (including manual closures).
        """
        stored_trade = self.active_trades.get(symbol)

        # Scenario 1: New Position detected (Real exists, Stored does not)
        if real_position and not stored_trade:
            self._handle_new_trade(symbol, real_position)
            return self.active_trades.get(symbol)

        # Scenario 2: Position Closed (Stored exists, Real does not)
        elif not real_position and stored_trade:
            self._handle_closed_trade(symbol, stored_trade)
            return None

        # Scenario 3: Update existing position (Both exist)
        elif real_position and stored_trade:
            # Check if deal_id changed (unlikely but possible if closed and reopened fast)
            # BingX usually has 'positionId' or we use symbol as key.
            # Assuming symbol is key is fine for isolated margin/one-way mode.
            self._handle_update_trade(symbol, stored_trade, real_position)
            return self.active_trades.get(symbol)

        return None

    def _handle_new_trade(self, symbol, real_position):
        """Register a new trade"""
        trade_data = {
            "symbol": symbol,
            "side": "LONG" if real_position.get("type", "").upper() == "BUY" else "SHORT" if real_position.get("type", "").upper() == "SELL" else real_position.get("side", "UNKNOWN"),
            "entry_price": float(real_position.get("entry", real_position.get("avgPrice", 0))),
            "amount": float(real_position.get("size", real_position.get("amount", 0))),
            "leverage": real_position.get("leverage"),
            "open_time": datetime.now().isoformat(),
            "status": "OPEN",
            "pnl_history": []
        }

        self.active_trades[symbol] = trade_data
        self._save_json(ACTIVE_TRADES_FILE, self.active_trades)

        log_trade(f"🆕 [TradeTracker] Detected NEW trade for {symbol} @ {trade_data['entry_price']}")
        info(f"🆕 [TradeTracker] New trade tracked: {symbol}")

    def _handle_closed_trade(self, symbol, stored_trade):
        """Archive a closed trade"""
        # Since we don't have the Real position anymore, we don't know the EXACT close price
        # unless we query order history. For now, we will mark it as closed.
        # Ideally, passed PnL from the last update is used.

        stored_trade["status"] = "CLOSED"
        stored_trade["close_time"] = datetime.now().isoformat()
        stored_trade["reason"] = "MANUAL_OR_TP_SL" # We infer this because it disappeared using sync()

        # Move to history
        history = self._load_json(HISTORY_FILE)
        if isinstance(history, dict): history = [] # Safe fallback
        history.append(stored_trade)
        self._save_json(HISTORY_FILE, history)

        # Remove from active
        del self.active_trades[symbol]
        self._save_json(ACTIVE_TRADES_FILE, self.active_trades)

        log_trade(f"🏁 [TradeTracker] Trade CLOSED for {symbol}. Last PnL: {stored_trade.get('last_pnl', 'N/A')}")
        info(f"🏁 [TradeTracker] Trade archived: {symbol}")

    def _handle_update_trade(self, symbol, stored_trade, real_position):
        """Update PnL and other stats"""
        current_pnl = float(real_position.get("unrealizedPnl", 0) or real_position.get("pnl", 0))
        stored_trade["last_pnl"] = current_pnl
        stored_trade["current_price"] = real_position.get("markPrice") or real_position.get("avgPrice")

        # Repair entry price if missing/zero (from previous bug)
        if stored_trade.get("entry_price", 0) == 0:
             stored_trade["entry_price"] = float(real_position.get("entry", real_position.get("avgPrice", 0)))
             # Also repair side if unknown
             if stored_trade.get("side") in ["UNKNOWN", None]:
                 stored_trade["side"] = "LONG" if real_position.get("type", "").upper() == "BUY" else "SHORT" if real_position.get("type", "").upper() == "SELL" else "UNKNOWN"

        # Track Max/Min PnL
        pnl_history = stored_trade.get("pnl_history", [])
        # Only keep last 100 points to save space? Or just max/min
        stored_trade["max_pnl"] = max(stored_trade.get("max_pnl", -999999), current_pnl)
        stored_trade["min_pnl"] = min(stored_trade.get("min_pnl", 999999), current_pnl)

        self.active_trades[symbol] = stored_trade
        self._save_json(ACTIVE_TRADES_FILE, self.active_trades)

    def get_active_trade_info(self, symbol):
        return self.active_trades.get(symbol)

    def force_sync_all(self, real_positions_dict: dict):
        """
        Force sync all trades with actual exchange positions.
        real_positions_dict: {symbol: position_data} from exchange

        Removes stale trades that don't exist on exchange.
        Adds trades that exist on exchange but not in tracker.
        """
        # 1. Remove stale trades (in tracker but not on exchange)
        stale_symbols = []
        for symbol in list(self.active_trades.keys()):
            if symbol not in real_positions_dict:
                stale_symbols.append(symbol)

        for symbol in stale_symbols:
            stored = self.active_trades[symbol]
            log_trade(f"🧹 [TradeTracker] Removing STALE trade {symbol} (not on exchange)")
            # Move to history as closed
            stored["status"] = "CLOSED_SYNC"
            stored["close_time"] = datetime.now().isoformat()
            stored["reason"] = "STALE_SYNC_CLEANUP"
            history = self._load_json(HISTORY_FILE)
            if isinstance(history, dict): history = []
            history.append(stored)
            self._save_json(HISTORY_FILE, history)
            del self.active_trades[symbol]

        # 2. Add missing trades (on exchange but not in tracker)
        for symbol, real_pos in real_positions_dict.items():
            if symbol not in self.active_trades and real_pos:
                self._handle_new_trade(symbol, real_pos)

        self._save_json(ACTIVE_TRADES_FILE, self.active_trades)

        if stale_symbols:
            info(f"🧹 [TradeTracker] Cleaned {len(stale_symbols)} stale trades: {stale_symbols}")

        return len(stale_symbols)

    def reload_from_disk(self):
        """Reload active trades from disk (useful if file was edited externally)"""
        self.active_trades = self._load_json(ACTIVE_TRADES_FILE)
        return len(self.active_trades)

