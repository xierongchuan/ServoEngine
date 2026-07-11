"""
BingX exchange client implementation.
Refactored version following SOLID principles.

Это новая реализация BingX клиента, которая:
1. Наследует ExchangeClient ABC
2. Использует DTO модели
3. Использует конфигурацию из config/
4. Инкапсулирует кэширование внутри экземпляра
"""

import time
import hmac
import hashlib
import json
import os
import requests
import threading
from urllib.parse import urlencode
from typing import Optional, List, Dict

from ..exchange_client import ExchangeClient
from ..errors import ExchangeStateUnavailableError
from ..config.base import ExchangeConfig
from ..dto.models import (
    Position,
    Order,
    Balance,
    Kline,
    Ticker,
    OrderBook,
    CommissionRate,
    FundingRate,
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    PositionsDict,
    OrdersList,
    KlinesList,
)
import logging

logger = logging.getLogger(__name__)


_KLINE_INTERVAL_ALIASES = {
    "MINUTE_1": "1m",
    "MINUTE_3": "3m",
    "MINUTE_5": "5m",
    "MINUTE_15": "15m",
    "MINUTE_30": "30m",
    "HOUR_1": "1h",
    "HOUR_2": "2h",
    "HOUR_4": "4h",
    "HOUR_6": "6h",
    "HOUR_8": "8h",
    "HOUR_12": "12h",
    "DAY_1": "1d",
    "DAY_3": "3d",
    "WEEK_1": "1w",
    "MONTH_1": "1M",
}


