import os
import hashlib
import hmac
import time
import threading
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import binance_client as bx
from db import init_db, get_session, SpotTrade, FuturesIncome, SyncState, DiscoverStatus
import pnl

app = FastAPI(title="Binance PnL Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your Telegram WebApp origin once deployed
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional manual list, e.g. "BTCUSDT,ETHUSDT,SOLUSDT" — leave blank to rely on
# auto-discovery instead (see /api/sync/discover).
SPOT_SYMBOLS = [s.strip() for s in os.getenv("SPOT_SYMBOLS", "").split(",") if s.strip()]
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DISCOVER_QUOTE_ASSETS = [
    s.strip() for s in os.getenv(
        "DISCOVER_QUOTE_ASSETS", "USDT,BUSD,USDC,FDUSD,BTC,ETH,BNB,TRY,EUR"
    ).split(",") if s.strip()
]


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

def _fetch_all_trades(symbol: str, start_time: int | None, limit: int = 1000, max_pages: int = 50):
    """Pages through myTrades so histories over 1000 trades aren't truncated."""
    all_trades = []
    cursor = start_time
    for _ in range(max_pages):
        batch = bx.get_spot_trades(symbol, start_time=cursor, limit=limit)
        if not batch:
            break
        all_trades.extend(batch)
        if len(batch) < limit:
            break
        cursor = batch[-1]["time"] + 1
    return all_trades


def _known_spot_symbols(db) -> list[str]:
    """Symbols we've already confirmed have trade history, from prior syncs/discovery."""
    rows = db.query(SyncState).filter(SyncState.key.like("spot:%")).all()
    return [r.key.split("spot:", 1)[1] for r in rows]


def _store_trades(db, symbol: str, trades: list, existing_state) -> int:
    added = 0
    latest_time = existing_state.last_time if existing_state else 0
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
    if trades:
        if existing_state:
            existing_state.last_time = latest_time
        else:
            db.add(SyncState(key=f"spot:{symbol}", last_time=latest_time))
    return added


@app.post("/api/sync/spot")
def sync_spot():
    """Fast incremental sync — only checks symbols already known (via SPOT_SYMBOLS
    or a prior /api/sync/discover run). Run discover first if you haven't yet."""
    db = get_session()
    try:
        symbols = SPOT_SYMBOLS or _known_spot_symbols(db)
        if not symbols:
            raise HTTPException(
                400,
                "No symbols known yet. Set SPOT_SYMBOLS, or run POST /api/sync/discover "
                "once to auto-scan every coin you've traded.",
            )
        added = 0
        for symbol in symbols:
            state = db.get(SyncState, f"spot:{symbol}")
            start_time = (state.last_time + 1) if state else None
            trades = _fetch_all_trades(symbol, start_time)
            added += _store_trades(db, symbol, trades, state)
        db.commit()
        return {"added": added, "symbols_checked": len(symbols)}
    finally:
        db.close()


@app.post("/api/sync/discover")
def start_discover():
    """Kicks off a background scan of every symbol on Binance (filtered to
    common quote assets) to find every coin you've ever spot-traded. Slow
    (rate-limited to ~1 request/sec) — poll /api/sync/discover/status."""
    db = get_session()
    try:
        status = db.get(DiscoverStatus, 1)
        if status and status.running:
            return {"started": False, "message": "Already running."}
        if not status:
            status = DiscoverStatus(id=1)
            db.add(status)
        status.running = True
        status.scanned = 0
        status.total = 0
        status.found = 0
        status.message = "Fetching symbol list…"
        db.commit()
    finally:
        db.close()

    thread = threading.Thread(target=_discover_worker, daemon=True)
    thread.start()
    return {"started": True}


def _discover_worker():
    db = get_session()
    try:
        status = db.get(DiscoverStatus, 1)
        all_symbols = bx.get_exchange_info()
        candidates = [
            s["symbol"] for s in all_symbols
            if s.get("quoteAsset") in DISCOVER_QUOTE_ASSETS
        ]
        known = set(_known_spot_symbols(db))
        to_scan = [s for s in candidates if s not in known]
        status.total = len(to_scan)
        status.message = f"Scanning {len(to_scan)} symbols…"
        db.commit()

        for symbol in to_scan:
            try:
                trades = _fetch_all_trades(symbol, start_time=None)
                if trades:
                    added = _store_trades(db, symbol, trades, None)
                    if added:
                        status.found += 1
                db.commit()
            except Exception as e:
                # keep going even if one symbol errors (e.g. delisted/invalid)
                print(f"discover error on {symbol}: {e}")
            status.scanned += 1
            db.commit()
            time.sleep(1.0)  # stay well under Binance's weight limit

        status.running = False
        status.message = f"Done. Found {status.found} traded symbols out of {status.total} scanned."
        db.commit()
    except Exception as e:
        status = db.get(DiscoverStatus, 1)
        status.running = False
        status.message = f"Failed: {e}"
        db.commit()
    finally:
        db.close()


@app.get("/api/sync/discover/status")
def discover_status():
    db = get_session()
    try:
        status = db.get(DiscoverStatus, 1)
        if not status:
            return {"running": False, "scanned": 0, "total": 0, "found": 0, "message": "Not started yet."}
        return {
            "running": status.running,
            "scanned": status.scanned,
            "total": status.total,
            "found": status.found,
            "message": status.message,
        }
    finally:
        db.close()


def _fetch_all_futures_income(start_time: int | None, limit: int = 1000, max_pages: int = 50):
    all_rows = []
    cursor = start_time
    for _ in range(max_pages):
        batch = bx.get_futures_income(start_time=cursor, limit=limit)
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < limit:
            break
        cursor = batch[-1]["time"] + 1
    return all_rows


@app.post("/api/sync/futures")
def sync_futures():
    db = get_session()
    added = 0
    try:
        state = db.get(SyncState, "futures")
        start_time = (state.last_time + 1) if state else None
        latest_time = start_time or 0
        rows = _fetch_all_futures_income(start_time=start_time)
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
