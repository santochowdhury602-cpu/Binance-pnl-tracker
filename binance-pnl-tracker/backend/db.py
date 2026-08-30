"""
SQLite database setup and models.
On Render, mount a persistent disk and point DB_PATH at it,
otherwise the DB resets on every deploy.
"""
import os
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, BigInteger, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = os.getenv("DB_PATH", "./data/pnl.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class SpotTrade(Base):
    __tablename__ = "spot_trades"
    id = Column(Integer, primary_key=True)
    trade_id = Column(BigInteger, unique=True, index=True)
    symbol = Column(String, index=True)
    price = Column(Float)
    qty = Column(Float)
    quote_qty = Column(Float)
    commission = Column(Float)
    commission_asset = Column(String)
    is_buyer = Column(Boolean)
    time = Column(BigInteger, index=True)  # ms epoch


class FuturesIncome(Base):
    __tablename__ = "futures_income"
    id = Column(Integer, primary_key=True)
    tran_id = Column(BigInteger, unique=True, index=True)
    symbol = Column(String, index=True)
    income_type = Column(String)  # REALIZED_PNL, COMMISSION, FUNDING_FEE, etc.
    income = Column(Float)
    asset = Column(String)
    time = Column(BigInteger, index=True)
    info = Column(String, nullable=True)


class SyncState(Base):
    __tablename__ = "sync_state"
    key = Column(String, primary_key=True)  # "spot:<symbol>" or "futures"
    last_time = Column(BigInteger, default=0)


class DiscoverStatus(Base):
    """Single-row table tracking the background full-market symbol scan."""
    __tablename__ = "discover_status"
    id = Column(Integer, primary_key=True, default=1)
    running = Column(Boolean, default=False)
    scanned = Column(Integer, default=0)
    total = Column(Integer, default=0)
    found = Column(Integer, default=0)
    message = Column(String, default="")


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