def _preview_payload(value, limit: int = 300) -> str:
    """Return a compact log preview without dumping large market responses."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def info(msg):
    logger.info(msg)

def error(msg):
    logger.error(msg)

def warning(msg):
    logger.warning(msg)


class BingXClient(ExchangeClient):
    """
    BingX API Client - новая реализация.

    Особенности:
    - Инкапсулированное кэширование (не class-level)
    - Использование DTO моделей
    - Конфигурация через ExchangeConfig
    """

    EXCHANGE_NAME: str = "bingx"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """
        Инициализация клиента.

        Args:
            config: Конфигурация биржи. Если None - создаётся автоматически.
        """
        # Загрузка конфигурации
        if config is None:
            from ..config import create_config
            from src.config import MODE
            config = create_config("bingx", is_demo=(MODE == "demo"))

        self._config = config

        # API ключи
        self.api_key = config.api_key
        self.secret_key = config.secret_key
        self.base_url = config.base_url
        self.market_base_url = os.getenv("BINGX_MARKET_API_URL", "https://open-api.bingx.com")

        # Thread lock for cache operations
        self._cache_lock = threading.RLock()

        # Инкапсулированное кэширование (instance-level)
        self._positions_cache: Optional[PositionsDict] = None
        self._positions_cache_time: float = 0
        self._positions_cache_ttl: float = config.positions_cache_ttl

        self._balance_cache: Optional[Balance] = None
        self._balance_cache_time: float = 0
        self._balance_cache_ttl: float = config.balance_cache_ttl

        self._commission_cache: Dict[str, CommissionRate] = {}
        self._commission_cache_time: Dict[str, float] = {}

        self._funding_cache: Dict[str, FundingRate] = {}
        self._funding_cache_time: Dict[str, float] = {}

        self._orders_cache: Dict[str, OrdersList] = {}
        self._orders_cache_time: Dict[str, float] = {}

        if not self.api_key or not self.secret_key:
            warning("⚠️ BingX API keys not configured! Private endpoints will fail.")

    # =========================================================================
    # ExchangeClient ABC Implementation - Market Data
    # =========================================================================

    def get_klines(self, symbol: str, interval: str = "5m", limit: int = 288) -> KlinesList:
        """
        Получить исторические данные свечей.
        Сначала пробует WebSocket кэш, потом REST API.
        """
        # 1. Try WebSocket shared cache first
        try:
            from src.exchanges.bingx_ws_data_provider import is_cache_ready, get_klines_from_shared_cache

            if is_cache_ready(symbol):
                # Get all available data from cache (ignore limit, take all)
                cached = get_klines_from_shared_cache(symbol, 10000)  # Get max available
                if len(cached) >= 100:  # At least 100 candles available
                    # Convert to KlinesList format
                    from src.exchanges.dto.models import Kline
                    klines = []
                    for c in cached:
                        klines.append(Kline(
                            timestamp=c.get("snapshotTimeUTC", ""),
                            open=float(c.get("openPrice", 0)),
                            high=float(c.get("highPrice", 0)),
                            low=float(c.get("lowPrice", 0)),
                            close=float(c.get("closePrice", 0)),
                            volume=float(c.get("volume", 0))
                        ))
                    info(f"📊 [WS CACHE] Using {len(klines)} candles for {symbol}")
                    return klines
        except ImportError:
            pass  # WS provider not available
        except Exception as e:
            warning(f"⚠️ WS cache error for {symbol}: {e}")

        # 2. Fallback to REST API
        return self._fetch_klines_rest(symbol, interval, limit)

    def get_kline_data(self, symbol: str, interval: str = "5m", limit: int = 288) -> List[Dict]:
        """Legacy adapter: возвращает свечи в dict-формате для старых модулей."""
        klines = self.get_klines(symbol, interval=interval, limit=limit)
        result: List[Dict] = []
        for kline in klines:
            result.append({
                "snapshotTimeUTC": str(kline.timestamp),
                "openPrice": float(kline.open),
                "highPrice": float(kline.high),
                "lowPrice": float(kline.low),
                "closePrice": float(kline.close),
                "volume": float(kline.volume),
            })
        return result

    def _fetch_klines_rest(self, symbol: str, interval: str, limit: int) -> KlinesList:
        """REST API fallback для получения свечей."""
        formatted_symbol = self._format_symbol(symbol)

        # Map internal verbose constants to the compact interval format accepted
        # by BingX swap v3 klines.
        bingx_interval = _KLINE_INTERVAL_ALIASES.get(
            interval,
            self._config.supported_intervals.get(interval, interval),
        )

        market_url = f"{self.market_base_url}/openApi/swap/v3/quote/klines"

        params = {
            "symbol": formatted_symbol,
            "interval": bingx_interval,
            "limit": limit
        }

        max_retries = 3
        retry_delay = 1
        data = None
        status_code = None

        for attempt in range(max_retries):
            response = None
            try:
                response = requests.get(market_url, params=params, timeout=6)
                status_code = response.status_code

                if response.status_code == 429:
                    retry_after_header = response.headers.get("Retry-After")
                    try:
                        retry_after = int(retry_after_header or retry_delay)
                    except (TypeError, ValueError):
                        retry_after = retry_delay
                    warning(
                        f"⚠️ BingX klines rate limited: symbol={formatted_symbol}, "
                        f"interval={bingx_interval}, limit={limit}, retry_after={retry_after}s"
                    )
                    time.sleep(retry_after)
                    retry_delay = min(retry_delay * 2, 30)
                    continue

                response.raise_for_status()
                data = response.json()
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                warning(
                    f"⚠️ BingX klines network error "
                    f"(attempt {attempt + 1}/{max_retries}): endpoint={market_url}, "
                    f"symbol={formatted_symbol}, interval={bingx_interval}, limit={limit}, error={e}"
                )
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    error(f"❌ BingX klines request failed after {max_retries} attempts: {e}")
                    return []
            except ValueError as e:
                body_preview = getattr(response, "text", "")
                error(
                    f"❌ BingX klines returned invalid JSON: endpoint={market_url}, "
                    f"status={status_code}, symbol={formatted_symbol}, interval={bingx_interval}, "
                    f"limit={limit}, body={body_preview[:300]}, error={e}"
                )
                return []
            except Exception as e:
                body_preview = getattr(response, "text", "")
                error(
                    f"❌ BingX klines request failed: endpoint={market_url}, status={status_code}, "
                    f"symbol={formatted_symbol}, interval={bingx_interval}, limit={limit}, "
                    f"body={body_preview[:300]}, error={e}"
                )
                return []

        if data and data.get("code") == 0:
            klines_data = data.get("data", [])
            if not isinstance(klines_data, list):
                warning(
                    f"⚠️ BingX klines data has unexpected type: endpoint={market_url}, "
                    f"status={status_code}, symbol={formatted_symbol}, interval={bingx_interval}, "
                    f"limit={limit}, data_type={type(klines_data).__name__}, "
                    f"data={_preview_payload(klines_data)}"
                )
                return []

            if not klines_data:
                warning(
                    f"⚠️ BingX klines returned empty data: endpoint={market_url}, "
                    f"status={status_code}, symbol={formatted_symbol}, interval={bingx_interval}, "
                    f"limit={limit}, msg={data.get('msg', '')!r}"
                )
                return []

            result: KlinesList = []
            skipped = 0

            for k in klines_data:
                if isinstance(k, dict):
                    ts_ms = k.get("time")
                    close_price = float(k.get("close", 0))
                    open_price = float(k.get("open", 0))
                    high_price = float(k.get("high", 0))
                    low_price = float(k.get("low", 0))
                    volume = float(k.get("volume", 0))
                elif isinstance(k, list):
                    ts_ms = k[0]
                    open_price = float(k[1])
                    high_price = float(k[2])
                    low_price = float(k[3])
                    close_price = float(k[4])
                    volume = float(k[5])
                else:
                    skipped += 1
                    continue

                # Skip if timestamp is missing
                if ts_ms is None:
                    skipped += 1
                    continue

                # Создаем DTO объект
                result.append(Kline(
                    timestamp=time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(int(ts_ms) / 1000)),
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                ))

            result.sort(key=lambda x: x.timestamp)
            if result:
                info(
                    f"✅ BingX klines OK: symbol={formatted_symbol}, interval={bingx_interval}, "
                    f"requested={limit}, received={len(result)}, skipped={skipped}, "
                    f"first={result[0].timestamp}, last={result[-1].timestamp}"
                )
            else:
                warning(
                    f"⚠️ BingX klines parsed to empty result: endpoint={market_url}, "
                    f"status={status_code}, symbol={formatted_symbol}, interval={bingx_interval}, "
                    f"limit={limit}, raw_count={len(klines_data)}, skipped={skipped}"
                )
            return result

        if data is not None:
            warning(
                f"⚠️ BingX klines API error: endpoint={market_url}, status={status_code}, "
                f"symbol={formatted_symbol}, interval={bingx_interval}, limit={limit}, "
                f"code={data.get('code')}, msg={data.get('msg', '')!r}, "
                f"data={_preview_payload(data.get('data'))}"
            )
        else:
            warning(
                f"⚠️ BingX klines returned no response data: endpoint={market_url}, "
                f"symbol={formatted_symbol}, interval={bingx_interval}, limit={limit}"
            )
        return []

    def _parse_ws_klines(self, klines: List[Dict]) -> KlinesList:
        """Преобразовать данные из WebSocket в DTO"""
        result: KlinesList = []
        for k in klines:
            result.append(Kline(
                timestamp=k.get("timestamp", ""),
                open=k.get("open", 0),
                high=k.get("high", 0),
                low=k.get("low", 0),
                close=k.get("close", 0),
                volume=k.get("volume", 0),
            ))
        return result

    def get_ticker(self, symbol: str) -> Ticker:
        """Получить текущий тикер."""
        formatted_symbol = self._format_symbol(symbol)

        market_url = f"{self.market_base_url}/openApi/swap/v2/quote/ticker"
        params = {"symbol": formatted_symbol}

        try:
            response = requests.get(market_url, params=params, timeout=6)
            response.raise_for_status()
            data = response.json()

            if data and data.get("code") == 0:
                ticker_data = data.get("data", {})
                return Ticker(
                    symbol=symbol,
                    last_price=float(ticker_data.get("lastPrice", 0) or 0),
                    bid_price=float(ticker_data.get("bestBidPrice", 0) or 0),
                    ask_price=float(ticker_data.get("bestAskPrice", 0) or 0),
                    volume_24h=float(ticker_data.get("volume", 0) or 0),
                    quote_volume_24h=float(ticker_data.get("quoteVolume", 0) or 0),
                )
        except Exception as e:
            error(f"❌ Failed to get ticker: {e}")

        # Return empty ticker on error
        return Ticker(
            symbol=symbol,
            last_price=0.0,
            bid_price=0.0,
            ask_price=0.0,
            volume_24h=0.0,
        )

    def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        """Получить стакан заявок."""
        formatted_symbol = self._format_symbol(symbol)

        market_url = f"{self.market_base_url}/openApi/swap/v2/quote/depth"
        params = {
            "symbol": formatted_symbol,
            "limit": min(limit, 100)
        }

        try:
            response = requests.get(market_url, params=params, timeout=6)
            response.raise_for_status()
            data = response.json()

            if data and data.get("code") == 0:
                depth_data = data.get("data", {})
                return OrderBook(
                    symbol=symbol,
                    bids=[[float(b[0]), float(b[1])] for b in depth_data.get("bids", [])],
                    asks=[[float(a[0]), float(a[1])] for a in depth_data.get("asks", [])],
                )
        except Exception as e:
            error(f"❌ Failed to get order book: {e}")

        return OrderBook(symbol=symbol, bids=[], asks=[])

    # =========================================================================
    # ExchangeClient ABC Implementation - Account
    # =========================================================================

    def get_balance(self) -> Balance:
        """Получить баланс Perpetual Futures."""
        # В DEMO режиме (VST) получаем РЕАЛЬНЫЙ баланс с демо-биржи!
        # Не используем захардкоженное значение 10000 - это реальные данные

        def _first_positive_float(payload: dict, keys: List[str]) -> float:
            for key in keys:
                value = payload.get(key)
                if value is None:
                    continue
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    return parsed
            return 0.0

        def _float_value(payload: dict, key: str) -> float:
            try:
                return float(payload.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        with self._cache_lock:
            now = time.time()
            # Не используем кэш для баланса - получаем актуальные данные
            # Кэш может привести к ошибкам при торговле

            endpoint = "/openApi/swap/v2/user/balance"
            response = self._make_request("get", endpoint)

            if response and response.get("code") == 0:
                data = response.get("data", {})

                if isinstance(data, dict):
                    balance_data = data.get("balance", data)
                else:
                    balance_data = {}

                total_balance = _first_positive_float(
                    balance_data,
                    [
                        "equity",
                        "balance",
                        "walletBalance",
                        "totalBalance",
                        "totalWalletBalance",
                        "marginBalance",
                    ],
                )
                available_balance = _first_positive_float(
                    balance_data,
                    [
                        "availableBalance",
                        "availableMargin",
                        "available",
                        "free",
                        "balance",
                    ],
                )
                wallet_balance = _float_value(balance_data, "walletBalance")
                base_balance = _float_value(balance_data, "balance")
                unrealized_pnl = _float_value(balance_data, "unrealizedProfit")
                if unrealized_pnl == 0 and wallet_balance and base_balance:
                    unrealized_pnl = wallet_balance - base_balance

                balance = Balance(
                    total_balance=total_balance,
                    available_balance=available_balance,
                    unrealized_pnl=unrealized_pnl,
                    locked_balance=max(total_balance - available_balance, 0.0),
                    asset=str(balance_data.get("asset") or "USDT"),
                )

                # Update cache
                self._balance_cache = balance
                self._balance_cache_time = now
                return balance

            code = response.get("code") if isinstance(response, dict) else None
            raise ExchangeStateUnavailableError(
                f"BingX balance недоступен или невалиден (code={code})"
            )

    def get_commission_rate(self, symbol: str) -> Optional[CommissionRate]:
        """Получить ставки комиссий."""
        with self._cache_lock:
            now = time.time()

            # Check cache
            if symbol in self._commission_cache:
                cached_time = self._commission_cache_time.get(symbol, 0)
                if now - cached_time < 3600:  # 1 hour TTL
                    return self._commission_cache[symbol]

            formatted_symbol = self._format_symbol(symbol)

        endpoint = "/openApi/swap/v2/user/commissionRate"

        try:
            response = self._make_request("get", endpoint, {"symbol": formatted_symbol})

            if response and response.get("code") == 0:
                data = response.get("data", {})
                maker_raw = float(data.get("makerCommissionRate", 0.0002))
                taker_raw = float(data.get("takerCommissionRate", 0.0005))

                result = CommissionRate(
                    maker=round(maker_raw * 100, 4),
                    taker=round(taker_raw * 100, 4),
                )

                with self._cache_lock:
                    self._commission_cache[symbol] = result
                    self._commission_cache_time[symbol] = now
                return result
        except Exception as e:
            warning(f"⚠️ Failed to get commission rate for {symbol}: {e}")

        return None

    def get_funding_rate(self, symbol: str) -> Optional[FundingRate]:
        """Получить ставку финансирования."""
        with self._cache_lock:
            now = time.time()

            # Check cache
            if symbol in self._funding_cache:
                cached_time = self._funding_cache_time.get(symbol, 0)
                if now - cached_time < 300:  # 5 min TTL
                    return self._funding_cache[symbol]

            formatted_symbol = self._format_symbol(symbol)

        market_url = f"{self.market_base_url}/openApi/swap/v2/quote/premiumIndex"

        try:
            response = requests.get(market_url, params={"symbol": formatted_symbol}, timeout=6)
            response.raise_for_status()
            data = response.json()

            if data and data.get("code") == 0:
                index_data = data.get("data", {})
                funding_rate = float(index_data.get("lastFundingRate", 0))
                next_time_ms = index_data.get("nextFundingTime", 0)

                result = FundingRate(
                    funding_rate=funding_rate,
                    funding_rate_pct=round(funding_rate * 100, 4),
                    next_funding_time=time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(int(next_time_ms) / 1000)) if next_time_ms else None,
                )

                with self._cache_lock:
                    self._funding_cache[symbol] = result
                    self._funding_cache_time[symbol] = now
                return result
        except Exception as e:
            warning(f"⚠️ Failed to get funding rate for {symbol}: {e}")

        return None

    # =========================================================================
    # ExchangeClient ABC Implementation - Positions
    # =========================================================================

    def get_positions(self) -> PositionsDict:
        """Получить открытые позиции (с кэшированием)."""
        with self._cache_lock:
            now = time.time()

            # Check cache
            if (self._positions_cache is not None and
                now - self._positions_cache_time < self._positions_cache_ttl):
                return self._positions_cache

            endpoint = "/openApi/swap/v2/user/positions"

        response = self._make_request("get", endpoint)

        if not response or response.get("code") != 0:
            code = response.get("code") if isinstance(response, dict) else None
            raise ExchangeStateUnavailableError(
                f"BingX positions недоступны или невалидны (code={code})"
            )
        data = response.get("data", [])
        if not isinstance(data, list):
            raise ExchangeStateUnavailableError("BingX positions вернул не список")

        positions: PositionsDict = {}
        for pos in data:
            size = float(pos.get("positionAmt", 0))
            if size == 0:
                continue

            # Normalize symbol
            symbol = pos.get("symbol", "").replace("-", "")

            if symbol not in positions:
                positions[symbol] = []

            # Determine side
            pos_side = pos.get("positionSide", "").upper()
            if pos_side == "SHORT":
                side = PositionSide.SHORT
            elif pos_side == "LONG":
                side = PositionSide.LONG
            else:
                side = PositionSide.LONG if size > 0 else PositionSide.SHORT

            positions[symbol].append(Position(
                symbol=symbol,
                side=side,
                size=abs(size),
                entry_price=float(pos.get("avgPrice", 0)),
                unrealized_pnl=float(pos.get("unrealizedProfit", 0)),
                leverage=int(float(pos.get("leverage", 0))) or None,
                position_id=pos.get("positionId", ""),
                mark_price=float(pos.get("markPrice", 0)) or None,
            ))

        with self._cache_lock:
            self._positions_cache = positions
            self._positions_cache_time = now

        return positions

    def close_position(self, symbol: str, position_id: str, percentage: float = 1.0) -> bool:
        """Закрыть позицию."""
        positions = self.get_positions()

        # Normalize symbol
        lookup_symbol = symbol.replace("-", "").replace("/", "")

        target_pos = None
        if lookup_symbol in positions:
            for p in positions[lookup_symbol]:
                if str(p.position_id) == str(position_id):
                    target_pos = p
                    break

        if not target_pos:
            error(f"❌ Position {position_id} not found")
            return False

        formatted_symbol = self._format_symbol(symbol)

        # Determine side for closing
        if target_pos.side == PositionSide.LONG:
            side = "SELL"
            position_side = "LONG"
        else:
            side = "BUY"
            position_side = "SHORT"

        qty_to_close = target_pos.size * percentage

        if qty_to_close <= 0:
            error(f"❌ Quantity to close is too small: {qty_to_close}")
            return False

        info(f"📉 Closing {percentage*100}% of position {position_id}")

        endpoint = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": formatted_symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": qty_to_close,
        }

        response = self._make_request("post", endpoint, params)

        if response and response.get("code") == 0:
            info(f"✅ Position {position_id} closed")
            self.invalidate_cache("positions")
            return True
        else:
            error(f"❌ Failed to close position: {response}")
            return False

    # =========================================================================
    # ExchangeClient ABC Implementation - Orders
    # =========================================================================

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        position_side: Optional[PositionSide] = None,
        leverage: Optional[int] = None,
    ) -> Optional[str]:
        """Разместить ордер."""
        # Плечо должно приходить из runtime-конфига стратегии. Дефолт биржи
        # используем только для прямых/старых вызовов без явного значения.
        order_leverage = int(leverage) if leverage is not None else self._config.default_leverage
        pos_side = position_side or (PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT)
        self.set_leverage(symbol, order_leverage, pos_side)

        formatted_symbol = self._format_symbol(symbol)

        endpoint = "/openApi/swap/v2/trade/order"

        params = {
            "symbol": formatted_symbol,
            "side": side.value,
            "positionSide": pos_side.value if pos_side else PositionSide.BOTH.value,
            "type": order_type.value,
            "quantity": quantity,
        }

        if order_type != OrderType.MARKET and price:
            params["price"] = price

        if tp:
            params["takeProfit"] = json.dumps({
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp,
                "workingType": "MARK_PRICE"
            })

        if sl:
            params["stopLoss"] = json.dumps({
                "type": "STOP_MARKET",
                "stopPrice": sl,
                "workingType": "MARK_PRICE"
            })

        response = self._make_request("post", endpoint, params)

        if response and response.get("code") == 0:
            order_data = response.get("data", {})
            order_id = order_data.get("orderId")

            if not order_id and "order" in order_data:
                order_id = order_data["order"].get("orderId")

            info(f"✅ Order placed: {order_id}")
            self.invalidate_cache("positions")
            return order_id
        else:
            error(f"❌ Failed to place order: {response}")
            return None

    def set_leverage(
        self,
        symbol: str,
        leverage: int,
        position_side: PositionSide = PositionSide.BOTH
    ) -> bool:
        """Установить кредитное плечо."""
        formatted_symbol = self._format_symbol(symbol)

        endpoint = "/openApi/swap/v2/trade/leverage"

        params = {
            "symbol": formatted_symbol,
            "leverage": leverage,
            "side": position_side.value
        }

        try:
            response = self._make_request("post", endpoint, params)

            if response and response.get("code") == 0:
                info(f"✅ Leverage set to {leverage}x for {symbol}")
                return True
            else:
                # Code 80001 means leverage already set
                if response and response.get("code") == 80001:
                    return True
                error(f"❌ Failed to set leverage: {response}")
                return False
        except Exception as e:
            error(f"❌ Error setting leverage: {e}")
            return False

    def set_sl_tp(
        self,
        symbol: str,
        position_side: PositionSide,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        quantity: Optional[float] = None,
    ) -> bool:
        """Установить SL/TP."""
        formatted_symbol = self._format_symbol(symbol)

        # Cancel existing orders
        self.cancel_all_orders(symbol)

        # Get position size if not provided
        size = quantity
        if not size:
            positions = self.get_positions()
            norm_symbol = symbol.replace("-", "").replace("/", "")
            if norm_symbol in positions and positions[norm_symbol]:
                size = positions[norm_symbol][0].size

        all_ok = True

        side = "SELL" if position_side == PositionSide.LONG else "BUY"

        if tp:
            info(f"🔄 Setting TP for {symbol} at {tp}")
            if size:
                params = {
                    "symbol": formatted_symbol,
                    "side": side,
                    "positionSide": position_side.value,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": tp,
                    "workingType": "MARK_PRICE",
                    "quantity": size
                }
                response = self._make_request("post", "/openApi/swap/v2/trade/order", params)
                if response and response.get("code") == 0:
                    info("✅ TP set")
                else:
                    error(f"❌ TP failed: {response}")
                    all_ok = False
            else:
                error("❌ Cannot set TP: position size unknown")
                all_ok = False

        if sl:
            info(f"🔄 Setting SL for {symbol} at {sl}")
            if size:
                params = {
                    "symbol": formatted_symbol,
                    "side": side,
                    "positionSide": position_side.value,
                    "type": "STOP_MARKET",
                    "stopPrice": sl,
                    "workingType": "MARK_PRICE",
                    "quantity": size
                }
                response = self._make_request("post", "/openApi/swap/v2/trade/order", params)
                if response and response.get("code") == 0:
                    info("✅ SL set")
                else:
                    error(f"❌ SL failed: {response}")
                    all_ok = False
            else:
                error("❌ Cannot set SL: position size unknown")
                all_ok = False

        return all_ok

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Отменить ордер."""
        formatted_symbol = self._format_symbol(symbol)

        endpoint = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": formatted_symbol,
            "orderId": order_id
        }

        response = self._make_request("delete", endpoint, params)

        if response and response.get("code") == 0:
            info(f"✅ Order {order_id} cancelled")
            return True
        else:
            error(f"❌ Failed to cancel order: {response}")
            return False

    def get_open_orders(self, symbol: Optional[str] = None) -> OrdersList:
        """Получить открытые ордера."""
        endpoint = "/openApi/swap/v2/trade/openOrders"
        params = {}

        if symbol:
            params["symbol"] = self._format_symbol(symbol)

        response = self._make_request("get", endpoint, params)

        if response and response.get("code") == 0:
            orders_data = response.get("data", {}).get("orders", [])
            return self._parse_orders(orders_data)

        return []

    def cancel_all_orders(self, symbol: str) -> bool:
        """Отменить все ордера для символа."""
        formatted_symbol = self._format_symbol(symbol)

        endpoint = "/openApi/swap/v2/trade/allOpenOrders"
        params = {"symbol": formatted_symbol}

        response = self._make_request("delete", endpoint, params)

        if response and response.get("code") == 0:
            info(f"✅ All orders cancelled for {symbol}")
            return True
        else:
            if response and response.get("code") == 80014:
                info(f"ℹ️ No orders to cancel for {symbol}")
                return True
            error(f"❌ Failed to cancel orders: {response}")
            return False

    def get_recent_orders(self, symbol: str, limit: int = 10) -> OrdersList:
        """Получить последние ордера."""
        formatted_symbol = self._format_symbol(symbol)

        endpoint = "/openApi/swap/v2/trade/allOrders"
        params = {
            "symbol": formatted_symbol,
            "limit": limit,
        }

        response = self._make_request("get", endpoint, params)

        if response and response.get("code") == 0:
            orders_data = response.get("data", {}).get("orders", [])
            return self._parse_orders(orders_data)

        return []

    # =========================================================================
    # ExchangeClient ABC Implementation - Utils
    # =========================================================================

    def normalize_symbol(self, symbol: str) -> str:
        """Нормализовать символ в универсальный формат (BTC-USDT)."""
        # BingX использует BTCUSDT, универсальный - BTC-USDT
        if not symbol:
            return symbol
        return symbol.replace("-", "").replace("/", "")

    def denormalize_symbol(self, symbol: str) -> str:
        """Денормализовать символ в формат BingX (BTCUSDT)."""
        if not symbol:
            return symbol
        # Уже денормализован
        if "-" in symbol or "/" in symbol:
            # Конвертируем в формат BingX
            return symbol.replace("/", "").replace("-", "")
        # Уже в формате BingX (BTCUSDT)
        return symbol

    def check_prerequisites(self) -> bool:
        """Проверить настройки."""
        if not self.api_key or not self.secret_key:
            error("❌ BingX API keys are missing")
            return False
        return True

    # =========================================================================
    # Cache Management
    # =========================================================================

    def invalidate_cache(self, cache_type: Optional[str] = None) -> None:
        """Инвалидировать кэш."""
        if cache_type is None or cache_type == "positions":
            self._positions_cache = None
            self._positions_cache_time = 0

        if cache_type is None or cache_type == "balance":
            self._balance_cache = None
            self._balance_cache_time = 0

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _format_symbol(self, symbol: str) -> str:
        """Форматировать символ для BingX API."""
        if symbol.endswith("/USD"):
            return symbol.replace("/USD", "-USDT")
        elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
            return symbol[:-4] + "-USDT"
        return symbol.replace("/", "-")

    def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None):
        """Выполнить запрос к API."""
        if params is None:
            params = {}

        url = f"{self.base_url}{endpoint}"

        # Add timestamp
        params["timestamp"] = int(time.time() * 1000)

        # Sign request
        if self.api_key and self.secret_key:
            params["apiKey"] = self.api_key
            query_string = urlencode(sorted(params.items()))
            signature = hmac.new(
                self.secret_key.encode("utf-8"),
                query_string.encode("utf-8"),
                digestmod=hashlib.sha256
            ).hexdigest()
            query_string = f"{query_string}&signature={signature}"
            headers = {"X-BX-APIKEY": self.api_key}
        else:
            query_string = urlencode(sorted(params.items()))
            headers = {}

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                if method.lower() == "get":
                    full_url = f"{url}?{query_string}"
                    response = requests.get(full_url, headers=headers, timeout=6)
                elif method.lower() == "post":
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                    response = requests.post(url, data=query_string, headers=headers, timeout=6)
                elif method.lower() == "delete":
                    full_url = f"{url}?{query_string}"
                    response = requests.delete(full_url, headers=headers, timeout=6)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", retry_delay))
                    warning(f"⚠️ Rate limited (429): {endpoint}")
                    time.sleep(retry_after)
                    retry_delay = min(retry_delay * 2, 30)
                    continue

                if response.status_code in (500, 502, 503, 504):
                    warning(f"⚠️ Server error {response.status_code}")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue

                response.raise_for_status()
                return response.json()

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                warning(f"⚠️ Network error (attempt {attempt+1}): {e}")
                time.sleep(retry_delay)
                retry_delay *= 2
            except Exception as e:
                error(f"❌ API request failed: {e}")
                return None

        error(f"❌ Failed after {max_retries} attempts")
        return None

    def _parse_orders(self, orders_data: List[Dict]) -> OrdersList:
        """Преобразовать данные ордеров в DTO."""
        result: OrdersList = []

        for o in orders_data:
            side_str = o.get("side", "").upper()
            side = OrderSide.BUY if side_str == "BUY" else OrderSide.SELL

            status_str = o.get("status", "").upper()
            status_map = {
                "NEW": OrderStatus.NEW,
                "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
                "FILLED": OrderStatus.FILLED,
                "CANCELED": OrderStatus.CANCELED,
                "REJECTED": OrderStatus.REJECTED,
                "EXPIRED": OrderStatus.EXPIRED,
            }
            status = status_map.get(status_str, OrderStatus.NEW)

            type_str = o.get("type", "").upper()
            order_type = OrderType.MARKET if "MARKET" in type_str else OrderType.LIMIT

            result.append(Order(
                order_id=str(o.get("orderId", "")),
                symbol=o.get("symbol", ""),
                side=side,
                order_type=order_type,
                status=status,
                price=float(o.get("price", 0) or 0),
                quantity=float(o.get("origQty", 0) or 0),
                filled_quantity=float(o.get("executedQty", 0) or 0),
                average_price=float(o.get("avgPrice", 0) or 0),
                commission=float(o.get("commission", 0) or 0),
                realized_pnl=float(o.get("profit", 0) or 0),
                position_side=PositionSide.LONG if o.get("positionSide") == "LONG" else PositionSide.SHORT,
            ))

        return result


# Регистрация в фабрике (выполняется при импорте)
def _register():
    from ..config.base import ConfigFactory
    from ..config.bingx_config import BingXConfig
    ConfigFactory.register("bingx", BingXConfig)

# Export for convenience
__all__ = ["BingXClient"]
