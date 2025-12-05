import requests
import time
import json
from src.config import USERNAME, PASSWORD, CAP_API_KEY, API_BASE, MODE, SYMBOLS
from src.utils.logger import info, error, warning
from .exchange_client import ExchangeClient

class CapitalClient(ExchangeClient):
    def __init__(self):
        self.base_url = API_BASE
        self._session_initialized = False
        self._cached_tokens = None
        self._tokens_cache_time = 0
        self.TOKEN_CACHE_TTL = 600

        # Map user-friendly intervals to Capital.com constants
        self.INTERVAL_MAP = {
            "1m": "MINUTE_1",
            "5m": "MINUTE_5",
            "15m": "MINUTE_15",
            "30m": "MINUTE_30",
            "1h": "HOUR_1",
            "4h": "HOUR_4",
            "1d": "DAY_1",
            "1w": "WEEK_1"
        }

    def check_prerequisites(self):
        """Checks if API keys are configured"""
        if not USERNAME or not PASSWORD or not CAP_API_KEY:
            error("❌ Capital.com credentials missing. Set CAP_API_USERNAME, CAP_API_PASSWORD, CAP_API_KEY.")
            return False
        return True

    def _get_session_token(self):
        """Authenticates and returns tokens"""
        current_time = time.time()
        if self._cached_tokens and (current_time - self._tokens_cache_time) < self.TOKEN_CACHE_TTL:
            return self._cached_tokens

        url = f"{self.base_url}session"
        headers = {
            "X-CAP-API-KEY": CAP_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "identifier": USERNAME,
            "password": PASSWORD,
            "encryptedPassword": False
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()

            tokens = {
                "cst": response.headers.get("CST"),
                "security_token": response.headers.get("X-SECURITY-TOKEN")
            }

            self._cached_tokens = tokens
            self._tokens_cache_time = current_time
            return tokens
        except Exception as e:
            error(f"❌ Auth failed: {e}")
            raise

    def _select_account(self):
        """Selects the appropriate account"""
        headers = self._get_headers()
        url = f"{self.base_url}accounts"

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            accounts = response.json().get("accounts", [])

            target_account = None
            for account in accounts:
                account_type = account.get("accountType")
                if MODE == "demo":
                    if account_type in ["CFD", "SPREADBET", "DEMO"]:
                        target_account = account
                        break
                else:
                    if account_type in ["CFD", "SPREADBET"]:
                        target_account = account
                        break

            if not target_account:
                raise Exception("No suitable account found")

            # Activate account
            session_url = f"{self.base_url}session"
            payload = {"accountId": target_account["accountId"]}
            headers["Version"] = "2"
            requests.put(session_url, json=payload, headers=headers, timeout=10)

            return target_account
        except Exception as e:
            error(f"❌ Account selection failed: {e}")
            raise

    def _init_session(self, force=False):
        """Initializes session"""
        if self._session_initialized and not force:
            return

        try:
            self._get_session_token()
            self._select_account()
            self._session_initialized = True
        except Exception:
            self._session_initialized = False
            raise

    def _get_headers(self):
        """Returns headers with tokens"""
        tokens = self._get_session_token()
        return {
            "X-SECURITY-TOKEN": tokens["security_token"],
            "CST": tokens["cst"],
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _make_request(self, method, endpoint, **kwargs):
        """Generic request handler with retry"""
        url = f"{self.base_url}{endpoint}"
        max_retries = 3

        for attempt in range(max_retries):
            try:
                if not "headers" in kwargs:
                    kwargs["headers"] = self._get_headers()

                response = requests.request(method, url, **kwargs)

                if response.status_code == 401 and attempt < max_retries - 1:
                    self._init_session(force=True)
                    kwargs["headers"] = self._get_headers() # Refresh headers
                    continue

                response.raise_for_status()
                return response
            except Exception as e:
                if attempt == max_retries - 1:
                    error(f"❌ Request failed: {e}")
                    return None
                time.sleep(1)
        return None

    def get_balance(self):
        """Get account balance"""
        self._init_session()
        response = self._make_request("get", "accounts")
        if response:
            accounts = response.json().get("accounts", [])
            # Assuming the active account is what we want, or we sum them up?
            # For simplicity, return the first one's balance or structure
            if accounts:
                return accounts[0].get("balance", {}).get("balance", 0)
        return 0

    def get_kline_data(self, symbol, interval="MINUTE_5", limit=288):
        """Get historical candles"""
        self._init_session()
        from src.utils.symbols import get_epic
        epic = get_epic(symbol)

        # Map interval if needed
        capital_interval = self.INTERVAL_MAP.get(interval, interval)

        params = {"resolution": capital_interval, "max": limit}
        headers = self._get_headers()
        headers["Version"] = "2"

        response = self._make_request("get", f"prices/{epic}", params=params, headers=headers)
        if response:
            prices = response.json().get("prices", [])
            return prices # Capital format: snapshotTimeUTC, openPrice, etc.
        return []

    def get_positions(self):
        """Get open positions"""
        self._init_session()
        response = self._make_request("get", "positions")
        positions = {}

        if response:

            for p in data:
                market = p.get("market", {})
                epic = market.get("epic", "")
                symbol = epic

                if symbol not in positions:
                    positions[symbol] = []

                pos = p["position"]
                positions[symbol].append({
                    "type": pos["direction"].lower(),
                    "entry": pos["level"],
                    "dealId": pos["dealId"],
                    "workingOrderId": pos.get("workingOrderId", ""),
                    "created": pos["createdDate"],
                    "size": pos["size"],
                    "pnl": pos["upl"]
                })
        return positions

    def place_order(self, symbol, side, price, quantity, type="MARKET", sl=None, tp=None):
        """Place order and return dealId"""
        self._init_session()
        from src.utils.symbols import get_epic
        epic = get_epic(symbol)

        payload = {
            "epic": epic,
            "direction": side.upper(),
            "size": quantity,
            "type": type.upper(),
            "forceOpen": True,
            "currencyCode": "USD"
        }

        if sl:
            payload["stopLevel"] = sl
        if tp:
            payload["profitLevel"] = tp

        response = self._make_request("post", "positions", json=payload)
        if response:
            deal_reference = response.json().get("dealReference")
            if not deal_reference:
                error("❌ No dealReference in response")
                return None

            # Confirm order to get dealId
            confirm_url = f"confirms/{deal_reference}"
            confirm_response = self._make_request("get", confirm_url)
            if confirm_response:
                deal_id = confirm_response.json().get("dealId")
                if deal_id:
                    return deal_id
                else:
                    error(f"❌ No dealId in confirmation: {confirm_response.json()}")
            else:
                error("❌ Confirmation request failed")

        return None

    def close_position(self, symbol, position_id):
        """Close position"""
        self._init_session()
        # Capital.com closes by dealId
        url = f"positions/{position_id}"
        response = self._make_request("delete", url)
        return response is not None
