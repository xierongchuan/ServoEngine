"""MEXC Spot V3: точные правила, явные типы и однократные мутации."""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from ..config.base import ExchangeConfig
from ..dto.models import (
    AssetBalance, Balance, CommissionRate, ExchangeCapabilities, InstrumentRules,
    Kline, MarketType, Order, OrderBook, OrderSide, OrderStatus, OrderSubmission,
    OrderType, SpotOrderRequest, SubmissionStatus, Ticker,
)
from ..errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from ..spot_client import SpotExchangeClient
from .mexc_transport import MEXCSpotTransport, assert_live_allowed, integer, mapping, number, records, state_error, unknown

_ORDER_STATUS = {"NEW": OrderStatus.NEW, "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED, "CANCELED": OrderStatus.CANCELED,
    "PARTIALLY_CANCELED": OrderStatus.CANCELED, "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED}
_TYPES = {"LIMIT", "MARKET", "LIMIT_MAKER", "IMMEDIATE_OR_CANCEL", "FILL_OR_KILL"}


def _floor_step(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _identifier(value, label):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value)):
        state_error(f"MEXC Spot: некорректный {label}")
    return str(value)


def _date(value):
    return None if value is None else datetime.fromtimestamp(integer(value, "время", positive=True) / 1000, timezone.utc)


