"""MEXC Spot V3 REST client без фьючерсной семантики позиций."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional

from ..config.base import ExchangeConfig
from ..dto.models import (
    AssetBalance, Balance, CommissionRate, ExchangeCapabilities, InstrumentRules,
    Kline, KlinesList, MarketType, Order, OrderBook, OrderSide, OrderStatus,
    OrdersList, OrderSubmission, OrderType, SpotOrderRequest, SubmissionStatus,
    Ticker,
)
from ..errors import ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from ..spot_client import SpotExchangeClient
from .mexc_transport import MEXCSpotTransport


_ORDER_STATUS = {
    "NEW": OrderStatus.NEW,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "PARTIALLY_CANCELED": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
}


def _decimal(value, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


class MEXCSpotClient(SpotExchangeClient):
    EXCHANGE_NAME = "mexc"

    def __init__(self, config: Optional[ExchangeConfig] = None, transport=None):
        if config is None:
            from ..config import create_config
            from src.config import MODE
            config = create_config("mexc", is_demo=(MODE == "demo"), market_type="spot")
        self._config = config
        self._transport = transport or MEXCSpotTransport(config)
        self._rules: Dict[str, tuple[float, InstrumentRules]] = {}

    @property
    def capabilities(self) -> ExchangeCapabilities:
        return ExchangeCapabilities(market_type=MarketType.SPOT, automated_strategy=False)

    def normalize_symbol(self, symbol: str) -> str:
        return (symbol or "").upper().replace("-", "").replace("_", "").replace("/", "")

    def denormalize_symbol(self, symbol: str) -> str:
        return self.normalize_symbol(symbol)

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        canonical = self.normalize_symbol(symbol)
        cached = self._rules.get(canonical)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        payload = self._transport.request("GET", "/api/v3/exchangeInfo", {"symbol": canonical})
        entries = payload.get("symbols", []) if isinstance(payload, dict) else []
        if not entries:
            raise ExchangeStateUnavailableError(f"MEXC Spot symbol rules not found: {canonical}")
        item = entries[0]
        min_qty = _decimal(item.get("baseSizePrecision"))
        rules = InstrumentRules(
            symbol=canonical,
            exchange_symbol=canonical,
            base_asset=str(item.get("baseAsset", "")),
            quote_asset=str(item.get("quoteAsset", "")),
            tradable=(str(item.get("status")) in {"1", "ENABLED"}
                      and bool(item.get("isSpotTradingAllowed"))
                      and int(item.get("tradeSideType", 1)) != 4),
            price_step=Decimal(1).scaleb(-int(item.get("quotePrecision", 8))),
            quantity_step=min_qty or Decimal(1).scaleb(-int(item.get("baseAssetPrecision", 8))),
            min_quantity=min_qty,
            max_quantity=Decimal("0"),
            min_notional=_decimal(item.get("quoteAmountPrecision")),
            max_notional=_decimal(item.get("maxQuoteAmount")),
            order_types=tuple(item.get("orderTypes") or ()),
            trade_side_type=int(item.get("tradeSideType", 1)),
        )
        self._rules[canonical] = (time.time(), rules)
        return rules

    def get_klines(self, symbol: str, interval: str = "5m", limit: int = 288) -> KlinesList:
        exchange_interval = self._config.supported_intervals.get(interval)
        if not exchange_interval:
            raise ValueError(f"MEXC Spot interval not supported: {interval}")
        data = self._transport.request("GET", "/api/v3/klines", {
            "symbol": self.normalize_symbol(symbol),
            "interval": exchange_interval,
            "limit": min(max(int(limit), 1), 500),
        })
        result = []
        for item in data or []:
            result.append(Kline(
                timestamp=datetime.fromtimestamp(int(item[0]) / 1000, timezone.utc).isoformat(),
                open=float(item[1]), high=float(item[2]), low=float(item[3]),
                close=float(item[4]), volume=float(item[5]),
                closed=int(item[6]) <= int(time.time() * 1000) if len(item) > 6 else True,
            ))
        return result

    def get_ticker(self, symbol: str) -> Ticker:
        canonical = self.normalize_symbol(symbol)
        data = self._transport.request("GET", "/api/v3/ticker/24hr", {"symbol": canonical})
        return Ticker(
            symbol=canonical,
            last_price=float(data.get("lastPrice", 0)),
            bid_price=float(data.get("bidPrice", 0)),
            ask_price=float(data.get("askPrice", 0)),
            volume_24h=float(data.get("volume", 0)),
            quote_volume_24h=float(data.get("quoteVolume", 0)),
            price_change_24h=float(data.get("priceChange", 0)),
            price_change_percent_24h=float(data.get("priceChangePercent", 0)),
            high_24h=float(data.get("highPrice", 0)),
            low_24h=float(data.get("lowPrice", 0)),
        )

    def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        canonical = self.normalize_symbol(symbol)
        data = self._transport.request("GET", "/api/v3/depth", {
            "symbol": canonical, "limit": min(max(int(limit), 5), 5000),
        })
        return OrderBook(
            symbol=canonical,
            bids=[[float(p), float(q)] for p, q, *_ in data.get("bids", [])],
            asks=[[float(p), float(q)] for p, q, *_ in data.get("asks", [])],
            last_update_id=int(data.get("lastUpdateId", 0)),
        )

    def get_asset_balances(self) -> List[AssetBalance]:
        data = self._transport.request("GET", "/api/v3/account", private=True)
        return [
            AssetBalance(str(item.get("asset", "")), _decimal(item.get("free")), _decimal(item.get("locked")))
            for item in data.get("balances", [])
        ]

    def get_balance(self) -> Balance:
        asset = self._config.settle_asset
        found = next((b for b in self.get_asset_balances() if b.asset == asset), None)
        if found is None:
            return Balance(0.0, 0.0, asset=asset)
        return Balance(float(found.total), float(found.free), locked_balance=float(found.locked), asset=asset)

    def get_commission_rate(self, symbol: str) -> Optional[CommissionRate]:
        data = self._transport.request("GET", "/api/v3/tradeFee", {
            "symbol": self.normalize_symbol(symbol),
        }, private=True)
        fees = data.get("data", data)
        return CommissionRate(
            maker=float(_decimal(fees.get("makerCommission")) * 100),
            taker=float(_decimal(fees.get("takerCommission")) * 100),
        )

    def place_spot_order(self, request: SpotOrderRequest) -> OrderSubmission:
        rules = self.get_instrument_rules(request.symbol)
        if not rules.tradable:
            raise UnsupportedCapabilityError(f"MEXC Spot trading disabled for {rules.symbol}")
        order_type = request.order_type.value
        if order_type not in rules.order_types and order_type not in {"IMMEDIATE_OR_CANCEL", "FILL_OR_KILL"}:
            raise UnsupportedCapabilityError(f"Order type {order_type} disabled for {rules.symbol}")
        side = request.side.value
        if rules.trade_side_type == 2 and side == "SELL":
            raise UnsupportedCapabilityError("Для символа разрешены только BUY ордера")
        if rules.trade_side_type == 3 and side == "BUY":
            raise UnsupportedCapabilityError("Для символа разрешены только SELL ордера")

        client_id = request.client_order_id or f"se{uuid.uuid4().hex[:28]}"
        params = {"symbol": rules.exchange_symbol, "side": side, "type": order_type, "newClientOrderId": client_id}
        if request.order_type == OrderType.MARKET and side == "BUY":
            if request.quote_quantity is None:
                raise ValueError("MARKET BUY требует quote_quantity")
            quote_qty = _floor_step(request.quote_quantity, rules.price_step)
            if quote_qty < rules.min_notional:
                raise ValueError("Сумма Spot BUY меньше минимальной")
            params["quoteOrderQty"] = format(quote_qty, "f")
        else:
            if request.base_quantity is None:
                raise ValueError("Ордер требует base_quantity")
            qty = _floor_step(request.base_quantity, rules.quantity_step)
            if qty < rules.min_quantity:
                raise ValueError("Количество Spot ордера меньше минимального")
            params["quantity"] = format(qty, "f")
        if request.order_type == OrderType.LIMIT:
            if request.price is None:
                raise ValueError("LIMIT ордер требует price")
            params["price"] = format(_floor_step(request.price, rules.price_step), "f")

        endpoint = "/api/v3/order/test" if request.test_only else "/api/v3/order"
        try:
            data = self._transport.request(
                "POST", endpoint, params, private=True, mutation=not request.test_only,
            )
        except ExchangeStateUnavailableError as exc:
            found = self._query_by_client_id(rules.symbol, client_id)
            if found:
                return OrderSubmission(SubmissionStatus.ACKNOWLEDGED, found.order_id, client_id, raw_data=found.raw_data)
            raise UnknownOrderStateError(f"Состояние Spot ордера {client_id} неизвестно") from exc
        if request.test_only:
            return OrderSubmission(SubmissionStatus.ACKNOWLEDGED, client_order_id=client_id, raw_data=data or {})
        return OrderSubmission(
            SubmissionStatus.ACKNOWLEDGED,
            str(data.get("orderId")), client_id, raw_data=data,
        )

    def _query_by_client_id(self, symbol: str, client_id: str) -> Optional[Order]:
        try:
            data = self._transport.request("GET", "/api/v3/order", {
                "symbol": self.normalize_symbol(symbol), "origClientOrderId": client_id,
            }, private=True, max_attempts=1)
            return self._parse_order(data) if data else None
        except Exception:
            return None

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        data = self._transport.request("DELETE", "/api/v3/order", {
            "symbol": self.normalize_symbol(symbol), "orderId": order_id,
        }, private=True, mutation=True)
        return bool(data)

    def cancel_all_orders(self, symbol: str) -> bool:
        raise UnsupportedCapabilityError(
            "Массовая отмена Spot запрещена: клиент не должен затрагивать ручные ордера"
        )

    def get_open_orders(self, symbol: Optional[str] = None) -> OrdersList:
        params = {"symbol": self.normalize_symbol(symbol)} if symbol else {}
        data = self._transport.request("GET", "/api/v3/openOrders", params, private=True)
        return [self._parse_order(item) for item in data or []]

    def get_recent_orders(self, symbol: str, limit: int = 10) -> OrdersList:
        data = self._transport.request("GET", "/api/v3/allOrders", {
            "symbol": self.normalize_symbol(symbol), "limit": min(max(limit, 1), 1000),
        }, private=True)
        return [self._parse_order(item) for item in (data or [])[-limit:]]

    def _parse_order(self, item: dict) -> Order:
        raw_type = str(item.get("type", "LIMIT"))
        try:
            order_type = OrderType(raw_type)
        except ValueError:
            order_type = OrderType.LIMIT
        return Order(
            order_id=str(item.get("orderId", "")),
            symbol=self.normalize_symbol(item.get("symbol", "")),
            side=OrderSide(str(item.get("side", "BUY"))),
            order_type=order_type,
            status=_ORDER_STATUS.get(str(item.get("status", "NEW")), OrderStatus.NEW),
            price=float(item.get("price", 0) or 0),
            quantity=float(item.get("origQty", item.get("quantity", 0)) or 0),
            filled_quantity=float(item.get("executedQty", 0) or 0),
            average_price=float(item.get("avgPrice", 0) or 0),
            stop_price=float(item.get("stopPrice", 0) or 0) or None,
            raw_data=item,
        )

    def check_prerequisites(self) -> bool:
        return bool(self._config.api_key and self._config.secret_key)

    # Явные предохранители для ошибочного использования Spot в futures runtime.
    def get_positions(self):
        raise UnsupportedCapabilityError("Spot не имеет биржевых позиций")

    def set_leverage(self, *args, **kwargs):
        raise UnsupportedCapabilityError("Spot не поддерживает leverage")

    def set_sl_tp(self, *args, **kwargs):
        raise UnsupportedCapabilityError("Spot REST API не документирует создание TP/SL")

    def get_funding_rate(self, *args, **kwargs):
        raise UnsupportedCapabilityError("Spot не имеет funding rate")

    def close_position(self, *args, **kwargs):
        raise UnsupportedCapabilityError("Spot inventory нельзя закрывать как futures position")
