"""MEXC USDT-M: isolated, проверка аккаунта и однократное исполнение."""

from __future__ import annotations

from copy import deepcopy
import json
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from src.utils.logger import error, warning
from ..config.base import ExchangeConfig
from ..dto.models import (
    Balance, CommissionRate, ExchangeCapabilities, FundingRate, InstrumentRules,
    Kline, KlinesList, MarketType, Order, OrderBook, OrderSide, OrderStatus,
    OrderType, OrdersList, Position, PositionSide, PositionsDict, Ticker,
)
from ..errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from ..exchange_client import ExchangeClient
from .mexc_transport import (
    MEXCFuturesTransport, ambiguous_code, assert_live_allowed, integer, mapping, number, records,
    state_error, unknown,
)

_ORDER_STATUS = {1: OrderStatus.NEW, 2: OrderStatus.NEW, 3: OrderStatus.FILLED,
                 4: OrderStatus.CANCELED, 5: OrderStatus.REJECTED}
_ORDER_TYPES = {1: "LIMIT", 2: "POST_ONLY", 3: "IMMEDIATE_OR_CANCEL", 4: "FILL_OR_KILL", 5: "MARKET"}


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _utc_datetime(value):
    return None if value in (None, "", 0) else datetime.fromtimestamp(integer(value, "время") / 1000, timezone.utc)


def _id(value, label="id") -> str:
    return str(integer(value, label, positive=True))


def _client_id(value=None) -> str:
    value = value if value is not None else f"se{uuid.uuid4().hex[:28]}"
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
        raise ValueError("MEXC client_order_id: от 1 до 32 букв, цифр, '_' или '-'")
    return value


