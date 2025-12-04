"""
BingX API Client Module
Handles interactions with BingX API for Standard Futures, Perpetual Futures, and Spot trading.
Supports both Demo (VST) and Real trading modes.
"""

import time
import hmac
import hashlib
import json
import requests
from urllib.parse import urlencode
from src.config import BINGX_API_KEY, BINGX_SECRET_KEY, BINGX_API_URL, MODE
from src.utils.logger import info, error, warning

from .exchange_client import ExchangeClient

class BingXClient(ExchangeClient):
    def __init__(self, api_key=None, secret_key=None):
        self.api_key = api_key or BINGX_API_KEY
        self.secret_key = secret_key or BINGX_SECRET_KEY
        self.base_url = BINGX_API_URL

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

    def make_request(self, method, endpoint, params=None):
        """Выполняет запрос к API BingX"""
        if params is None:
            params = {}

        params["timestamp"] = int(time.time() * 1000)

        # Manually construct the query string to ensure exact match for signature
        if self.api_key and self.secret_key:
            params["apiKey"] = self.api_key
            # Sort and encode params
            query_string = urlencode(sorted(params.items()))
            # Calculate signature on the exact string we will send
            signature = hmac.new(
                self.secret_key.encode("utf-8"),
                query_string.encode("utf-8"),
                digestmod=hashlib.sha256
            ).hexdigest()
            # Append signature
            final_query_string = f"{query_string}&signature={signature}"

            headers = {
                "X-BX-APIKEY": self.api_key,
            }
        else:
            final_query_string = urlencode(sorted(params.items()))
            headers = {}

        url = f"{self.base_url}{endpoint}"

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                if method.lower() == "get":
                    # For GET, append to URL
                    full_url = f"{url}?{final_query_string}"
                    response = requests.get(full_url, headers=headers, timeout=20)
                elif method.lower() == "post":
                    # For POST, send as body
                    # Ensure correct content type
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                    response = requests.post(url, data=final_query_string, headers=headers, timeout=20)
                elif method.lower() == "delete":
                    # For DELETE, BingX might expect params in URL
                    full_url = f"{url}?{final_query_string}"
                    response = requests.delete(full_url, headers=headers, timeout=20)
                else:
                    raise ValueError(f"Unsupported method: {method}")

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
                    except:
                        pass
                return None

        error(f"❌ Failed to connect to BingX after {max_retries} attempts.")
        return None

    def get_perpetual_balance(self):
        """Получает баланс Perpetual Futures (Swap)"""
        endpoint = "/openApi/swap/v2/user/balance"
        response = self.make_request("get", endpoint)

        if response and response.get("code") == 0:
            return response.get("data", {}).get("balance", {})

        error(f"❌ Failed to get perpetual balance. Response: {response}")
        return None

    def get_spot_balance(self):
        """Получает баланс Spot аккаунта"""
        endpoint = "/openApi/spot/v1/account/balance"
        try:
            response = self.make_request("get", endpoint)
            if response and response.get("code") == 0:
                return response.get("data", {}).get("balances", [])
        except Exception as e:
            error(f"❌ Failed to get Spot balance: {e}")
        return None

    def get_standard_futures_balance(self):
        """Получает баланс Standard Futures"""
        endpoint = "/openApi/contract/v1/balance"
        try:
            response = self.make_request("get", endpoint)
            if response and response.get("code") == 0:
                return response.get("data", {}).get("balances", [])
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

    def get_kline_data(self, symbol, interval="5m", limit=288):
        """Получает исторические данные свечей"""
        # BingX формат символа: BTC-USDT
        # Если символ заканчивается на /USD, меняем на -USDT
        if symbol.endswith("/USD"):
            formatted_symbol = symbol.replace("/USD", "-USDT")
        elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
             # Handle BTCUSDT -> BTC-USDT
            formatted_symbol = symbol[:-4] + "-USDT"
        else:
            formatted_symbol = symbol.replace("/", "-")

        # Для рыночных данных всегда используем основной API, так как на VST может не быть ликвидности/данных
        # или эндпоинт может отличаться. Рыночные данные одинаковы для демо и реала.
        market_url = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"

        # Map Capital.com interval constants to BingX format
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

        # Используем requests напрямую для market_url, чтобы не путать с self.base_url
        try:
            response = requests.get(market_url, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            error(f"❌ BingX Market Data request failed: {e}")
            print(f"❌ BingX Market Data request failed: {e}")
            return []

        if data and data.get("code") == 0:
            # BingX returns: [time, open, high, low, close, volume, ...]
            # Need to convert to Capital.com format:
            # {"snapshotTimeUTC": "2023-10-27T10:00:00", "closePrice": 34000.50, ...}

            klines = data.get("data", [])
            formatted_data = []

            for k in klines:
                # k structure: {"time": 123, "open": "1.2", ...}
                if isinstance(k, dict):
                    ts_ms = k.get("time")
                    close_price = float(k.get("close"))
                    open_price = float(k.get("open"))
                    high_price = float(k.get("high"))
                    low_price = float(k.get("low"))
                    volume = float(k.get("volume"))
                elif isinstance(k, list):
                    # Fallback for list format if API changes
                    ts_ms = k[0]
                    open_price = float(k[1])
                    high_price = float(k[2])
                    low_price = float(k[3])
                    close_price = float(k[4])
                    volume = float(k[5])
                else:
                    continue

                # Convert timestamp to ISO format
                dt = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts_ms / 1000))

                formatted_data.append({
                    "snapshotTimeUTC": dt,
                    "closePrice": close_price,
                    "openPrice": open_price,
                    "highPrice": high_price,
                    "lowPrice": low_price,
                    "volume": volume
                })

            # Capital.com returns oldest first?
            # BingX usually returns newest first or oldest first depending on API
            # Let's assume we need to sort by time ascending
            formatted_data.sort(key=lambda x: x["snapshotTimeUTC"])

            return formatted_data

        return []

    def get_positions(self):
        """Получает открытые позиции"""
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

                # Adapt to Capital.com format structure
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
                    "dealId": pos.get("positionId", ""), # Use positionId as dealId
                    "workingOrderId": pos.get("positionId", ""),
                    "created": time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()), # BingX might not give creation time easily in this endpoint
                    "hold_minutes": 60, # Default, as we don't store this in BingX
                    "size": abs(size),
                    "pnl": float(pos.get("unrealizedProfit", 0))
                })

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

    def place_order(self, symbol, side, price, quantity, type="MARKET", sl=None, tp=None, positionSide=None):
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
        leverage_side = "LONG" if side.upper() == "BUY" else "SHORT"
        self.set_leverage(symbol, LEVERAGE, leverage_side)

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

        # Normalize symbol for lookup
        lookup_symbol = symbol.replace("-", "/")

        if lookup_symbol in positions:
            for p in positions[lookup_symbol]:
                if str(p["dealId"]) == str(position_id):
                    target_pos = p
                    break

        if not target_pos:
            error(f"❌ Position {position_id} not found for closing")
            return False

        # Ensure symbol format is correct (BTC-USDT)
        if symbol.endswith("/USD"):
            formatted_symbol = symbol.replace("/USD", "-USDT")
        elif symbol.endswith("USDT") and "-" not in symbol and "/" not in symbol:
            formatted_symbol = symbol[:-4] + "-USDT"
        else:
            formatted_symbol = symbol.replace("/", "-")

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

        # We need to manually call place_order logic but with specific positionSide
        # Since place_order currently auto-calculates positionSide, we need to modify place_order OR
        # manually construct params here.
        # Better to modify place_order to accept positionSide override.

        # For now, let's use a direct request here to avoid breaking place_order signature if we don't want to change it yet,
        # OR better: update place_order to accept **kwargs or explicit positionSide.

        # Let's update place_order signature in a separate step if needed, but for now I will duplicate the request logic
        # here for safety and precision, or better yet, I will use place_order if I update it.

        # Actually, I'll update place_order in the next step. For now, let's assume place_order supports it
        # or I'll pass it via a new argument if I change it.
        # Wait, I can't change place_order in this same tool call easily without conflict.
        # I will implement the request directly here to be self-contained and safe.

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
        """
        # 1. Cancel existing open orders (SL/TP are open orders)
        open_orders = self.get_open_orders(symbol)
        for order in open_orders:
            # Cancel only SL/TP orders? Or all?
            # Safer to cancel all open orders for this symbol to avoid duplicates
            # But be careful if there are other limit orders.
            # Usually bots like this only have SL/TP as open orders.
            self.cancel_order(symbol, order["orderId"])

        # 2. Place new SL/TP
        # For LONG position: SL/TP are SELL orders with positionSide=LONG
        # For SHORT position: SL/TP are BUY orders with positionSide=SHORT

        side = "SELL" if position_side.upper() == "LONG" else "BUY"

        if tp:
            info(f"🔄 Setting TP for {symbol} ({position_side}) at {tp}")
            # TP is TAKE_PROFIT_MARKET
            # Quantity is not strictly required for SL/TP in some modes if it closes position,
            # but usually required. We might need to know position size.
            # BingX often allows 0 or omitting quantity for "close all" or "entire position" logic in some endpoints,
            # but standard trade/order endpoint usually needs quantity.
            # However, for SL/TP specifically, there might be a different way.
            # Let's check if we can use the 'takeProfit' param on a new order? No, we are updating.

            # If we don't know quantity, we might fail.
            # But wait, we can fetch position size.
            size = quantity
            if not size:
                positions = self.get_positions()
                # Normalize symbol
                norm_symbol = symbol.replace("-", "")
                if norm_symbol in positions and positions[norm_symbol]:
                    # Assuming one position per symbol
                    size = positions[norm_symbol][0]["size"]

            if size:
                params = {
                    "symbol": symbol.replace("/", "-").replace("USDT", "-USDT") if "-" not in symbol else symbol,
                    "side": side,
                    "positionSide": position_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": tp,
                    "workingType": "MARK_PRICE",
                    "quantity": size
                }
                # Use make_request directly as place_order wrapper might be too simple
                endpoint = "/openApi/swap/v2/trade/order"
                self.make_request("post", endpoint, params)
            else:
                error(f"❌ Cannot set TP: Position size unknown for {symbol}")

        if sl:
            info(f"🔄 Setting SL for {symbol} ({position_side}) at {sl}")
            # Fetch size again if needed (optimized in real code)
            size = quantity
            if not size:
                positions = self.get_positions()
                norm_symbol = symbol.replace("-", "")
                if norm_symbol in positions and positions[norm_symbol]:
                    size = positions[norm_symbol][0]["size"]

            if size:
                params = {
                    "symbol": symbol.replace("/", "-").replace("USDT", "-USDT") if "-" not in symbol else symbol,
                    "side": side,
                    "positionSide": position_side,
                    "type": "STOP_MARKET",
                    "stopPrice": sl,
                    "workingType": "MARK_PRICE",
                    "quantity": size
                }
                endpoint = "/openApi/swap/v2/trade/order"
                self.make_request("post", endpoint, params)
            else:
                error(f"❌ Cannot set SL: Position size unknown for {symbol}")

# Global instance
bingx_client = BingXClient()
