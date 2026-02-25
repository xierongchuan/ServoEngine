import fcntl
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

    def _save_active_trades(self, symbol: str = None, delete: bool = False):
        """Атомарный read-modify-write для active_trades.json."""
        try:
            if os.path.exists(ACTIVE_TRADES_FILE):
                with open(ACTIVE_TRADES_FILE, "r+") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        f.seek(0)
                        content = f.read()
                        if content.strip():
                            try:
                                disk_data = json.loads(content)
                            except json.JSONDecodeError:
                                warning("[TradeTracker] Corrupt active_trades, rebuilding")
                                disk_data = {}
                        else:
                            disk_data = {}

                        if symbol:
                            if delete:
                                disk_data.pop(symbol, None)
                            elif symbol in self.active_trades:
                                disk_data[symbol] = self.active_trades[symbol]
                            # Обновляем in-memory данные других символов
                            for key, value in disk_data.items():
                                if key != symbol:
                                    self.active_trades[key] = value
                        else:
                            # Полная запись (force_sync_all при старте)
                            disk_data = dict(self.active_trades)

                        f.seek(0)
                        f.truncate()
                        json.dump(disk_data, f, indent=4, default=str)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
            else:
                with open(ACTIVE_TRADES_FILE, "w") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        json.dump(self.active_trades, f, indent=4, default=str)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            warning(f"Failed to save {ACTIVE_TRADES_FILE}: {e}")

    def _append_history(self, entry: dict):
        """Атомарный append в trade_history.json."""
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r+") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        f.seek(0)
                        content = f.read()
                        if content.strip():
                            try:
                                history = json.loads(content)
                            except json.JSONDecodeError:
                                warning("[TradeTracker] Corrupt history, starting fresh")
                                history = []
                        else:
                            history = []
                        if not isinstance(history, list):
                            history = []
                        history.append(entry)
                        f.seek(0)
                        f.truncate()
                        json.dump(history, f, indent=4, default=str)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
            else:
                with open(HISTORY_FILE, "w") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        json.dump([entry], f, indent=4, default=str)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            warning(f"Failed to append to {HISTORY_FILE}: {e}")

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

    def set_entry_context(self, symbol: str, context: dict):
        """
        Сохраняет контекст входа в сделку для анализа производительности.

        Args:
            symbol: Торговая пара
            context: dict с ключами: entry_regime, entry_score, entry_quality,
                     entry_rsi, entry_atr, entry_volume_ratio
        """
        trade = self.active_trades.get(symbol)
        if trade:
            for key in ("entry_regime", "entry_score", "entry_quality",
                        "entry_rsi", "entry_atr", "entry_volume_ratio"):
                if key in context:
                    trade[key] = context[key]
            self.active_trades[symbol] = trade
            self._save_active_trades(symbol)

    def _handle_new_trade(self, symbol, real_position):
        """Register a new trade"""
        from src.config import TRADING_FEE_TAKER, LEVERAGE

        entry_price = float(real_position.get("entry", real_position.get("avgPrice", 0)))
        amount = float(real_position.get("size", real_position.get("amount", 0)))
        estimated_entry_fee = entry_price * amount * (TRADING_FEE_TAKER / 100.0)

        trade_data = {
            "symbol": symbol,
            "dealId": real_position.get("dealId", ""),
            "side": "LONG" if real_position.get("type", "").upper() == "BUY" else "SHORT" if real_position.get("type", "").upper() == "SELL" else real_position.get("side", "UNKNOWN"),
            "entry_price": entry_price,
            "amount": amount,
            "leverage": real_position.get("leverage") or LEVERAGE,
            "open_time": datetime.now().isoformat(),
            "status": "OPEN",
            "pnl_history": [],
            "estimated_entry_fee": round(estimated_entry_fee, 4),
            "estimated_total_fees": round(estimated_entry_fee * 2, 4),
            "fee_rate_used": TRADING_FEE_TAKER,
            "net_pnl": 0.0,
            "cumulative_funding": 0.0,
        }

        self.active_trades[symbol] = trade_data
        self._save_active_trades(symbol)

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
        self._append_history(stored_trade)

        # Remove from active
        del self.active_trades[symbol]
        self._save_active_trades(symbol, delete=True)

        log_trade(f"🏁 [TradeTracker] Trade CLOSED for {symbol}. Last PnL: {stored_trade.get('last_pnl', 'N/A')}")
        info(f"🏁 [TradeTracker] Trade archived: {symbol}")

    def _handle_update_trade(self, symbol, stored_trade, real_position):
        """Update PnL and other stats"""
        current_pnl = float(real_position.get("unrealizedPnl", 0) or real_position.get("pnl", 0))
        stored_trade["last_pnl"] = current_pnl
        current_mark = real_position.get("markPrice") or real_position.get("avgPrice")
        stored_trade["current_price"] = current_mark

        # Repair missing dealId for trades created before this field was stored
        if "dealId" not in stored_trade and real_position.get("dealId"):
            stored_trade["dealId"] = real_position.get("dealId")

        # Always sync entry_price from exchange (source of truth)
        exchange_entry = float(real_position.get("entry", real_position.get("avgPrice", 0)))
        if exchange_entry > 0:
            stored_trade["entry_price"] = exchange_entry

        # Repair side if unknown
        if stored_trade.get("side") in ["UNKNOWN", None]:
            stored_trade["side"] = "LONG" if real_position.get("type", "").upper() == "BUY" else "SHORT" if real_position.get("type", "").upper() == "SELL" else "UNKNOWN"

        # Repair missing leverage from exchange data
        if not stored_trade.get("leverage") and real_position.get("leverage"):
            stored_trade["leverage"] = real_position.get("leverage")

        # Recalculate fees and net PnL
        entry_fee = stored_trade.get("estimated_entry_fee", 0)
        amount = stored_trade.get("amount", 0)
        if current_mark and amount > 0:
            from src.config import TRADING_FEE_TAKER
            fee_rate = stored_trade.get("fee_rate_used", TRADING_FEE_TAKER)
            exit_fee = float(current_mark) * amount * (fee_rate / 100.0)
            stored_trade["estimated_total_fees"] = round(entry_fee + exit_fee, 4)
            stored_trade["net_pnl"] = round(current_pnl - entry_fee - exit_fee, 4)

        # Track Max/Min PnL
        pnl_history = stored_trade.get("pnl_history", [])
        # Only keep last 100 points to save space? Or just max/min
        stored_trade["max_pnl"] = max(stored_trade.get("max_pnl", -999999), current_pnl)
        stored_trade["min_pnl"] = min(stored_trade.get("min_pnl", 999999), current_pnl)

        self.active_trades[symbol] = stored_trade
        self._save_active_trades(symbol)

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
            self._append_history(stored)
            del self.active_trades[symbol]

        # 2. Add missing trades (on exchange but not in tracker)
        for symbol, real_pos in real_positions_dict.items():
            if symbol not in self.active_trades and real_pos:
                self._handle_new_trade(symbol, real_pos)

        self._save_active_trades()

        if stale_symbols:
            info(f"🧹 [TradeTracker] Cleaned {len(stale_symbols)} stale trades: {stale_symbols}")

        return len(stale_symbols)


