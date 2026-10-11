"""Явный bounded opt-in VST probe: один native entry, один full close, exact-ID cleanup.

Не импортировать как live pytest. Только Podman, временный SERVO_DATA_DIR и read-only mount.
Без подтверждения аргументом не создаёт клиент и не читает ключи.
"""

import argparse
import json
import os
import time
import uuid
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


class VSTProbeGuard:
    def __init__(self, *, listen_only=False):
        self.listen_only = listen_only
        self.deadline = time.monotonic() + 120
        self.reads = 0
        self.counts = {"entry": 0, "close": 0, "delete": 0, "listen": 0, "leverage": 0}
        self.client = None
        self.quantity = None

    def check_http(self, method, url, kwargs):
        parsed = urlsplit(url)
        method = method.upper()
        if time.monotonic() >= self.deadline or self.reads + sum(self.counts.values()) >= 150:
            raise RuntimeError("VST probe: исчерпан временной/HTTP budget; повтор запрещён")
        if (parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or parsed.password
                or parsed.hostname not in {"open-api-vst.bingx.com", "open-api.bingx.com"}
                or kwargs.get("allow_redirects") is not False):
            raise RuntimeError("VST probe: host/redirect запрещён")
        private = bool(kwargs.get("headers", {}).get("X-BX-APIKEY")) or "signature=" in parsed.query
        if (private or method != "GET") and parsed.hostname != "open-api-vst.bingx.com":
            raise RuntimeError("VST probe: production private/mutation запрещён")
        kwargs["timeout"] = 5
        if method == "GET":
            self.reads += 1
            return
        if self.client is None or self.client.settings["execution_mode"] != "demo":
            raise RuntimeError("VST probe: нет demo coordinator")
        params = {k: v[0] for k, v in parse_qs(kwargs.get("data") or parsed.query).items()}
        intent = getattr(self.client._local, "intent", None)
        if not intent:
            raise RuntimeError("VST probe: мутация вне durable intent запрещена")
        if method == "POST" and parsed.path == "/openApi/user/auth/userDataStream":
            kind = "listen"
        elif self.listen_only:
            raise RuntimeError("VST диагностика listenKey запрещает любые торговые мутации")
        elif method == "POST" and parsed.path == "/openApi/swap/v2/trade/leverage":
            if params.get("symbol") != "BTC-USDT" or params.get("leverage") != "1":
                raise RuntimeError("VST probe: неверное плечо/symbol")
            kind = "leverage"
        elif method == "POST" and parsed.path == "/openApi/swap/v2/trade/order":
            if params.get("symbol") != "BTC-USDT" or params.get("type") != "MARKET" or Decimal(params.get("quantity", "0")) != self.quantity:
                raise RuntimeError("VST probe: неверная торговая команда")
            if params.get("stopLoss") and params.get("takeProfit"):
                if intent["operation"] != "grid_entry" or params.get("side") != "BUY" or self.deadline - time.monotonic() < 60:
                    raise RuntimeError("VST probe: entry budget/side запрещён")
                self.client._native_flat("BTC-USDT")
                kind = "entry"
            else:
                if intent["operation"] != "close" or not intent["request"].get("native_entry_command") or params.get("side") != "SELL":
                    raise RuntimeError("VST probe: закрытие вне собственного native lifecycle запрещено")
                kind = "close"
        elif method == "DELETE" and parsed.path == "/openApi/swap/v2/trade/order":
            if params.get("symbol") != "BTC-USDT" or self.client._symbol_positions(self.client._positions(), "BTC-USDT"):
                raise RuntimeError("VST probe: DELETE без fresh flat запрещён")
            records = self.client._grid_records("BTC-USDT", pending=False)
            ids = {str(e.get("i")) for r in records for e in r["context"].get("native_links", [])
                   if str(e.get("li")) == str(r.get("order_id"))}
            if params.get("orderId") not in ids:
                raise RuntimeError("VST probe: DELETE чужого/неизвестного ID запрещён")
            kind = "delete"
        else:
            raise RuntimeError("VST probe: HTTP mutation endpoint запрещён")
        limit = 2 if kind == "delete" else 1
        if self.counts[kind] >= limit:
            raise RuntimeError("VST probe: повтор/лишняя мутация запрещены")
        self.counts[kind] += 1

    @staticmethod
    def check_ws(url, kwargs):
        parsed = urlsplit(url)
        if (parsed.scheme != "wss" or parsed.hostname != "vst-open-api-ws.bingx.com"
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or parsed.path != "/swap-market" or kwargs.get("redirect_limit") != 0):
            raise RuntimeError("VST probe: production/redirect WS запрещён")
        kwargs["timeout"] = 5


