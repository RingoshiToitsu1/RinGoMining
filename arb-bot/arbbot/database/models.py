"""SQLAlchemy models. PostgreSQL is the source of truth for every trading
record; the same schema runs on SQLite for tests and local paper trading."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Money = Numeric(20, 6)
BigId = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class KV(Base):
    """Runtime configuration and small persistent state blobs."""
    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MarketRow(Base):
    __tablename__ = "markets"
    exchange: Mapped[str] = mapped_column(String(16), primary_key=True)
    market_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rules_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MappingRow(Base):
    __tablename__ = "market_mappings"
    __table_args__ = (UniqueConstraint("kalshi_id", "polymarket_id"),)
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    kalshi_id: Mapped[str] = mapped_column(String(255), index=True)
    polymarket_id: Mapped[str] = mapped_column(String(255), index=True)
    inverted: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float)
    settlement_compatible: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), index=True)   # candidate|verified|rejected|stale
    method: Mapped[str] = mapped_column(String(32), default="heuristic")
    checks: Mapped[Any] = mapped_column(JSON, default=dict)
    kalshi_rules_hash: Mapped[str] = mapped_column(String(64), default="")
    polymarket_rules_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OpportunityRow(Base):
    """Every evaluated opportunity, including rejections and the reason."""
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    mode: Mapped[str] = mapped_column(String(8))
    mapping_id: Mapped[int] = mapped_column(BigId, index=True)
    direction: Mapped[str] = mapped_column(String(32))       # e.g. kalshi_yes+polymarket_no
    status: Mapped[str] = mapped_column(String(16), index=True)  # accepted|rejected|executed|blocked
    reason: Mapped[str] = mapped_column(Text, default="")
    size: Mapped[Decimal] = mapped_column(Money, default=0)
    capital: Mapped[Decimal] = mapped_column(Money, default=0)
    gross: Mapped[Decimal] = mapped_column(Money, default=0)
    fees: Mapped[Decimal] = mapped_column(Money, default=0)
    net: Mapped[Decimal] = mapped_column(Money, default=0)
    roi: Mapped[float] = mapped_column(Float, default=0)
    required_roi: Mapped[float] = mapped_column(Float, default=0)
    details: Mapped[Any] = mapped_column(JSON, default=dict)


class ExecutionRow(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mapping_id: Mapped[int] = mapped_column(BigId, index=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigId)
    state: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[Any] = mapped_column(JSON, default=dict)
    planned_size: Mapped[Decimal] = mapped_column(Money, default=0)
    matched_size: Mapped[Decimal] = mapped_column(Money, default=0)
    total_cost: Mapped[Decimal] = mapped_column(Money, default=0)   # acquisition of matched pairs
    fees: Mapped[Decimal] = mapped_column(Money, default=0)
    expected_net: Mapped[Decimal] = mapped_column(Money, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(Money, default=0)  # losses crystallised by unwinds
    slippage: Mapped[Decimal] = mapped_column(Money, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExecutionEventRow(Base):
    """Append-only execution journal: one row per state transition."""
    __tablename__ = "execution_events"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32))
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(8))
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)   # idempotency key
    purpose: Mapped[str] = mapped_column(String(16))   # leg1|leg2|hedge|unwind|close
    exchange: Mapped[str] = mapped_column(String(16))
    market_id: Mapped[str] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(4))
    side: Mapped[str] = mapped_column(String(4))
    tif: Mapped[str] = mapped_column(String(4))
    limit_price: Mapped[Decimal] = mapped_column(Money)
    size: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(16), index=True)
    exchange_order_id: Mapped[str] = mapped_column(String(128), default="")
    filled_size: Mapped[Decimal] = mapped_column(Money, default=0)
    avg_price: Mapped[Decimal] = mapped_column(Money, default=0)
    fee: Mapped[Decimal] = mapped_column(Money, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PositionRow(Base):
    """kind=pair: a matched hedge (leg a + leg b). kind=naked: residual single-leg exposure."""
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    mapping_id: Mapped[int] = mapped_column(BigId, index=True)
    kind: Mapped[str] = mapped_column(String(8), default="pair")
    status: Mapped[str] = mapped_column(String(16), index=True)   # open|settled|closed
    title: Mapped[str] = mapped_column(Text, default="")
    size: Mapped[Decimal] = mapped_column(Money)
    a_exchange: Mapped[str] = mapped_column(String(16))
    a_market_id: Mapped[str] = mapped_column(String(255))
    a_outcome: Mapped[str] = mapped_column(String(4))
    a_cost: Mapped[Decimal] = mapped_column(Money, default=0)
    a_fee: Mapped[Decimal] = mapped_column(Money, default=0)
    b_exchange: Mapped[str | None] = mapped_column(String(16))
    b_market_id: Mapped[str | None] = mapped_column(String(255))
    b_outcome: Mapped[str | None] = mapped_column(String(4))
    b_cost: Mapped[Decimal] = mapped_column(Money, default=0)
    b_fee: Mapped[Decimal] = mapped_column(Money, default=0)
    expected_payout: Mapped[Decimal] = mapped_column(Money, default=0)
    proceeds: Mapped[Decimal] = mapped_column(Money, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(Money, default=0)
    close_reason: Mapped[str] = mapped_column(String(64), default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    @property
    def cost_basis(self) -> Decimal:
        return self.a_cost + self.b_cost + self.a_fee + self.b_fee

    @property
    def fees(self) -> Decimal:
        return self.a_fee + self.b_fee

    def exchange_cost(self, exchange: str) -> Decimal:
        """Capital this position ties up on one exchange."""
        total = Decimal(0)
        if self.a_exchange == exchange:
            total += self.a_cost + self.a_fee
        if self.b_exchange == exchange:
            total += self.b_cost + self.b_fee
        return total


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    kalshi_balance: Mapped[Decimal] = mapped_column(Money)
    polymarket_balance: Mapped[Decimal] = mapped_column(Money)
    deployed: Mapped[Decimal] = mapped_column(Money)
    equity: Mapped[Decimal] = mapped_column(Money)


class RiskEventRow(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(12))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[Any] = mapped_column(JSON, default=dict)


Index("ix_positions_mode_status", PositionRow.mode, PositionRow.status)
Index("ix_exec_events_exec_seq", ExecutionEventRow.execution_id, ExecutionEventRow.seq)
