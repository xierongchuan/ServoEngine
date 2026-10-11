"""Совместимый словарный адаптер BingX; транспорт и безопасность только в impl."""

import threading

from .config.bingx_config import BingXConfig
from .exchange_client import ExchangeClient
from .impl.bingx_client import BingXClient as RESTClient
from .impl.bingx_client import _fail
from .errors import UnsupportedCapabilityError


class BingXClient(ExchangeClient):
    EXCHANGE_NAME = "bingx"

    def __init__(self, api_key=None, secret_key=None, *, config=None, trading_settings=None, submission_observer=None):
        config = config or BingXConfig(trading_settings=trading_settings)
        self._client = RESTClient(config, trading_settings=trading_settings,
                                  submission_observer=submission_observer,
                                  api_key=api_key, secret_key=secret_key)

    @property
    def api_key(self):
        return self._client.api_key

    @property
    def secret_key(self):
        return self._client.secret_key

    @property
    def base_url(self):
        return self._client.base_url

    @property
    def market_base_url(self):
        return self._client.market_base_url

    @property
    def capabilities(self):
        return self._client.capabilities

    @property
    def last_submission(self):
        return self._client.last_submission

    @property
    def last_protection_confirmed(self):
        return self._client.last_protection_confirmed

    def check_prerequisites(self):
        return self._client.check_prerequisites()

    def make_request(self, method, endpoint, params=None):
        if method.lower() != "get":
            _fail(UnsupportedCapabilityError, "BingX: raw mutation запрещена; используйте адресные методы")
        return self._client._make_request(method, endpoint, params)

    def _format_symbol(self, symbol):
        return self._client._format_symbol(symbol)

    def normalize_symbol(self, symbol):
        return self._format_symbol(symbol)

    def denormalize_symbol(self, symbol):
        return self._client.denormalize_symbol(symbol)

    def get_instrument_rules(self, symbol):
        return self._client.get_instrument_rules(symbol)

    def get_perpetual_balance(self):
        balance = self._client.get_balance()
        return {"balance": balance.total_balance, "available": balance.available_balance,
                "equity": balance.total_with_pnl, "unrealizedProfit": balance.unrealized_pnl,
                "asset": balance.asset}

    def get_balance(self):
        return self.get_perpetual_balance()

    def get_all_balances(self):
        return {"perpetual": self.get_perpetual_balance()}

    def get_spot_balance(self):
        _fail(UnsupportedCapabilityError, "BingX Swap не обслуживает Spot")

    def get_standard_futures_balance(self):
        _fail(UnsupportedCapabilityError, "BingX Swap не обслуживает Standard Futures")

    def get_positions(self, *, force_refresh=False):
        return {symbol: [{"type": "buy" if p.is_long else "sell", "entry": p.entry_price,
                          "dealId": p.position_id, "workingOrderId": p.position_id,
                          "created": p.created_at.isoformat() if p.created_at else None,
                          "size": p.size, "pnl": p.unrealized_pnl, "leverage": p.leverage,
                          "markPrice": p.mark_price} for p in positions]
                for symbol, positions in self._client.get_positions(force_refresh=force_refresh).items()}

    def get_positions_legacy(self):
        return self.get_positions()

    def place_order(self, symbol, side, price=None, quantity=None, type="MARKET", sl=None,
                    tp=None, positionSide=None, leverage=None, *, client_order_id=None):
        return self._client.place_order(symbol, side, quantity, order_type=type, price=price,
                                       sl=sl, tp=tp, position_side=positionSide, leverage=leverage,
                                       client_order_id=client_order_id)

    def close_position(self, symbol, position_id, percentage=1.0, *, client_order_id=None):
        return self._client.close_position(symbol, position_id, percentage, client_order_id=client_order_id)

    def set_leverage(self, symbol, leverage, side="LONG"):
        return self._client.set_leverage(symbol, leverage, side)

    def set_sl_tp(self, symbol, position_side, tp=None, sl=None, quantity=None, *, position_id=None):
        return self._client.set_sl_tp(symbol, position_side, sl=sl, tp=tp, quantity=quantity, position_id=position_id)

    def get_order(self, symbol, order_id=None, *, client_order_id=None):
        return self._client.get_order(symbol, order_id, client_order_id=client_order_id)

    @staticmethod
    def _order_dict(order):
        return dict(order.raw_data, orderId=order.order_id, symbol=order.symbol,
                    side=order.side.value, type=order.order_type.value, status=order.status.value,
                    positionSide=order.position_side.value, price=order.price,
                    origQty=order.quantity, executedQty=order.filled_quantity,
                    avgPrice=order.average_price, profit=order.realized_pnl, commission=order.commission)

    def get_open_orders(self, symbol=None):
        return [self._order_dict(o) for o in self._client.get_open_orders(symbol)]

    def get_recent_orders(self, symbol, limit=10):
        return [self._order_dict(o) for o in self._client.get_recent_orders(symbol, limit)]

    def cancel_order(self, symbol, order_id):
        return self._client.cancel_order(symbol, order_id)

    def cancel_all_orders(self, symbol):
        return self._client.cancel_all_orders(symbol)

    def get_klines(self, symbol, interval="5m", limit=288):
        return self.get_kline_data(symbol, interval, limit)

    def get_kline_data(self, symbol, interval="5m", limit=288):
        return self._client.get_kline_data(symbol, interval, limit)

    def _fetch_klines_rest(self, symbol, interval="5m", limit=288):
        return self.get_kline_data(symbol, interval, limit)

    def get_ticker(self, symbol):
        t = self._client.get_ticker(symbol)
        return {"lastPrice": t.last_price, "bidPrice": t.bid_price, "askPrice": t.ask_price,
                "volume": t.volume_24h, "quoteVolume": t.quote_volume_24h}

    def get_order_book(self, symbol, limit=20):
        book = self._client.get_order_book(symbol, limit)
        return {"bids": book.bids, "asks": book.asks}

    def get_commission_rate(self, symbol):
        rate = self._client.get_commission_rate(symbol)
        return {"maker": rate.maker, "taker": rate.taker}

    def get_funding_rate(self, symbol):
        rate = self._client.get_funding_rate(symbol)
        return {"funding_rate": rate.funding_rate, "funding_rate_pct": rate.funding_rate_pct,
                "next_funding_time": rate.next_funding_time}

    def invalidate_cache(self, cache_type=None):
        self._client.invalidate_cache(cache_type)

    @classmethod
    def reset_class_cache(cls):
        # Общего кэша аккаунтов больше нет; метод оставлен старым тестам/интеграциям.
        return None

    @classmethod
    def invalidate_positions_cache(cls):
        # По умолчанию позиции вообще не кэшируются.
        return None

    def register_owned_order(self, *args, **kwargs):
        return self._client.register_owned_order(*args, **kwargs)

    def register_owned_position(self, *args, **kwargs):
        return self._client.register_owned_position(*args, **kwargs)

    def register_owned_protection(self, *args, **kwargs):
        return self._client.register_owned_protection(*args, **kwargs)

    def reconcile_submission(self, request_params):
        return self._client.reconcile_submission(request_params)


class _LazyClient:
    """Импорт не загружает конфиг/секреты и не создаёт транспорт."""
    def __init__(self):
        self._instance = None
        self._lock = threading.RLock()

    def __getattr__(self, name):
        with self._lock:
            if self._instance is None:
                self._instance = BingXClient()
            return getattr(self._instance, name)


bingx_client = _LazyClient()
