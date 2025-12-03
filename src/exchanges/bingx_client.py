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
        
        # Если есть ключи, добавляем подпись
        if self.api_key and self.secret_key:
            params["apiKey"] = self.api_key
            params["signature"] = self._get_sign(params)
            headers = {
                "X-BX-APIKEY": self.api_key,
            }
        else:
            # Для публичных эндпоинтов без ключей
            headers = {}

        url = f"{self.base_url}{endpoint}"

        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                if method.lower() == "get":
                    response = requests.get(url, params=params, headers=headers, timeout=20)
                elif method.lower() == "post":
                    response = requests.post(url, params=params, headers=headers, timeout=20)
                elif method.lower() == "delete":
                    response = requests.delete(url, params=params, headers=headers, timeout=20)
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
                    
                symbol = pos.get("symbol", "").replace("-", "/")
                
                if symbol not in positions:
                    positions[symbol] = []
                
                # Adapt to Capital.com format structure
                positions[symbol].append({
                    "type": "buy" if size > 0 else "sell",
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

    def place_order(self, symbol, side, price, quantity, type="MARKET", sl=None, tp=None):
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
            params["takeProfit"] = json.dumps({"type": "TP", "stopPrice": tp, "price": tp})
        if sl:
            params["stopLoss"] = json.dumps({"type": "SL", "stopPrice": sl, "price": sl})

        # Note: BingX API for TP/SL might be complex. 
        # Often it's easier to place the order first, then place TP/SL orders.
        # For now, let's just place the main order.
        
        response = self.make_request("post", endpoint, params)
        
        if response and response.get("code") == 0:
            order_data = response.get("data", {})
            order_id = order_data.get("orderId")
            info(f"✅ BingX Order Placed: {order_id}")
            return order_id
        else:
            error(f"❌ Failed to place BingX order: {response}")
            return None

    def close_position(self, symbol, position_id):
        """Закрывает позицию"""
        # In BingX, to close a position, you place an opposing order
        # Or use the "close all" endpoint if available
        # Or use /openApi/swap/v2/trade/closeAll (Close All Positions)
        
        # To close a specific position, we usually place a market order in opposite direction
        # But we need to know the size.
        
        # Let's fetch the position first to get size
        positions = self.get_positions()
        target_pos = None
        
        if symbol in positions:
            for p in positions[symbol]:
                if p["dealId"] == position_id:
                    target_pos = p
                    break
        
        if not target_pos:
            error(f"❌ Position {position_id} not found for closing")
            return False
            
        formatted_symbol = symbol.replace("/", "-")
        side = "SELL" if target_pos["type"] == "buy" else "BUY"
        size = target_pos["size"]
        
        return self.place_order(symbol, side, 0, size, "MARKET")

# Global instance
bingx_client = BingXClient()
