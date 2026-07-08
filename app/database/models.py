"""ORM models for candles, hypotheses, backtests, and reports."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.db import Base


class Candle(Base):
    """A single OHLCV bar for one asset/timeframe."""

    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("asset", "timeframe", "open_time", name="uq_candle"),
    )

    id = Column(Integer, primary_key=True)
    asset = Column(String, index=True, nullable=False)      # e.g. BTC/USDT
    timeframe = Column(String, index=True, nullable=False)  # 1h | 4h
    open_time = Column(BigInteger, index=True, nullable=False)  # ms epoch
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)


class Hypothesis(Base):
    """A testable market hypothesis."""

    __tablename__ = "hypotheses"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    logic = Column(Text, nullable=False)          # human-readable logic
    conditions = Column(JSON, nullable=False)     # machine-readable rule spec
    direction = Column(String, nullable=False)    # long | short
    timeframe = Column(String, nullable=False)
    asset = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    backtests = relationship("Backtest", back_populates="hypothesis",
                             cascade="all, delete-orphan")


class Backtest(Base):
    """Backtest result metrics for a hypothesis."""

    __tablename__ = "backtests"

    id = Column(Integer, primary_key=True)
    hypothesis_id = Column(Integer, ForeignKey("hypotheses.id"), index=True)

    total_return = Column(Float)      # fraction, e.g. 0.35 = +35%
    win_rate = Column(Float)          # fraction 0..1
    profit_factor = Column(Float)
    max_drawdown = Column(Float)      # fraction 0..1 (positive number)
    sharpe = Column(Float)
    num_trades = Column(Integer)
    avg_win = Column(Float)
    avg_loss = Column(Float)

    rejected = Column(Boolean, default=False)
    reject_reasons = Column(JSON, default=list)
    equity_curve = Column(JSON, default=list)     # list[{t, equity}]
    created_at = Column(DateTime, default=datetime.utcnow)

    hypothesis = relationship("Hypothesis", back_populates="backtests")
    report = relationship("Report", back_populates="backtest",
                          uselist=False, cascade="all, delete-orphan")


class Report(Base):
    """Generated markdown research report tied to a backtest."""

    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    backtest_id = Column(Integer, ForeignKey("backtests.id"), index=True)
    hypothesis_id = Column(Integer, ForeignKey("hypotheses.id"), index=True)
    title = Column(String, nullable=False)
    markdown = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    backtest = relationship("Backtest", back_populates="report")
