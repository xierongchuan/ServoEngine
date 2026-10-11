"""Определение причины VST-отказа: архивный payload → /order/test, без торговых мутаций.

Журнал монтируется read-only. UNKNOWN не снимается и entry не повторяется.
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


class ValidationGuard:
    def __init__(self):
        self.deadline = time.monotonic() + 45
        self.reads = 0
        self.validations = 0

    def check(self, method, url, kwargs):
        parsed = urlsplit(url)
        if (time.monotonic() >= self.deadline or parsed.scheme != "https"
                or parsed.hostname != "open-api-vst.bingx.com" or parsed.port not in (None, 443)
                or parsed.username or parsed.password or kwargs.get("allow_redirects") is not False):
            raise RuntimeError("Проверка VST: host/redirect/time budget запрещён")
        method = method.upper()
        if (method == "GET" and self.reads < 8 and parsed.path in {
                "/openApi/swap/v2/server/time", "/openApi/swap/v2/user/balance"}):
            self.reads += 1
        elif method == "POST" and parsed.path == "/openApi/swap/v2/trade/order/test" and not self.validations:
            self.validations += 1
        else:
            raise RuntimeError("Проверка VST: торговые мутации и повтор POST запрещены")
        kwargs["timeout"] = 5


def archived_request(path):
    path = Path(path)
    # immutable безопасен только для остановленного probe без неперенесённого WAL.
    wal = Path(str(path) + "-wal")
    if not path.is_file() or (wal.exists() and wal.stat().st_size):
        raise RuntimeError("Архив VST отсутствует или имеет активный WAL")
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        rows = db.execute("SELECT event FROM wires WHERE account=? AND command_id=?",
                          ("bounded-vst-probe", "vst-entry")).fetchall()
    requests = [json.loads(row[0])["request_params"] for row in rows
                if json.loads(row[0]).get("endpoint") == "/openApi/swap/v2/trade/order"]
    if len(requests) != 1 or requests[0].get("symbol") != "BTC-USDT" or requests[0].get("type") != "MARKET":
        raise RuntimeError("Архив не содержит единственный ожидаемый VST entry")
    return requests[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Один /order/test на VST; entry, close и изменение ledger запрещены")
    parser.add_argument("--confirm-vst-validation-only", action="store_true", required=True)
    parser.parse_args(argv)
    import requests
    from src.exchanges.config.bingx_config import BingXConfig
    from src.exchanges.impl.bingx_client import BingXClient
    from src.exchanges.rate_limit import get_exchange_rate_limiters
    from src.trading_settings import validate_trading_settings
    from src.utils.log_context import SecretRedactor

    params = archived_request(Path("/probes/native-market/probe.sqlite"))
    settings = validate_trading_settings({"exchange": "bingx", "execution_mode": "demo"})
    limiter = get_exchange_rate_limiters("bingx", data_dir="/tmp/vst-validation")["private"]
    client = BingXClient(BingXConfig(settings=settings), rate_limiter=limiter)
    guard, redactor = ValidationGuard(), SecretRedactor()
    original = requests.sessions.Session.request
    result = {"demo_only": True, "trading_mutations": 0, "journal_modified": False}

    def guarded(session, method, url, **kwargs):
        guard.check(method, url, kwargs)
        return original(session, method, url, **kwargs)

    with patch.object(requests.sessions.Session, "request", guarded):
        try:
            if not client._sync_server_clock() or client.get_balance().asset != "VST":
                raise RuntimeError("Время/баланс VST не подтверждены")
            limiter.acquire()
            wire, headers = client._signed_payload("post", params)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            with requests.post(client.base_url + "/openApi/swap/v2/trade/order/test", data=wire,
                               headers=headers, timeout=5, allow_redirects=False, stream=True) as response:
                result["http_status"] = response.status_code
                body = bytearray()
                for chunk in response.iter_content(4096):
                    body.extend(chunk)
                    if len(body) > 65536 or time.monotonic() >= guard.deadline:
                        raise RuntimeError("Ответ VST превысил budget")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Неверный envelope")
                result["code"] = payload.get("code") if type(payload.get("code")) is int else None
                result["message"] = redactor.text(str(payload.get("msg", "")))[:1024]
                result["validation_ok"] = result["code"] == 0 and response.status_code == 200
                result["response_fields"] = sorted(payload)
        except Exception as exc:
            result["failure_type"] = type(exc).__name__
    result["get_count"], result["test_post_count"] = guard.reads, guard.validations
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result.get("validation_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
