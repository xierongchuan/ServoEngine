"""Opt-in GET-проверка BingX VST с жёстким запретом торговых HTTP-методов.

Запускается только в Podman с --env-file .env; конфигурацию проекта не меняет.
Суммы, ключи, подписи и идентификаторы позиций не выводятся.
"""

import json
from urllib.parse import urlsplit
from unittest.mock import patch

import requests

from src.exchanges.config.bingx_config import BingXConfig
from src.exchanges.impl.bingx_client import BingXClient
from src.exchanges.rate_limit import get_exchange_rate_limiters
from src.trading_settings import validate_trading_settings


def main():
    settings = validate_trading_settings({"exchange": "bingx", "execution_mode": "demo"})
    config = BingXConfig(settings=settings)
    if not config.api_key or not config.secret_key:
        raise RuntimeError("Для GET-проверки VST нужны ключи BingX")
    limiter = get_exchange_rate_limiters("bingx", data_dir="/tmp/vst-get-probe")["private"]
    client = BingXClient(config, rate_limiter=limiter)
    # Даже пользовательский override public host не может изменить цель проверки.
    client.market_base_url = "https://open-api.bingx.com"
    original = requests.sessions.Session.request
    reads = []

    def get_only(session, method, url, **kwargs):
        parsed = urlsplit(url)
        private = bool(kwargs.get("headers", {}).get("X-BX-APIKEY"))
        if str(method).upper() != "GET":
            raise RuntimeError("Проверка запрещает любые торговые HTTP-методы")
        if parsed.scheme != "https" or parsed.hostname not in {"open-api-vst.bingx.com", "open-api.bingx.com"}:
            raise RuntimeError("Проверка запрещает неподтверждённый host")
        if (private or "signature=" in parsed.query) and parsed.hostname != "open-api-vst.bingx.com":
            raise RuntimeError("Private GET разрешён только на VST")
        if kwargs.get("allow_redirects") is not False:
            raise RuntimeError("Проверка запрещает HTTP redirects")
        reads.append({"host": parsed.hostname, "path": parsed.path})
        return original(session, method, url, **kwargs)

    with patch.object(requests.sessions.Session, "request", get_only):
        clock_ok = client._sync_server_clock()
        balance = client.get_balance()
        positions = client.get_positions(force_refresh=True)
        orders = client.get_open_orders("BTC-USDT")
        rules = client.get_instrument_rules("BTC-USDT")
    print(json.dumps({
        "private_endpoint": client.base_url, "execution_mode": settings["execution_mode"],
        "server_clock_confirmed": clock_ok, "balance_asset": balance.asset,
        "position_symbols": len(positions), "btc_open_orders": len(orders),
        "instrument_rules_confirmed": rules is not None,
        "http_get_count": len(reads), "mutations": 0,
    }, ensure_ascii=False, indent=2), flush=True)
    if not clock_ok or rules is None:
        raise RuntimeError("GET-проверка VST не подтвердила время/правила инструмента")


if __name__ == "__main__":
    main()