class MEXCFuturesClient(ExchangeClient):
    EXCHANGE_NAME = "mexc"

    def __init__(self, config: Optional[ExchangeConfig] = None, transport=None):
        if config is None:
            from ..config import create_config
            config = create_config("mexc", market_type="perpetual")
        self._config = config
        self._transport = transport or MEXCFuturesTransport(config)
        self._rules = {}
        self._instrument_metadata = {}
        self._position_details = {}
        self._owned_orders = set()
        self._owned_positions = {}
        self._owned_stop_orders = {}
        self._owned_stop_snapshots = {}
        self._positions_cache = self._balance_cache = None
        self._positions_cache_time = self._balance_cache_time = 0.0
        self.last_protection_confirmed = False

    @property
    def capabilities(self):
        return ExchangeCapabilities(market_type=MarketType.PERPETUAL, positions=True,
            shorting=True, leverage=True, funding=True, native_protection=True,
            attached_protection=True, automated_strategy=True)

    @property
    def _open_type(self):
        if getattr(self._config, "margin_mode", None) != "isolated":
            state_error("MEXC: разрешён только isolated; Cross запрещён")
        return 1

    @property
    def _position_mode(self):
        mode = getattr(self._config, "position_mode", None)
        if mode not in {"hedge", "one_way"}:
            state_error("MEXC: неизвестный режим позиции")
        return 1 if mode == "hedge" else 2

    @property
    def _trend(self):
        protection = getattr(self._config, "protection", None)
        if protection is None:
            settings = getattr(self._config, "_settings", {})
            protection = settings.get("protection", {})
        trigger = protection.get("trigger_price", "mark")
        if trigger not in {"last", "mark"}:
            state_error("MEXC: неизвестный источник цены защиты")
        return 1 if trigger == "last" else 2

    def normalize_symbol(self, symbol):
        return (symbol or "").upper().replace("-", "").replace("_", "").replace("/", "")

    def denormalize_symbol(self, symbol):
        return self.normalize_symbol(symbol)

    def _exchange_symbol(self, symbol):
        canonical = self.normalize_symbol(symbol)
        settle = getattr(self._config, "settle_asset", "USDT")
        if settle != "USDT" or not canonical.endswith(settle) or len(canonical) <= len(settle):
            raise ValueError("MEXC: поддерживается только USDT-M")
        return f"{canonical[:-len(settle)]}_{settle}"

    def _data(self, response):
        response = mapping(response, "ответ Futures")
        if response.get("success") is not True or integer(response.get("code", 0), "code") != 0:
            state_error("MEXC: некорректный успешный ответ Futures")
        if "data" not in response:
            state_error("MEXC: отсутствует data")
        return response["data"]

    def get_instrument_rules(self, symbol):
        canonical = self.normalize_symbol(symbol)
        cached = self._rules.get(canonical)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        exchange_symbol = self._exchange_symbol(symbol)
        data = self._data(self._transport.request("GET", "/api/v1/contract/detail/country", {"symbol": exchange_symbol}))
        if isinstance(data, list):
            matches = [item for item in records(data, "контракты") if item.get("symbol") == exchange_symbol]
            if len(matches) != 1:
                state_error("MEXC: контракт не найден однозначно")
            data = matches[0]
        data = mapping(data, "контракт")
        if data.get("symbol") != exchange_symbol or not isinstance(data.get("apiAllowed"), bool):
            state_error("MEXC: несовпадающий контракт или некорректный apiAllowed")
        opening = integer(data.get("positionOpenType"), "positionOpenType")
        if opening not in {1, 2, 3}:
            state_error("MEXC: неизвестный positionOpenType")
        tiers = records(data.get("riskLimitCustom", []), "riskLimitCustom")
        if data.get("riskLimitMode") == "CUSTOM" and not tiers:
            state_error("MEXC: отсутствуют CUSTOM risk limits")
        for tier in tiers:
            number(tier.get("maxVol"), "risk maxVol", positive=True)
            integer(tier.get("maxLeverage"), "risk maxLeverage", positive=True)
        rules = InstrumentRules(
            symbol=canonical, exchange_symbol=exchange_symbol,
            base_asset=str(data.get("baseCoin", "")), quote_asset=str(data.get("quoteCoin", "")),
            tradable=(integer(data.get("futureType"), "futureType") == 1
                and data.get("settleCoin") == "USDT" and integer(data.get("state"), "state") == 0
                and data["apiAllowed"] and opening in {1, 3}),
            price_step=number(data.get("priceUnit"), "priceUnit", positive=True),
            quantity_step=number(data.get("volUnit"), "volUnit", positive=True),
            min_quantity=number(data.get("minVol"), "minVol", positive=True),
            max_quantity=number(data.get("maxVol"), "maxVol", positive=True),
            contract_size=number(data.get("contractSize"), "contractSize", positive=True),
            min_leverage=integer(data.get("minLeverage"), "minLeverage", positive=True),
            max_leverage=integer(data.get("countryConfigContractMaxLeverage", data.get("maxLeverage")), "maxLeverage", positive=True),
            leverage_tiers=tuple(tiers), order_types=("LIMIT", "MARKET"),
        )
        if rules.min_quantity > rules.max_quantity or rules.min_leverage > rules.max_leverage:
            state_error("MEXC: противоречивые ограничения контракта")
        self._instrument_metadata[canonical] = dict(data)
        self._rules[canonical] = (time.monotonic(), rules)
        return rules

    def _contracts_from_base(self, rules, quantity, *, limit_order=False):
        contracts = _floor_step(number(quantity, "quantity", positive=True) / rules.contract_size, rules.quantity_step)
        maximum = rules.max_quantity
        if limit_order:
            maximum = number(self._instrument_metadata[rules.symbol].get("limitMaxVol", maximum), "limitMaxVol", positive=True)
        if contracts < rules.min_quantity or contracts > maximum:
            raise ValueError("MEXC: объём контрактов вне minVol/maxVol")
        return contracts

    def _price(self, rules, value):
        if value is None:
            return None
        result = _floor_step(number(value, "цена", positive=True), rules.price_step)
        if result <= 0:
            raise ValueError("MEXC: цена после округления равна нулю")
        return format(result, "f")

    def get_klines(self, symbol, interval="5m", limit=288) -> KlinesList:
        exchange_interval = self._config.supported_intervals.get(interval)
        durations = {"Min1": 60, "Min5": 300, "Min15": 900, "Min30": 1800, "Min60": 3600,
                     "Hour4": 14400, "Hour8": 28800, "Day1": 86400, "Week1": 604800, "Month1": 2678400}
        if exchange_interval not in durations or not 1 <= integer(limit, "limit") <= 2000:
            raise ValueError("MEXC: некорректный interval/limit")
        now = int(time.time())
        data = mapping(self._data(self._transport.request("GET", f"/api/v1/contract/kline/{self._exchange_symbol(symbol)}", {
            "interval": exchange_interval, "start": now - (limit + 1) * durations[exchange_interval], "end": now,
        })), "свечи")
        arrays = [data.get(key) for key in ("time", "open", "high", "low", "close", "vol")]
        if any(not isinstance(a, list) for a in arrays) or len({len(a) for a in arrays}) != 1:
            state_error("MEXC: несовпадающие массивы свечей")
        result, previous = [], -1
        for ts, o, h, l, c, v in zip(*arrays):
            ts = integer(ts, "время свечи", positive=True)
            if ts <= previous or ts > now:
                state_error("MEXC: нарушен порядок свечей")
            previous = ts
            dt = datetime.fromtimestamp(ts, timezone.utc)
            end = ts + durations[exchange_interval]
            if exchange_interval == "Month1":
                end = int(dt.replace(year=dt.year + (dt.month == 12), month=dt.month % 12 + 1, day=1).timestamp())
            prices = [number(x, "OHLC", positive=True) for x in (o, h, l, c)]
            if prices[1] < max(prices) or prices[2] > min(prices):
                state_error("MEXC: некорректные OHLC")
            result.append(Kline(dt.isoformat(), *(float(x) for x in prices), float(number(v, "vol", nonnegative=True)), closed=end <= now))
        return result[-limit:]

    def get_ticker(self, symbol):
        data = mapping(self._data(self._transport.request("GET", "/api/v1/contract/ticker", {"symbol": self._exchange_symbol(symbol)})), "тикер")
        if data.get("symbol") != self._exchange_symbol(symbol):
            state_error("MEXC: несовпадающий символ тикера")
        n = lambda key: float(number(data.get(key), key))
        return Ticker(symbol=self.normalize_symbol(symbol), last_price=n("lastPrice"), bid_price=n("bid1"), ask_price=n("ask1"),
            volume_24h=n("volume24"), quote_volume_24h=n("amount24"), price_change_24h=n("riseFallValue"),
            price_change_percent_24h=n("riseFallRate") * 100, high_24h=n("high24Price"), low_24h=n("lower24Price"),
            mark_price=n("fairPrice"), index_price=n("indexPrice"), funding_rate=n("fundingRate"))

    def get_order_book(self, symbol, limit=20):
        rules = self.get_instrument_rules(symbol)
        data = mapping(self._data(self._transport.request("GET", f"/api/v1/contract/depth/{rules.exchange_symbol}", {"limit": limit})), "стакан")
        def convert(rows):
            if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) < 2 for row in rows):
                state_error("MEXC: некорректный стакан")
            return [[float(number(row[0], "цена", positive=True)), float(number(row[1], "vol", nonnegative=True) * rules.contract_size)] for row in rows]
        return OrderBook(rules.symbol, convert(data.get("bids")), convert(data.get("asks")), integer(data.get("version"), "version"))

    def get_balance(self):
        if self._balance_cache is not None and time.monotonic() - self._balance_cache_time < self._config.balance_cache_ttl:
            return self._balance_cache
        items = records(self._data(self._transport.request("GET", "/api/v1/private/account/assets", private=True)), "активы")
        matches = [x for x in items if x.get("currency") == self._config.settle_asset]
        if len(matches) != 1:
            state_error("MEXC: баланс расчётного актива не найден однозначно")
        item = matches[0]
        balance = Balance(float(number(item.get("equity"), "equity")),
            float(number(item.get("availableOpen", item.get("availableBalance")), "availableOpen")),
            float(number(item.get("unrealized"), "unrealized")), float(number(item.get("frozenBalance"), "frozenBalance")), asset=self._config.settle_asset)
        self._balance_cache, self._balance_cache_time = balance, time.monotonic()
        return balance

    def _fees(self, symbol):
        data = mapping(self._data(self._transport.request("GET", "/api/v1/private/account/tiered_fee_rate/v2", {"symbol": self._exchange_symbol(symbol)}, private=True)), "комиссии")
        if data.get("symbol") != self._exchange_symbol(symbol) or data.get("feeRateMode") not in {"NORMAL", "LEVERAGE", "TIERED"}:
            state_error("MEXC: неизвестная схема комиссий")
        number(data.get("realMakerFee"), "realMakerFee")
        number(data.get("realTakerFee"), "realTakerFee")
        return data

    def get_commission_rate(self, symbol):
        data = self._fees(symbol)
        return CommissionRate(float(number(data["realMakerFee"], "realMakerFee") * 100), float(number(data["realTakerFee"], "realTakerFee") * 100))

    def get_funding_rate(self, symbol):
        data = mapping(self._data(self._transport.request("GET", f"/api/v1/contract/funding_rate/{self._exchange_symbol(symbol)}")), "funding")
        rate = float(number(data.get("fundingRate"), "fundingRate"))
        return FundingRate(rate, rate * 100, _utc_datetime(data.get("nextSettleTime")))

    def get_positions(self) -> PositionsDict:
        if self._positions_cache is not None and time.monotonic() - self._positions_cache_time < self._config.positions_cache_ttl:
            return self._positions_cache
        rows = records(self._data(self._transport.request("GET", "/api/v1/private/position/open_positions", private=True)), "позиции")
        positions, details = {}, {}
        for item in rows:
            contracts = number(item.get("holdVol"), "holdVol", nonnegative=True)
            if contracts == 0:
                continue
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not symbol.endswith("_USDT"):
                state_error("MEXC: отсутствует корректный symbol позиции")
            rules = self.get_instrument_rules(symbol)
            side = integer(item.get("positionType"), "positionType")
            open_type = integer(item.get("openType"), "openType")
            if side not in {1, 2} or open_type not in {1, 2}:
                state_error("MEXC: неизвестная сторона/маржа позиции")
            pid = _id(item.get("positionId"), "positionId")
            if pid in details:
                state_error("MEXC: повторный positionId")
            number(item.get("frozenVol"), "frozenVol", nonnegative=True)
            details[pid] = dict(item)
            position = Position(symbol=rules.symbol, side=PositionSide.LONG if side == 1 else PositionSide.SHORT,
                size=float(contracts * rules.contract_size), entry_price=float(number(item.get("holdAvgPrice", item.get("openAvgPrice")), "holdAvgPrice", positive=True)),
                unrealized_pnl=float(number(item.get("unRealizedPnl", item.get("unrealized", 0)), "unrealized")),
                leverage=integer(item.get("leverage"), "leverage", positive=True), position_id=pid,
                liquidation_price=float(number(item.get("liquidatePrice", 0), "liquidatePrice", nonnegative=True)) or None,
                margin=float(number(item.get("im", 0), "im", nonnegative=True)) or None,
                created_at=_utc_datetime(item.get("createTime")), updated_at=_utc_datetime(item.get("updateTime")),
                exchange_quantity=float(contracts), contract_size=float(rules.contract_size))
            positions.setdefault(rules.symbol, []).append(position)
        self._position_details = details
        self._positions_cache, self._positions_cache_time = positions, time.monotonic()
        return positions

    def _validate_position_mode(self):
        data = self._data(self._transport.request("GET", "/api/v1/private/position/position_mode", private=True))
        actual = data.get("positionMode") if isinstance(data, dict) else data
        actual = integer(actual, "positionMode")
        if actual not in {1, 2} or actual != self._position_mode:
            state_error("MEXC: режим аккаунта не совпадает с настройкой; автоматическое переключение запрещено")
        return True

    def _mutation_positions(self):
        assert_live_allowed(self._config)
        self._open_type
        self._validate_position_mode()
        self.invalidate_cache()
        positions = self.get_positions()
        if any(integer(item.get("openType"), "openType") != 1 for item in self._position_details.values()):
            state_error("MEXC: на общем аккаунте есть Cross-позиция; исполнение остановлено")
        return positions

    def _send(self, endpoint, params, *, symbol=None, client_id=None):
        try:
            response = self._transport.request("POST", endpoint, params, private=True, mutation=True)
            envelope = mapping(response, "ACK мутации")
            if envelope.get("success") is not True or integer(envelope.get("code", 0), "code") != 0:
                state_error("MEXC: недостоверный ACK мутации")
            return envelope
        except UnknownOrderStateError as exc:
            exc.symbol, exc.client_order_id, exc.external_oid, exc.operation = symbol, client_id, client_id, endpoint
            raise
        except ExchangeStateUnavailableError as exc:
            raise unknown("MEXC: результат мутации неизвестен", symbol=symbol, client_order_id=client_id, operation=endpoint) from exc
        finally:
            self.invalidate_cache()

    def _ack_id(self, response, symbol, client_id, operation):
        try:
            data = self._data(response)
            value = data.get("orderId") if isinstance(data, dict) else data
            return _id(value, "orderId")
        except ExchangeStateUnavailableError as exc:
            raise unknown("MEXC: отсутствует достоверный id принятого ордера", symbol=symbol, client_order_id=client_id, operation=operation) from exc

    def place_order(self, symbol, side, quantity, order_type=OrderType.MARKET, price=None,
                    sl=None, tp=None, position_side=None, leverage=None, client_order_id=None):
        side = side if isinstance(side, OrderSide) else OrderSide(side)
        type_name = getattr(order_type, "value", order_type)
        type_codes = {"LIMIT": 1, "LIMIT_MAKER": 2, "POST_ONLY": 2, "IMMEDIATE_OR_CANCEL": 3, "FILL_OR_KILL": 4, "MARKET": 5}
        if type_name not in type_codes:
            raise UnsupportedCapabilityError("MEXC: неподдерживаемый тип entry")
        intended = PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT
        if position_side is not None:
            position_side = position_side if isinstance(position_side, PositionSide) else PositionSide(position_side)
            if position_side != intended and not (self._position_mode == 2 and position_side == PositionSide.BOTH):
                raise ValueError("MEXC: side несовместим с открываемой позицией; для закрытия используйте close_position")
        external_id = _client_id(client_order_id)
        positions = self._mutation_positions().get(self.normalize_symbol(symbol), [])
        rules = self.get_instrument_rules(symbol)
        if not rules.tradable:
            raise ExchangeAPIError("MEXC: контракт недоступен для isolated API trading")
        pending = self._pages("/api/v1/private/order/list/open_orders")
        pending_volume = Decimal(0)
        for row in pending:
            if integer(row.get("openType"), "order openType") != 1:
                state_error("MEXC: на аккаунте есть Cross-ордер; entry остановлен")
            if row.get("symbol") != rules.exchange_symbol:
                continue
            order_side = integer(row.get("side"), "order side")
            if order_side not in {1, 2, 3, 4}:
                state_error("MEXC: неизвестная сторона pending ордера")
            if self._position_mode == 2 and order_side in {1, 3} and order_side != (1 if intended == PositionSide.LONG else 3):
                state_error("MEXC: противоположный pending entry в one-way")
            if order_side == (1 if intended == PositionSide.LONG else 3):
                volume = number(row.get("vol"), "pending vol", positive=True)
                filled = number(row.get("dealVol"), "pending dealVol", nonnegative=True)
                if filled > volume:
                    state_error("MEXC: некорректный pending объём")
                pending_volume += volume - filled
        if self._position_mode == 2 and any(p.side != intended for p in positions):
            state_error("MEXC: entry one-way уменьшил бы/перевернул противоположную позицию")
        lev = integer(leverage if leverage is not None else self._config.default_leverage, "leverage", positive=True)
        if any(p.side == intended and p.leverage != lev for p in positions):
            state_error("MEXC: entry не должен менять leverage общей позиции")
        fees = self._fees(symbol)
        minimum = max(rules.min_leverage, integer(fees.get("minLeverage"), "account minLeverage", positive=True))
        maximum = min(rules.max_leverage, integer(fees.get("maxLeverage"), "account maxLeverage", positive=True))
        if not minimum <= lev <= maximum:
            raise ValueError("MEXC: leverage вне ограничений контракта/аккаунта")
        if any(row.get("symbol") == rules.exchange_symbol and integer(row.get("side"), "side") in {1, 3} and integer(row.get("leverage"), "order leverage", positive=True) != lev for row in pending):
            state_error("MEXC: entry не должен менять leverage pending ордера")
        contracts = self._contracts_from_base(rules, quantity, limit_order=type_name != "MARKET")
        total = contracts + pending_volume + sum((number(self._position_details[p.position_id]["holdVol"], "holdVol") for p in positions if p.side == intended), Decimal(0))
        if rules.leverage_tiers and not any(total <= number(t["maxVol"], "risk maxVol") and lev <= integer(t["maxLeverage"], "risk leverage") for t in rules.leverage_tiers):
            raise ValueError("MEXC: превышен riskLimitCustom")
        sl, tp = self._price(rules, sl), self._price(rules, tp)
        params = {"symbol": rules.exchange_symbol, "price": self._price(rules, price) if type_name != "MARKET" else "0",
            "vol": format(contracts, "f"), "leverage": lev, "side": 1 if intended == PositionSide.LONG else 3,
            "type": type_codes[type_name], "openType": 1, "externalOid": external_id,
            "positionMode": self._position_mode}
        if type_name != "MARKET" and params["price"] is None:
            raise ValueError("MEXC: LIMIT требует цену")
        if sl is not None:
            params.update(stopLossPrice=sl, lossTrend=self._trend)
        if tp is not None:
            params.update(takeProfitPrice=tp, profitTrend=self._trend)
        self.last_protection_confirmed = not (sl or tp)
        response = self._send("/api/v1/private/order/create", params, symbol=rules.symbol, client_id=external_id)
        oid = self._ack_id(response, rules.symbol, external_id, "entry")
        self._owned_orders.add((rules.symbol, oid))
        if sl or tp:
            # Поля entry ACK не доказывают существование активного полного TP/SL.
            try:
                targets = [p for p in self.get_positions().get(rules.symbol, []) if p.side == intended]
                self.last_protection_confirmed = len(targets) == 1 and self._protection_matches(targets[0], sl, tp)
            except (ExchangeStateUnavailableError, ExchangeAPIError):
                self.last_protection_confirmed = False
            if not self.last_protection_confirmed:
                warning("⚠️ MEXC: entry принят, полная защита ещё не подтверждена; требуется пауза/recovery")
        return oid

    def register_owned_position(self, symbol, position_id, quantity, *, position_side=None):
        """Wrapper передаёт доказанный snapshot журнала, не присваивает ручной REST объём."""
        canonical = self.normalize_symbol(symbol)
        self._exchange_symbol(canonical)
        pid = _id(position_id, "positionId")
        side = position_side if isinstance(position_side, PositionSide) else PositionSide(position_side)
        if side not in {PositionSide.LONG, PositionSide.SHORT}:
            raise ValueError("MEXC: доказанный snapshot требует LONG или SHORT")
        self._owned_positions[pid] = (canonical, side, number(quantity, "собственный объём", positive=True))

    def place_reduce_order(self, symbol, side, quantity, order_type=OrderType.LIMIT,
                           price=None, position_side=None, leverage=None,
                           client_order_id=None, position_id=None, reduce_only=True,
                           command_id=None):
        """Однократный raw reduce для coordinator; ACK не означает fill.

        До вызова wrapper восстанавливает proven snapshot через register_owned_position.
        Replay/UNKNOWN и обновление ownership после исполнения принадлежат durable journal.
        Количество задано в base asset и должно точно соответствовать contract/vol шагам.
        """
        if reduce_only is not True:
            raise ValueError("MEXC: place_reduce_order требует reduce_only=True")
        kind = getattr(order_type, "value", order_type)
        if kind not in {"LIMIT", "MARKET"}:
            raise UnsupportedCapabilityError("MEXC: raw reduce поддерживает только LIMIT/MARKET")
        side = side if isinstance(side, OrderSide) else OrderSide(side)
        pos_side = position_side if isinstance(position_side, PositionSide) else PositionSide(position_side)
        pid = _id(position_id, "positionId")
        if client_order_id is None:
            raise ValueError("MEXC: raw reduce требует стабильный client_order_id из журнала")
        external_id = _client_id(client_order_id)
        if command_id is not None and (isinstance(command_id, bool) or not isinstance(command_id, (str, int)) or not str(command_id).strip()):
            raise ValueError("MEXC: некорректный command_id")
        canonical = self.normalize_symbol(symbol)
        requested = number(quantity, "quantity", positive=True)
        claim = self._owned_positions.get(pid)
        if claim is None or claim[0] != canonical:
            state_error("MEXC: reduce требует доказанный собственный positionId от wrapper")
        positions = self._mutation_positions().get(canonical, [])
        targets = [p for p in positions if p.position_id == pid]
        if len(targets) != 1 or (self._position_mode == 2 and len(positions) != 1):
            state_error("MEXC: reduce positionId не найден однозначно в текущем режиме")
        target = targets[0]
        rules = self.get_instrument_rules(canonical)
        detail = self._position_details[pid]
        hold = number(detail["holdVol"], "holdVol", positive=True)
        frozen = number(detail["frozenVol"], "frozenVol", nonnegative=True)
        if integer(detail["openType"], "openType") != 1:
            state_error("MEXC: reduce разрешён только для фактической isolated-позиции")
        if target.side != claim[1] or hold * rules.contract_size != claim[2]:
            state_error("MEXC: ID/сторона/объём позиции изменены вне доказанного snapshot")
        if pos_side != target.side and not (self._position_mode == 2 and pos_side == PositionSide.BOTH):
            raise ValueError("MEXC: position_side не совпадает с закрываемой позицией")
        expected_side = OrderSide.SELL if target.side == PositionSide.LONG else OrderSide.BUY
        if side != expected_side:
            raise ValueError("MEXC: reduce side должен закрывать позицию; автоматическая смена стороны запрещена")
        if leverage is not None and integer(leverage, "leverage", positive=True) != target.leverage:
            state_error("MEXC: reduce не должен менять leverage общей позиции")
        if not rules.tradable:
            raise ExchangeAPIError("MEXC: контракт недоступен для isolated API reduce")
        contracts = self._contracts_from_base(rules, requested, limit_order=kind == "LIMIT")
        if contracts * rules.contract_size != requested:
            raise ValueError("MEXC: reduce quantity должна точно совпадать с шагом контрактов")
        if frozen > hold or contracts > hold - frozen or requested > claim[2]:
            raise ValueError("MEXC: reduce превышает доказанный доступный/незамороженный объём")
        if kind == "LIMIT":
            limit_price = self._price(rules, price)
            if limit_price is None or Decimal(limit_price) != number(price, "price", positive=True):
                raise ValueError("MEXC: reduce LIMIT требует положительную цену, точно кратную priceUnit")
        else:
            if price is not None:
                raise ValueError("MEXC: reduce MARKET не принимает limit price")
            limit_price = "0"
        params = {"symbol": rules.exchange_symbol, "price": limit_price,
            "vol": format(contracts, "f"), "side": 4 if target.side == PositionSide.LONG else 2,
            "type": 1 if kind == "LIMIT" else 5, "openType": 1,
            "positionId": integer(pid, "positionId"), "externalOid": external_id,
            "positionMode": self._position_mode}
        if self._position_mode == 2:
            params["reduceOnly"] = True
        try:
            response = self._send("/api/v1/private/order/create", params, symbol=canonical, client_id=external_id)
            if not isinstance(response.get("data"), dict):
                raise unknown("MEXC: reduce ACK не содержит объект ордера", symbol=canonical, client_order_id=external_id, operation="reduce")
            oid = self._ack_id(response, canonical, external_id, "reduce")
            ack = response["data"]
            try:
                mismatch = any(key in ack and ack[key] != params[key] for key in ("symbol", "externalOid"))
                for key in ("positionId", "side", "type", "openType", "positionMode"):
                    if key in ack:
                        mismatch |= integer(ack[key], f"ACK {key}", positive=True) != params[key]
                if "orderType" in ack:
                    mismatch |= integer(ack["orderType"], "ACK orderType", positive=True) != params["type"]
                for key in ("vol", "price"):
                    if key in ack:
                        mismatch |= number(ack[key], f"ACK {key}", nonnegative=True) != Decimal(params[key])
                if "reduceOnly" in ack:
                    mismatch |= not isinstance(ack["reduceOnly"], bool) or ack["reduceOnly"] is not params.get("reduceOnly", False)
            except ExchangeStateUnavailableError as exc:
                raise unknown("MEXC: некорректные поля reduce ACK", symbol=canonical, client_order_id=external_id, order_id=oid, operation="reduce") from exc
            if mismatch:
                raise unknown("MEXC: reduce ACK не соответствует запросу", symbol=canonical, client_order_id=external_id, order_id=oid, operation="reduce")
        except (UnknownOrderStateError, ExchangeAPIError, ExchangeStateUnavailableError) as exc:
            exc.command_id = command_id
            exc.position_id = pid
            exc.reduce_only = True
            exc.request_params = deepcopy(params)
            raise
        self._owned_orders.add((canonical, oid))
        return oid

    def _query_external(self, exchange_symbol, external_id):
        data = self._data(self._transport.request("GET", f"/api/v1/private/order/external/{exchange_symbol}/{_client_id(external_id)}", private=True, max_attempts=1))
        if data is None:
            state_error("MEXC: lookup вернул null; отсутствие ордера не подтверждено")
        order = self._parse_order(data)
        if order.symbol != self.normalize_symbol(exchange_symbol) or order.raw_data.get("externalOid") != external_id:
            state_error("MEXC: lookup вернул другой ордер")
        return order

    def set_leverage(self, symbol, leverage, position_side=PositionSide.BOTH):
        positions = self._mutation_positions().get(self.normalize_symbol(symbol), [])
        side = position_side if isinstance(position_side, PositionSide) else PositionSide(position_side)
        if side == PositionSide.BOTH and self._position_mode == 1:
            raise ValueError("MEXC: hedge leverage требует LONG или SHORT")
        candidates = [p for p in positions if side == PositionSide.BOTH or p.side == side]
        if len(candidates) > 1:
            state_error("MEXC: неоднозначная позиция для leverage")
        rules, fees = self.get_instrument_rules(symbol), self._fees(symbol)
        lev = integer(leverage, "leverage", positive=True)
        if not max(rules.min_leverage, integer(fees.get("minLeverage"), "minLeverage")) <= lev <= min(rules.max_leverage, integer(fees.get("maxLeverage"), "maxLeverage")):
            raise ValueError("MEXC: leverage вне ограничений")
        if candidates:
            params = {"positionId": integer(candidates[0].position_id, "positionId"), "leverage": lev}
        else:
            if side == PositionSide.BOTH:
                raise ValueError("MEXC: без позиции укажите LONG или SHORT")
            params = {"openType": 1, "leverage": lev, "symbol": rules.exchange_symbol, "positionType": 1 if side == PositionSide.LONG else 2}
        self._send("/api/v1/private/position/change_leverage", params, symbol=rules.symbol)
        try:
            if candidates:
                fresh = self.get_positions().get(rules.symbol, [])
                current = next((p for p in fresh if p.position_id == candidates[0].position_id), None)
                if current is None or current.leverage != lev:
                    state_error("MEXC: leverage не совпадает с readback позиции")
            else:
                rows = records(self._data(self._transport.request("GET", "/api/v1/private/position/leverage", {"symbol": rules.exchange_symbol}, private=True)), "leverage")
                matches = [row for row in rows if integer(row.get("openType"), "openType") == 1 and integer(row.get("positionType"), "positionType") == params["positionType"]]
                if len(matches) != 1 or integer(matches[0].get("leverage"), "leverage") != lev:
                    state_error("MEXC: leverage не совпадает с readback")
        except (ExchangeStateUnavailableError, ExchangeAPIError) as exc:
            raise unknown("MEXC: изменение leverage не подтверждено", symbol=rules.symbol, operation="leverage") from exc
        return True

    def close_position(self, symbol, position_id, percentage=1.0, client_order_id=None):
        fraction = number(percentage, "percentage", positive=True)
        if fraction > 1:
            raise ValueError("MEXC: percentage должен быть в (0,1]")
        pid, external_id = _id(position_id, "positionId"), _client_id(client_order_id)
        targets = [p for p in self._mutation_positions().get(self.normalize_symbol(symbol), []) if p.position_id == pid]
        if len(targets) != 1:
            state_error("MEXC: закрываемая позиция не найдена однозначно")
        target, rules = targets[0], self.get_instrument_rules(symbol)
        detail = dict(self._position_details[pid])
        before = number(detail["holdVol"], "holdVol", positive=True)
        contracts = _floor_step(before * fraction, rules.quantity_step)
        available = before - number(detail["frozenVol"], "frozenVol", nonnegative=True)
        if contracts < rules.min_quantity or contracts > available or contracts > rules.max_quantity:
            raise ValueError("MEXC: закрывающий объём вне min/max или уже заморожен")
        params = {"symbol": rules.exchange_symbol, "price": "0", "vol": format(contracts, "f"),
            "side": 4 if target.side == PositionSide.LONG else 2, "type": 5, "openType": 1,
            "positionId": integer(pid, "positionId"), "externalOid": external_id, "positionMode": self._position_mode}
        if self._position_mode == 2:
            params["reduceOnly"] = True
        response = self._send("/api/v1/private/order/create", params, symbol=rules.symbol, client_id=external_id)
        oid = self._ack_id(response, rules.symbol, external_id, "close")
        self._owned_orders.add((rules.symbol, oid))
        terminal_no_fill = False
        try:
            for attempt in range(3):
                order = self._query_external(rules.exchange_symbol, external_id)
                if order.order_id != oid or integer(order.raw_data.get("side"), "side") != params["side"] or number(order.raw_data.get("vol"), "vol") != contracts:
                    state_error("MEXC: reconciliation закрытия вернул несовпадающий ордер")
                filled = number(order.raw_data.get("dealVol"), "dealVol", nonnegative=True)
                if order.status in {OrderStatus.REJECTED, OrderStatus.CANCELED} and filled == 0:
                    terminal_no_fill = True
                    error("❌ MEXC: закрывающий ордер завершён без исполнения")
                    raise ExchangeAPIError("MEXC: закрывающий ордер завершён без исполнения")
                self.invalidate_cache()
                fresh = self.get_positions().get(rules.symbol, [])
                remaining = next((number(self._position_details[p.position_id]["holdVol"], "holdVol") for p in fresh if p.position_id == pid), Decimal(0))
                if order.raw_data.get("positionId") is not None and _id(order.raw_data["positionId"], "positionId") != pid:
                    state_error("MEXC: закрывающий ордер относится к другой позиции")
                if filled == contracts and order.status == OrderStatus.FILLED and before - remaining >= contracts:
                    return True
                if attempt < 2:
                    time.sleep(0.2)
        except ExchangeAPIError as exc:
            if terminal_no_fill:
                raise
            raise unknown("MEXC: lookup закрытия отклонён; исполнение неизвестно", symbol=rules.symbol, client_order_id=external_id, order_id=oid, operation="close") from exc
        except (ExchangeStateUnavailableError, UnsupportedCapabilityError) as exc:
            raise unknown("MEXC: исполнение закрытия не удалось сверить", symbol=rules.symbol, client_order_id=external_id, order_id=oid, operation="close") from exc
        finally:
            self.invalidate_cache()
        raise unknown("MEXC: закрытие pending/partial или уменьшение позиции не подтверждено", symbol=rules.symbol, client_order_id=external_id, order_id=oid, operation="close")

    def _pages(self, endpoint, params=None):
        result, seen = [], set()
        for page in range(1, 101):
            data = self._data(self._transport.request("GET", endpoint, {**(params or {}), "page_num": page, "page_size": 100}, private=True))
            if isinstance(data, dict):
                data = data.get("resultList", data.get("list"))
            rows = records(data, "страница ордеров")
            fingerprint = json.dumps(rows, sort_keys=True, default=str)
            if rows and fingerprint in seen:
                state_error("MEXC: API повторяет страницу; полнота ордеров неизвестна")
            seen.add(fingerprint)
            result.extend(rows)
            if len(rows) < 100:
                return result
        state_error("MEXC: превышен предел пагинации; полнота ордеров неизвестна")

    def get_grid_attached_protection(self, symbol, order, spec, *, previous=None):
        """Только GET: parent orderId доказывает связь, сходство цены её не доказывает."""
        self._validate_position_mode()
        rules = self.get_instrument_rules(symbol)
        previous = previous or {}
        rows = self._pages("/api/v1/private/stoporder/list/orders", {"symbol": rules.exchange_symbol})
        linked = [r for r in rows if str(r.get("orderId")) == str(order.order_id)]
        ids = [_id(r.get("id"), "attached stop id") for r in linked]
        if len(set(ids)) != len(ids) or len(linked) > 1:
            state_error("MEXC GRID: неоднозначные attached планы; объединение по цене запрещено")
        saved_ids = previous.get("plan_ids", [])
        if saved_ids and ids != saved_ids:
            state_error("MEXC GRID: attached ID исчез или заменён вне команды")
        filled = number(order.raw_data.get("dealVol"), "dealVol", nonnegative=True) * rules.contract_size
        if not linked:
            if filled or order.status in {OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                state_error("MEXC GRID: fill/terminal entry без точного attached readback")
            return {"state": "PENDING", "confirmed": False, "plan_ids": [], "closed_quantity": "0", "plans": []}
        row = linked[0]
        if (row.get("symbol") != rules.exchange_symbol
                or integer(row.get("positionType"), "positionType") != (1 if spec["owned_side"] == "LONG" else 2)
                or integer(row.get("errorCode"), "errorCode") != 0):
            state_error("MEXC GRID: attached план имеет другую идентичность/ошибку")
        for key, field, trend in (("sl", "stopLossPrice", "lossTrend"), ("tp", "takeProfitPrice", "profitTrend")):
            if spec.get(key) is not None and (number(row.get(field), field, positive=True) != Decimal(spec[key])
                    or integer(row.get(trend), trend) != self._trend):
                state_error("MEXC GRID: attached цена/reference изменены вне команды")
        state, finished = integer(row.get("state"), "state"), integer(row.get("isFinished"), "isFinished")
        if state not in {1, 2, 3, 4, 5} or finished not in {0, 1}:
            state_error("MEXC GRID: неизвестное состояние attached плана")
        closed = Decimal(0)
        pid = str(row.get("positionId", ""))
        parent_pid = str(order.raw_data.get("positionId", ""))
        if filled and (not pid or pid == "0" or pid != parent_pid):
            state_error("MEXC GRID: attached position ID не совпадает с entry")
        if previous.get("position_id") and pid != previous["position_id"]:
            state_error("MEXC GRID: attached position ID изменён")
        if previous.get("plans"):
            prior = previous["plans"][0]
            if state == 1 and number(previous.get("entry_filled_quantity", 0), "предыдущий entry fill") == filled and self._stop_fingerprint(prior) != self._stop_fingerprint(row):
                state_error("MEXC GRID: attached snapshot изменён без нового entry fill")
            if (integer(row.get("version"), "version") < integer(prior.get("version"), "предыдущая version")
                    or integer(row.get("updateTime"), "updateTime") < integer(prior.get("updateTime"), "предыдущее updateTime")):
                state_error("MEXC GRID: attached version/время уменьшились")
        if number(previous.get("closed_quantity", 0), "предыдущий trigger fill") > 0:
            prior = previous["plans"][0]
            if any(str(row.get(k)) != str(prior.get(k)) for k in ("state", "isFinished", "triggerSide", "placeOrderId", "realityVol", "vol", "version", "updateTime")):
                state_error("MEXC GRID: терминальный trigger snapshot изменён вне команды")
        if state == 3:
            if order.status not in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                state_error("MEXC GRID: защита сработала при оставшемся pending entry; дальнейшее покрытие неизвестно")
            if integer(row.get("triggerSide"), "triggerSide") not in {1, 2} or finished != 1:
                state_error("MEXC GRID: trigger не подтверждён терминальным планом")
            child_id = _id(row.get("placeOrderId"), "placeOrderId")
            child = self._parse_order(self._data(self._transport.request("GET", "/api/v1/private/order/get/" + child_id, private=True)))
            raw = child.raw_data
            if (str(child.order_id) != child_id or child.symbol != rules.symbol
                    or str(raw.get("positionId")) != pid
                    or integer(raw.get("side"), "side") != (4 if spec["owned_side"] == "LONG" else 2)
                    or child.order_type != OrderType.MARKET):
                state_error("MEXC GRID: trigger child не подтверждает exact-ID закрытие")
            closed = number(raw.get("dealVol"), "child dealVol", nonnegative=True) * rules.contract_size
            delegated = number(row.get("realityVol"), "realityVol", positive=True) * rules.contract_size
            if delegated != filled or number(raw.get("vol"), "child vol", positive=True) * rules.contract_size != delegated or not 0 <= closed <= filled:
                state_error("MEXC GRID: trigger child объём не совпадает с собственным fill")
            if child.status != OrderStatus.FILLED or closed != filled:
                state_error("MEXC GRID: trigger child pending/partial, остаток защиты неизвестен")
            receipt_state = "TRIGGERED"
        elif state == 1 and finished == 0:
            if not filled and order.status in {OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                state_error("MEXC GRID: отменённый entry оставил активный attached план")
            if not filled and number(row.get("vol"), "pending vol", nonnegative=True) != 0:
                state_error("MEXC GRID: attached объём есть без доказанного entry fill")
            if filled:
                from types import SimpleNamespace
                target = SimpleNamespace(position_id=pid, side=PositionSide(spec["owned_side"]))
                if integer(row.get("volType"), "volType") != 2:
                    state_error("MEXC GRID: volType attached плана не соответствует сохраняемому контракту сверки")
                # Проверка объёма именно этого parent, не всей агрегированной позиции.
                if not self._coverage(target, [row], spec.get("sl"), spec.get("tp"), needed_vol=filled / rules.contract_size):
                    state_error("MEXC GRID: attached SL/TP не покрывают точный partial fill")
            receipt_state = "ACTIVE" if filled else "PENDING"
        elif not filled and order.status in {OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED} and state == 2 and finished == 1:
            receipt_state = "CANCELED"
        else:
            state_error("MEXC GRID: attached защита отменена/неактивна при собственном fill")
        if closed < number(previous.get("closed_quantity", 0), "предыдущий close"):
            state_error("MEXC GRID: доказанный trigger fill уменьшился")
        return {"state": receipt_state, "confirmed": receipt_state == "ACTIVE", "plan_ids": ids,
                "position_id": pid if filled else None, "entry_filled_quantity": str(filled),
                "closed_quantity": str(closed), "plans": deepcopy(linked)}

    def _get_position_stop_orders(self, exchange_symbol, position_id):
        rows = self._pages("/api/v1/private/stoporder/list/orders", {"symbol": exchange_symbol, "is_finished": 0})
        result = []
        for item in rows:
            if str(item.get("positionId")) != str(position_id):
                continue
            if item.get("symbol") != exchange_symbol:
                state_error("MEXC: несовпадающий символ защиты")
            _id(item.get("id"), "stop id")
            state = integer(item.get("state"), "stop state")
            finished = integer(item.get("isFinished"), "isFinished")
            if state not in {1, 2, 3, 4, 5} or finished not in {0, 1}:
                state_error("MEXC: неизвестный статус защиты")
            if state == 1 and finished == 0:
                result.append(item)
        return result

    def _stop_fingerprint(self, item):
        keys = ("id", "symbol", "positionId", "positionType", "vol", "volType", "profitLossVolType",
                "stopLossVol", "takeProfitVol", "lossTrend", "profitTrend", "stopLossPrice", "takeProfitPrice",
                "stopLossType", "takeProfitType", "stopLossReverse", "takeProfitReverse", "priceProtect", "version", "updateTime")
        return tuple((key, str(item.get(key))) for key in keys)

    def _coverage(self, target, rows, sl, tp, *, needed_vol=None):
        needed = number(self._position_details[target.position_id]["holdVol"] if needed_vol is None else needed_vol, "holdVol", positive=True)
        totals = {"stopLoss": Decimal(0), "takeProfit": Decimal(0)}
        for row in rows:
            if (integer(row.get("errorCode"), "stop errorCode") != 0
                or integer(row.get("volType"), "volType") not in {1, 2}
                or integer(row.get("priceProtect"), "priceProtect") != 0):
                state_error("MEXC: некорректное состояние/тип защитного ордера")
            if integer(row.get("positionType"), "positionType") != (1 if target.side == PositionSide.LONG else 2):
                state_error("MEXC: защита другой стороны позиции")
            if row.get("profitLossVolType") not in {"SAME", "SEPARATE"}:
                state_error("MEXC: неизвестный тип объёма защиты")
            for prefix, price in (("stopLoss", sl), ("takeProfit", tp)):
                if price is None:
                    continue
                trend = "lossTrend" if prefix == "stopLoss" else "profitTrend"
                if (number(row.get(prefix + "Price", 0), prefix + "Price", nonnegative=True) == Decimal(price)
                    and integer(row.get(trend), trend) == self._trend
                    and integer(row.get(prefix + "Type"), prefix + "Type") == 0
                    and integer(row.get(prefix + "Reverse"), prefix + "Reverse") == 2):
                    vol = row.get(prefix + "Vol") if row["profitLossVolType"] == "SEPARATE" else row.get("vol")
                    totals[prefix] += number(vol, "защитный vol", positive=True)
        return all(totals[prefix] == needed for prefix, price in (("stopLoss", sl), ("takeProfit", tp)) if price is not None)

    def _protection_matches(self, target, sl, tp):
        rows = self._get_position_stop_orders(self._exchange_symbol(target.symbol), target.position_id)
        return self._coverage(target, rows, sl, tp)

    def set_sl_tp(self, symbol, position_side, sl=None, tp=None, quantity=None):
        self.last_protection_confirmed = False
        if sl is None and tp is None:
            raise ValueError("MEXC: требуется SL или TP")
        side = position_side if isinstance(position_side, PositionSide) else PositionSide(position_side)
        positions = self._mutation_positions().get(self.normalize_symbol(symbol), [])
        targets = [p for p in positions if p.side == side or (side == PositionSide.BOTH and self._position_mode == 2)]
        if len(targets) != 1:
            state_error("MEXC: позиция защиты не найдена однозначно")
        target, rules = targets[0], self.get_instrument_rules(symbol)
        contracts = number(self._position_details[target.position_id]["holdVol"], "holdVol", positive=True)
        if quantity is not None and self._contracts_from_base(rules, quantity) != contracts:
            raise ValueError("MEXC: требуется защита полного объёма позиции")
        sl, tp = self._price(rules, sl), self._price(rules, tp)
        rows = self._get_position_stop_orders(rules.exchange_symbol, target.position_id)
        if self._coverage(target, rows, sl, tp):
            self.last_protection_confirmed = True
            return True
        if rows:
            if len(rows) != 1:
                state_error("MEXC: неоднозначная защита; ручные ордера не изменяются")
            row = rows[0]
            sid = _id(row["id"])
            if self._owned_stop_orders.get(sid) != self._stop_fingerprint(row):
                state_error("MEXC: защита ручная либо изменена извне; изменение запрещено")
            if row.get("profitLossVolType") != "SAME" or number(row.get("vol"), "vol") != contracts:
                state_error("MEXC: изменение цены не исправляет неполное покрытие защиты")
            previous_sl = number(row.get("stopLossPrice", 0), "stopLossPrice", nonnegative=True)
            previous_tp = number(row.get("takeProfitPrice", 0), "takeProfitPrice", nonnegative=True)
            sl = sl if sl is not None else self._price(rules, previous_sl) if previous_sl else None
            tp = tp if tp is not None else self._price(rules, previous_tp) if previous_tp else None
            self._owned_stop_orders.pop(sid)
            self._owned_stop_snapshots.pop(sid, None)
            self._send("/api/v1/private/stoporder/change_plan_price", {"stopPlanOrderId": integer(sid, "stop id"),
                "stopLossPrice": sl, "takeProfitPrice": tp}, symbol=rules.symbol)
        else:
            if number(self._position_details[target.position_id]["frozenVol"], "frozenVol", nonnegative=True) != 0:
                state_error("MEXC: нельзя создать полную защиту при замороженном закрывающем объёме")
            params = {"lossTrend": self._trend, "profitTrend": self._trend, "positionId": integer(target.position_id, "positionId"),
                "vol": format(contracts, "f"), "stopLossPrice": sl, "takeProfitPrice": tp, "priceProtect": 0,
                "profitLossVolType": "SAME", "volType": 2, "takeProfitType": 0, "takeProfitOrderPrice": "0",
                "stopLossType": 0, "stopLossOrderPrice": "0", "takeProfitReverse": 2, "stopLossReverse": 2}
            response = self._send("/api/v1/private/stoporder/place", params, symbol=rules.symbol)
            sid = self._ack_id(response, rules.symbol, None, "protection")
        try:
            for attempt in range(3):
                self.invalidate_cache()
                fresh = self.get_positions().get(rules.symbol, [])
                current = next((p for p in fresh if p.position_id == target.position_id), None)
                plans = self._get_position_stop_orders(rules.exchange_symbol, target.position_id)
                owned = next((r for r in plans if _id(r["id"]) == sid), None)
                if current is not None and owned is not None and self._coverage(current, [owned], sl, tp):
                    self._owned_stop_orders[sid] = self._stop_fingerprint(owned)
                    self._owned_stop_snapshots[sid] = deepcopy(owned)
                    self.last_protection_confirmed = True
                    return True
                if attempt < 2:
                    time.sleep(0.2)
        except (ExchangeStateUnavailableError, ExchangeAPIError) as exc:
            raise unknown("MEXC: TP/SL принят, readback недоступен", symbol=rules.symbol, order_id=sid, operation="protection") from exc
        raise unknown("MEXC: полная защита после мутации не подтверждена", symbol=rules.symbol, order_id=sid, operation="protection")

    def cancel_order(self, symbol, order_id):
        self._mutation_positions()
        canonical, oid = self.normalize_symbol(symbol), _id(order_id, "orderId")
        if (canonical, oid) not in self._owned_orders:
            state_error("MEXC: отмена ручного/непринадлежащего клиенту ордера запрещена")
        response = self._send("/api/v1/private/order/cancel", {"orderIds": [integer(oid, "orderId")]}, symbol=canonical)
        definite_rejection = False
        try:
            rows = records(self._data(response), "результаты отмены")
            if len(rows) != 1 or _id(rows[0].get("orderId")) != oid:
                state_error("MEXC: отмена вернула несовпадающий orderId")
            code = integer(rows[0].get("errorCode"), "errorCode")
            if code != 0:
                if ambiguous_code(code):
                    raise unknown("MEXC: отмена вернула неоднозначный код", symbol=canonical, order_id=oid, operation="cancel")
                definite_rejection = True
                error(f"❌ MEXC: отмена отклонена, код {code}")
                raise ExchangeAPIError("MEXC: отмена отклонена", code=code)
            order = self._parse_order(self._data(self._transport.request("GET", f"/api/v1/private/order/get/{oid}", private=True)))
            if order.order_id != oid or order.symbol != canonical or order.status != OrderStatus.CANCELED:
                state_error("MEXC: конечная отмена не подтверждена")
        except ExchangeAPIError as exc:
            if definite_rejection:
                raise
            raise unknown("MEXC: readback отмены отклонён", symbol=canonical, order_id=oid, operation="cancel") from exc
        except (ExchangeStateUnavailableError, UnsupportedCapabilityError) as exc:
            raise unknown("MEXC: результат отмены неизвестен", symbol=canonical, order_id=oid, operation="cancel") from exc
        self._owned_orders.discard((canonical, oid))
        return True

    def restore_order_ownership(self, symbol, order_id):
        """Только для восстановления доказанной принадлежности из журнала main."""
        self._owned_orders.add((self.normalize_symbol(symbol), _id(order_id)))

    def restore_protection_ownership(self, snapshot):
        """Сохраняет исходный fingerprint, а не присваивает текущую ручную защиту."""
        snapshot = mapping(snapshot, "journal stop snapshot")
        sid = _id(snapshot.get("id"))
        self._owned_stop_orders[sid] = self._stop_fingerprint(snapshot)
        self._owned_stop_snapshots[sid] = deepcopy(snapshot)

    def get_owned_protection_snapshots(self):
        """Подтверждённые снимки для сохранения принадлежности в журнале main."""
        return deepcopy(list(self._owned_stop_snapshots.values()))

    def cancel_all_orders(self, symbol):
        canonical = self.normalize_symbol(symbol)
        for order in self.get_open_orders(canonical):
            if (canonical, order.order_id) in self._owned_orders:
                self.cancel_order(canonical, order.order_id)
        return True

    def get_open_orders(self, symbol=None) -> OrdersList:
        orders = [self._parse_order(item) for item in self._pages("/api/v1/private/order/list/open_orders")]
        canonical = self.normalize_symbol(symbol) if symbol else None
        return [o for o in orders if canonical is None or o.symbol == canonical]

    def get_recent_orders(self, symbol, limit=10):
        if not 1 <= integer(limit, "limit") <= 100:
            raise ValueError("MEXC: limit от 1 до 100")
        data = self._data(self._transport.request("GET", "/api/v1/private/order/list/history_orders", {
            "symbol": self._exchange_symbol(symbol), "page_num": 1, "page_size": limit}, private=True))
        if isinstance(data, dict):
            data = data.get("resultList", data.get("list"))
        return [self._parse_order(item) for item in records(data, "история")][:limit]

    def _parse_order(self, item):
        item = mapping(item, "ордер")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.endswith("_USDT"):
            state_error("MEXC: отсутствует корректный symbol ордера")
        rules = self.get_instrument_rules(symbol)
        side_id = integer(item.get("side"), "side")
        state, type_id = integer(item.get("state"), "state"), integer(item.get("orderType", item.get("type")), "orderType")
        if side_id not in {1, 2, 3, 4} or state not in _ORDER_STATUS or type_id not in _ORDER_TYPES:
            state_error("MEXC: неизвестный side/state/type ордера")
        type_name = "LIMIT_MAKER" if type_id == 2 else _ORDER_TYPES[type_id]
        try:
            order_type = OrderType(type_name)
        except ValueError:
            raise UnsupportedCapabilityError(f"MEXC: DTO ещё не поддерживает {type_name}")
        vol = number(item.get("vol"), "vol", positive=True)
        filled = number(item.get("dealVol"), "dealVol", nonnegative=True)
        if filled > vol or (state == 3 and filled != vol):
            state_error("MEXC: противоречивый объём исполнения")
        status = _ORDER_STATUS[state]
        if state in {1, 2} and filled > 0:
            status = OrderStatus.PARTIALLY_FILLED
        return Order(order_id=_id(item.get("orderId")), symbol=rules.symbol,
            side=OrderSide.BUY if side_id in {1, 2} else OrderSide.SELL, order_type=order_type, status=status,
            price=float(number(item.get("price", 0), "price", nonnegative=True)), quantity=float(vol * rules.contract_size),
            filled_quantity=float(filled * rules.contract_size), average_price=float(number(item.get("dealAvgPrice", 0), "dealAvgPrice", positive=filled > 0, nonnegative=True)),
            commission=float(number(item.get("takerFee", 0), "takerFee") + number(item.get("makerFee", 0), "makerFee")),
            realized_pnl=float(number(item.get("profit", 0), "profit")), stop_price=float(number(item.get("stopLossPrice", 0), "stopLossPrice", nonnegative=True)) or None,
            position_side=PositionSide.LONG if side_id in {1, 4} else PositionSide.SHORT,
            created_at=_utc_datetime(item.get("createTime")), updated_at=_utc_datetime(item.get("updateTime")), raw_data=dict(item))

    def check_prerequisites(self):
        try:
            self._mutation_positions()
            return True
        except (ExchangeAPIError, ExchangeStateUnavailableError) as exc:
            error(f"❌ MEXC: проверка предпосылок не пройдена: {exc}")
            return False

    def invalidate_cache(self, cache_type=None):
        if cache_type is None or cache_type == "positions":
            self._positions_cache, self._positions_cache_time = None, 0
        if cache_type is None or cache_type == "balance":
            self._balance_cache, self._balance_cache_time = None, 0
