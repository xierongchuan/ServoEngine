"""Grid Executor — управление сеткой лимитных ордеров для Grid Trading."""

import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from src.exchanges.exchange_factory import get_exchange_client
from src.utils.logger import info, error, warning
from src.config import POSITION_LIMITS


@dataclass
class GridLevel:
    """Уровень сетки."""
    price: float
    side: str  # "BUY" or "SELL"
    quantity: float
    order_id: Optional[str] = None
    filled: bool = False


@dataclass
class GridState:
    """Состояние сетки."""
    center_price: float = 0.0
    spacing_pct: float = 0.3
    inventory: float = 0.0
    total_filled_buy: int = 0
    total_filled_sell: int = 0
    total_buy_value: float = 0.0  # Сумма покупок в USDT
    total_sell_value: float = 0.0  # Сумма продаж в USDT
    total_fees: float = 0.0  # Общие комиссии
    gross_pnl: float = 0.0  # PnL без комиссий
    net_pnl: float = 0.0  # PnL с учетом комиссий
    last_sync_time: float = 0.0


class GridExecutor:
    """
    Управляет сеткой лимитных ордеров.

    Логика inventory management:
    - inventory > 0: накоплен long → смещаем центр вниз, увеличиваем sell
    - inventory < 0: накоплен short → смещаем центр вверх, увеличиваем buy
    """

    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.client = get_exchange_client()

        # Grid parameters from config
        self.num_levels = config.get("grid_levels", 5)
        self.spacing_pct = config.get("grid_spacing_pct", 0.3)
        self.order_size_usdt = config.get("order_size_usdt", 10.0)
        self.inventory_limit = config.get("inventory_limit", 100.0)
        self.emergency_stop_loss_pct = config.get("emergency_stop_loss_pct", 5.0)

        # Fee rate from config (default 0.1% for BingX)
        from src.config import BOT_CONFIG
        exchange_fees = BOT_CONFIG.get("EXCHANGE_FEES", {})
        self.fee_rate = exchange_fees.get("bingx", 0.1)  # 0.1%

        # Precision
        self.price_precision = POSITION_LIMITS.get("price_precision", 4)
        self.quantity_precision = POSITION_LIMITS.get("quantity_precision", 4)

        # State
        self.state = GridState(spacing_pct=self.spacing_pct)
        self.active_orders: Dict[str, GridLevel] = {}  # order_id -> GridLevel
        self.known_order_ids: set = set()  # Для отслеживания исполненных ордеров

        info(f"[GRID] Initialized GridExecutor for {symbol}")
        info(f"[GRID] Levels: {self.num_levels}, Spacing: {self.spacing_pct}%, Order size: ${self.order_size_usdt}, Fee: {self.fee_rate}%")

    def calculate_grid_prices(
        self,
        center_price: float,
        spacing_mult: float = 1.0,
        inventory_offset: float = 0.0
    ) -> Dict[str, List[float]]:
        """
        Рассчитывает уровни сетки.

        Args:
            center_price: Центральная цена сетки
            spacing_mult: Множитель расстояния (для адаптации к волатильности)
            inventory_offset: Смещение центра на основе inventory (в % от цены)

        Returns:
            {"buy": [price1, price2, ...], "sell": [price1, price2, ...]}
        """
        # Применяем inventory offset
        adjusted_center = center_price * (1 + inventory_offset / 100)

        # Расстояние между уровнями
        spacing = adjusted_center * (self.spacing_pct / 100) * spacing_mult

        buy_prices = []
        sell_prices = []

        for i in range(1, self.num_levels + 1):
            buy_price = round(adjusted_center - spacing * i, self.price_precision)
            sell_price = round(adjusted_center + spacing * i, self.price_precision)
            buy_prices.append(buy_price)
            sell_prices.append(sell_price)

        return {"buy": buy_prices, "sell": sell_prices}

    def calculate_inventory_offset(self) -> float:
        """
        Рассчитывает смещение центра сетки на основе inventory.

        При накоплении позиции смещаем сетку против накопления:
        - Long inventory → смещаем вниз (чтобы sell ордера были ближе)
        - Short inventory → смещаем вверх (чтобы buy ордера были ближе)
        """
        if self.inventory_limit <= 0:
            return 0.0

        # Нормализованный inventory (-1 до +1)
        inventory_ratio = self.state.inventory / self.inventory_limit

        # Смещение: при +1 (max long) смещаем на -spacing_pct (вниз)
        # при -1 (max short) смещаем на +spacing_pct (вверх)
        offset = -inventory_ratio * self.spacing_pct

        return offset

    def sync_orders(self, target_prices: Dict[str, List[float]], current_price: float) -> bool:
        """
        Синхронизирует ордера с целевыми уровнями.

        1. Получает текущие открытые ордера
        2. Отменяет ордера вне целевых уровней
        3. Выставляет недостающие ордера

        Returns:
            True если синхронизация успешна
        """
        try:
            # 1. Получаем текущие открытые ордера
            open_orders = self.client.get_open_orders(self.symbol)

            # Строим map существующих ордеров по цене
            # Ключ: (price, side) -> order
            existing_orders: Dict[tuple, dict] = {}
            for order in open_orders:
                price = float(order.get("price", 0))
                side = order.get("side", "").upper()
                if price > 0:
                    existing_orders[(round(price, self.price_precision), side)] = order

            # 2. Определяем целевые уровни
            target_set = set()
            for price in target_prices["buy"]:
                target_set.add((price, "BUY"))
            for price in target_prices["sell"]:
                target_set.add((price, "SELL"))

            # 3. Отменяем ордера вне целевых уровней
            for (price, side), order in existing_orders.items():
                if (price, side) not in target_set:
                    order_id = order.get("orderId")
                    if order_id:
                        self.client.cancel_order(self.symbol, order_id)
                        info(f"[GRID] Cancelled stale {side} order at {price}")

            # 4. Выставляем недостающие ордера
            for price in target_prices["buy"]:
                key = (price, "BUY")
                if key not in existing_orders:
                    if self._can_place_buy():
                        self._place_grid_order("BUY", price, current_price)

            for price in target_prices["sell"]:
                key = (price, "SELL")
                if key not in existing_orders:
                    if self._can_place_sell():
                        self._place_grid_order("SELL", price, current_price)

            self.state.last_sync_time = time.time()
            return True

        except Exception as e:
            error(f"[GRID] Error syncing orders: {e}")
            return False

    def _place_grid_order(self, side: str, price: float, current_price: float) -> Optional[str]:
        """Размещает один ордер сетки."""
        try:
            quantity = self._calculate_order_quantity(price)

            if quantity <= 0:
                warning(f"[GRID] Quantity too small for {side} at {price}")
                return None

            # Определяем positionSide для hedge mode
            position_side = "LONG" if side == "BUY" else "SHORT"

            order_id = self.client.place_order(
                symbol=self.symbol,
                side=side,
                price=price,
                quantity=quantity,
                type="LIMIT",
                positionSide=position_side
            )

            if order_id:
                info(f"[GRID] Placed {side} order at {price:.{self.price_precision}f}, qty: {quantity}")
                self.active_orders[order_id] = GridLevel(
                    price=price,
                    side=side,
                    quantity=quantity,
                    order_id=order_id
                )
                return order_id

            return None

        except Exception as e:
            error(f"[GRID] Error placing {side} order at {price}: {e}")
            return None

    def _calculate_order_quantity(self, price: float) -> float:
        """Рассчитывает размер ордера."""
        if price <= 0:
            return 0.0
        quantity = self.order_size_usdt / price
        return round(quantity, self.quantity_precision)

    def _can_place_buy(self) -> bool:
        """Проверяет можно ли выставить BUY (inventory limit)."""
        return self.state.inventory < self.inventory_limit

    def _can_place_sell(self) -> bool:
        """Проверяет можно ли выставить SELL."""
        return self.state.inventory > -self.inventory_limit

    def update_inventory(self) -> float:
        """
        Обновляет inventory на основе реальных позиций.

        Returns:
            Текущий inventory (positive = long, negative = short)
        """
        try:
            positions = self.client.get_positions()

            # Нормализуем символ для поиска
            norm_symbol = self.symbol.replace("-", "").replace("/", "")
            symbol_pos = positions.get(norm_symbol, [])

            if symbol_pos:
                pos = symbol_pos[0]
                size = float(pos.get("size", 0))
                side = pos.get("type", "buy")

                # Inventory: positive для long, negative для short
                self.state.inventory = size if side == "buy" else -size
            else:
                self.state.inventory = 0.0

            return self.state.inventory

        except Exception as e:
            error(f"[GRID] Error updating inventory: {e}")
            return self.state.inventory

    def check_emergency_conditions(self, current_price: float) -> bool:
        """
        Проверяет условия для экстренного закрытия.

        Returns:
            True если нужно экстренное закрытие
        """
        # 1. Проверка inventory limit
        if abs(self.state.inventory) > self.inventory_limit * 1.5:
            warning(f"[GRID] Emergency: Inventory {self.state.inventory} exceeds limit")
            return True

        # 2. Проверка unrealized PnL (если есть позиция)
        try:
            positions = self.client.get_positions()
            norm_symbol = self.symbol.replace("-", "").replace("/", "")
            symbol_pos = positions.get(norm_symbol, [])

            if symbol_pos:
                pnl = float(symbol_pos[0].get("pnl", 0))
                entry = float(symbol_pos[0].get("entry", current_price))

                if entry > 0:
                    pnl_pct = ((current_price - entry) / entry) * 100
                    if abs(pnl_pct) > self.emergency_stop_loss_pct:
                        warning(f"[GRID] Emergency: PnL {pnl_pct:.2f}% exceeds limit")
                        return True
        except Exception as e:
            error(f"[GRID] Error checking emergency: {e}")

        return False

    def emergency_close(self) -> bool:
        """
        Экстренное закрытие всех позиций и отмена ордеров.

        Returns:
            True если операция успешна
        """
        warning(f"[GRID] EMERGENCY CLOSE triggered for {self.symbol}")

        try:
            # 1. Отменяем все ордера
            self.client.cancel_all_orders(self.symbol)

            # 2. Закрываем все позиции
            positions = self.client.get_positions()
            norm_symbol = self.symbol.replace("-", "").replace("/", "")
            symbol_pos = positions.get(norm_symbol, [])

            for pos in symbol_pos:
                deal_id = pos.get("dealId")
                if deal_id:
                    self.client.close_position(self.symbol, deal_id)

            # 3. Сбрасываем состояние
            self.state.inventory = 0.0
            self.active_orders.clear()

            info(f"[GRID] Emergency close completed for {self.symbol}")
            return True

        except Exception as e:
            error(f"[GRID] Error during emergency close: {e}")
            return False

    def check_filled_orders(self) -> List[dict]:
        """
        Проверяет исполненные ордера, сравнивая с текущим состоянием на бирже.

        Returns:
            Список исполненных ордеров [{side, price, qty, order_id}, ...]
        """
        try:
            # Получаем текущие открытые ордера с биржи
            current_orders = self.client.get_open_orders(self.symbol)
            current_order_ids = {o.get("orderId") for o in current_orders if o.get("orderId")}

            filled = []

            # Проверяем какие из наших активных ордеров исчезли (исполнены)
            for order_id, level in list(self.active_orders.items()):
                if order_id not in current_order_ids:
                    # Ордер исполнен или отменен
                    filled.append({
                        "side": level.side,
                        "price": level.price,
                        "qty": level.quantity,
                        "order_id": order_id
                    })
                    # Записываем исполнение с учетом комиссий
                    self.record_fill(level.side, level.price, level.quantity)
                    # Удаляем из активных
                    del self.active_orders[order_id]

            return filled

        except Exception as e:
            error(f"[GRID] Error checking filled orders: {e}")
            return []

    def record_fill(self, side: str, price: float, qty: float):
        """
        Записывает исполнение ордера с учетом комиссии.

        Args:
            side: "BUY" или "SELL"
            price: Цена исполнения
            qty: Количество
        """
        trade_value = price * qty
        fee = trade_value * (self.fee_rate / 100)

        # Обновляем статистику
        self.state.total_fees += fee

        if side.upper() == "BUY":
            self.state.total_filled_buy += 1
            self.state.total_buy_value += trade_value
        else:  # SELL
            self.state.total_filled_sell += 1
            self.state.total_sell_value += trade_value

        # Рассчитываем PnL
        # Gross PnL = продажи - покупки
        self.state.gross_pnl = self.state.total_sell_value - self.state.total_buy_value
        # Net PnL = gross - комиссии
        self.state.net_pnl = self.state.gross_pnl - self.state.total_fees

        info(f"[GRID] FILLED: {side} {price:.{self.price_precision}f} x {qty:.4f}, "
             f"fee: ${fee:.4f}, net_pnl: ${self.state.net_pnl:.4f}")

    def get_stats(self) -> dict:
        """Возвращает статистику сетки."""
        return {
            "symbol": self.symbol,
            "center_price": self.state.center_price,
            "inventory": self.state.inventory,
            "inventory_limit": self.inventory_limit,
            "inventory_pct": (self.state.inventory / self.inventory_limit * 100) if self.inventory_limit > 0 else 0,
            "active_orders": len(self.active_orders),
            "total_filled_buy": self.state.total_filled_buy,
            "total_filled_sell": self.state.total_filled_sell,
            "total_fees": self.state.total_fees,
            "gross_pnl": self.state.gross_pnl,
            "net_pnl": self.state.net_pnl,
            "last_sync": self.state.last_sync_time
        }
