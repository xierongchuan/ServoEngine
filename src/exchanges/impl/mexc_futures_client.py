"""MEXC USDT-M perpetual client на актуальном Futures API."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional

from ..config.base import ExchangeConfig
from ..dto.models import (
    Balance, CommissionRate, ExchangeCapabilities, FundingRate, InstrumentRules,
    Kline, KlinesList, MarketType, Order, OrderBook, OrderSide, OrderStatus,
    OrderType, OrdersList, Position, PositionSide, PositionsDict, Ticker,
)
from ..errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError
from ..exchange_client import ExchangeClient
from .mexc_transport import MEXCFuturesTransport


logger = logging.getLogger(__name__)

_ORDER_TYPES = {1: OrderType.LIMIT, 2: OrderType.LIMIT, 3: OrderType.LIMIT, 4: OrderType.LIMIT, 5: OrderType.MARKET}
_ORDER_STATUS = {
    1: OrderStatus.NEW, 2: OrderStatus.NEW, 3: OrderStatus.FILLED,
    4: OrderStatus.CANCELED, 5: OrderStatus.REJECTED,
}


def _decimal(value, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _utc_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, timezone.utc)


class MEXCFuturesClient(ExchangeClient):
    EXCHANGE_NAME = "mexc"

    def __init__(self, config: Optional[ExchangeConfig] = None, transport=None):
        if config is None:
            from ..config import create_config
            from src.config import MODE
            config = create_config("mexc", is_demo=(MODE == "demo"), market_type="perpetual")
        self._config = config
        self._transport = transport or MEXCFuturesTransport(config)
        self._rules: Dict[str, tuple[float, InstrumentRules]] = {}
        self._positions_cache: Optional[PositionsDict] = None
        self._positions_cache_time = 0.0
        self._balance_cache: Optional[Balance] = None
        self._balance_cache_time = 0.0

    @property
    def capabilities(self) -> ExchangeCapabilities:
        return ExchangeCapabilities(
            market_type=MarketType.PERPETUAL,
            positions=True, shorting=True, leverage=True, funding=True,
            native_protection=True, attached_protection=True,
            automated_strategy=True,
        )

    @property
    def _open_type(self) -> int:
        return 2 if getattr(self._config, "margin_mode", "isolated") == "cross" else 1

    @property
    def _position_mode(self) -> int:
        return 2 if getattr(self._config, "position_mode", "hedge") == "one_way" else 1

    def normalize_symbol(self, symbol: str) -> str:
        return (symbol or "").upper().replace("-", "").replace("_", "").replace("/", "")

    def denormalize_symbol(self, symbol: str) -> str:
        return self.normalize_symbol(symbol)

    def _exchange_symbol(self, symbol: str) -> str:
        canonical = self.normalize_symbol(symbol)
        settle = getattr(self._config, "settle_asset", "USDT")
        if canonical.endswith(settle):
            return f"{canonical[:-len(settle)]}_{settle}"
        raise ValueError(f"MEXC Futures поддерживает только {settle}-M: {symbol}")

    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        canonical = self.normalize_symbol(symbol)
        cached = self._rules.get(canonical)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        exchange_symbol = self._exchange_symbol(canonical)
        response = self._transport.request(
            "GET", "/api/v1/contract/detail/country", {"symbol": exchange_symbol}
        )
        data = response.get("data") if isinstance(response, dict) else None
        if isinstance(data, list):
            data = next((item for item in data if item.get("symbol") == exchange_symbol), None)
        if not isinstance(data, dict):
            raise ExchangeStateUnavailableError(f"MEXC Futures symbol rules not found: {exchange_symbol}")
        settle = getattr(self._config, "settle_asset", "USDT")
        max_leverage = int(data.get("countryConfigContractMaxLeverage") or data.get("maxLeverage") or 1)
        rules = InstrumentRules(
            symbol=canonical,
            exchange_symbol=exchange_symbol,
            base_asset=str(data.get("baseCoin", "")),
            quote_asset=str(data.get("quoteCoin", "")),
            tradable=(int(data.get("futureType", 0)) == 1
                      and str(data.get("settleCoin", "")) == settle
                      and int(data.get("state", -1)) == 0
                      and bool(data.get("apiAllowed"))),
            price_step=_decimal(data.get("priceUnit"), "1"),
            quantity_step=_decimal(data.get("volUnit"), "1"),
            min_quantity=_decimal(data.get("minVol"), "1"),
            max_quantity=_decimal(data.get("maxVol")),
            contract_size=_decimal(data.get("contractSize"), "1"),
            min_leverage=int(data.get("minLeverage") or 1),
            max_leverage=max_leverage,
            leverage_tiers=tuple(data.get("riskLimitList") or data.get("riskLimitTierList") or ()),
            order_types=("LIMIT", "MARKET"),
        )
        self._rules[canonical] = (time.time(), rules)
        return rules

    def _contracts_from_base(self, rules: InstrumentRules, quantity: float) -> Decimal:
        contracts = _floor_step(_decimal(quantity) / rules.contract_size, rules.quantity_step)
        if contracts < rules.min_quantity:
            raise ValueError(f"Объём меньше minVol={rules.min_quantity} контрактов")
        if rules.max_quantity and contracts > rules.max_quantity:
            raise ValueError(f"Объём больше maxVol={rules.max_quantity} контрактов")
        return contracts

    def get_klines(self, symbol: str, interval: str = "5m", limit: int = 288) -> KlinesList:
        try:
            from ..ws_provider_factory import get_klines_from_shared_cache, is_cache_ready
            if is_cache_ready(symbol):
                cached = get_klines_from_shared_cache(symbol, limit)
                if cached:
                    return [Kline(
                        timestamp=str(item.get("snapshotTimeUTC", "")),
                        open=float(item.get("openPrice", 0)),
                        high=float(item.get("highPrice", 0)),
                        low=float(item.get("lowPrice", 0)),
                        close=float(item.get("closePrice", 0)),
                        volume=float(item.get("volume", 0)),
                        closed=True,
                    ) for item in cached]
        except Exception:
            # REST остаётся надёжным fallback при любом сбое shared WS cache.
            pass

        exchange_interval = self._config.supported_intervals.get(interval)
        if not exchange_interval:
            raise ValueError(f"MEXC Futures interval not supported: {interval}")
        now = int(time.time())
        seconds = {
            "Min1": 60, "Min5": 300, "Min15": 900, "Min30": 1800,
            "Min60": 3600, "Hour4": 14400, "Hour8": 28800,
            "Day1": 86400, "Week1": 604800, "Month1": 2592000,
        }[exchange_interval]
        response = self._transport.request("GET", f"/api/v1/contract/kline/{self._exchange_symbol(symbol)}", {
            "interval": exchange_interval,
            "start": now - min(max(int(limit), 1), 2000) * seconds,
            "end": now,
        })
        data = response.get("data", {})
        times = data.get("time", [])
        result = []
        for idx, ts in enumerate(times):
            result.append(Kline(
                timestamp=datetime.fromtimestamp(int(ts), timezone.utc).isoformat(),
                open=float(data["open"][idx]), high=float(data["high"][idx]),
                low=float(data["low"][idx]), close=float(data["close"][idx]),
                volume=float(data.get("vol", [0] * len(times))[idx]),
                closed=(int(ts) + seconds) <= now,
            ))
        return result[-limit:]

    def get_ticker(self, symbol: str) -> Ticker:
        response = self._transport.request("GET", "/api/v1/contract/ticker", {
            "symbol": self._exchange_symbol(symbol),
        })
        data = response.get("data", {})
        canonical = self.normalize_symbol(data.get("symbol", symbol))
        return Ticker(
            symbol=canonical,
            last_price=float(data.get("lastPrice", 0)),
            bid_price=float(data.get("bid1", 0)),
            ask_price=float(data.get("ask1", 0)),
            volume_24h=float(data.get("volume24", 0)),
            quote_volume_24h=float(data.get("amount24", 0)),
            price_change_24h=float(data.get("riseFallValue", 0)),
            price_change_percent_24h=float(data.get("riseFallRate", 0)) * 100,
            high_24h=float(data.get("high24Price", 0)),
            low_24h=float(data.get("lower24Price", 0)),
            mark_price=float(data.get("fairPrice", 0)) or None,
            index_price=float(data.get("indexPrice", 0)) or None,
            funding_rate=float(data.get("fundingRate", 0)),
        )

    def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        rules = self.get_instrument_rules(symbol)
        response = self._transport.request("GET", f"/api/v1/contract/depth/{rules.exchange_symbol}", {
            "limit": limit,
        })
        data = response.get("data", {})
        convert = lambda rows: [[float(row[0]), float(_decimal(row[1]) * rules.contract_size)] for row in rows]
        return OrderBook(
            symbol=rules.symbol,
            bids=convert(data.get("bids", [])), asks=convert(data.get("asks", [])),
            last_update_id=int(data.get("version", 0)),
        )

    def get_balance(self) -> Balance:
        if self._balance_cache and time.time() - self._balance_cache_time < self._config.balance_cache_ttl:
            return self._balance_cache
        response = self._transport.request("GET", "/api/v1/private/account/assets", private=True)
        settle = getattr(self._config, "settle_asset", "USDT")
        item = next((x for x in response.get("data", []) if x.get("currency") == settle), None)
        if item is None:
            raise ExchangeStateUnavailableError(f"MEXC Futures balance {settle} not found")
        balance = Balance(
            total_balance=float(item.get("equity", 0)),
            available_balance=float(item.get("availableOpen", item.get("availableBalance", 0))),
            unrealized_pnl=float(item.get("unrealized", 0)),
            locked_balance=float(item.get("frozenBalance", 0)),
            asset=settle,
        )
        self._balance_cache, self._balance_cache_time = balance, time.time()
        return balance

    def get_commission_rate(self, symbol: str) -> Optional[CommissionRate]:
        response = self._transport.request("GET", "/api/v1/private/account/tiered_fee_rate/v2", {
            "symbol": self._exchange_symbol(symbol),
        }, private=True)
        data = response.get("data", {})
        maker = data.get("makerFee", data.get("makerFeeRate"))
        taker = data.get("takerFee", data.get("takerFeeRate"))
        if maker is None or taker is None:
            # При отсутствии данных API используем консервативный fallback комиссии.
            return CommissionRate(maker=0.06, taker=0.08)
        return CommissionRate(maker=float(_decimal(maker) * 100), taker=float(_decimal(taker) * 100))

    def get_funding_rate(self, symbol: str) -> Optional[FundingRate]:
        response = self._transport.request("GET", f"/api/v1/contract/funding_rate/{self._exchange_symbol(symbol)}")
        data = response.get("data", {})
        rate = float(data.get("fundingRate", 0))
        return FundingRate(
            funding_rate=rate,
            funding_rate_pct=rate * 100,
            next_funding_time=datetime.fromtimestamp(int(data["nextSettleTime"]) / 1000, timezone.utc).isoformat()
            if data.get("nextSettleTime") else None,
        )

    def get_positions(self) -> PositionsDict:
        if self._positions_cache is not None and time.time() - self._positions_cache_time < self._config.positions_cache_ttl:
            return self._positions_cache
        response = self._transport.request("GET", "/api/v1/private/position/open_positions", private=True)
        positions: PositionsDict = {}
        for item in response.get("data", []) or []:
            contracts = _decimal(item.get("holdVol"))
            if contracts <= 0:
                continue
            rules = self.get_instrument_rules(item.get("symbol", ""))
            canonical = rules.symbol
            position = Position(
                symbol=canonical,
                side=PositionSide.LONG if int(item.get("positionType", 1)) == 1 else PositionSide.SHORT,
                size=float(contracts * rules.contract_size),
                entry_price=float(item.get("holdAvgPrice", item.get("openAvgPrice", 0))),
                unrealized_pnl=float(item.get("unRealizedPnl", item.get("unrealized", 0)) or 0),
                leverage=int(item.get("leverage", 0)) or None,
                position_id=str(item.get("positionId", "")),
                liquidation_price=float(item.get("liquidatePrice", 0)) or None,
                margin=float(item.get("im", 0)) or None,
                created_at=_utc_datetime(item.get("createTime")),
                updated_at=_utc_datetime(item.get("updateTime")),
                exchange_quantity=float(contracts),
                contract_size=float(rules.contract_size),
            )
            positions.setdefault(canonical, []).append(position)
        self._positions_cache, self._positions_cache_time = positions, time.time()
        return positions

    def place_order(
        self, symbol: str, side: OrderSide, quantity: float,
        order_type: OrderType = OrderType.MARKET, price: Optional[float] = None,
        sl: Optional[float] = None, tp: Optional[float] = None,
        position_side: Optional[PositionSide] = None, leverage: Optional[int] = None,
    ) -> Optional[str]:
        rules = self.get_instrument_rules(symbol)
        if not rules.tradable:
            raise ExchangeAPIError(f"MEXC Futures trading disabled for {rules.exchange_symbol}")
        side_enum = side if isinstance(side, OrderSide) else OrderSide(str(side).upper())
        pos_side = position_side
        if isinstance(pos_side, str):
            pos_side = PositionSide(pos_side.upper())
        pos_side = pos_side or (PositionSide.LONG if side_enum == OrderSide.BUY else PositionSide.SHORT)
        lev = int(leverage or self._config.default_leverage)
        if not rules.min_leverage <= lev <= rules.max_leverage:
            raise ValueError(f"Leverage {lev} outside {rules.min_leverage}..{rules.max_leverage}")
        self.set_leverage(symbol, lev, pos_side)
        contracts = self._contracts_from_base(rules, quantity)
        direction = 1 if pos_side == PositionSide.LONG else 3
        order_type_id = 5 if order_type == OrderType.MARKET else 1
        external_id = f"se{uuid.uuid4().hex[:28]}"
        params = {
            "symbol": rules.exchange_symbol,
            "price": format(_floor_step(_decimal(price or 0), rules.price_step), "f"),
            "vol": float(contracts),
            "leverage": lev,
            "side": direction,
            "type": order_type_id,
            "openType": self._open_type,
            "externalOid": external_id,
            "positionMode": self._position_mode,
            "stopLossPrice": sl,
            "takeProfitPrice": tp,
            "lossTrend": 2 if sl else None,
            "profitTrend": 2 if tp else None,
        }
        try:
            response = self._transport.request(
                "POST", "/api/v1/private/order/create", params,
                private=True, mutation=True,
            )
        except ExchangeStateUnavailableError as exc:
            found = self._query_external(rules.exchange_symbol, external_id)
            if found:
                return found.order_id
            raise UnknownOrderStateError(f"Состояние Futures ордера {external_id} неизвестно") from exc
        order_id = (response.get("data") or {}).get("orderId")
        if not order_id:
            raise ExchangeAPIError("MEXC Futures не вернул orderId")
        if sl or tp:
            attached = self._query_external(rules.exchange_symbol, external_id)
            if attached is None:
                logger.warning(f"⚠️ MEXC пока не подтвердил attached TP/SL для ордера {order_id}")
            else:
                raw = attached.raw_data
                missing_sl = sl and not raw.get("stopLossPrice")
                missing_tp = tp and not raw.get("takeProfitPrice")
                if missing_sl or missing_tp:
                    logger.error(f"❌ MEXC не подтвердил всю защиту entry order {order_id}")
        self.invalidate_cache("positions")
        return str(order_id)

    def _query_external(self, exchange_symbol: str, external_id: str) -> Optional[Order]:
        try:
            response = self._transport.request(
                "GET", f"/api/v1/private/order/external/{exchange_symbol}/{external_id}",
                private=True, max_attempts=1,
            )
            data = response.get("data")
            return self._parse_order(data) if data else None
        except Exception:
            return None

    def set_leverage(self, symbol: str, leverage: int, position_side: PositionSide = PositionSide.BOTH) -> bool:
        rules = self.get_instrument_rules(symbol)
        side = position_side
        if isinstance(side, str):
            side = PositionSide(side.upper())
        position_type = 2 if side == PositionSide.SHORT else 1
        self._transport.request("POST", "/api/v1/private/position/change_leverage", {
            "openType": self._open_type,
            "leverage": int(leverage),
            "symbol": rules.exchange_symbol,
            "positionType": position_type,
        }, private=True, mutation=True)
        return True

    def close_position(self, symbol: str, position_id: str, percentage: float = 1.0) -> bool:
        if not 0 < percentage <= 1:
            raise ValueError("percentage must be in (0, 1]")
        target = None
        for position in self.get_positions().get(self.normalize_symbol(symbol), []):
            if str(position.position_id) == str(position_id):
                target = position
                break
        if target is None or target.exchange_quantity is None:
            raise ExchangeStateUnavailableError(f"Позиция {position_id} не найдена")
        rules = self.get_instrument_rules(symbol)
        contracts = _floor_step(_decimal(target.exchange_quantity) * _decimal(percentage), rules.quantity_step)
        if contracts < rules.min_quantity:
            raise ValueError("Частичное закрытие меньше minVol")
        params = {
            "symbol": rules.exchange_symbol,
            "price": "0",
            "vol": float(contracts),
            "side": 4 if target.side == PositionSide.LONG else 2,
            "type": 5,
            "openType": self._open_type,
            "positionId": int(position_id),
            "externalOid": f"se{uuid.uuid4().hex[:28]}",
            "positionMode": self._position_mode,
            "reduceOnly": self._position_mode == 2,
        }
        try:
            self._transport.request(
                "POST", "/api/v1/private/order/create", params,
                private=True, mutation=True,
            )
        except ExchangeStateUnavailableError as exc:
            found = self._query_external(rules.exchange_symbol, params["externalOid"])
            if found:
                self.invalidate_cache("positions")
                return True
            raise UnknownOrderStateError(
                f"Состояние закрывающего ордера {params['externalOid']} неизвестно"
            ) from exc
        self.invalidate_cache("positions")
        return True

    def set_sl_tp(
        self, symbol: str, position_side: PositionSide, sl: Optional[float] = None,
        tp: Optional[float] = None, quantity: Optional[float] = None,
    ) -> bool:
        if not sl and not tp:
            return True
        side = position_side
        if isinstance(side, str):
            side = PositionSide(side.upper())
        target = next((p for p in self.get_positions().get(self.normalize_symbol(symbol), []) if p.side == side), None)
        if target is None or target.exchange_quantity is None:
            raise ExchangeStateUnavailableError(f"Позиция {symbol} {side.value} не найдена для TP/SL")
        rules = self.get_instrument_rules(symbol)
        contracts = _decimal(target.exchange_quantity)
        if quantity is not None:
            contracts = self._contracts_from_base(rules, quantity)
        params = {
            "lossTrend": 2,
            "profitTrend": 2,
            "positionId": int(target.position_id),
            "vol": float(contracts),
            "stopLossPrice": sl,
            "takeProfitPrice": tp,
            "priceProtect": 1,
            "profitLossVolType": "SAME",
            "volType": 2,
            "takeProfitType": 0,
            "takeProfitOrderPrice": 0,
            "stopLossType": 0,
            "stopLossOrderPrice": 0,
        }
        self._transport.request("POST", "/api/v1/private/stoporder/place", params, private=True, mutation=True)
        return True

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        response = self._transport.request("POST", "/api/v1/private/order/cancel", [int(order_id)], private=True, mutation=True)
        results = response.get("data", [])
        return bool(results) and int(results[0].get("errorCode", -1)) == 0

    def cancel_all_orders(self, symbol: str) -> bool:
        self._transport.request("POST", "/api/v1/private/order/cancel_all", {
            "symbol": self._exchange_symbol(symbol),
        }, private=True, mutation=True)
        return True

    def get_open_orders(self, symbol: Optional[str] = None) -> OrdersList:
        params = {"page_num": 1, "page_size": 100}
        response = self._transport.request("GET", "/api/v1/private/order/list/open_orders", params, private=True)
        data = response.get("data", []) or []
        if isinstance(data, dict):
            data = data.get("resultList", [])
        orders = [self._parse_order(item) for item in data]
        canonical = self.normalize_symbol(symbol) if symbol else None
        return [order for order in orders if not canonical or order.symbol == canonical]

    def get_recent_orders(self, symbol: str, limit: int = 10) -> OrdersList:
        response = self._transport.request("GET", "/api/v1/private/order/list/history_orders", {
            "symbol": self._exchange_symbol(symbol), "page_num": 1,
            "page_size": min(max(limit, 1), 100),
        }, private=True)
        data = response.get("data", []) or []
        if isinstance(data, dict):
            data = data.get("resultList", [])
        return [self._parse_order(item) for item in data][:limit]

    def _parse_order(self, item: dict) -> Order:
        exchange_symbol = str(item.get("symbol", ""))
        try:
            rules = self.get_instrument_rules(exchange_symbol)
            contract_size = rules.contract_size
        except Exception:
            contract_size = Decimal("1")
        side_id = int(item.get("side", 1))
        side = OrderSide.BUY if side_id in {1, 2} else OrderSide.SELL
        position_side = PositionSide.LONG if side_id in {1, 4} else PositionSide.SHORT
        return Order(
            order_id=str(item.get("orderId", "")),
            symbol=self.normalize_symbol(exchange_symbol),
            side=side,
            order_type=_ORDER_TYPES.get(int(item.get("orderType", 1)), OrderType.LIMIT),
            status=_ORDER_STATUS.get(int(item.get("state", 1)), OrderStatus.NEW),
            price=float(item.get("price", 0) or 0),
            quantity=float(_decimal(item.get("vol")) * contract_size),
            filled_quantity=float(_decimal(item.get("dealVol")) * contract_size),
            average_price=float(item.get("dealAvgPrice", 0) or 0),
            commission=float(item.get("takerFee", 0) or 0) + float(item.get("makerFee", 0) or 0),
            realized_pnl=float(item.get("profit", 0) or 0),
            stop_price=float(item.get("stopLossPrice", 0) or 0) or None,
            position_side=position_side,
            created_at=_utc_datetime(item.get("createTime")),
            updated_at=_utc_datetime(item.get("updateTime")),
            raw_data=item,
        )

    def _validate_position_mode(self) -> bool:
        try:
            response = self._transport.request("GET", "/api/v1/private/position/position_mode", private=True)
            data = response.get("data", {})
            actual = data.get("positionMode") if isinstance(data, dict) else data
            if actual is None:
                logger.error("❌ MEXC не вернул positionMode")
                return False
            if int(actual) != self._position_mode:
                logger.error("❌ MEXC position mode не совпадает с MEXC_POSITION_MODE")
                return False
            return True
        except Exception as exc:
            logger.error(f"❌ Не удалось проверить MEXC position mode: {exc}")
            return False

    def check_prerequisites(self) -> bool:
        if not self._config.api_key or not self._config.secret_key:
            logger.error("❌ MEXC API ключи не настроены")
            return False
        if self._config.is_demo:
            logger.error("❌ MEXC не предоставляет API sandbox; MODE=demo запрещает автоторговлю")
            return False
        if not getattr(self._config, "live_trading_enabled", False):
            logger.error("❌ MEXC live trading выключен (MEXC_ENABLE_LIVE_TRADING=false)")
            return False
        if getattr(self._config, "margin_mode", "isolated") not in {"isolated", "cross"}:
            logger.error("❌ MEXC_MARGIN_MODE должен быть isolated или cross")
            return False
        if getattr(self._config, "position_mode", "hedge") not in {"hedge", "one_way"}:
            logger.error("❌ MEXC_POSITION_MODE должен быть hedge или one_way")
            return False
        return self._validate_position_mode()

    def invalidate_cache(self, cache_type: Optional[str] = None) -> None:
        if cache_type is None or cache_type == "positions":
            self._positions_cache = None
            self._positions_cache_time = 0
        if cache_type is None or cache_type == "balance":
            self._balance_cache = None
            self._balance_cache_time = 0