def response_shape(payload):
    """Только типы/имена полей и числовой code; ни token, ни raw body."""
    result = {"type": type(payload).__name__}
    if isinstance(payload, dict):
        result["fields"] = sorted(payload)
        result["field_types"] = {key: type(value).__name__ for key, value in payload.items()}
        code = payload.get("code")
        if type(code) is int:
            result["code"] = code
        if isinstance(payload.get("data"), dict):
            result["data_fields"] = sorted(payload["data"])
            result["data_field_types"] = {key: type(value).__name__ for key, value in payload["data"].items()}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Один native-protected MARKET только на VST; реальные сделки запрещены")
    optin = parser.add_mutually_exclusive_group(required=True)
    optin.add_argument("--confirm-vst-grid-market", action="store_true")
    optin.add_argument("--confirm-vst-listen-only", action="store_true")
    args = parser.parse_args(argv)
    root = Path("/vst-probes")
    if not root.is_dir():
        parser.error("Требуется отдельный writable mount .cache/vst-probes:/vst-probes; временный ledger запрещён")
    os.environ["SERVO_DATA_DIR"] = str(root / "runtime")
    import requests
    import websocket
    from src.core.execution_coordinator import attach_execution_safety
    from src.exchanges.config.bingx_config import BingXConfig
    from src.exchanges.impl.bingx_client import BingXClient
    from src.exchanges.rate_limit import get_exchange_rate_limiters
    from src.trading_settings import validate_trading_settings

    if args.confirm_vst_listen_only:
        directory = Path(mkdtemp(prefix="listen-", dir=root))
    else:
        # Один постоянный ledger/command ID: новый запуск не скрывает прежний UNKNOWN.
        directory = root / "native-market"
        directory.mkdir(mode=0o700, exist_ok=True)
    settings = validate_trading_settings({"exchange": "bingx", "execution_mode": "demo", "position_mode": "hedge",
        "protection": {"failure_policy": "close", "verification_timeout_seconds": 5}})
    raw = BingXClient(BingXConfig(settings=settings), rate_limiter=get_exchange_rate_limiters("bingx", data_dir=directory)["private"])
    if raw.base_url != "https://open-api-vst.bingx.com":
        raise RuntimeError("VST probe: REST endpoint не demo")
    raw.market_base_url = "https://open-api.bingx.com"
    client = attach_execution_safety(raw, settings=settings, journal_path=directory / "probe.sqlite",
                                     account_id="bounded-vst-probe", owner_id="bounded-vst-grid")
    guard = VSTProbeGuard(listen_only=args.confirm_vst_listen_only)
    guard.client = client
    http, connect = requests.sessions.Session.request, websocket.create_connection
    result = {"demo_only": True, "listen_only": args.confirm_vst_listen_only, "journal_directory": str(directory)}
    def guarded_http(session, method, url, **kwargs):
        guard.check_http(method, url, kwargs)
        response = http(session, method, url, **kwargs)
        if str(method).upper() != "GET":
            try:
                shape = response_shape(response.json())
            except ValueError:
                shape = {"type": "non_json"}
            path = urlsplit(url).path
            from src.utils.log_context import SecretRedactor
            try:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("code"):
                    shape["message"] = SecretRedactor().text(str(payload.get("msg", "")))[:1024]
            except ValueError:
                pass
            result.setdefault("mutation_response_shapes", []).append({"endpoint": path, "shape": shape})
            if path == "/openApi/user/auth/userDataStream":
                result["listen_response_shape"] = shape
        return response
    def guarded_ws(url, **kwargs):
        guard.check_ws(url, kwargs)
        return connect(url, **kwargs)
    with patch.object(requests.sessions.Session, "request", guarded_http), patch.object(websocket, "create_connection", guarded_ws):
        try:
            client._native_flat("BTC-USDT")
            if not raw._sync_server_clock():
                raise RuntimeError("VST probe: clock не подтверждён")
            result["balance_asset"] = raw.get_balance().asset
            if result["balance_asset"] != "VST":
                raise RuntimeError("VST probe: balance asset не VST; никакой entry не разрешён")
            if args.confirm_vst_listen_only:
                with client._operation("BTC-USDT", "grid_listen", {}, command_id="vst-listen") as (intent, fresh):
                    if not fresh:
                        raise RuntimeError("VST listenKey не переотправляется")
                    stream = raw.open_grid_attachment_stream(5)
                    try:
                        result["listen_schema"] = stream.listen_receipt.schema
                        result["private_ws_ready"] = stream.ready.is_set() and not stream.failed
                        client.journal.finish(client.account_id, intent["command_id"], "RESOLVED",
                            result={"listen_schema": result["listen_schema"], "private_ws_ready": result["private_ws_ready"]})
                    finally:
                        stream.close()
                result["listen_response_verified"] = True
            else:
                run_market_probe(client, raw, guard, result)
        except Exception as exc:
            # Не печатаем сетевые исключения: URL может содержать listenKey/подпись.
            result["failure_type"] = type(exc).__name__
            if type(getattr(exc, "code", None)) is int:
                result["failure_code"] = exc.code
        try:
            result["symbol_flat"] = not client._symbol_positions(client._positions(), "BTC-USDT")
            result["remaining_open_orders"] = len(raw.get_open_orders("BTC-USDT"))
            result["blocked"] = bool(client.journal.blocked(client.account_id, "BTC-USDT"))
        except Exception:
            result["final_readback_unknown"] = True
    result["http_reads"], result["bounded_mutations"] = guard.reads, guard.counts
    success = result.get("listen_response_verified") if args.confirm_vst_listen_only else result.get("active_owned_pair_verified")
    exit_code = 0 if (success and result.get("symbol_flat") and result.get("remaining_open_orders") == 0
                      and not result.get("blocked")) else 1
    result["exit_code"] = exit_code
    report_path = directory / f"report-{uuid.uuid4().hex}.json"
    result["report_path"] = str(report_path)
    with report_path.open("x", encoding="utf-8") as report:
        json.dump(result, report, ensure_ascii=False, indent=2)
        report.flush()
        os.fsync(report.fileno())
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return exit_code


