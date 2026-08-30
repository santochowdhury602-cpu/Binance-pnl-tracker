"""
PnL calculation.

Spot: FIFO cost-basis matching per base asset. Every BUY adds a lot,
every SELL consumes lots oldest-first and realizes (sell_price - lot_price) * qty.
Fees are subtracted in the trade's fee asset (approximated in quote terms).

Futures: Binance already reports REALIZED_PNL per income row, so we just
sum it, and sum COMMISSION and FUNDING_FEE separately so you can see the
full cost breakdown, not just "PnL".
"""
from collections import defaultdict, deque
from sqlalchemy.orm import Session
from db import SpotTrade, FuturesIncome


def symbol_base_asset(symbol: str, quote_assets=("USDT", "BUSD", "USDC", "BTC", "ETH", "BNB", "FDUSD")):
    for q in quote_assets:
        if symbol.endswith(q):
            return symbol[: -len(q)], q
    return symbol, ""


def compute_spot_pnl(db: Session):
    """Returns dict: {base_asset: {"realized": float, "fees": float, "trades": int}}"""
    trades = db.query(SpotTrade).order_by(SpotTrade.time.asc()).all()
    lots = defaultdict(deque)   # base_asset -> deque of [qty, price]
    result = defaultdict(lambda: {"realized": 0.0, "fees": 0.0, "trades": 0, "buy_qty": 0.0, "sell_qty": 0.0})

    for t in trades:
        base, quote = symbol_base_asset(t.symbol)
        r = result[base]
        r["trades"] += 1
        # crude fee normalization: if fee charged in quote asset, count directly;
        # if in base asset, convert at trade price; otherwise ignore (BNB fee discount case)
        if t.commission_asset == quote:
            r["fees"] += t.commission
        elif t.commission_asset == base:
            r["fees"] += t.commission * t.price

        if t.is_buyer:
            lots[base].append([t.qty, t.price])
            r["buy_qty"] += t.qty
        else:
            qty_to_sell = t.qty
            r["sell_qty"] += t.qty
            dq = lots[base]
            while qty_to_sell > 1e-12 and dq:
                lot_qty, lot_price = dq[0]
                matched = min(lot_qty, qty_to_sell)
                r["realized"] += (t.price - lot_price) * matched
                lot_qty -= matched
                qty_to_sell -= matched
                if lot_qty <= 1e-12:
                    dq.popleft()
                else:
                    dq[0][0] = lot_qty
            # if qty_to_sell remains, it means sells predate our sync window —
            # ignore rather than guess a cost basis

    # open position size + remaining cost basis (unrealized needs live price, left to frontend)
    for base, dq in lots.items():
        open_qty = sum(q for q, _ in dq)
        cost = sum(q * p for q, p in dq)
        result[base]["open_qty"] = open_qty
        result[base]["avg_cost"] = (cost / open_qty) if open_qty > 1e-12 else 0.0

    return result


def compute_futures_pnl(db: Session):
    """Returns dict: {symbol: {"realized_pnl":..., "commission":..., "funding":...}}"""
    rows = db.query(FuturesIncome).all()
    result = defaultdict(lambda: {"realized_pnl": 0.0, "commission": 0.0, "funding": 0.0, "other": 0.0})
    for row in rows:
        sym = row.symbol or "OTHER"
        r = result[sym]
        if row.income_type == "REALIZED_PNL":
            r["realized_pnl"] += row.income
        elif row.income_type == "COMMISSION":
            r["commission"] += row.income
        elif row.income_type == "FUNDING_FEE":
            r["funding"] += row.income
        else:
            r["other"] += row.income
    return result


def daily_pnl_series(db: Session):
    """Combined day-by-day net PnL (spot realized approximated by trade day + futures income)."""
    from datetime import datetime, timezone
    buckets = defaultdict(float)

    # futures: exact, from income rows
    for row in db.query(FuturesIncome).all():
        day = datetime.fromtimestamp(row.time / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        buckets[day] += row.income

    # spot: recompute FIFO but bucket realized gains by the sell's day
    trades = db.query(SpotTrade).order_by(SpotTrade.time.asc()).all()
    lots = defaultdict(deque)
    for t in trades:
        base, quote = symbol_base_asset(t.symbol)
        if t.is_buyer:
            lots[base].append([t.qty, t.price])
        else:
            qty_to_sell = t.qty
            dq = lots[base]
            day = datetime.fromtimestamp(t.time / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            realized = 0.0
            while qty_to_sell > 1e-12 and dq:
                lot_qty, lot_price = dq[0]
                matched = min(lot_qty, qty_to_sell)
                realized += (t.price - lot_price) * matched
                lot_qty -= matched
                qty_to_sell -= matched
                if lot_qty <= 1e-12:
                    dq.popleft()
                else:
                    dq[0][0] = lot_qty
            buckets[day] += realized
            fee = t.commission if t.commission_asset == quote else 0.0
            buckets[day] -= fee

    return [{"date": d, "pnl": round(v, 4)} for d, v in sorted(buckets.items())]