class MEXCSpotClient(SpotExchangeClient):
    EXCHANGE_NAME = "mexc"

    def __init__(self, config: Optional[ExchangeConfig] = None, transport=None):
        if config is None:
            from ..config import create_config
            config = create_config("mexc", market_type="spot")
        self._config = config
        self._transport = transport or MEXCSpotTransport(config)
        self._rules, self._metadata, self._owned_orders = {}, {}, set()

    @property
    def capabilities(self):
        return ExchangeCapabilities(market_type=MarketType.SPOT, automated_strategy=False)

    def normalize_symbol(self, symbol):
        return (symbol or "").upper().replace("-", "").replace("_", "").replace("/", "")

    def denormalize_symbol(self, symbol):
        return self.normalize_symbol(symbol)

    def get_instrument_rules(self, symbol):
        canonical = self.normalize_symbol(symbol)
        cached = self._rules.get(canonical)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        payload = mapping(self._transport.request("GET", "/api/v3/exchangeInfo", {"symbol": canonical}), "exchangeInfo")
        matches = [x for x in records(payload.get("symbols"), "symbols") if x.get("symbol") == canonical]
        if len(matches) != 1:
            state_error("MEXC Spot: символ не найден однозначно")
        item = matches[0]
        if not isinstance(item.get("isSpotTradingAllowed"), bool):
            state_error("MEXC Spot: некорректный isSpotTradingAllowed")
        base_precision = integer(item.get("baseAssetPrecision"), "baseAssetPrecision")
        quote_precision = integer(item.get("quotePrecision"), "quotePrecision")
        if not 0 <= base_precision <= 30 or not 0 <= quote_precision <= 30:
            state_error("MEXC Spot: некорректная точность")
        types = item.get("orderTypes")
        if not isinstance(types, list) or not types or any(t not in _TYPES for t in types):
            state_error("MEXC Spot: неизвестные orderTypes")
        side = integer(item.get("tradeSideType"), "tradeSideType")
        if side not in {1, 2, 3, 4}:
            state_error("MEXC Spot: неизвестный tradeSideType")
        for key in ("quoteAmountPrecisionMarket", "maxQuoteAmountMarket"):
            number(item.get(key), key, nonnegative=True)
        filters = records(item.get("filters", []), "filters")
        if any(f.get("filterType") not in {"PRICE_FILTER", "LOT_SIZE", "MIN_NOTIONAL", "NOTIONAL", "PERCENT_PRICE_BY_SIDE"} for f in filters):
            state_error("MEXC Spot: неизвестный фильтр")
        rules = InstrumentRules(symbol=canonical, exchange_symbol=canonical,
            base_asset=str(item.get("baseAsset", "")), quote_asset=str(item.get("quoteAsset", "")),
            tradable=str(item.get("status")) in {"1", "ENABLED"} and item["isSpotTradingAllowed"] and side != 4,
            price_step=Decimal(1).scaleb(-quote_precision), quantity_step=Decimal(1).scaleb(-base_precision),
            min_quantity=number(item.get("baseSizePrecision"), "baseSizePrecision", nonnegative=True), max_quantity=Decimal(0),
            min_notional=number(item.get("quoteAmountPrecision"), "quoteAmountPrecision", nonnegative=True),
            max_notional=number(item.get("maxQuoteAmount"), "maxQuoteAmount", nonnegative=True),
            order_types=tuple(types), trade_side_type=side)
        if not rules.base_asset or not rules.quote_asset or (rules.max_notional and rules.min_notional > rules.max_notional):
            state_error("MEXC Spot: противоречивые правила символа")
        self._metadata[canonical] = dict(item)
        self._rules[canonical] = (time.monotonic(), rules)
        return rules

    def get_klines(self, symbol, interval="5m", limit=288):
        exchange_interval = self._config.supported_intervals.get(interval)
        if not exchange_interval or not 1 <= integer(limit, "limit") <= 500:
            raise ValueError("MEXC Spot: некорректный interval/limit")
        data = self._transport.request("GET", "/api/v3/klines", {"symbol": self.normalize_symbol(symbol), "interval": exchange_interval, "limit": limit})
        if not isinstance(data, list):
            state_error("MEXC Spot: свечи должны быть списком")
        result, previous = [], -1
        for item in data:
            if not isinstance(item, list) or len(item) < 7:
                state_error("MEXC Spot: неполная свеча")
            ts, end = integer(item[0], "openTime", positive=True), integer(item[6], "closeTime", positive=True)
            prices = [number(x, "OHLC", positive=True) for x in item[1:5]]
            if ts <= previous or end < ts or prices[1] < max(prices) or prices[2] > min(prices):
                state_error("MEXC Spot: некорректная свеча")
            previous = ts
            result.append(Kline(datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(), *(float(x) for x in prices),
                float(number(item[5], "volume", nonnegative=True)), closed=end < int(time.time() * 1000)))
        return result

    def get_ticker(self, symbol):
        canonical = self.normalize_symbol(symbol)
        data = mapping(self._transport.request("GET", "/api/v3/ticker/24hr", {"symbol": canonical}), "тикер")
        if data.get("symbol") != canonical:
            state_error("MEXC Spot: символ тикера не совпадает")
        n = lambda key: float(number(data.get(key), key))
        # quoteVolume бывает null; объём котировки тогда не известен, а не price*volume.
        quote = 0.0 if data.get("quoteVolume") is None else n("quoteVolume")
        return Ticker(canonical, n("lastPrice"), n("bidPrice"), n("askPrice"), n("volume"), quote,
            n("priceChange"), n("priceChangePercent") * 100, n("highPrice"), n("lowPrice"))

    def get_order_book(self, symbol, limit=20):
        canonical = self.normalize_symbol(symbol)
        if not 5 <= integer(limit, "limit") <= 5000:
            raise ValueError("MEXC Spot: limit стакана от 5 до 5000")
        data = mapping(self._transport.request("GET", "/api/v3/depth", {"symbol": canonical, "limit": limit}), "стакан")
        def convert(rows):
            if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) < 2 for row in rows):
                state_error("MEXC Spot: некорректный стакан")
            return [[float(number(row[0], "цена", positive=True)), float(number(row[1], "количество", nonnegative=True))] for row in rows]
        return OrderBook(canonical, convert(data.get("bids")), convert(data.get("asks")), integer(data.get("lastUpdateId"), "lastUpdateId"))

    def get_asset_balances(self):
        data = mapping(self._transport.request("GET", "/api/v3/account", private=True), "аккаунт")
        result, seen = [], set()
        for item in records(data.get("balances"), "balances"):
            asset = item.get("asset")
            if not isinstance(asset, str) or not asset or asset in seen:
                state_error("MEXC Spot: некорректный/повторный актив")
            seen.add(asset)
            result.append(AssetBalance(asset, number(item.get("free"), "free", nonnegative=True), number(item.get("locked"), "locked", nonnegative=True)))
        return result

    def get_balance(self):
        asset = self._config.settle_asset
        found = next((b for b in self.get_asset_balances() if b.asset == asset), None)
        return Balance(float(found.total), float(found.free), locked_balance=float(found.locked), asset=asset) if found else Balance(0.0, 0.0, asset=asset)

    def get_commission_rate(self, symbol):
        data = mapping(self._transport.request("GET", "/api/v3/tradeFee", {"symbol": self.normalize_symbol(symbol)}, private=True), "tradeFee")
        fees = mapping(data.get("data", data), "ставки комиссии")
        return CommissionRate(float(number(fees.get("makerCommission"), "makerCommission") * 100), float(number(fees.get("takerCommission"), "takerCommission") * 100))

    def place_spot_order(self, request: SpotOrderRequest):
        # Даже order/test отправляется только явно разрешённым live-клиентом.
        assert_live_allowed(self._config)
        rules = self.get_instrument_rules(request.symbol)
        kind, side = getattr(request.order_type, "value", request.order_type), getattr(request.side, "value", request.side)
        if not rules.tradable or kind not in _TYPES or kind not in rules.order_types:
            raise UnsupportedCapabilityError("MEXC Spot: символ/тип ордера недоступен")
        if side not in {"BUY", "SELL"}:
            raise ValueError("MEXC Spot: неизвестная сторона")
        if (rules.trade_side_type == 2 and side == "SELL") or (rules.trade_side_type == 3 and side == "BUY"):
            raise UnsupportedCapabilityError("MEXC Spot: сторона запрещена правилами символа")
        client_id = _identifier(request.client_order_id or f"se{uuid.uuid4().hex[:28]}", "client_order_id")
        if len(client_id) > 32:
            raise ValueError("MEXC Spot: client_order_id не длиннее 32 символов")
        params = {"symbol": rules.symbol, "side": side, "type": kind, "newClientOrderId": client_id}
        metadata = self._metadata[rules.symbol]
        market = kind == "MARKET"
        minimum = number(metadata["quoteAmountPrecisionMarket"], "market min") if market else rules.min_notional
        maximum = number(metadata["maxQuoteAmountMarket"], "market max") if market else rules.max_notional
        if maximum and minimum > maximum:
            state_error("MEXC Spot: противоречивый market notional")
        qty = price = None
        if market and side == "BUY":
            if request.base_quantity is not None or request.price is not None:
                raise ValueError("MEXC Spot: MARKET BUY требует только quote_quantity")
            notional = _floor_step(number(request.quote_quantity, "quote_quantity", positive=True), rules.price_step)
            params["quoteOrderQty"] = format(notional, "f")
        else:
            if request.quote_quantity is not None:
                raise ValueError("MEXC Spot: используйте base_quantity для SELL/LIMIT")
            qty = _floor_step(number(request.base_quantity, "base_quantity", positive=True), rules.quantity_step)
            if qty <= 0 or qty < rules.min_quantity:
                raise ValueError("MEXC Spot: количество меньше минимального после округления")
            params["quantity"] = format(qty, "f")
            if not market:
                price = _floor_step(number(request.price, "price", positive=True), rules.price_step)
                if price <= 0:
                    raise ValueError("MEXC Spot: цена равна нулю после округления")
                params["price"] = format(price, "f")
                notional = qty * price
            else:
                if request.price is not None:
                    raise ValueError("MEXC Spot: MARKET не принимает price")
                ticker = mapping(self._transport.request("GET", "/api/v3/ticker/price", {"symbol": rules.symbol}), "цена")
                if ticker.get("symbol") != rules.symbol:
                    state_error("MEXC Spot: несовпадающий символ цены")
                notional = qty * number(ticker.get("price"), "market price", positive=True)
        if notional <= 0 or notional < minimum or (maximum and notional > maximum):
            raise ValueError("MEXC Spot: notional вне ограничений")
        for f in metadata.get("filters", []):
            name = f["filterType"]
            if name == "PERCENT_PRICE_BY_SIDE" and not market:
                # В опубликованном примере .2 не определено как множитель или отклонение.
                raise UnsupportedCapabilityError("MEXC Spot: семантика PERCENT_PRICE_BY_SIDE не подтверждена; LIMIT запрещён fail-closed")
            if name in {"PRICE_FILTER", "LOT_SIZE"}:
                value = price if name == "PRICE_FILTER" else qty
                if value is None:
                    continue
                keys = ("minPrice", "maxPrice", "tickSize") if name == "PRICE_FILTER" else ("minQty", "maxQty", "stepSize")
                lo, hi, step = (number(f.get(key), key, nonnegative=True) for key in keys)
                if value < lo or (hi and value > hi) or (step and value % step):
                    raise ValueError("MEXC Spot: нарушен PRICE_FILTER/LOT_SIZE")
            if name in {"MIN_NOTIONAL", "NOTIONAL"}:
                # Неизвестные applyToMarket не трактуются как разрешение обходить фильтр.
                lo = number(f.get("minNotional"), "minNotional", nonnegative=True)
                hi = number(f.get("maxNotional", 0), "maxNotional", nonnegative=True)
                if notional < lo or (hi and notional > hi):
                    raise ValueError("MEXC Spot: нарушен NOTIONAL фильтр")
        endpoint = "/api/v3/order/test" if request.test_only else "/api/v3/order"
        try:
            data = self._transport.request("POST", endpoint, params, private=True, mutation=not request.test_only)
        except UnknownOrderStateError as exc:
            exc.symbol, exc.client_order_id, exc.operation = rules.symbol, client_id, endpoint
            raise
        except ExchangeStateUnavailableError as exc:
            raise unknown("MEXC Spot: результат отправки неизвестен", symbol=rules.symbol, client_order_id=client_id, operation=endpoint) from exc
        if request.test_only:
            mapping(data, "order/test")
            return OrderSubmission(SubmissionStatus.ACKNOWLEDGED, client_order_id=client_id, raw_data=data)
        try:
            data = mapping(data, "ACK")
            oid = _identifier(data.get("orderId"), "orderId")
            if data.get("symbol") != rules.symbol or data.get("side") != side or data.get("type") != kind:
                state_error("MEXC Spot: несовпадающий ACK")
        except ExchangeStateUnavailableError as exc:
            raise unknown("MEXC Spot: недостоверный ACK", symbol=rules.symbol, client_order_id=client_id, operation=endpoint) from exc
        self._owned_orders.add((rules.symbol, oid))
        return OrderSubmission(SubmissionStatus.ACKNOWLEDGED, oid, client_id, raw_data=data)

    def _query_by_client_id(self, symbol, client_id):
        canonical = self.normalize_symbol(symbol)
        try:
            data = self._transport.request("GET", "/api/v3/order", {"symbol": canonical, "origClientOrderId": _identifier(client_id, "client id")}, private=True, max_attempts=1)
        except ExchangeAPIError as exc:
            if exc.code == -2013:
                return None
            raise
        order = self._parse_order(data)
        if order.symbol != canonical or data.get("clientOrderId") != client_id:
            state_error("MEXC Spot: lookup вернул другой ордер")
        return order

    def cancel_order(self, symbol, order_id):
        assert_live_allowed(self._config)
        canonical, oid = self.normalize_symbol(symbol), _identifier(order_id, "orderId")
        if (canonical, oid) not in self._owned_orders:
            state_error("MEXC Spot: отмена ручного/непринадлежащего ордера запрещена")
        try:
            data = self._transport.request("DELETE", "/api/v3/order", {"symbol": canonical, "orderId": oid}, private=True, mutation=True)
        except UnknownOrderStateError as exc:
            exc.symbol, exc.order_id, exc.operation = canonical, oid, "cancel"
            raise
        except ExchangeStateUnavailableError as exc:
            raise unknown("MEXC Spot: результат отмены неизвестен", symbol=canonical, order_id=oid, operation="cancel") from exc
        try:
            order = self._parse_order(data)
            if order.order_id != oid or order.symbol != canonical or order.status != OrderStatus.CANCELED:
                state_error("MEXC Spot: отмена не подтверждена")
        except (ExchangeStateUnavailableError, UnsupportedCapabilityError) as exc:
            raise unknown("MEXC Spot: недостоверный результат отмены", symbol=canonical, order_id=oid, operation="cancel") from exc
        self._owned_orders.discard((canonical, oid))
        return True

    def restore_order_ownership(self, symbol, order_id):
        """Только для восстановления доказанной принадлежности из журнала main."""
        self._owned_orders.add((self.normalize_symbol(symbol), _identifier(order_id, "orderId")))

    def cancel_all_orders(self, symbol):
        raise UnsupportedCapabilityError("Массовая отмена Spot запрещена: ручные ордера не затрагиваются")

    def get_open_orders(self, symbol=None):
        params = {"symbol": self.normalize_symbol(symbol)} if symbol else {}
        return [self._parse_order(item) for item in records(self._transport.request("GET", "/api/v3/openOrders", params, private=True), "openOrders")]

    def get_recent_orders(self, symbol, limit=10):
        if not 1 <= integer(limit, "limit") <= 1000:
            raise ValueError("MEXC Spot: limit от 1 до 1000")
        data = self._transport.request("GET", "/api/v3/allOrders", {"symbol": self.normalize_symbol(symbol), "limit": limit}, private=True)
        return [self._parse_order(item) for item in records(data, "allOrders")][-limit:]

    def _parse_order(self, item):
        item = mapping(item, "ордер")
        raw_type, raw_status = item.get("type"), item.get("status")
        if raw_type not in _TYPES or raw_status not in _ORDER_STATUS or item.get("side") not in {"BUY", "SELL"}:
            state_error("MEXC Spot: неизвестный type/status/side")
        try:
            kind = OrderType(raw_type)
        except ValueError:
            # Не подменяем LIMIT_MAKER/IOC/FOK обычным LIMIT в общем DTO.
            raise UnsupportedCapabilityError(f"MEXC Spot: DTO ещё не поддерживает {raw_type}")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            state_error("MEXC Spot: отсутствует symbol ордера")
        qty = number(item.get("origQty", item.get("Qty", item.get("quantity"))), "origQty", positive=True)
        filled = number(item.get("executedQty"), "executedQty", nonnegative=True)
        if filled > qty or (raw_status == "FILLED" and filled != qty):
            state_error("MEXC Spot: противоречивый executedQty")
        quote = item.get("cumulativeQuoteQty", item.get("cummulativeQuoteQty"))
        if quote is not None:
            quote = number(quote, "cumulativeQuoteQty", nonnegative=True)
            average = quote / filled if filled else Decimal(0)
        else:
            average = number(item.get("avgPrice", 0), "avgPrice", positive=filled > 0, nonnegative=True)
        if filled > 0 and average <= 0:
            state_error("MEXC Spot: отсутствует достоверная цена фактического исполнения")
        return Order(order_id=_identifier(item.get("orderId"), "orderId"), symbol=self.normalize_symbol(symbol),
            side=OrderSide(item["side"]), order_type=kind, status=_ORDER_STATUS[raw_status],
            price=float(number(item.get("price"), "price", nonnegative=True)), quantity=float(qty), filled_quantity=float(filled),
            average_price=float(average), stop_price=float(number(item.get("stopPrice", 0), "stopPrice", nonnegative=True)) or None,
            created_at=_date(item.get("time", item.get("transactTime"))), updated_at=_date(item.get("updateTime")), raw_data=dict(item))

    def check_prerequisites(self):
        return bool(self._config.api_key and self._config.secret_key)

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