def run_market_probe(client, raw, guard, result):
    rules = raw.get_instrument_rules("BTC-USDT")
    reference = raw._protection_reference("BTC-USDT", rules)
    last = Decimal(str(raw.get_ticker("BTC-USDT").last_price))
    step = rules.quantity_step * rules.contract_size
    qty = (max(rules.min_quantity, rules.min_notional / last) / step).to_integral_value(rounding=ROUND_CEILING) * step
    if qty <= 0 or qty * max(last, reference) > 100:
        raise RuntimeError("VST probe: min entry превышает 100 USDT demo notional")
    guard.quantity = qty
    result["stage"] = "native_entry"
    client.place_native_grid_entry("BTC-USDT", "BUY", qty, sl=reference * Decimal(".95"),
        tp=reference * Decimal("1.05"), leverage=1, gross_limit=qty, command_id="vst-entry")
    result["active_owned_pair_verified"] = True
    rows = client.journal.owned(client.account_id, owner=client.owner_id, symbol="BTC-USDT")
    if len(rows) != 1:
        raise RuntimeError("VST probe: нет единственного own position")
    result["stage"] = "full_close"
    client.close_position("BTC-USDT", rows[0]["position_id"], command_id="vst-close")
    records = client._grid_records("BTC-USDT", pending=False)
    if any((r.get("result") or {}).get("attachment", {}).get("cleanup_pending") for r in records):
        result["stage"] = "exact_cleanup"
        client.cleanup_native_grid("BTC-USDT", command_id="vst-cleanup")
    result["stage"] = "completed"


if __name__ == "__main__":
    raise SystemExit(main())
