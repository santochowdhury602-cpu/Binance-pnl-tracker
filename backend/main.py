import os
import hashlib
import hmac
import time
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import binance_client as bx
from db import init_db, get_session, SpotTrade, FuturesIncome, SyncState
import pnl

app = FastAPI(title="Binance PnL Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your Telegram WebApp origin once deployed
    allow_methods=["*"],
    allow_headers=["*"],
)

# comma-separated list, e.g. "BTCUSDT,ETHUSDT,SOLUSDT"
SPOT_SYMBOLS = [s.strip() for s in os.getenv("SPOT_SYMBOLS", "").split(",") if s.strip()]
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


@app.on_event("startup")
def startup():
    init_db()


# ---------- Telegram WebApp auth verification ----------

def verify_telegram_init_data(init_data: str) -> bool:
    """Validates the initData string Telegram sends the Mini App, per
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not TELEGRAM_BOT_TOKEN:
        return True  # skip check if not configured (local dev)
    parsed = dict(parse_qsl(init_data))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return False
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return computed_hash == received_hash


# ---------- Sync endpoints ----------

@app.post("/api/sync/spot")
def sync_spot():
    if not SPOT_SYMBOLS:
        raise HTTPException(400, "Set SPOT_SYMBOLS env var to a comma-separated symbol list, e.g. BTCUSDT,ETHUSDT")
    db = get_session()
    added = 0
    try:
        for symbol in SPOT_SYMBOLS:
            state = db.get(SyncState, f"spot:{symbol}")
            start_time = (state.last_time + 1) if state else None
            trades = bx.get_spot_trades(symbol, start_time=start_time)
            latest_time = start_time or 0
            for t in trades:
                if db.get(SpotTrade, t["id"]):
                    continue
                row = SpotTrade(
                    trade_id=t["id"],
                    symbol=symbol,
                    price=float(t["price"]),
                    qty=float(t["qty"]),
                    quote_qty=float(t["quoteQty"]),
                    commission=float(t["commission"]),
                    commission_asset=t["commissionAsset"],
                    is_buyer=t["isBuyer"],
                    time=t["time"],
                )
                db.add(row)
                added += 1
                latest_time = max(latest_time, t["time"])
            if not state:
                state = SyncState(key=f"spot:{symbol}", last_time=latest_time)
                db.add(state)
            else:
                state.last_time = latest_time
        db.commit()
        return {"added": added}
    finally:
        db.close()


@app.post("/api/sync/futures")
def sync_futures():
    db = get_session()
    added = 0
    try:
        state = db.get(SyncState, "futures")
        start_time = (state.last_time + 1) if state else None
        latest_time = start_time or 0
        rows = bx.get_futures_income(start_time=start_time)
        for r in rows:
            if db.get(FuturesIncome, r["tranId"]):
                continue
            row = FuturesIncome(
                tran_id=r["tranId"],
                symbol=r.get("symbol") or "",
                income_type=r["incomeType"],
                income=float(r["income"]),
                asset=r["asset"],
                time=r["time"],
                info=r.get("info"),
            )
            db.add(row)
            added += 1
            latest_time = max(latest_time, r["time"])
        if not state:
            state = SyncState(key="futures", last_time=latest_time)
            db.add(state)
        else:
            state.last_time = latest_time
        db.commit()
        return {"added": added}
    finally:
        db.close()


# ---------- Read endpoints ----------

@app.get("/api/pnl/summary")
def pnl_summary():
    db = get_session()
    try:
        spot = pnl.compute_spot_pnl(db)
        fut = pnl.compute_futures_pnl(db)
        spot_realized = sum(v["realized"] - v["fees"] for v in spot.values())
        fut_realized = sum(v["realized_pnl"] + v["commission"] + v["funding"] for v in fut.values())
        return {
            "spot_realized_net": round(spot_realized, 4),
            "futures_realized_net": round(fut_realized, 4),
            "total_net": round(spot_realized + fut_realized, 4),
            "spot_by_asset": spot,
            "futures_by_symbol": fut,
        }
    finally:
        db.close()


@app.get("/api/pnl/history")
def pnl_history():
    db = get_session()
    try:
        return pnl.daily_pnl_series(db)
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"ok": True, "time": int(time.time())}


# ---------- Serve the Telegram Mini App static files ----------
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
