import fcntl
import json
import os
import time
from datetime import datetime
from src.config import DATA_DIR
from src.utils.logger import info, warning, log_trade

HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.json")
ACTIVE_TRADES_FILE = os.path.join(DATA_DIR, "active_trades.json")
BATCH_WRITE_INTERVAL = 15  # секунд между записями PnL-обновлений

class TradeTracker:
    def __init__(self):
        self._ensure_files()
        self.active_trades = self._load_json(ACTIVE_TRADES_FILE)
        self._dirty = False
        self._last_save_time = time.monotonic()

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
                data = json.load(f)
                if filepath == ACTIVE_TRADES_FILE and not isinstance(data, dict):
                    warning(f"Invalid format in {filepath}, resetting to {{}}")
                    return {}
                if filepath == HISTORY_FILE and not isinstance(data, list):
                    warning(f"Invalid format in {filepath}, resetting to []")
                    return []
                return data
        except Exception:
            return {} if filepath == ACTIVE_TRADES_FILE else []

    def get_active_trade(self, symbol: str):
        """Возвращает активную позицию по символу или None."""
        normalized = symbol.replace("-", "")
        return self.active_trades.get(normalized)

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

    def _is_duplicate_in_history(self, deal_id: str) -> bool:
        """Проверяет, есть ли уже сделка с таким dealId в истории.

        Также проверяет по паре (symbol, entry_price, side, open_time) для надёжности.
        """
        if not deal_id:
            return False
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f:
                    content = f.read()
                    if content.strip():
                        try:
                            history = json.loads(content)
                            if isinstance(history, list):
                                # Check by dealId
                                for trade in history:
                                    if isinstance(trade, dict) and trade.get('dealId') == deal_id:
                                        return True
                                return False
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass
        return False

    def _check_trade_exists(self, trade: dict) -> bool:
        """Проверяет, существует ли сделка в истории по нескольким критериям."""
        deal_id = trade.get('dealId')
        symbol = trade.get('symbol')
        entry_price = trade.get('entry_price')
        side = trade.get('side')
        open_time = trade.get('open_time')

        # If we have dealId, use it
        if deal_id:
            return self._is_duplicate_in_history(deal_id)

        # Otherwise, check by combination of symbol, entry_price, side, open_time
        if not all([symbol, entry_price, side, open_time]):
            return False

        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f:
                    content = f.read()
                    if content.strip():
                        try:
                            history = json.loads(content)
                            if isinstance(history, list):
                                for t in history:
                                    if isinstance(t, dict):
                                        # Check by combination
                                        if (t.get('symbol') == symbol and
                                            t.get('entry_price') == entry_price and
                                            t.get('side') == side and
                                            t.get('open_time') == open_time):
                                            return True
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass
        return False

    def sync_position(self, symbol, real_position, exchange_client=None):
        """
        Synchronizes the internal state with the real position from the exchange.
        Handles detecting new trades and closed trades (including manual closures).
        """
        # Normalize symbol: use format without hyphen to match exchange format (BTCUSDT not BTC-USDT)
        normalized_symbol = symbol.replace("-", "")
        stored_trade = self.active_trades.get(normalized_symbol)

        # Scenario 1: New Position detected (Real exists, Stored does not)
        if real_position and not stored_trade:
            self._handle_new_trade(normalized_symbol, real_position)
            return self.active_trades.get(normalized_symbol)

        # Scenario 2: Position Closed (Stored exists, Real does not)
        elif not real_position and stored_trade:
            self._handle_closed_trade(normalized_symbol, stored_trade, exchange_client=exchange_client)
            return None

        # Scenario 3: Update existing position (Both exist)
        elif real_position and stored_trade:
            # Check if deal_id changed (unlikely but possible if closed and reopened fast)
            # BingX usually has 'positionId' or we use symbol as key.
            # Assuming symbol is key is fine for isolated margin/one-way mode.
            self._handle_update_trade(normalized_symbol, stored_trade, real_position)
            return self.active_trades.get(normalized_symbol)

        return None

    def set_entry_context(self, symbol: str, context: dict):
        """
        Сохраняет контекст входа в сделку для анализа производительности.

        Args:
            symbol: Торговая пара
            context: dict с ключами: entry_regime, entry_score, entry_quality,
                     entry_rsi, entry_atr, entry_volume_ratio
        """
        # Normalize symbol
        normalized_symbol = symbol.replace("-", "")
        trade = self.active_trades.get(normalized_symbol)
        if trade:
            for key in ("entry_regime", "entry_score", "entry_quality",
                        "entry_rsi", "entry_atr", "entry_volume_ratio"):
                if key in context:
                    trade[key] = context[key]
            self.active_trades[normalized_symbol] = trade
            self._save_active_trades(normalized_symbol)

    def _handle_new_trade(self, symbol, real_position):
        """Register a new trade"""
        from src.config import TRADING_FEE_TAKER, LEVERAGE

        # Support both dict and Position dataclass
        # Check for Position dataclass first (has entry_price attribute)
        if hasattr(real_position, 'entry_price') and hasattr(real_position, 'size'):
            # Position dataclass object
            entry_price = float(real_position.entry_price)
            amount = float(real_position.size)
            side = "LONG" if real_position.is_long else "SHORT"
            leverage = real_position.leverage or LEVERAGE
            position_id = getattr(real_position, 'position_id', getattr(real_position, 'dealId', ''))
        elif isinstance(real_position, list):
            # New format: list of Position objects
            if real_position:
                pos = real_position[0]  # Take first position
                if hasattr(pos, 'entry_price'):  # Position dataclass
                    entry_price = float(pos.entry_price)
                    amount = float(pos.size)
                    side = "LONG" if pos.is_long else "SHORT"
                    leverage = pos.leverage or LEVERAGE
                    position_id = pos.position_id
                else:  # dict format
                    entry_price = float(pos.get("entry", pos.get("avgPrice", 0)))
                    amount = float(pos.get("size", pos.get("amount", 0)))
                    side = pos.get("side", "UNKNOWN")
                    leverage = pos.get("leverage") or LEVERAGE
                    position_id = pos.get("positionId", "")
            else:
                return
        else:
            # Old dict format
            entry_price = float(real_position.get("entry", real_position.get("avgPrice", 0)))
            amount = float(real_position.get("size", real_position.get("amount", 0)))
            side = "LONG" if real_position.get("type", "").upper() == "BUY" else "SHORT" if real_position.get("type", "").upper() == "SELL" else real_position.get("side", "UNKNOWN")
            leverage = real_position.get("leverage") or LEVERAGE
            position_id = real_position.get("dealId", real_position.get("positionId", ""))

        estimated_entry_fee = entry_price * amount * (TRADING_FEE_TAKER / 100.0)
        try:
            from src.core.position_ownership import get_position_ownership_store
            owner = get_position_ownership_store().get_owner(symbol)
        except Exception:
            owner = None

        trade_data = {
            "symbol": symbol,
            "dealId": position_id,
            "side": side,
            "entry_price": entry_price,
            "amount": amount,
            "leverage": leverage,
            "open_time": datetime.now().isoformat(),
            "status": "OPEN",
            "pnl_history": [],
            "estimated_entry_fee": round(estimated_entry_fee, 4),
            "estimated_total_fees": round(estimated_entry_fee * 2, 4),
            "fee_rate_used": TRADING_FEE_TAKER,
            "net_pnl": 0.0,
            "cumulative_funding": 0.0,
        }
        if owner:
            trade_data["strategy_instance_id"] = owner.owner_id
            trade_data["strategy"] = owner.strategy
            if position_id:
                try:
                    from src.core.position_ownership import get_position_ownership_store
                    get_position_ownership_store().update_position_id(symbol, owner.owner_id, position_id)
                except Exception:
                    pass

        self.active_trades[symbol] = trade_data
        self._save_active_trades(symbol)

        log_trade(f"🆕 [TradeTracker] Detected NEW trade for {symbol} @ {trade_data['entry_price']}")
        info(f"🆕 [TradeTracker] New trade tracked: {symbol}")

    def _handle_closed_trade(self, symbol, stored_trade, exchange_client=None):
        """Archive a closed trade, enriching with real exchange data when available."""
        stored_trade["status"] = "CLOSED"
        stored_trade["close_time"] = datetime.now().isoformat()
        stored_trade["reason"] = "MANUAL_OR_TP_SL"

        # Try to enrich with real close data from exchange
        enriched = False
        if exchange_client:
            try:
                recent_orders = exchange_client.get_recent_orders(symbol, limit=10)
                # Find last FILLED order with opposite side (LONG→SELL, SHORT→BUY)
                trade_side = stored_trade.get("side", "").upper()
                close_side = "SELL" if trade_side == "LONG" else "BUY"

                close_order = None
                for order in recent_orders:
                    if (order.get("status", "").upper() == "FILLED" and
                            order.get("side", "").upper() == close_side):
                        close_order = order
                        break

                if close_order:
                    close_price = close_order["avgPrice"]
                    realized_pnl = close_order["profit"]
                    close_fee = abs(close_order["commission"])
                    entry_fee = stored_trade.get("estimated_entry_fee", 0)

                    stored_trade["close_price"] = close_price
                    stored_trade["realized_pnl"] = round(realized_pnl, 4)
                    stored_trade["last_pnl"] = round(realized_pnl, 4)
                    stored_trade["actual_close_fee"] = round(close_fee, 4)
                    stored_trade["actual_total_fees"] = round(entry_fee + close_fee, 4)
                    stored_trade["net_pnl"] = round(realized_pnl - entry_fee - close_fee, 4)
                    stored_trade["close_order_id"] = close_order["orderId"]

                    # Use actual close time from exchange
                    update_time = close_order.get("updateTime", 0)
                    if update_time:
                        import time as _time
                        stored_trade["close_time"] = _time.strftime(
                            '%Y-%m-%dT%H:%M:%S', _time.gmtime(update_time / 1000)
                        )

                    enriched = True
                    info(f"💰 [TradeTracker] Enriched close data for {symbol}: "
                         f"close_price={close_price}, realized_pnl={realized_pnl:.4f}, "
                         f"fees={entry_fee + close_fee:.4f}, net_pnl={stored_trade['net_pnl']:.4f}")
                else:
                    warning(f"⚠️ [TradeTracker] No matching close order found for {symbol} "
                            f"(side={trade_side}, looked for {close_side} FILLED, "
                            f"got {len(recent_orders)} orders). Using estimated PnL.")
            except Exception as e:
                warning(f"⚠️ [TradeTracker] Failed to enrich close data for {symbol}: {e}")
        else:
            warning(f"⚠️ [TradeTracker] No exchange client for {symbol}, using estimated PnL")

        # Check for duplicate before adding to history
        if self._check_trade_exists(stored_trade):
            deal_id = stored_trade.get('dealId', 'N/A')
            info(f"⚠️ [TradeTracker] Сделка {deal_id} уже в истории, пропускаем")
        else:
            self._append_history(stored_trade)

        # Remove from active
        del self.active_trades[symbol]
        self._save_active_trades(symbol, delete=True)

        pnl_source = "real" if enriched else "estimated"
        pnl_val = stored_trade.get('last_pnl', 'N/A')
        net_val = stored_trade.get('net_pnl', 'N/A')
        close_p = stored_trade.get('close_price', 'N/A')

        log_trade(f"🏁 [TradeTracker] Trade CLOSED for {symbol}: "
                  f"PnL={pnl_val} ({pnl_source}), net_pnl={net_val}, close_price={close_p}")
        info(f"🏁 [TradeTracker] Trade archived: {symbol} [{pnl_source} data]")

    def _handle_update_trade(self, symbol, stored_trade, real_position):
        """Update PnL and other stats"""
        # Support both dict and Position dataclass (list from new BingX client)
        if isinstance(real_position, list):
            if real_position:
                pos = real_position[0]  # Take first position
                if hasattr(pos, 'unrealized_pnl'):  # Position dataclass
                    current_pnl = float(pos.unrealized_pnl)
                    current_mark = pos.mark_price or pos.entry_price
                    exchange_entry = float(pos.entry_price)
                    leverage = pos.leverage
                    side = "LONG" if pos.is_long else "SHORT"
                    position_id = pos.position_id
                else:  # dict format
                    current_pnl = float(pos.get("unrealizedPnl", 0) or pos.get("pnl", 0))
                    current_mark = pos.get("markPrice") or pos.get("avgPrice")
                    exchange_entry = float(pos.get("entry", pos.get("avgPrice", 0)))
                    leverage = pos.get("leverage")
                    side = pos.get("side", "UNKNOWN")
                    position_id = pos.get("positionId", "")
            else:
                return
        elif hasattr(real_position, 'unrealized_pnl'):
            # Position dataclass (not a list)
            pos = real_position
            current_pnl = float(pos.unrealized_pnl)
            current_mark = pos.mark_price or pos.entry_price
            exchange_entry = float(pos.entry_price)
            leverage = pos.leverage
            side = "LONG" if pos.is_long else "SHORT"
            position_id = pos.position_id
        else:
            # Old dict format
            current_pnl = float(real_position.get("unrealizedPnl", 0) or real_position.get("pnl", 0))
            current_mark = real_position.get("markPrice") or real_position.get("avgPrice")
            exchange_entry = float(real_position.get("entry", real_position.get("avgPrice", 0)))
            leverage = real_position.get("leverage")
            side = real_position.get("type", "").upper()
            position_id = real_position.get("dealId", real_position.get("positionId", ""))

        stored_trade["last_pnl"] = current_pnl
        stored_trade["current_price"] = current_mark

        # Repair missing dealId for trades created before this field was stored
        if "dealId" not in stored_trade and position_id:
            stored_trade["dealId"] = position_id

        # Always sync entry_price from exchange (source of truth)
        if exchange_entry > 0:
            stored_trade["entry_price"] = exchange_entry

        # Repair side if unknown
        if stored_trade.get("side") in ["UNKNOWN", None]:
            stored_trade["side"] = side

        # Repair missing leverage from exchange data
        if not stored_trade.get("leverage") and leverage:
            stored_trade["leverage"] = leverage

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
        stored_trade.get("pnl_history", [])
        # Only keep last 100 points to save space? Or just max/min
        stored_trade["max_pnl"] = max(stored_trade.get("max_pnl", -999999), current_pnl)
        stored_trade["min_pnl"] = min(stored_trade.get("min_pnl", 999999), current_pnl)

        self.active_trades[symbol] = stored_trade
        self._mark_dirty()
        self._save_if_due(symbol)

    def _mark_dirty(self):
        """Помечает данные как изменённые для отложенной записи."""
        self._dirty = True

    def flush(self):
        """Сбрасывает накопленные изменения на диск, если есть что записывать."""
        if self._dirty:
            self._save_active_trades()
            self._dirty = False
            self._last_save_time = time.monotonic()

    def _save_if_due(self, symbol: str = None):
        """Записывает на диск, если прошло достаточно времени с последней записи."""
        if time.monotonic() - self._last_save_time >= BATCH_WRITE_INTERVAL:
            self._save_active_trades(symbol)
            self._dirty = False
            self._last_save_time = time.monotonic()

    def force_sync_all(self, real_positions_dict: dict):
        """
        Force sync all trades with actual exchange positions.
        real_positions_dict: {symbol: position_data} from exchange

        Removes stale trades that don't exist on exchange.
        Adds trades that exist on exchange but not in tracker.
        """
        # Normalize exchange symbols to match internal format (without hyphen)
        normalized_exchange_symbols = {s.replace("-", "") for s in real_positions_dict.keys()}

        # 1. Remove stale trades (in tracker but not on exchange)
        stale_symbols = []
        for symbol in list(self.active_trades.keys()):
            # Normalize tracker symbol for comparison
            normalized_symbol = symbol.replace("-", "")
            if normalized_symbol not in normalized_exchange_symbols:
                stale_symbols.append(symbol)

        for symbol in stale_symbols:
            stored = self.active_trades[symbol]
            log_trade(f"🧹 [TradeTracker] Removing STALE trade {symbol} (not on exchange)")
            # Move to history as closed
            stored["status"] = "CLOSED_SYNC"
            stored["close_time"] = datetime.now().isoformat()
            stored["reason"] = "STALE_SYNC_CLEANUP"
            # Check for duplicate before adding to history
            if self._check_trade_exists(stored):
                deal_id = stored.get('dealId', 'N/A')
                info(f"⚠️ [TradeTracker] Сделка {deal_id} уже в истории (stale sync), пропускаем")
            else:
                self._append_history(stored)
            del self.active_trades[symbol]

        # 2. Add missing trades (on exchange but not in tracker)
        for symbol, real_pos in real_positions_dict.items():
            # Normalize symbol for comparison
            normalized_symbol = symbol.replace("-", "")
            if normalized_symbol not in self.active_trades and real_pos:
                self._handle_new_trade(normalized_symbol, real_pos)

        self._save_active_trades()

        if stale_symbols:
            info(f"🧹 [TradeTracker] Cleaned {len(stale_symbols)} stale trades: {stale_symbols}")

        return len(stale_symbols)
