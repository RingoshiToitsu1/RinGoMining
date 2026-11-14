"""Autonomous execution engine.

Cross-platform orders are not atomic, so each trade runs through a persisted
state machine. Every transition is journaled before the next action, every
order is journaled (with its idempotency key) before it is sent, and a
restart resumes from the journal.

Legs are sent one after the other, the leg most likely to fail first: if it
does not fill there is no exposure at all. Once a first leg is filled the
only goal is to remove the unmatched exposure, by whichever of
"complete the hedge" or "unwind" loses less. The original edge is never chased.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..config.runtime import RuntimeConfig
from ..data.orderbooks import OrderBookStore
from ..database.models import ExecutionRow, OrderRow, PositionRow
from ..database.repo import Repository
from ..exchanges.base import ExchangeAdapter, ExchangeError, ExchangeUnavailable, TradingDisabled
from ..infra.cache import Cache, LockNotAcquired
from . import events
from .capital_allocator import CapitalAllocator, InsufficientCapital
from .events import EventBus
from .fees import estimate_fee
from .profitability import Evaluation, LegInput, ProfitabilityEngine
from .risk_manager import RiskManager
from .types import (ONE, ZERO, D, Exchange, Mapping, Market, OrderBook, OrderRequest, OrderResult, OrderStatus,
                    Outcome, Side, TIF, floor_to)

log = logging.getLogger(__name__)


class S:
    """Execution states."""
    DETECTED = "opportunity_detected"
    VALIDATED = "opportunity_validated"
    RESERVED = "capital_reserved"
    SUBMITTED = "orders_submitted"
    LEG1_PARTIAL = "first_leg_partially_filled"
    LEG1_FILLED = "first_leg_fully_filled"
    LEG2_PARTIAL = "second_leg_partially_filled"
    MATCHED = "both_legs_matched"
    HEDGE = "emergency_hedge"
    UNWIND = "emergency_unwind"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class Leg:
    market: Market
    outcome: Outcome
    limit: Decimal = ZERO
    planned_vwap: Decimal = ZERO
    est_fee: Decimal = ZERO
    filled: Decimal = ZERO
    cost: Decimal = ZERO
    fee: Decimal = ZERO
    orders: list[str] = field(default_factory=list)

    @property
    def exchange(self) -> Exchange:
        return self.market.exchange

    @property
    def avg(self) -> Decimal:
        return self.cost / self.filled if self.filled > 0 else ZERO

    def add(self, r: OrderResult) -> None:
        self.filled += r.filled_size
        self.cost += r.filled_size * r.avg_price
        self.fee += r.fee

    def plan(self) -> dict:
        return {"exchange": self.exchange.value, "market_id": self.market.market_id,
                "outcome": self.outcome.value, "limit": str(self.limit),
                "planned_vwap": str(self.planned_vwap), "est_fee": str(self.est_fee)}


@dataclass(slots=True)
class Ctx:
    id: str
    mapping: Mapping
    first: Leg
    second: Leg
    size: Decimal
    started: float
    seq: int = 0
    unwound: Decimal = ZERO
    unwind_proceeds: Decimal = ZERO
    unwind_fee: Decimal = ZERO
    unhedged_since: float = 0.0
    halted: str = ""
    evaluation: Evaluation | None = None

    @property
    def unmatched(self) -> Decimal:
        return self.first.filled - self.second.filled - self.unwound


class ExecutionEngine:
    def __init__(self, cfg: RuntimeConfig, repo: Repository, bus: EventBus, venues: dict[Exchange, ExchangeAdapter],
                 store: OrderBookStore, allocator: CapitalAllocator, risk: RiskManager,
                 profit: ProfitabilityEngine, cache: Cache, *, retry_delay_s: float = 0.25):
        self.cfg, self.repo, self.bus, self.venues = cfg, repo, bus, venues
        self.store, self.allocator, self.risk, self.profit, self.cache = store, allocator, risk, profit, cache
        self._lock = asyncio.Lock()
        self._retry_delay = retry_delay_s
        self.busy = False

    # ------------------------------------------------------------------ helpers
    def leg_input(self, market: Market, outcome: Outcome, book: OrderBook) -> LegInput:
        cfg = self.cfg
        lat = cfg.kalshi_latency_ms if market.exchange is Exchange.KALSHI else cfg.polymarket_latency_ms
        return LegInput(market, outcome, book, latency_s=lat / 1000,
                        vol_per_s=self.store.vol_per_s(market.exchange, market.market_id),
                        conversion_bps=self.venues[market.exchange].conversion_bps)

    async def _journal(self, ctx: Ctx, state: str, payload: dict | None = None, **fields: Any) -> None:
        await self.repo.journal(ctx.id, state, payload, **fields)
        await self.bus.publish(events.EXECUTION, execution_id=ctx.id, state=state, payload=payload or {})

    def _guard(self, venue: ExchangeAdapter) -> None:
        if self.cfg.mode == "paper" and not venue.is_simulated:
            raise TradingDisabled("paper mode must never reach a real exchange")
        if self.cfg.mode == "live" and (venue.is_simulated or not self.cfg.live_unlocked):
            raise TradingDisabled("live trading is not unlocked")

    async def _expected_position(self, leg: Leg) -> Decimal:
        total = ZERO
        for p in await self.repo.open_positions(self.cfg.mode):
            for ex, mid, oc in ((p.a_exchange, p.a_market_id, p.a_outcome), (p.b_exchange, p.b_market_id, p.b_outcome)):
                if ex == leg.exchange.value and mid == leg.market.market_id and oc == leg.outcome.value:
                    total += p.size
        return total

    async def _resolve_unknown(self, ctx: Ctx, leg: Leg, side: Side, limit: Decimal, size: Decimal,
                               known_before: Decimal) -> OrderResult:
        """The exchange did not confirm an order. Determine what happened from the
        account's actual position: journaled holdings + this execution's known
        fills, versus what the exchange reports."""
        venue = self.venues[leg.exchange]
        for attempt in range(4):
            try:
                actual = (await venue.get_positions()).get((leg.market.market_id, leg.outcome), ZERO)
                base = await self._expected_position(leg) + known_before
                delta = actual - base if side is Side.BUY else base - actual
                filled = min(max(delta, ZERO), size)
                if filled <= 0:
                    return OrderResult(OrderStatus.UNFILLED, message="resolved from positions: no fill")
                fee = estimate_fee(leg.market.fee, limit, filled)
                status = OrderStatus.FILLED if filled == size else OrderStatus.PARTIAL
                return OrderResult(status, filled_size=filled, avg_price=limit, fee=fee,
                                   message="resolved from positions (price assumed at limit)")
            except Exception as e:
                log.warning("position lookup failed (%s), retry %d", e, attempt)
                await asyncio.sleep(self._retry_delay * (attempt + 1))
        return OrderResult(OrderStatus.UNKNOWN, message="order outcome could not be determined")

    async def _order(self, ctx: Ctx, leg: Leg, side: Side, limit: Decimal, size: Decimal, tif: TIF,
                     purpose: str, held_before: Decimal) -> OrderResult:
        """Journal, submit and settle one immediate order. Never raises for exchange errors."""
        venue = self.venues[leg.exchange]
        ctx.seq += 1
        coid = f"{ctx.id}-{ctx.seq}"
        await self.repo.add_order(OrderRow(
            execution_id=ctx.id, mode=self.cfg.mode, client_order_id=coid, purpose=purpose,
            exchange=leg.exchange.value, market_id=leg.market.market_id, outcome=leg.outcome.value,
            side=side.value, tif=tif.value, limit_price=limit, size=size, status=OrderStatus.PENDING.value))
        await self.risk.note_managed(leg.exchange, leg.market.market_id)
        req = OrderRequest(leg.exchange, leg.market, leg.outcome, side, limit, size, tif, coid)
        try:
            self._guard(venue)
            res = await asyncio.wait_for(venue.place_order(req), timeout=min(15.0, self.cfg.max_execution_seconds))
        except TradingDisabled as e:
            res = OrderResult(OrderStatus.REJECTED, message=str(e))
        except (ExchangeUnavailable, asyncio.TimeoutError, OSError) as e:
            log.warning("order %s outcome unknown: %s", coid, e)
            res = await self._resolve_unknown(ctx, leg, side, limit, size, held_before)
        except ExchangeError as e:
            res = OrderResult(OrderStatus.REJECTED, message=str(e))
        except Exception as e:  # unexpected adapter bug: assume nothing, verify from positions
            log.exception("order %s crashed", coid)
            res = await self._resolve_unknown(ctx, leg, side, limit, size, held_before)
            res.message = f"{res.message}; adapter error: {e}"
        await self.repo.update_order(coid, status=res.status.value, exchange_order_id=res.order_id,
                                     filled_size=res.filled_size, avg_price=res.avg_price, fee=res.fee,
                                     message=res.message[:500])
        leg.orders.append(coid)
        if side is Side.BUY:
            self.profit.record_leg_result(leg.exchange, failed=res.filled_size < size)
        return res

    async def _fresh_pair(self, a: Market, b: Market) -> tuple[OrderBook, OrderBook]:
        """Fetch two books so both are fresh when used: the rate-limited venue (Polymarket) first,
        then the fast one. Fetching them concurrently lets the fast book go stale while the slow
        request waits for its rate-limit slot."""
        if a.exchange is Exchange.POLYMARKET:
            ba = await self._fresh_book(a)
            return ba, await self._fresh_book(b)
        bb = await self._fresh_book(b)
        return await self._fresh_book(a), bb

    async def _fresh_book(self, market: Market) -> OrderBook:
        book = await asyncio.wait_for(self.venues[market.exchange].get_orderbook(market), timeout=10)
        await self.store.put_async(book)
        return book

    # ------------------------------------------------------------------ execute
    async def execute(self, mapping: Mapping, kalshi_outcome: Outcome, opportunity_id: int | None = None
                      ) -> ExecutionRow | None:
        """Run one arbitrage end to end. Returns the final execution row, or None when
        another execution holds the lock."""
        if self._lock.locked():
            return None
        async with self._lock:
            try:
                async with self.cache.lock("execution", ttl=self.cfg.max_execution_seconds * 3 + 60):
                    self.busy = True
                    try:
                        return await self._execute(mapping, kalshi_outcome, opportunity_id)
                    finally:
                        self.busy = False
            except LockNotAcquired:
                return None

    async def _execute(self, mapping: Mapping, kalshi_outcome: Outcome, opportunity_id: int | None) -> ExecutionRow:
        cfg = self.cfg
        k = Leg(mapping.kalshi, kalshi_outcome)
        p = Leg(mapping.polymarket, mapping.poly_outcome_for(kalshi_outcome).opposite)
        ctx = Ctx(uuid.uuid4().hex[:24], mapping, k, p, ZERO, time.time())
        await self.repo.create_execution(ExecutionRow(
            id=ctx.id, mode=cfg.mode, mapping_id=mapping.id, opportunity_id=opportunity_id, state=S.DETECTED,
            title=mapping.kalshi.title, plan={"direction": f"kalshi_{k.outcome.value}+polymarket_{p.outcome.value}"}))
        await self.bus.publish(events.EXECUTION, execution_id=ctx.id, state=S.DETECTED, payload={})

        async def fail(reason: str) -> ExecutionRow:
            self.allocator.release(ctx.id)
            await self._journal(ctx, S.FAILED, {"reason": reason}, error=reason)
            if opportunity_id:
                await self.repo.set_opportunity_status(opportunity_id, "blocked", reason)
            await self.bus.publish(events.EXECUTION_FAILED, execution_id=ctx.id, title=mapping.kalshi.title,
                                   reason=reason, exposure=False)
            return await self.repo.get_execution(ctx.id)  # type: ignore[return-value]

        allowed, why = self.risk.trading_allowed()
        if not allowed:
            return await fail(f"risk manager blocked trade: {why}")
        # 2-5: refresh both books, verify balances, size and price on fresh data
        try:
            bk, bp = await self._fresh_pair(k.market, p.market)
        except Exception as e:
            return await fail(f"could not refresh order books: {e}")
        ok = await self.allocator.refresh_balances()
        if not all(ok.values()):
            return await fail("could not verify balances")
        ev = await self.allocator.plan(mapping, self.leg_input(k.market, k.outcome, bk),
                                       self.leg_input(p.market, p.outcome, bp))
        if not ev.accepted or ev.quote is None:
            return await fail(f"no longer qualifies on fresh books: {ev.reason}")
        allowed, why = await self.risk.pre_trade(mapping, ev)
        if not allowed:
            return await fail(f"risk manager blocked trade: {why}")
        q = ev.quote
        ctx.evaluation, ctx.size = ev, q.size
        for leg, lq in ((k, q.a), (p, q.b)):
            leg.limit, leg.planned_vwap, leg.est_fee = lq.limit_price, lq.vwap, lq.fee
        # the leg more likely to fail goes first: failing there costs nothing
        if q.b.fail_prob > q.a.fail_prob:
            ctx.first, ctx.second = p, k
        plan = {"direction": f"kalshi_{k.outcome.value}+polymarket_{p.outcome.value}",
                "first": ctx.first.plan(), "second": ctx.second.plan(), "size": str(q.size),
                "expected_net": str(q.net), "expected_profit": str(q.expected_profit), "roi": q.roi,
                "required_roi": ev.required_roi, "confidence": mapping.confidence,
                "fail_prob": q.fail_prob, "capital": str(q.capital)}
        await self._journal(ctx, S.VALIDATED, plan, plan=plan, planned_size=q.size, expected_net=q.net)

        # 6: reserve capital on both venues
        try:
            await self.allocator.reserve(ctx.id, {k.exchange: q.a.capital, p.exchange: q.b.capital})
        except InsufficientCapital as e:
            return await fail(f"capital reservation failed: {e}")
        await self._journal(ctx, S.RESERVED, {"kalshi": str(q.a.capital), "polymarket": str(q.b.capital)})

        try:
            return await asyncio.wait_for(self._trade(ctx, opportunity_id), timeout=cfg.max_execution_seconds * 2 + 30)
        except asyncio.TimeoutError:
            await self.risk.trip("execution_discrepancy", f"execution {ctx.id} overran its time limit")
            return await self._finalize(ctx, opportunity_id, error="execution timed out")
        finally:
            self.allocator.release(ctx.id)

    async def _trade(self, ctx: Ctx, opportunity_id: int | None) -> ExecutionRow:
        first, second = ctx.first, ctx.second
        # 7-8: submit
        await self._journal(ctx, S.SUBMITTED, {"first": first.exchange.value, "size": str(ctx.size)})
        r1 = await self._order(ctx, first, Side.BUY, first.limit, ctx.size, TIF.IOC, "leg1", ZERO)
        if r1.status is OrderStatus.UNKNOWN:
            await self.risk.trip("execution_discrepancy",
                                 f"{first.exchange.value} order outcome unknown in execution {ctx.id}")
            return await self._finalize(ctx, opportunity_id, error="first leg outcome unknown")
        first.add(r1)
        if first.filled <= 0:
            return await self._finalize(ctx, opportunity_id, error=f"first leg unfilled ({r1.message or r1.status.value})")
        ctx.unhedged_since = time.time()
        if first.filled < ctx.size:
            await self._journal(ctx, S.LEG1_PARTIAL, {"filled": str(first.filled), "of": str(ctx.size)})
            await self.bus.publish(events.PARTIAL_FILL, execution_id=ctx.id, title=ctx.mapping.kalshi.title,
                                   leg="first", exchange=first.exchange.value, filled=first.filled, wanted=ctx.size)
        else:
            await self._journal(ctx, S.LEG1_FILLED, {"filled": str(first.filled), "avg": str(first.avg)})

        # 9-10: second leg for exactly what the first leg got
        r2 = await self._order(ctx, second, Side.BUY, second.limit, first.filled, TIF.IOC, "leg2", ZERO)
        if r2.status is OrderStatus.UNKNOWN:
            # Never hedge blind: a second order could double the position. Stop and escalate.
            return await self._halt_unknown(ctx, opportunity_id, second)
        second.add(r2)
        if ctx.unmatched <= 0:
            await self._journal(ctx, S.MATCHED, {"matched": str(second.filled)})
            return await self._finalize(ctx, opportunity_id)
        if second.filled > 0:
            await self._journal(ctx, S.LEG2_PARTIAL, {"filled": str(second.filled), "of": str(first.filled)})
        await self.bus.publish(events.PARTIAL_FILL, execution_id=ctx.id, title=ctx.mapping.kalshi.title,
                               leg="second", exchange=second.exchange.value, filled=second.filled, wanted=first.filled)
        # 11: remove the unmatched exposure
        await self._resolve_exposure(ctx)
        if ctx.halted:
            await self.risk.trip("execution_discrepancy", f"execution {ctx.id}: {ctx.halted}")
        return await self._finalize(ctx, opportunity_id, error=ctx.halted)

    async def _halt_unknown(self, ctx: Ctx, opportunity_id: int | None, leg: Leg) -> ExecutionRow:
        await self.risk.trip("execution_discrepancy",
                             f"execution {ctx.id}: {leg.exchange.value} order outcome unknown; trading paused, "
                             f"verify positions manually")
        return await self._finalize(ctx, opportunity_id, error=f"{leg.exchange.value} order outcome unknown")

    # ------------------------------------------------------------ hedge / unwind
    async def _resolve_exposure(self, ctx: Ctx) -> None:
        cfg = self.cfg
        first, second = ctx.first, ctx.second
        deadline = (ctx.unhedged_since or time.time()) + cfg.max_unhedged_seconds
        step = max(first.market.size_step, second.market.size_step)
        fee1_pc = first.fee / first.filled if first.filled else ZERO
        await self._journal(ctx, S.HEDGE, {"unmatched": str(ctx.unmatched)})
        await self.bus.publish(events.EMERGENCY, execution_id=ctx.id, title=ctx.mapping.kalshi.title, kind="hedge",
                               unmatched=ctx.unmatched, exchange=second.exchange.value)

        # Phase 1: complete the hedge while that loses less than unwinding.
        while ctx.unmatched > 0 and time.time() < deadline:
            try:
                b2, b1 = await self._fresh_pair(second.market, first.market)
            except Exception as e:
                log.warning("hedge: book refresh failed: %s", e)
                await asyncio.sleep(self._retry_delay)
                continue
            asks = b2.asks(second.outcome)
            if not asks:
                break
            need = ctx.unmatched
            fee2_pc = estimate_fee(second.market.fee, asks[0].price, need) / need
            max_price = ONE - first.avg - fee1_pc - fee2_pc + Decimal(str(cfg.max_hedge_loss_pct / 100))
            max_price = floor_to(min(max_price, ONE - second.market.tick_size), second.market.tick_size)
            bid1 = b1.best_bid(first.outcome) or ZERO
            exit_fee_pc = estimate_fee(first.market.fee, bid1, need) / need if bid1 > 0 else ZERO
            unwind_loss_pc = first.avg + fee1_pc - (bid1 - exit_fee_pc)
            hedge_loss_pc = first.avg + fee1_pc + asks[0].price + fee2_pc - ONE
            if asks[0].price > max_price or hedge_loss_pc > unwind_loss_pc:
                break  # unwinding is the cheaper way out
            # sweep as deep as needed, but never past the worst acceptable price
            limit, cum = asks[0].price, ZERO
            for lv in asks:
                if lv.price > max_price or cum >= need:
                    break
                limit, cum = lv.price, cum + lv.size
            r = await self._order(ctx, second, Side.BUY, limit, need, TIF.IOC, "hedge", second.filled)
            if r.status is OrderStatus.UNKNOWN:
                ctx.halted = f"{second.exchange.value} hedge order outcome unknown"
                return
            second.add(r)
            if ctx.unmatched > 0:
                await asyncio.sleep(self._retry_delay)
        if ctx.unmatched <= 0:
            return

        # Phase 2: unwind the excess first leg.
        await self._journal(ctx, S.UNWIND, {"unmatched": str(ctx.unmatched)})
        await self.bus.publish(events.EMERGENCY, execution_id=ctx.id, title=ctx.mapping.kalshi.title, kind="unwind",
                               unmatched=ctx.unmatched, exchange=first.exchange.value)
        floor = max(floor_to(first.avg * Decimal(str(cfg.unwind_floor_pct / 100)), first.market.tick_size),
                    first.market.tick_size)
        unwind_deadline = max(deadline, time.time() + min(cfg.max_unhedged_seconds, 10.0))
        attempts = 0
        while ctx.unmatched > 0 and (time.time() < unwind_deadline or attempts == 0):
            attempts += 1
            try:
                b1 = await self._fresh_book(first.market)
            except Exception as e:
                log.warning("unwind: book refresh failed: %s", e)
                await asyncio.sleep(self._retry_delay)
                continue
            bids = [lv for lv in b1.bids(first.outcome) if lv.price >= floor]
            need = floor_to(ctx.unmatched, step)
            if not bids or need <= 0:
                await asyncio.sleep(self._retry_delay)
                continue
            limit, cum = bids[0].price, ZERO
            for lv in bids:
                if cum >= need:
                    break
                limit, cum = lv.price, cum + lv.size
            held = first.filled - ctx.unwound
            r = await self._order(ctx, first, Side.SELL, limit, min(need, cum), TIF.IOC, "unwind", held)
            if r.status is OrderStatus.UNKNOWN:
                ctx.halted = f"{first.exchange.value} unwind order outcome unknown"
                return
            if r.filled_size > 0:
                ctx.unwound += r.filled_size
                ctx.unwind_proceeds += r.filled_size * r.avg_price
                ctx.unwind_fee += r.fee
            if ctx.unmatched > 0:
                await asyncio.sleep(self._retry_delay)

    # ------------------------------------------------------------------ finalize
    async def _finalize(self, ctx: Ctx, opportunity_id: int | None, error: str = "") -> ExecutionRow:
        """12: record realised results, open the hedged position, flag any residual."""
        cfg, first, second, m = self.cfg, ctx.first, ctx.second, ctx.mapping
        matched = min(first.filled, second.filled)
        residual = ctx.unmatched
        share = (lambda part: part / first.filled) if first.filled > 0 else (lambda part: ZERO)
        a_cost, a_fee = first.cost * share(matched), first.fee * share(matched)
        unwind_pnl = ZERO
        if ctx.unwound > 0:
            unwind_pnl = ctx.unwind_proceeds - ctx.unwind_fee - (first.cost + first.fee) * share(ctx.unwound)
        total_cost = a_cost + second.cost
        fees = first.fee + second.fee + ctx.unwind_fee
        expected_net = matched - total_cost - a_fee - second.fee
        slippage = total_cost - (first.planned_vwap + second.planned_vwap) * matched if matched > 0 else ZERO

        position_id = None
        if matched > 0:
            position_id = await self.repo.add_position(PositionRow(
                execution_id=ctx.id, mode=cfg.mode, mapping_id=m.id, kind="pair", status="open", title=m.kalshi.title,
                size=matched, a_exchange=first.exchange.value, a_market_id=first.market.market_id,
                a_outcome=first.outcome.value, a_cost=a_cost, a_fee=a_fee, b_exchange=second.exchange.value,
                b_market_id=second.market.market_id, b_outcome=second.outcome.value, b_cost=second.cost,
                b_fee=second.fee, expected_payout=matched))
        if residual > 0:
            await self.repo.add_position(PositionRow(
                execution_id=ctx.id, mode=cfg.mode, mapping_id=m.id, kind="naked", status="open",
                title=f"UNHEDGED {m.kalshi.title}", size=residual, a_exchange=first.exchange.value,
                a_market_id=first.market.market_id, a_outcome=first.outcome.value,
                a_cost=first.cost * share(residual), a_fee=first.fee * share(residual), expected_payout=ZERO))
            error = error or f"{residual} contracts left unhedged on {first.exchange.value}"
            await self.risk.trip("unexpected_exposure",
                                 f"execution {ctx.id}: {residual} {first.outcome.value} contracts on "
                                 f"{first.exchange.value} could not be hedged or unwound",
                                 data={"execution_id": ctx.id})
        if not error and matched <= 0:
            error = "nothing matched; exposure fully unwound" if ctx.unwound > 0 else "nothing filled"
        state = S.COMPLETED if matched > 0 and residual <= 0 else S.FAILED

        await self._journal(ctx, state, {
            "matched": str(matched), "unwound": str(ctx.unwound), "residual": str(residual),
            "unwind_pnl": str(unwind_pnl), "error": error},
            matched_size=matched, total_cost=total_cost, fees=fees, expected_net=expected_net,
            realized_pnl=unwind_pnl, slippage=slippage, error=error)
        if opportunity_id:
            await self.repo.set_opportunity_status(opportunity_id, "executed" if matched > 0 else "blocked", error)

        for leg in (first, second):
            if leg.filled > 0 and leg.est_fee > 0 and ctx.size > 0:
                await self.risk.check_fee(leg.exchange, leg.est_fee * leg.filled / ctx.size, leg.fee)

        elapsed = time.time() - ctx.started
        if matched > 0:
            k, p = (first, second) if first.exchange is Exchange.KALSHI else (second, first)
            yes, no = (k, p) if k.outcome is Outcome.YES else (p, k)
            cap = total_cost + a_fee + second.fee
            await self.bus.publish(
                events.TRADE_COMPLETED, execution_id=ctx.id, position_id=position_id, title=m.kalshi.title,
                mode=cfg.mode, partial=matched < ctx.size,
                kalshi={"market": k.market.market_id, "outcome": k.outcome.value, "avg": k.avg, "orders": k.orders},
                polymarket={"market": p.market.market_id, "outcome": p.outcome.value, "avg": p.avg, "orders": p.orders},
                yes_price=yes.avg, yes_exchange=yes.exchange.value, no_price=no.avg, no_exchange=no.exchange.value,
                quantity=matched, planned=ctx.size, acquisition=total_cost, fees=a_fee + second.fee, payout=matched,
                expected_net=expected_net, roi_pct=float(expected_net / cap * 100) if cap else 0.0, capital=cap,
                exec_seconds=elapsed, confidence=m.confidence, unwind_pnl=unwind_pnl)
        if state == S.FAILED:
            await self.bus.publish(events.EXECUTION_FAILED, execution_id=ctx.id, title=m.kalshi.title,
                                   reason=error, exposure=residual > 0, unwind_pnl=unwind_pnl)
        return await self.repo.get_execution(ctx.id)  # type: ignore[return-value]

    # ---------------------------------------------------------- restart recovery
    async def recover(self) -> list[str]:
        """Reconcile after a restart, before any new trade:
        cancel unexpected resting orders, then finish every execution the journal
        shows as in flight using the positions the exchanges actually report."""
        notes: list[str] = []
        for ex, venue in self.venues.items():
            try:
                for o in await venue.get_open_orders():
                    market = await self.repo.get_market(ex, o.get("market_id", ""))
                    if market:
                        await venue.cancel_order(o["order_id"], market)
                        notes.append(f"cancelled resting order {o['order_id']} on {ex.value}")
            except Exception as e:
                notes.append(f"could not list open orders on {ex.value}: {e}")

        for row in await self.repo.open_executions(self.cfg.mode):
            plan = row.plan or {}
            if "first" not in plan:      # died before validation: nothing was sent
                await self.repo.journal(row.id, S.FAILED, {"reason": "interrupted before submission"},
                                        error="interrupted before submission")
                notes.append(f"execution {row.id}: interrupted before any order")
                continue
            mapping = await self.repo.get_mapping(row.mapping_id)
            if mapping is None:
                await self.risk.trip("execution_discrepancy", f"execution {row.id}: mapping missing during recovery")
                continue
            legs = []
            for spec in (plan["first"], plan["second"]):
                market = mapping.kalshi if spec["exchange"] == Exchange.KALSHI.value else mapping.polymarket
                legs.append(Leg(market, Outcome(spec["outcome"]), D(spec["limit"]), D(spec["planned_vwap"]),
                                D(spec["est_fee"])))
            ctx = Ctx(row.id, mapping, legs[0], legs[1], D(plan["size"]), time.time())
            orders = await self.repo.orders_for(row.id)
            ctx.seq = len(orders)
            unresolved = False
            for o in orders:
                leg = legs[0] if (o.exchange, o.outcome) == (legs[0].exchange.value, legs[0].outcome.value) else legs[1]
                side = Side(o.side)
                if o.status == OrderStatus.PENDING.value:   # sent (or not) right before the crash
                    held = leg.filled - (ctx.unwound if leg is legs[0] else ZERO)
                    res = await self._resolve_unknown(ctx, leg, side, D(o.limit_price), D(o.size), held)
                    await self.repo.update_order(o.client_order_id, status=res.status.value,
                                                 filled_size=res.filled_size, avg_price=res.avg_price, fee=res.fee,
                                                 message=res.message)
                    if res.status is OrderStatus.UNKNOWN:
                        unresolved = True
                        continue
                else:
                    res = OrderResult(OrderStatus(o.status), filled_size=D(o.filled_size), avg_price=D(o.avg_price),
                                      fee=D(o.fee))
                if side is Side.BUY:
                    leg.add(res)
                else:
                    ctx.unwound += res.filled_size
                    ctx.unwind_proceeds += res.filled_size * res.avg_price
                    ctx.unwind_fee += res.fee
                leg.orders.append(o.client_order_id)
            if unresolved:
                await self.risk.trip("execution_discrepancy",
                                     f"execution {row.id}: order outcome still unknown after restart")
                continue
            async with self._lock:
                self.busy = True
                try:
                    if ctx.unmatched > 0:
                        ctx.unhedged_since = time.time()
                        await self._resolve_exposure(ctx)
                    if ctx.halted:
                        await self.risk.trip("execution_discrepancy", f"execution {row.id}: {ctx.halted}")
                    done = await self._finalize(ctx, row.opportunity_id, error=ctx.halted or (
                        "" if legs[0].filled > 0 else "interrupted; nothing filled"))
                finally:
                    self.busy = False
            notes.append(f"execution {row.id}: recovered -> {done.state} (matched {done.matched_size})")
        return notes

    # ------------------------------------------------------------ close positions
    async def close_position(self, position_id: int) -> tuple[bool, str]:
        """Sell an open position at the current bids. Both legs of a pair are sold
        for the same quantity; an imbalance becomes a flagged naked position."""
        async with self._lock:
            self.busy = True
            try:
                return await self._close(position_id)
            finally:
                self.busy = False

    async def _close(self, position_id: int) -> tuple[bool, str]:
        pos = await self.repo.get_position(position_id)
        if pos is None or pos.status != "open" or pos.mode != self.cfg.mode:
            return False, "position not found or not open"
        specs = [(pos.a_exchange, pos.a_market_id, pos.a_outcome)]
        if pos.kind == "pair":
            specs.append((pos.b_exchange, pos.b_market_id, pos.b_outcome))
        legs: list[Leg] = []
        books: list[OrderBook] = []
        for ex, mid, oc in specs:
            market = await self.repo.get_market(Exchange(ex), mid)
            if market is None:
                return False, f"market {mid} unknown"
            try:
                books.append(await self._fresh_book(market))
            except Exception as e:
                return False, f"cannot fetch {ex} book: {e}"
            legs.append(Leg(market, Outcome(oc)))
        step = max(l.market.size_step for l in legs)
        qty = pos.size
        for leg, book in zip(legs, books):
            qty = min(qty, sum((lv.size for lv in book.bids(leg.outcome)), ZERO))
        qty = floor_to(qty, step)
        if qty <= 0:
            return False, "not eligible: no bids to sell into"
        mapping = await self.repo.get_mapping(pos.mapping_id)
        ctx = Ctx(uuid.uuid4().hex[:24], mapping, legs[0], legs[-1], qty, time.time())  # type: ignore[arg-type]
        await self.repo.create_execution(ExecutionRow(id=ctx.id, mode=self.cfg.mode, mapping_id=pos.mapping_id,
                                                      state="closing", title=f"CLOSE {pos.title}",
                                                      plan={"close_position": position_id}))
        sold: list[OrderResult] = []
        target = qty
        for leg, book in zip(legs, books):
            limit, cum = ZERO, ZERO
            for lv in book.bids(leg.outcome):
                if cum >= target:
                    break
                limit, cum = lv.price, cum + lv.size
            r = await self._order(ctx, leg, Side.SELL, limit, target, TIF.IOC, "close", ZERO)
            sold.append(r)
            if len(legs) == 2 and leg is legs[0]:
                target = r.filled_size     # second leg sells exactly what the first sold
                if target <= 0:
                    break
        closed = min((r.filled_size for r in sold), default=ZERO) if len(sold) == len(legs) else ZERO
        proceeds = sum((r.filled_size * r.avg_price - r.fee for r in sold), ZERO)
        frac = closed / pos.size
        imbalance = (sold[0].filled_size - closed) if sold else ZERO
        cost_closed = pos.cost_basis * frac
        if imbalance > 0:
            # first leg sold more than the second: the second leg's excess is now unhedged
            await self.repo.add_position(PositionRow(
                execution_id=ctx.id, mode=self.cfg.mode, mapping_id=pos.mapping_id, kind="naked", status="open",
                title=f"UNHEDGED {pos.title}", size=imbalance, a_exchange=pos.b_exchange, a_market_id=pos.b_market_id,
                a_outcome=pos.b_outcome, a_cost=pos.b_cost * imbalance / pos.size,
                a_fee=pos.b_fee * imbalance / pos.size, expected_payout=ZERO))
            cost_closed += (pos.a_cost + pos.a_fee) * imbalance / pos.size
            await self.risk.trip("unexpected_exposure", f"closing position {position_id} left {imbalance} "
                                                        f"contracts unhedged on {pos.b_exchange}")
        pnl = proceeds - cost_closed
        gone = closed + imbalance
        remaining = pos.size - gone
        await self.repo.journal(ctx.id, S.COMPLETED if gone > 0 else S.FAILED,
                                {"closed": str(closed), "proceeds": str(proceeds), "pnl": str(pnl)},
                                matched_size=closed, fees=sum((r.fee for r in sold), ZERO))
        if gone <= 0:
            return False, "no fills while closing"
        if remaining > 0:
            keep = remaining / pos.size
            await self.repo.update_position(position_id, size=remaining, a_cost=pos.a_cost * keep,
                                            a_fee=pos.a_fee * keep, b_cost=pos.b_cost * keep, b_fee=pos.b_fee * keep,
                                            expected_payout=pos.expected_payout * keep)
            await self.repo.add_position(PositionRow(
                execution_id=ctx.id, mode=self.cfg.mode, mapping_id=pos.mapping_id, kind=pos.kind, status="closed",
                title=pos.title, size=gone, a_exchange=pos.a_exchange, a_market_id=pos.a_market_id,
                a_outcome=pos.a_outcome, a_cost=pos.a_cost * (1 - keep), a_fee=pos.a_fee * (1 - keep),
                b_exchange=pos.b_exchange, b_market_id=pos.b_market_id, b_outcome=pos.b_outcome,
                b_cost=pos.b_cost * (1 - keep), b_fee=pos.b_fee * (1 - keep), proceeds=proceeds, realized_pnl=pnl,
                close_reason="closed by operator", closed_at=datetime.now(timezone.utc)))
        else:
            await self.repo.close_position(position_id, status="closed", proceeds=proceeds, realized_pnl=pnl,
                                           reason="closed by operator")
        return True, f"sold {gone} of {pos.size}, proceeds ${proceeds:.2f}, realised P&L ${pnl:.2f}"
