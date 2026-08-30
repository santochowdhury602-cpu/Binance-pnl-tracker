"""
Minimal signed Binance REST client — no third-party SDK, so you can see
exactly what's being sent. Uses read-only endpoints only.

Your API key should be created with "Enable Reading" ONLY.
Never enable withdrawals on the key you put in this project.
"""
import hmac
import hashlib
import time
import os
import requests

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"

API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")


def _sign(params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10000
    query = "&".join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(
        API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    params["signature"] = signature
    return params


def _get(base: str, path: str, params: dict | None = None):
    params = params or {}
    headers = {"X-MBX-APIKEY": API_KEY}
    signed = _sign(params)
    resp = requests.get(f"{base}{path}", headers=headers, params=signed, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_spot_symbols_with_balance_history() -> list[str]:
    """Account info doesn't list historical symbols, so callers should
    pass a known symbol list (see main.py SYMBOLS env var)."""
    return _get(SPOT_BASE, "/api/v3/account")


def get_spot_trades(symbol: str, start_time: int | None = None, limit: int = 1000):
    params = {"symbol": symbol, "limit": limit}
    if start_time:
        params["startTime"] = start_time
    return _get(SPOT_BASE, "/api/v3/myTrades", params)


def get_futures_income(start_time: int | None = None, limit: int = 1000, income_type: str | None = None):
    params = {"limit": limit}
    if start_time:
        params["startTime"] = start_time
    if income_type:
        params["incomeType"] = income_type
    return _get(FUTURES_BASE, "/fapi/v1/income", params)


def get_futures_positions():
    return _get(FUTURES_BASE, "/fapi/v2/positionRisk")


def get_spot_account():
    return _get(SPOT_BASE, "/api/v3/account")


def get_exchange_info():
    """Public endpoint, no signing needed — full list of every symbol Binance lists."""
    resp = requests.get(f"{SPOT_BASE}/api/v3/exchangeInfo", timeout=20)
    resp.raise_for_status()
    return resp.json()["symbols"]
