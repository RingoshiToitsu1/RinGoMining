"""Persistence layer. One Repository instance is shared by every service."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ..config.runtime import RuntimeConfig
from ..core.types import ZERO, Exchange, Mapping, Market
from .models import (KV, Base, EquitySnapshot, ExecutionEventRow, ExecutionRow, MappingRow, MarketRow,
                     OpportunityRow, OrderRow, PositionRow, RiskEventRow, utcnow)

TERMINAL_STATES = ("completed", "failed")


def jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def rules_hash(m: Market) -> str:
    blob = "|".join([m.title, m.yes_label, m.rules, m.resolution_source,
                     m.close_time.isoformat() if m.close_time else ""])
    return hashlib.sha256(blob.encode()).hexdigest()


class Repository:
    def __init__(self, url: str):
        kw: dict[str, Any] = {}
        if url.startswith("sqlite") and ":memory:" in url:
            from sqlalchemy.pool import StaticPool
            kw = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        self.engine: AsyncEngine = create_async_engine(url, **kw)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        """Used for SQLite/tests. Production schema is managed by Alembic."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> bool:
        try:
            async with self.session() as s:
                await s.execute(select(1))
            return True
        except Exception:
            return False

    # -- kv / runtime config -------------------------------------------------
    async def kv_get(self, key: str, default: Any = None) -> Any:
        async with self.session() as s:
            row = await s.get(KV, key)
            return row.value if row else default

    async def kv_set(self, key: str, value: Any) -> None:
        async with self.session() as s:
            row = await s.get(KV, key)
            if row:
                row.value = jsonable(value)
            else:
                s.add(KV(key=key, value=jsonable(value)))
            await s.commit()

    async def load_config(self) -> RuntimeConfig:
        raw = await self.kv_get("runtime_config")
        return RuntimeConfig(**raw) if raw else RuntimeConfig()

    async def save_config(self, cfg: RuntimeConfig) -> None:
        await self.kv_set("runtime_config", cfg.model_dump(mode="json"))

    # -- markets ---------------------------------------------------------------
    async def upsert_markets(self, markets: Sequence[Market]) -> None:
        """Bulk upsert: one lookup per 500 markets, writes only for new or changed ones."""
        markets = list({m.key: m for m in markets}.values())
        for c in range(0, len(markets), 500):
            chunk = markets[c:c + 500]
            async with self.session() as s:
                by_ex: dict[str, list[str]] = {}
                for m in chunk:
                    by_ex.setdefault(m.exchange.value, []).append(m.market_id)
                existing: dict[tuple[str, str], MarketRow] = {}
                for ex, ids in by_ex.items():
                    rows = (await s.execute(select(MarketRow).where(
                        MarketRow.exchange == ex, MarketRow.market_id.in_(ids)))).scalars().all()
                    existing.update({(r.exchange, r.market_id): r for r in rows})
                for m in chunk:
                    h = rules_hash(m)
                    row = existing.get((m.exchange.value, m.market_id))
                    if row is None:
                        s.add(MarketRow(exchange=m.exchange.value, market_id=m.market_id, title=m.title,
                                        status=m.status, close_time=m.close_time, rules_hash=h, data=m.to_dict()))
                    elif row.rules_hash != h or row.status != m.status or (row.data or {}).get("fee") != m.fee.to_dict():
                        row.title, row.status, row.close_time = m.title, m.status, m.close_time
                        row.rules_hash, row.data = h, m.to_dict()
                await s.commit()

    async def get_market(self, exchange: Exchange, market_id: str) -> Market | None:
        async with self.session() as s:
            row = await s.get(MarketRow, (exchange.value, market_id))
            return Market.from_dict(row.data) if row else None

    async def list_markets(self, exchange: Exchange, status: str = "open") -> list[Market]:
        async with self.session() as s:
            rows = (await s.execute(select(MarketRow).where(
                MarketRow.exchange == exchange.value, MarketRow.status == status))).scalars().all()
            return [Market.from_dict(r.data) for r in rows]

    # -- mappings ------------------------------------------------------------
    async def get_mapping_row(self, kalshi_id: str, polymarket_id: str) -> MappingRow | None:
        async with self.session() as s:
            return (await s.execute(select(MappingRow).where(
                MappingRow.kalshi_id == kalshi_id, MappingRow.polymarket_id == polymarket_id))).scalar_one_or_none()

    async def save_mapping(self, kalshi: Market, poly: Market, *, inverted: bool, confidence: float,
                           settlement_compatible: bool, status: str, method: str, checks: dict) -> int:
        async with self.session() as s:
            row = (await s.execute(select(MappingRow).where(
                MappingRow.kalshi_id == kalshi.market_id,
                MappingRow.polymarket_id == poly.market_id))).scalar_one_or_none()
            if row is None:
                row = MappingRow(kalshi_id=kalshi.market_id, polymarket_id=poly.market_id)
                s.add(row)
            row.inverted, row.confidence, row.settlement_compatible = inverted, confidence, settlement_compatible
            row.status, row.method, row.checks = status, method, jsonable(checks)
            row.kalshi_rules_hash, row.polymarket_rules_hash = rules_hash(kalshi), rules_hash(poly)
            row.validated_at = utcnow()
            await s.commit()
            return row.id

    async def update_mapping(self, mapping_id: int, **fields: Any) -> None:
        async with self.session() as s:
            await s.execute(update(MappingRow).where(MappingRow.id == mapping_id).values(**fields))
            await s.commit()

    async def _hydrate(self, s: Any, rows: Sequence[MappingRow]) -> list[Mapping]:
        out = []
        for r in rows:
            k = await s.get(MarketRow, (Exchange.KALSHI.value, r.kalshi_id))
            p = await s.get(MarketRow, (Exchange.POLYMARKET.value, r.polymarket_id))
            if k and p:
                out.append(Mapping(r.id, Market.from_dict(k.data), Market.from_dict(p.data), r.inverted,
                                   r.confidence, r.settlement_compatible, r.status, r.method, r.checks or {}))
        return out

    async def list_mappings(self, statuses: Sequence[str] = ("verified",), limit: int = 5000) -> list[Mapping]:
        async with self.session() as s:
            rows = (await s.execute(select(MappingRow).where(MappingRow.status.in_(statuses))
                                    .order_by(MappingRow.confidence.desc()).limit(limit))).scalars().all()
            return await self._hydrate(s, rows)

    async def get_mapping(self, mapping_id: int) -> Mapping | None:
        async with self.session() as s:
            row = await s.get(MappingRow, mapping_id)
            got = await self._hydrate(s, [row]) if row else []
            return got[0] if got else None

    async def mapping_rows(self, statuses: Sequence[str]) -> list[MappingRow]:
        async with self.session() as s:
            return list((await s.execute(select(MappingRow).where(MappingRow.status.in_(statuses)))).scalars().all())

    # -- opportunities -------------------------------------------------------
    async def add_opportunity(self, **f: Any) -> int:
        async with self.session() as s:
            f["details"] = jsonable(f.get("details", {}))
            row = OpportunityRow(**f)
            s.add(row)
            await s.commit()
            return row.id

    async def set_opportunity_status(self, opp_id: int, status: str, reason: str = "") -> None:
        async with self.session() as s:
            await s.execute(update(OpportunityRow).where(OpportunityRow.id == opp_id)
                            .values(status=status, reason=reason))
            await s.commit()

    async def recent_opportunities(self, mode: str, statuses: Sequence[str] | None = None,
                                   limit: int = 20) -> list[OpportunityRow]:
        async with self.session() as s:
            q = select(OpportunityRow).where(OpportunityRow.mode == mode)
            if statuses:
                q = q.where(OpportunityRow.status.in_(statuses))
            return list((await s.execute(q.order_by(OpportunityRow.id.desc()).limit(limit))).scalars().all())

    # -- executions / journal / orders -----------------------------------------
    async def create_execution(self, row: ExecutionRow) -> None:
        async with self.session() as s:
            row.plan = jsonable(row.plan)
            s.add(row)
            await s.flush()          # the execution must exist before its first journal entry (FK)
            s.add(ExecutionEventRow(execution_id=row.id, seq=0, state=row.state, payload={}))
            await s.commit()

    async def journal(self, execution_id: str, state: str, payload: dict | None = None, **fields: Any) -> None:
        """Atomically record a state transition and update the execution row."""
        async with self.session() as s:
            seq = (await s.execute(select(func.count()).select_from(ExecutionEventRow)
                                   .where(ExecutionEventRow.execution_id == execution_id))).scalar_one()
            s.add(ExecutionEventRow(execution_id=execution_id, seq=seq, state=state, payload=jsonable(payload or {})))
            values: dict[str, Any] = {"state": state, **fields}
            if state in TERMINAL_STATES:
                values["finished_at"] = utcnow()
            await s.execute(update(ExecutionRow).where(ExecutionRow.id == execution_id).values(**values))
            await s.commit()

    async def get_execution(self, execution_id: str) -> ExecutionRow | None:
        async with self.session() as s:
            return await s.get(ExecutionRow, execution_id)

    async def execution_events(self, execution_id: str) -> list[ExecutionEventRow]:
        async with self.session() as s:
            return list((await s.execute(select(ExecutionEventRow).where(
                ExecutionEventRow.execution_id == execution_id).order_by(ExecutionEventRow.seq))).scalars().all())

    async def open_executions(self, mode: str) -> list[ExecutionRow]:
        async with self.session() as s:
            return list((await s.execute(select(ExecutionRow).where(
                ExecutionRow.mode == mode, ExecutionRow.state.notin_(TERMINAL_STATES)))).scalars().all())

    async def recent_executions(self, mode: str, limit: int = 10) -> list[ExecutionRow]:
        async with self.session() as s:
            return list((await s.execute(select(ExecutionRow).where(ExecutionRow.mode == mode)
                                         .order_by(ExecutionRow.started_at.desc()).limit(limit))).scalars().all())

    async def add_order(self, row: OrderRow) -> int:
        async with self.session() as s:
            s.add(row)
            await s.commit()
            return row.id

    async def update_order(self, client_order_id: str, **fields: Any) -> None:
        async with self.session() as s:
            await s.execute(update(OrderRow).where(OrderRow.client_order_id == client_order_id).values(**fields))
            await s.commit()

    async def orders_for(self, execution_id: str) -> list[OrderRow]:
        async with self.session() as s:
            return list((await s.execute(select(OrderRow).where(OrderRow.execution_id == execution_id)
                                         .order_by(OrderRow.id))).scalars().all())

    # -- positions -------------------------------------------------------------
    async def add_position(self, row: PositionRow) -> int:
        async with self.session() as s:
            s.add(row)
            await s.commit()
            return row.id

    async def get_position(self, position_id: int) -> PositionRow | None:
        async with self.session() as s:
            return await s.get(PositionRow, position_id)

    async def open_positions(self, mode: str) -> list[PositionRow]:
        async with self.session() as s:
            return list((await s.execute(select(PositionRow).where(
                PositionRow.mode == mode, PositionRow.status == "open").order_by(PositionRow.id))).scalars().all())

    async def close_position(self, position_id: int, *, status: str, proceeds: Decimal,
                             realized_pnl: Decimal, reason: str, size: Decimal | None = None) -> None:
        async with self.session() as s:
            values: dict[str, Any] = dict(status=status, proceeds=proceeds, realized_pnl=realized_pnl,
                                          close_reason=reason, closed_at=utcnow())
            if size is not None:
                values["size"] = size
            await s.execute(update(PositionRow).where(PositionRow.id == position_id).values(**values))
            await s.commit()

    async def update_position(self, position_id: int, **fields: Any) -> None:
        async with self.session() as s:
            await s.execute(update(PositionRow).where(PositionRow.id == position_id).values(**fields))
            await s.commit()

    async def closed_positions(self, mode: str, limit: int = 20) -> list[PositionRow]:
        async with self.session() as s:
            return list((await s.execute(select(PositionRow).where(
                PositionRow.mode == mode, PositionRow.status != "open")
                .order_by(PositionRow.closed_at.desc()).limit(limit))).scalars().all())

    # -- p&l -----------------------------------------------------------------
    async def realized_pnl(self, mode: str, since: datetime | None = None) -> Decimal:
        """Realised = settled/closed positions + losses crystallised inside executions."""
        async with self.session() as s:
            q1 = select(func.coalesce(func.sum(PositionRow.realized_pnl), 0)).where(
                PositionRow.mode == mode, PositionRow.status != "open")
            q2 = select(func.coalesce(func.sum(ExecutionRow.realized_pnl), 0)).where(ExecutionRow.mode == mode)
            if since:
                q1 = q1.where(PositionRow.closed_at >= since)
                q2 = q2.where(ExecutionRow.finished_at >= since)
            a = (await s.execute(q1)).scalar_one()
            b = (await s.execute(q2)).scalar_one()
            return Decimal(str(a)) + Decimal(str(b))

    async def stats(self, mode: str) -> dict[str, Any]:
        async with self.session() as s:
            ex = (await s.execute(select(ExecutionRow).where(ExecutionRow.mode == mode))).scalars().all()
            pos = (await s.execute(select(PositionRow).where(PositionRow.mode == mode))).scalars().all()
            first = (await s.execute(select(func.min(ExecutionRow.started_at)).where(ExecutionRow.mode == mode))).scalar_one()
        done = [e for e in ex if e.state in TERMINAL_STATES]
        matched = [e for e in done if e.matched_size > 0]
        failed = [e for e in done if e.state == "failed"]
        partial = [e for e in matched if e.matched_size < e.planned_size]
        durs = [(e.finished_at - e.started_at).total_seconds() for e in done if e.finished_at]
        pairs = [p for p in pos if p.kind == "pair"]
        settled = [p for p in pos if p.status != "open"]
        fees = sum((e.fees for e in ex), ZERO)
        gross = sum((p.proceeds - p.a_cost - p.b_cost for p in settled), ZERO)
        realized = sum((p.realized_pnl for p in settled), ZERO) + sum((e.realized_pnl for e in ex), ZERO)
        open_pairs = [p for p in pairs if p.status == "open"]
        locked = sum((p.expected_payout - p.cost_basis for p in open_pairs), ZERO)
        slip_base = sum((e.total_cost for e in matched), ZERO)
        return {
            "total_trades": len(done), "matched_pairs": len(matched), "failed": len(failed),
            "partial_fills": len(partial), "gross_profit": gross, "fees": fees, "net_realized": realized,
            "locked_in": locked, "open_positions": len([p for p in pos if p.status == "open"]),
            "deployed": sum((p.cost_basis for p in pos if p.status == "open"), ZERO),
            "avg_profit": (realized / len(settled)) if settled else ZERO,
            "avg_exec_s": sum(durs) / len(durs) if durs else 0.0,
            "avg_slippage_pct": float(sum((e.slippage for e in matched), ZERO) / slip_base * 100) if slip_base else 0.0,
            "first_trade_at": first, "fail_rate_pct": len(failed) / len(done) * 100 if done else 0.0,
        }

    # -- equity ----------------------------------------------------------------
    async def add_equity_snapshot(self, mode: str, kalshi: Decimal, poly: Decimal, deployed: Decimal) -> None:
        async with self.session() as s:
            s.add(EquitySnapshot(mode=mode, kalshi_balance=kalshi, polymarket_balance=poly,
                                 deployed=deployed, equity=kalshi + poly + deployed))
            await s.commit()

    async def equity_curve(self, mode: str, days: int = 90) -> list[tuple[datetime, Decimal]]:
        since = utcnow() - timedelta(days=days)
        async with self.session() as s:
            rows = (await s.execute(select(EquitySnapshot.ts, EquitySnapshot.equity).where(
                EquitySnapshot.mode == mode, EquitySnapshot.ts >= since).order_by(EquitySnapshot.ts))).all()
            return [(r[0], Decimal(str(r[1]))) for r in rows]

    # -- risk events -------------------------------------------------------------
    async def add_risk_event(self, kind: str, severity: str, message: str, data: dict | None = None) -> None:
        async with self.session() as s:
            s.add(RiskEventRow(kind=kind, severity=severity, message=message, data=jsonable(data or {})))
            await s.commit()

    async def recent_risk_events(self, limit: int = 10) -> list[RiskEventRow]:
        async with self.session() as s:
            return list((await s.execute(select(RiskEventRow).order_by(RiskEventRow.id.desc()).limit(limit))).scalars().all())


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; normalise so arithmetic is always tz-aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
