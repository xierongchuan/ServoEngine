"""
BingX API Client Module
Handles interactions with BingX API for Standard Futures, Perpetual Futures, and Spot trading.
Supports both Demo (VST) and Real trading modes.
"""

import time
import hmac
import hashlib
import json
import os
import requests
from urllib.parse import urlencode
from src.config import BINGX_API_KEY, BINGX_SECRET_KEY, BINGX_API_URL
from src.utils.logger import info, error, warning

from .exchange_client import ExchangeClient

class BingXClient(ExchangeClient):
    # Class-level cache for positions (shared across instances)
    _positions_cache = None
    _positions_cache_time = 0
    _positions_cache_ttl = 10  # Cache TTL in seconds

    # Class-level cache for balance
    _balance_cache = None
    _balance_cache_time = 0
    _balance_cache_ttl = 10  # Cache TTL in seconds

    # Class-level cache for commission rate
    _commission_cache = {}  # {symbol: {"data": {...}, "time": float}}
    _commission_cache_ttl = 3600  # 1 hour

    # Class-level cache for funding rate
    _funding_cache = {}  # {symbol: {"data": {...}, "time": float}}
    _funding_cache_ttl = 300  # 5 minutes

    # Class-level cache for recent orders
    _orders_cache = {}  # {symbol: {"data": [...], "time": float}}
    _orders_cache_ttl = 5  # seconds

    def __init__(self, api_key=None, secret_key=None):
        self.api_key = api_key or BINGX_API_KEY
        self.secret_key = secret_key or BINGX_SECRET_KEY
        self.base_url = BINGX_API_URL
        self.market_base_url = os.getenv("BINGX_MARKET_API_URL", "https://open-api.bingx.com")

        if not self.api_key or not self.secret_key:
            warning("⚠️ BingX API keys not configured! Private endpoints will fail.")

    def check_prerequisites(self):
        """Checks if API keys are configured"""
        if not self.api_key or not self.secret_key:
            error("❌ BingX API keys are missing. Please set BINGX_API_KEY and BINGX_SECRET_KEY.")
            return False
        return True

    def _get_sign(self, params):
        """Генерирует подпись для запроса"""
        if not self.secret_key:
            raise ValueError("Cannot sign request: Secret Key is missing")

        params_string = urlencode(sorted(params.items()))
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            params_string.encode("utf-8"),
            digestmod=hashlib.sha256
        ).hexdigest()
        return signature

    def _build_query(self, params):
        """Build signed query string with fresh timestamp."""
        params["timestamp"] = int(time.time() * 1000)

        if self.api_key and self.secret_key:
            params["apiKey"] = self.api_key
            query_string = urlencode(sorted(params.items()))
            signature = hmac.new(
                self.secret_key.encode("utf-8"),
                query_string.encode("utf-8"),
                digestmod=hashlib.sha256
            ).hexdigest()
            return f"{query_string}&signature={signature}", {"X-BX-APIKEY": self.api_key}
        else:
            return urlencode(sorted(params.items())), {}

    def make_request(self, method, endpoint, params=None):
        """Выполняет запрос к API BingX"""
        if params is None:
            params = {}

        url = f"{self.base_url}{endpoint}"
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            # Fresh timestamp + signature on every attempt
            final_query_string, headers = self._build_query(params)

            try:
                if method.lower() == "get":
                    full_url = f"{url}?{final_query_string}"
                    response = requests.get(full_url, headers=headers, timeout=6)
                elif method.lower() == "post":
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                    response = requests.post(url, data=final_query_string, headers=headers, timeout=6)
                elif method.lower() == "delete":
                    full_url = f"{url}?{final_query_string}"
                    response = requests.delete(full_url, headers=headers, timeout=6)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", retry_delay))
                    warning(f"⚠️ Rate limited (429) on {endpoint} (attempt {attempt+1}/{max_retries}). Backing off {retry_after}s...")
                    time.sleep(retry_after)
                    retry_delay = min(retry_delay * 2, 30)
                    continue

                if response.status_code in (500, 502, 503, 504):
                    warning(f"⚠️ Server error {response.status_code} on {endpoint} (attempt {attempt+1}/{max_retries}). Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue

                response.raise_for_status()
                return response.json()

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                warning(f"⚠️ Network error (attempt {attempt+1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            except Exception as e:
                error(f"❌ BingX API request failed: {e}")
                if 'response' in locals():
                    try:
                        error(f"   Response: {response.text}")
                    except Exception:
                        pass
                return None

        error(f"❌ Failed to connect to BingX after {max_retries} attempts.")
        return None

    def get_perpetual_balance(self):
        """Получает баланс Perpetual Futures (Swap) с кэшированием"""
        # Check cache first
        now = time.time()
        if (BingXClient._balance_cache is not None and
            now - BingXClient._balance_cache_time < BingXClient._balance_cache_ttl):
            return BingXClient._balance_cache

        endpoint = "/openApi/swap/v2/user/balance"
        response = self.make_request("get", endpoint)

        # DEBUG: Log full response
        info(f"🔍 [DEBUG] Perpetual balance API response: {json.dumps(response, indent=2) if response else 'None'}")

        if response and response.get("code") == 0:
            # Try different response structures
            data = response.get("data", {})

            # VST Demo might return balance directly in data, or in data.balance
            if isinstance(data, dict):
                # Check if balance is nested or direct
                if "balance" in data:
                    balance_data = data.get("balance", {})
                else:
                    balance_data = data  # Direct balance object
            else:
                balance_data = {}

            info(f"🔍 [DEBUG] Parsed balance_data: {json.dumps(balance_data, indent=2) if balance_data else 'empty'}")

            # Update cache
            BingXClient._balance_cache = balance_data
            BingXClient._balance_cache_time = time.time()
            return balance_data

        error(f"❌ Failed to get perpetual balance. Response: {response}")
        return None

    def get_spot_balance(self):
        """Получает баланс Spot аккаунта"""
        endpoint = "/openApi/spot/v1/account/balance"
        try:
            response = self.make_request("get", endpoint)
            if response and response.get("code") == 0:
                data = response.get("data")
                if isinstance(data, list):
                    return data
                return (data or {}).get("balances", [])
        except Exception as e:
            error(f"❌ Failed to get Spot balance: {e}")
        return None

    def get_standard_futures_balance(self):
        """Получает баланс Standard Futures"""
        endpoint = "/openApi/contract/v1/balance"
        try:
            response = self.make_request("get", endpoint)
            if response and response.get("code") == 0:
                data = response.get("data")
                if isinstance(data, list):
                    return data
                return (data or {}).get("balances", [])
        except Exception as e:
            error(f"❌ Failed to get Standard Futures balance: {e}")
        return None

    def get_all_balances(self):
        """Получает балансы всех типов аккаунтов"""
        balances = {
            "perpetual": self.get_perpetual_balance(),
            "spot": self.get_spot_balance(),
            "standard_futures": self.get_standard_futures_balance()
        }
        return balances

    def get_balance(self):
        """Legacy method: Получает баланс Perpetual Futures (для совместимости)"""
        return self.get_perpetual_balance()

    def get_klines(self, symbol, interval="5m", limit=288):
        """Получить исторические данные свечей (klines)."""
        return self.get_kline_data(symbol, interval, limit)

    def normalize_symbol(self, symbol: str) -> str:
        """Нормализовать символ в универсальный формат (BTC-USDT)."""
        if symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
            return symbol[:-4] + "-" + symbol[-4:]
        return symbol.replace("/", "-")

    def denormalize_symbol(self, symbol: str) -> str:
        """Денормализовать символ в формат BingX (BTCUSDT)."""
        return symbol.replace("-", "").replace("/", "")

    def get_kline_data(self, symbol, interval="5m", limit=288):
        """
        Получает исторические данные свечей.
        Сначала пробует WebSocket кэш, потом REST API.
        """
        # 1. Try WebSocket shared cache first
        ws_cache_result = None
        try:
            from src.exchanges.bingx_ws_data_provider import is_cache_ready, get_klines_from_shared_cache

            cache_ready = is_cache_ready(symbol)

            if cache_ready:
                cached = get_klines_from_shared_cache(symbol, limit)
                if len(cached) >= limit * 0.8:  # 80% data available
                    ws_cache_result = cached
        except ImportError as ie:
            warning(f"⚠️ WS cache import failed for {symbol}: {ie}")
        except Exception as e:
            warning(f"⚠️ WS cache error for {symbol}: {e}")

        if ws_cache_result is not None:
            info(f"📊 [WS CACHE] Using {len(ws_cache_result)} candles for {symbol}")
            return ws_cache_result

        # 2. Fallback to REST API
        return self._fetch_klines_rest(symbol, interval, limit)

    def _fetch_klines_rest(self, symbol, interval="5m", limit=288):
        """REST API fallback для получения свечей."""
        # BingX формат символа: BTC-USDT
        if symbol.endswith("/USD"):
            formatted_symbol = symbol.replace("/USD", "-USDT")
        elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
            formatted_symbol = symbol[:-4] + "-USDT"
        else:
            formatted_symbol = symbol.replace("/", "-")

        market_url = f"{self.market_base_url}/openApi/swap/v3/quote/klines"

        # Map verbose interval constants to BingX format
        interval_map = {
            "MINUTE_1": "1m",
            "MINUTE_5": "5m",
            "MINUTE_15": "15m",
            "MINUTE_30": "30m",
            "HOUR_1": "1h",
            "HOUR_4": "4h",
            "DAY_1": "1d"
        }

        bingx_interval = interval_map.get(interval, interval)

        params = {
            "symbol": formatted_symbol,
            "interval": bingx_interval,
            "limit": limit
        }

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                response = requests.get(market_url, params=params, timeout=6)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", retry_delay))
                    warning(f"⚠️ Rate limited (429): klines {symbol}. Backing off {retry_after}s...")
                    time.sleep(retry_after)
                    retry_delay = min(retry_delay * 2, 30)
                    continue

                response.raise_for_status()
                data = response.json()
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                warning(f"⚠️ Market data network error (attempt {attempt+1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    error(f"❌ BingX Market Data request failed after {max_retries} attempts: {e}")
                    return []
            except Exception as e:
                error(f"❌ BingX Market Data request failed: {e}")
                return []

        if data and data.get("code") == 0:
            klines = data.get("data", [])
            formatted_data = []

            for k in klines:
                if isinstance(k, dict):
                    ts_ms = k.get("time")
                    close_price = float(k.get("close"))
                    open_price = float(k.get("open"))
                    high_price = float(k.get("high"))
                    low_price = float(k.get("low"))
                    volume = float(k.get("volume"))
                elif isinstance(k, list):
                    ts_ms = k[0]
                    open_price = float(k[1])
                    high_price = float(k[2])
                    low_price = float(k[3])
                    close_price = float(k[4])
                    volume = float(k[5])
                else:
                    continue

                dt = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts_ms / 1000))

                formatted_data.append({
                    "snapshotTimeUTC": dt,
                    "closePrice": close_price,
                    "openPrice": open_price,
                    "highPrice": high_price,
                    "lowPrice": low_price,
                    "volume": volume
                })

            formatted_data.sort(key=lambda x: x["snapshotTimeUTC"])
            return formatted_data

        return []

    @classmethod
    def invalidate_positions_cache(cls):
        """Invalidates positions cache so next get_positions() fetches fresh data."""
        cls._positions_cache = None
        cls._positions_cache_time = 0

    def get_positions(self):
        """Получает открытые позиции (с кэшированием)"""
        # Check cache first
        now = time.time()
        if (BingXClient._positions_cache is not None and
            now - BingXClient._positions_cache_time < BingXClient._positions_cache_ttl):
            return BingXClient._positions_cache

        endpoint = "/openApi/swap/v2/user/positions"
        response = self.make_request("get", endpoint)

        positions = {}

        if response and response.get("code") == 0:
            data = response.get("data", [])

            for pos in data:
                # Filter out closed positions or zero size
                size = float(pos.get("positionAmt", 0))
                if size == 0:
                    continue

                # Normalize to match config format (BTCUSDT)
                symbol = pos.get("symbol", "").replace("-", "")

                if symbol not in positions:
                    positions[symbol] = []

                # Adapt to unified format
                # Determine side based on positionSide if available, else fallback to amount sign
                pos_side = pos.get("positionSide", "").upper()
                if pos_side == "SHORT":
                    side = "sell"
                elif pos_side == "LONG":
                    side = "buy"
                else:
                    # Fallback for One-Way mode where positionSide might be BOTH or empty
                    side = "buy" if size > 0 else "sell"

                positions[symbol].append({
                    "type": side,
                    "entry": float(pos.get("avgPrice", 0)),
                    "dealId": pos.get("positionId", ""),
                    "workingOrderId": pos.get("positionId", ""),
                    "created": None,
                    "size": abs(size),
                    "pnl": float(pos.get("unrealizedProfit", 0)),
                    "leverage": int(float(pos.get("leverage", 0))) or None,
                    "markPrice": float(pos.get("markPrice", 0)) or None,
                })

        # Update cache
        BingXClient._positions_cache = positions
        BingXClient._positions_cache_time = time.time()

        return positions

    def set_leverage(self, symbol, leverage, side="LONG"):
        """Устанавливает кредитное плечо"""
        # Endpoint: /openApi/swap/v2/trade/leverage
        endpoint = "/openApi/swap/v2/trade/leverage"

        if symbol.endswith("/USD"):
            formatted_symbol = symbol.replace("/USD", "-USDT")
        elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
            formatted_symbol = symbol[:-4] + "-USDT"
        else:
            formatted_symbol = symbol.replace("/", "-")

        params = {
            "symbol": formatted_symbol,
            "leverage": leverage,
            "side": side.upper() # LONG or SHORT
        }

        try:
            response = self.make_request("post", endpoint, params)
            if response and response.get("code") == 0:
                info(f"✅ Leverage set to {leverage}x for {symbol} ({side})")
                return True
            else:
                # Code 80001 usually means leverage already set or not modified
                if response and response.get("code") == 80001:
                     return True
                error(f"❌ Failed to set leverage: {response}")
                return False
        except Exception as e:
            error(f"❌ Error setting leverage: {e}")
            return False

    def place_order(self, symbol, side, price, quantity, type="MARKET", sl=None, tp=None, positionSide=None, leverage=None):
        """Размещает ордер"""
        from src.config import LEVERAGE

        if symbol.endswith("/USD"):
            formatted_symbol = symbol.replace("/USD", "-USDT")
        elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
            formatted_symbol = symbol[:-4] + "-USDT"
        else:
            formatted_symbol = symbol.replace("/", "-")

        # Set leverage before placing order
        # For One-Way mode, we might need to set for "LONG" (Buy) or "SHORT" (Sell)
        # or just "LONG" if it applies to both in some modes.
        # Safe bet: set for the direction we are trading.
        order_leverage = int(leverage) if leverage is not None else LEVERAGE
        leverage_side = "LONG" if side.upper() == "BUY" else "SHORT"
        self.set_leverage(symbol, order_leverage, leverage_side)

        endpoint = "/openApi/swap/v2/trade/order"

        # Side: BUY or SELL
        # PositionSide: LONG or SHORT (Hedge Mode) or BOTH (One-way Mode)
        # Assuming One-way Mode for simplicity or Isolated Margin

        params = {
            "symbol": formatted_symbol,
            "side": side.upper(), # BUY or SELL
            "positionSide": positionSide if positionSide else ("LONG" if side.upper() == "BUY" else "SHORT"), # Use provided or infer
            "type": type.upper(),
            "quantity": quantity,
        }

        if type.upper() != "MARKET":
            params["price"] = price

        # TP/SL
        # BingX allows setting TP/SL when opening order or separately
        # For simplicity, let's try to set it in the order if supported,
        # or we might need separate calls.
        # Advanced TP/SL in BingX is often a separate endpoint or parameters

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

        # Note: BingX API for TP/SL might be complex.
        # Often it's easier to place the order first, then place TP/SL orders.
        # For now, let's just place the main order.

        response = self.make_request("post", endpoint, params)

        if response and response.get("code") == 0:
            order_data = response.get("data", {})
            # BingX response structure might be data: { order: { orderId: ... } } or data: { orderId: ... }
            order_id = order_data.get("orderId")
            if not order_id and "order" in order_data:
                order_id = order_data["order"].get("orderId")

            info(f"✅ BingX Order Placed: {order_id}")
            # Invalidate positions cache so next get_positions() fetches fresh data
            BingXClient.invalidate_positions_cache()
            return order_id
        else:
            error(f"❌ Failed to place BingX order: {response}")
            return None

    def get_open_orders(self, symbol=None):
        """Получает список открытых ордеров"""
        endpoint = "/openApi/swap/v2/trade/openOrders"
        params = {}
        if symbol:
            if symbol.endswith("/USD"):
                formatted_symbol = symbol.replace("/USD", "-USDT")
            elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
                formatted_symbol = symbol[:-4] + "-USDT"
            else:
                formatted_symbol = symbol.replace("/", "-")
            params["symbol"] = formatted_symbol

        response = self.make_request("get", endpoint, params)

        if response and response.get("code") == 0:
            return response.get("data", {}).get("orders", [])
        return []

    def cancel_order(self, symbol, order_id):
        """Отменяет ордер"""
        endpoint = "/openApi/swap/v2/trade/order"

        if symbol.endswith("/USD"):
            formatted_symbol = symbol.replace("/USD", "-USDT")
        elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
            formatted_symbol = symbol[:-4] + "-USDT"
        else:
            formatted_symbol = symbol.replace("/", "-")

        params = {
            "symbol": formatted_symbol,
            "orderId": order_id
        }

        response = self.make_request("delete", endpoint, params)

        if response and response.get("code") == 0:
            info(f"✅ Order {order_id} cancelled")
            return True
        else:
            error(f"❌ Failed to cancel order {order_id}: {response}")
            return False

    def close_position(self, symbol, position_id, percentage=1.0):
        """
        Закрывает позицию (полностью или частично).
        :param percentage: Доля закрытия (0.0 - 1.0). По умолчанию 1.0 (100%).
        """
        # Let's fetch the position first to get size
        positions = self.get_positions()
        target_pos = None

        # Normalize symbol for lookup (get_positions() keys are like "BTCUSDT")
        lookup_symbol = symbol.replace("-", "").replace("/", "")

        if lookup_symbol in positions:
            for p in positions[lookup_symbol]:
                if str(p["dealId"]) == str(position_id):
                    target_pos = p
                    break

        if not target_pos:
            error(f"❌ Position {position_id} not found for closing")
            return False

        formatted_symbol = self._format_symbol(symbol)

        # Determine side and positionSide for closing
        # To close a LONG, we SELL with positionSide=LONG
        # To close a SHORT, we BUY with positionSide=SHORT
        if target_pos["type"] == "buy":
            side = "SELL"
            position_side = "LONG"
        else:
            side = "BUY"
            position_side = "SHORT"

        # Calculate quantity to close
        full_size = target_pos["size"]
        qty_to_close = full_size * percentage

        # Ensure quantity respects precision (assuming 4 decimals for now, ideally should come from exchange info)
        qty_to_close = float(f"{qty_to_close:.4f}")

        if qty_to_close <= 0:
            error(f"❌ Quantity to close is too small: {qty_to_close}")
            return False

        info(f"📉 Closing {percentage*100}% of position {position_id} ({qty_to_close} / {full_size})")

        # Cancel existing SL/TP orders to avoid orphaned conditional orders
        self.cancel_all_orders(symbol)

        endpoint = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": formatted_symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": qty_to_close,
        }

        response = self.make_request("post", endpoint, params)

        if response and response.get("code") == 0:
            info(f"✅ Position {position_id} closed (partial: {percentage})")
            # Invalidate positions cache so next get_positions() fetches fresh data
            BingXClient._positions_cache = None
            BingXClient._positions_cache_time = 0
            return True
        else:
            error(f"❌ Failed to close position {position_id}: {response}")
            return False

    def set_sl_tp(self, symbol, position_side, tp=None, sl=None, quantity=None):
        """
        Устанавливает или обновляет SL/TP для позиции.
        :param symbol: Символ (BTCUSDT)
        :param position_side: Сторона позиции (LONG или SHORT)
        :param tp: Цена Take Profit (опционально)
        :param sl: Цена Stop Loss (опционально)
        :param quantity: Размер позиции (опционально, если не указан - берется из API)
        :return: True если все запрошенные SL/TP успешно поставлены
        """
        formatted_symbol = self._format_symbol(symbol)

        # 1. Cancel existing open orders (SL/TP are open orders)
        open_orders = self.get_open_orders(symbol)
        for order in open_orders:
            self.cancel_order(symbol, order["orderId"])

        # 2. Place new SL/TP
        side = "SELL" if position_side.upper() == "LONG" else "BUY"

        # Fetch position size once if not provided
        size = quantity
        if not size:
            positions = self.get_positions()
            norm_symbol = symbol.replace("-", "")
            if norm_symbol in positions and positions[norm_symbol]:
                size = positions[norm_symbol][0]["size"]

        all_ok = True

        if tp:
            info(f"🔄 Setting TP for {symbol} ({position_side}) at {tp}")
            if size:
                params = {
                    "symbol": formatted_symbol,
                    "side": side,
                    "positionSide": position_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": tp,
                    "workingType": "MARK_PRICE",
                    "quantity": size
                }
                endpoint = "/openApi/swap/v2/trade/order"
                response = self.make_request("post", endpoint, params)
                if response and response.get("code") == 0:
                    info(f"✅ TP order placed for {symbol} at {tp}")
                else:
                    error(f"❌ TP order FAILED for {symbol}: {response}")
                    all_ok = False
            else:
                error(f"❌ Cannot set TP: Position size unknown for {symbol}")
                all_ok = False

        if sl:
            info(f"🔄 Setting SL for {symbol} ({position_side}) at {sl}")
            if size:
                params = {
                    "symbol": formatted_symbol,
                    "side": side,
                    "positionSide": position_side,
                    "type": "STOP_MARKET",
                    "stopPrice": sl,
                    "workingType": "MARK_PRICE",
                    "quantity": size
                }
                endpoint = "/openApi/swap/v2/trade/order"
                response = self.make_request("post", endpoint, params)
                if response and response.get("code") == 0:
                    info(f"✅ SL order placed for {symbol} at {sl}")
                else:
                    error(f"❌ SL order FAILED for {symbol}: {response}")
                    all_ok = False
            else:
                error(f"❌ Cannot set SL: Position size unknown for {symbol}")
                all_ok = False

        return all_ok

    def _format_symbol(self, symbol: str) -> str:
        """Централизованное форматирование символа для BingX API."""
        if symbol.endswith("/USD"):
            return symbol.replace("/USD", "-USDT")
        elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
            return symbol[:-4] + "-USDT"
        return symbol.replace("/", "-")

    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """
        Получает стакан заявок (order book / depth).
        Returns: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
        Bids sorted descending (best bid first), asks sorted ascending (best ask first).
        """
        formatted_symbol = self._format_symbol(symbol)

        # Публичный endpoint
        market_url = f"{self.market_base_url}/openApi/swap/v2/quote/depth"

        params = {
            "symbol": formatted_symbol,
            "limit": min(limit, 100)  # BingX max limit is 100
        }

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                response = requests.get(market_url, params=params, timeout=6)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", retry_delay))
                    warning(f"⚠️ Rate limited (429): order book {symbol}. Backing off {retry_after}s...")
                    time.sleep(retry_after)
                    retry_delay = min(retry_delay * 2, 30)
                    continue

                response.raise_for_status()
                data = response.json()

                if data and data.get("code") == 0:
                    depth_data = data.get("data", {})
                    return {
                        "bids": [[float(b[0]), float(b[1])] for b in depth_data.get("bids", [])],
                        "asks": [[float(a[0]), float(a[1])] for a in depth_data.get("asks", [])]
                    }
                else:
                    error(f"❌ Failed to get order book: {data}")
                    return {"bids": [], "asks": []}

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                warning(f"⚠️ Order book request error (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
            except Exception as e:
                error(f"❌ Order book request failed: {e}")
                return {"bids": [], "asks": []}

        return {"bids": [], "asks": []}

    def get_ticker(self, symbol: str) -> dict:
        """
        Получает текущий тикер с лучшими bid/ask ценами.
        Returns: {"bid": float, "ask": float, "last": float, "volume": float}
        """
        formatted_symbol = self._format_symbol(symbol)

        # Публичный endpoint
        market_url = f"{self.market_base_url}/openApi/swap/v2/quote/ticker"

        params = {"symbol": formatted_symbol}

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                response = requests.get(market_url, params=params, timeout=6)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", retry_delay))
                    warning(f"⚠️ Rate limited (429): ticker {symbol}. Backing off {retry_after}s...")
                    time.sleep(retry_after)
                    retry_delay = min(retry_delay * 2, 30)
                    continue

                response.raise_for_status()
                data = response.json()

                if data and data.get("code") == 0:
                    ticker_data = data.get("data", {})
                    return {
                        "bid": float(ticker_data.get("bestBidPrice", 0) or 0),
                        "ask": float(ticker_data.get("bestAskPrice", 0) or 0),
                        "last": float(ticker_data.get("lastPrice", 0) or 0),
                        "volume": float(ticker_data.get("volume", 0) or 0)
                    }
                else:
                    error(f"❌ Failed to get ticker: {data}")
                    return {"bid": 0, "ask": 0, "last": 0, "volume": 0}

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                warning(f"⚠️ Ticker request error (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
            except Exception as e:
                error(f"❌ Ticker request failed: {e}")
                return {"bid": 0, "ask": 0, "last": 0, "volume": 0}

        return {"bid": 0, "ask": 0, "last": 0, "volume": 0}

    def cancel_all_orders(self, symbol: str) -> bool:
        """
        Отменяет все открытые ордера для символа.
        Returns True if successful, False otherwise.
        """
        formatted_symbol = self._format_symbol(symbol)
        endpoint = "/openApi/swap/v2/trade/allOpenOrders"

        params = {"symbol": formatted_symbol}

        response = self.make_request("delete", endpoint, params)

        if response and response.get("code") == 0:
            info(f"✅ All orders cancelled for {symbol}")
            return True
        else:
            # Code 80014 might mean no orders to cancel - that's OK
            if response and response.get("code") == 80014:
                info(f"ℹ️ No open orders to cancel for {symbol}")
                return True
            error(f"❌ Failed to cancel all orders for {symbol}: {response}")
            return False

    def get_commission_rate(self, symbol: str) -> dict:
        """
        Получает ставки комиссий maker/taker для символа.
        BingX API возвращает rates как десятичные (0.0005 = 0.05%).
        Returns: {"maker": float, "taker": float} в процентах (0.02 = 0.02%) или None.
        """
        now = time.time()
        cached = BingXClient._commission_cache.get(symbol)
        if cached and now - cached["time"] < BingXClient._commission_cache_ttl:
            return cached["data"]

        try:
            formatted_symbol = self._format_symbol(symbol)
            endpoint = "/openApi/swap/v2/user/commissionRate"
            response = self.make_request("get", endpoint, {"symbol": formatted_symbol})

            if response and response.get("code") == 0:
                data = response.get("data", {})
                # BingX returns decimal rates (e.g. 0.0005 = 0.05%)
                maker_raw = float(data.get("makerCommissionRate", 0.0002))
                taker_raw = float(data.get("takerCommissionRate", 0.0005))
                result = {
                    "maker": round(maker_raw * 100, 4),  # Convert to percent
                    "taker": round(taker_raw * 100, 4),
                }
                BingXClient._commission_cache[symbol] = {"data": result, "time": now}
                return result
            else:
                warning(f"⚠️ Commission rate API error: {response}")
                return None
        except Exception as e:
            warning(f"⚠️ Failed to get commission rate for {symbol}: {e}")
            return None

    def get_funding_rate(self, symbol: str) -> dict:
        """
        Получает текущую ставку финансирования для символа.
        Public endpoint — не требует подписи.
        Returns: {"funding_rate": float, "funding_rate_pct": float, "next_funding_time": str} или None.
        """
        now = time.time()
        cached = BingXClient._funding_cache.get(symbol)
        if cached and now - cached["time"] < BingXClient._funding_cache_ttl:
            return cached["data"]

        try:
            formatted_symbol = self._format_symbol(symbol)
            market_url = f"{self.market_base_url}/openApi/swap/v2/quote/premiumIndex"
            params = {"symbol": formatted_symbol}

            max_retries = 3
            retry_delay = 1
            data = None
            for attempt in range(max_retries):
                try:
                    response = requests.get(market_url, params=params, timeout=6)

                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", retry_delay))
                        warning(f"⚠️ Rate limited (429): funding rate {symbol}. Backing off {retry_after}s...")
                        time.sleep(retry_after)
                        retry_delay = min(retry_delay * 2, 30)
                        continue

                    response.raise_for_status()
                    data = response.json()
                    break
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                    warning(f"⚠️ Funding rate request error (attempt {attempt+1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        return None

            if data and data.get("code") == 0:
                index_data = data.get("data", {})
                funding_rate = float(index_data.get("lastFundingRate", 0))
                next_time_ms = index_data.get("nextFundingTime", 0)
                next_time_str = ""
                if next_time_ms:
                    next_time_str = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(int(next_time_ms) / 1000))

                result = {
                    "funding_rate": funding_rate,
                    "funding_rate_pct": round(funding_rate * 100, 4),
                    "next_funding_time": next_time_str,
                }
                BingXClient._funding_cache[symbol] = {"data": result, "time": now}
                return result
            else:
                warning(f"⚠️ Funding rate API error: {data}")
                return None
        except Exception as e:
            warning(f"⚠️ Failed to get funding rate for {symbol}: {e}")
            return None

    def get_recent_orders(self, symbol: str, limit: int = 10) -> list:
        """
        Получает последние ордера (все статусы) для символа.
        Returns list of dicts: orderId, side, status, avgPrice, executedQty, profit, commission, updateTime
        """
        now = time.time()
        cached = BingXClient._orders_cache.get(symbol)
        if cached and now - cached["time"] < BingXClient._orders_cache_ttl:
            return cached["data"]

        try:
            formatted_symbol = self._format_symbol(symbol)
            endpoint = "/openApi/swap/v2/trade/allOrders"
            params = {
                "symbol": formatted_symbol,
                "limit": limit,
            }

            response = self.make_request("get", endpoint, params)

            if response and response.get("code") == 0:
                raw_orders = response.get("data", {}).get("orders", [])
                result = []
                for o in raw_orders:
                    result.append({
                        "orderId": o.get("orderId", ""),
                        "side": o.get("side", ""),
                        "positionSide": o.get("positionSide", ""),
                        "status": o.get("status", ""),
                        "avgPrice": float(o.get("avgPrice", 0) or 0),
                        "executedQty": float(o.get("executedQty", 0) or 0),
                        "profit": float(o.get("profit", 0) or 0),
                        "commission": float(o.get("commission", 0) or 0),
                        "updateTime": int(o.get("updateTime", 0) or 0),
                    })
                BingXClient._orders_cache[symbol] = {"data": result, "time": now}
                return result
            else:
                warning(f"⚠️ Recent orders API error: {response}")
                return []
        except Exception as e:
            warning(f"⚠️ Failed to get recent orders for {symbol}: {e}")
            return []


# Global instance
bingx_client = BingXClient()
