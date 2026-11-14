"""The arbitrage engine: continuous scan -> evaluate -> rank -> execute, plus
settlement tracking for open positions. It owns no exchange specifics."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from ..config.runtime import RuntimeConfig
from ..data.orderbooks import BookFeed, OrderBookStore
from ..database.repo import Repository
from ..exchanges.base import ExchangeAdapter
from . import events
from .capital_allocator import CapitalAllocator
from .events import EventBus
from .execution_engine import ExecutionEngine
from .profitability import Evaluation
from .risk_manager import RiskManager
from .types import ZERO, Exchange, Mapping, Outcome, Resolution

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Opportunity:
    mapping: Mapping
    kalshi_outcome: Outcome
    evaluation: Evaluation
    opportunity_id: int | None = None

    @property
    def direction(self) -> str:
        p = self.mapping.poly_outcome_for(self.kalshi_outcome).opposite
        return f"kalshi_{self.kalshi_outcome.value}+polymarket_{p.value}"


class ArbitrageEngine:
    def __init__(self, cfg: RuntimeConfig, repo: Repository, bus: EventBus, venues: dict[Exchange, ExchangeAdapter],
                 store: OrderBookStore, feed: BookFeed, allocator: CapitalAllocator, risk: RiskManager,
                 execution: ExecutionEngine):
        self.cfg, self.repo, self.bus, self.venues = cfg, repo, bus, venues
        self.store, self.feed, self.allocator, self.risk, self.execution = store, feed, allocator, risk, execution
        self.mappings: list[Mapping] = []
        self.latest: list[Opportunity] = []           # last scan, ranked (accepted first)
        self.last_scan_ts = 0.0
        self.scans = 0
        self._logged: dict[tuple[int, str], tuple[str, float, int]] = {}   # dedupe of journaled evaluations
        self._notified: dict[tuple[int, str], float] = {}
        self._scan_lock = asyncio.Lock()
        self.screen_mode = False
        self.screen_stats: dict = {}

    # -- mappings -------------------------------------------------------------
    async def reload_mappings(self) -> None:
        """Verified mappings are traded; candidates are evaluated for display only."""
        self.mappings = await self.repo.list_mappings(("verified", "candidate"), limit=2000)
        # verified pairs are traded and always watched; unverified candidates only feed the
        # review screen, so only the most promising few get order books (exchange rate limits)
        markets = []
        if self.screen_mode:
            # screening fetches quotes in bulk; only open positions need continuously polled books
            candidates = []
            self.feed.watch([])
        candidates = [] if self.screen_mode else sorted((m for m in self.mappings if m.status == "candidate" and m.confidence >= 60),
                            key=lambda m: -m.confidence)[: self.cfg.max_watched_candidates]
        if not self.screen_mode:
            for m in [m for m in self.mappings if m.status == "verified"] + candidates:
                markets += [m.kalshi, m.polymarket]
        # markets behind open positions must stay watched too (for marks and closes)
        for p in await self.repo.open_positions(self.cfg.mode):
            for ex, mid in ((p.a_exchange, p.a_market_id), (p.b_exchange, p.b_market_id)):
                if ex:
                    mk = await self.repo.get_market(Exchange(ex), mid)
                    if mk:
                        markets.append(mk)
        self.feed.watch(markets)

    # -- evaluation -------------------------------------------------------------
    async def evaluate_mapping(self, m: Mapping, now: float | None = None) -> list[Opportunity]:
        out = []
        bk = self.store.get(Exchange.KALSHI, m.kalshi.market_id)
        bp = self.store.get(Exchange.POLYMARKET, m.polymarket.market_id)
        if bk is None or bp is None:
            return out
        tradeable = m.status == "verified"
        for k_out in (Outcome.YES, Outcome.NO):
            p_out = m.poly_outcome_for(k_out).opposite
            a = self.execution.leg_input(m.kalshi, k_out, bk)
            b = self.execution.leg_input(m.polymarket, p_out, bp)
            ev = await self.allocator.plan(m, a, b, now=now, require_confidence=tradeable)
            if not tradeable and (ev.accepted or ev.reason.startswith("sizing: Kelly")):
                ev = Evaluation(False, f"mapping not verified (confidence {m.confidence:.0f}); approve with "
                                       f"/mappings approve {m.id}", ev.quote, ev.required_roi, ev.risk_score,
                                ev.days_to_settlement, ev.components)
            out.append(Opportunity(m, k_out, ev))
        return out

    async def _record(self, opp: Opportunity) -> None:
        """Journal every evaluation that had a gross edge, including rejections and the
        reason. Unchanged repeats are throttled so the table stays readable."""
        ev = opp.evaluation
        if ev.quote is None and "no gross edge" in ev.reason:
            return
        key = (opp.mapping.id, opp.direction)
        status = "accepted" if ev.accepted else "rejected"
        sig = f"{status}:{ev.reason.split('(')[0][:40]}"
        prev = self._logged.get(key)
        now = time.time()
        if prev and prev[0] == sig and now - prev[1] < 300:
            opp.opportunity_id = prev[2]
            return
        q = ev.quote
        details = {"title": opp.mapping.kalshi.title, "confidence": opp.mapping.confidence,
                   "risk_score": ev.risk_score, "components": ev.components, "days": ev.days_to_settlement}
        if q:
            details.update({
                "kalshi": {"outcome": q.a.outcome.value, "vwap": q.a.vwap, "limit": q.a.limit_price, "fee": q.a.fee},
                "polymarket": {"outcome": q.b.outcome.value, "vwap": q.b.vwap, "limit": q.b.limit_price, "fee": q.b.fee},
                "payout": q.payout, "conversion": q.conversion, "slippage_buffer": q.slippage_buffer,
                "leg_risk_buffer": q.leg_risk_buffer, "settlement_buffer": q.settlement_buffer,
                "operational_buffer": q.operational_buffer, "capital_cost": q.capital_cost,
                "fail_prob": q.fail_prob, "expected_profit": q.expected_profit})
        opp.opportunity_id = await self.repo.add_opportunity(
            mode=self.cfg.mode, mapping_id=opp.mapping.id, direction=opp.direction, status=status,
            reason=ev.reason, size=q.size if q else ZERO, capital=q.capital if q else ZERO,
            gross=q.gross if q else ZERO, fees=q.fees if q else ZERO, net=q.net if q else ZERO,
            roi=q.roi if q else 0.0, required_roi=ev.required_roi, details=details)
        self._logged[key] = (sig, now, opp.opportunity_id)

    async def scan_once(self, execute: bool = True) -> list[Opportunity]:
        """Evaluate every mapping from the books already in the store (books mode)."""
        async with self._scan_lock:
            opps: list[Opportunity] = []
            for m in self.mappings:
                try:
                    opps.extend(await self.evaluate_mapping(m))
                except Exception:
                    log.exception("evaluation failed for mapping %s", m.id)
            return await self._finish(opps, execute)

    @staticmethod
    def _ask(q: tuple, outcome: Outcome) -> Decimal | None:
        bid, ask = q
        if outcome is Outcome.YES:
            return ask
        return Decimal(1) - bid if bid is not None else None

    async def screen_once(self, execute: bool = True) -> list[Opportunity]:
        """Screen mode: bulk top-of-book quotes for every pair (a handful of requests), then full
        order books and the real evaluation only for pairs whose quotes cost less than $1."""
        async with self._scan_lock:
            maps = [m for m in self.mappings if m.status in ("verified", "candidate")]
            stats = {"pairs": len(maps), "quoted": 0, "deep": 0, "best_gross": None, "best_pair": ""}
            if not maps:
                self.screen_stats = stats
                return await self._finish([], execute)
            kmk = list({m.kalshi.market_id: m.kalshi for m in maps}.values())
            pmk = list({m.polymarket.market_id: m.polymarket for m in maps}.values())
            kq, pq = await asyncio.gather(self.venues[Exchange.KALSHI].get_quotes(kmk),
                                          self.venues[Exchange.POLYMARKET].get_quotes(pmk))
            gaps: list[tuple[Decimal, Mapping]] = []
            for m in maps:
                a, b = kq.get(m.kalshi.market_id), pq.get(m.polymarket.market_id)
                if not a or not b:
                    continue
                stats["quoted"] += 1
                best = None
                for k_out in (Outcome.YES, Outcome.NO):
                    ka, pa = self._ask(a, k_out), self._ask(b, m.poly_outcome_for(k_out).opposite)
                    if ka is not None and pa is not None:
                        g = ka + pa
                        best = g if best is None else min(best, g)
                        if stats["best_gross"] is None or g < stats["best_gross"]:
                            stats["best_gross"] = g
                            p_out = m.poly_outcome_for(k_out).opposite
                            stats["best_pair"] = (f"{m.kalshi.market_id} {k_out.value.upper()} @ {ka} + "
                                                  f"{m.polymarket.market_id} {p_out.value.upper()} @ {pa}")
                if best is not None and best < 1:
                    gaps.append((best, m))
            gaps.sort(key=lambda x: x[0])
            opps: list[Opportunity] = []
            for _, m in gaps[: self.cfg.screen_max_deep]:
                try:
                    bk, bp = await asyncio.gather(self.venues[Exchange.KALSHI].get_orderbook(m.kalshi),
                                                  self.venues[Exchange.POLYMARKET].get_orderbook(m.polymarket))
                except Exception as e:
                    log.warning("order books for mapping %s unavailable: %s", m.id, str(e)[:160])
                    continue
                await self.store.put_async(bk)
                await self.store.put_async(bp)
                stats["deep"] += 1
                opps.extend(await self.evaluate_mapping(m))
            self.screen_stats = stats
            return await self._finish(opps, execute)

    async def _finish(self, opps: list[Opportunity], execute: bool) -> list[Opportunity]:
        opps.sort(key=lambda o: (o.evaluation.accepted, o.evaluation.score,
                                 o.evaluation.quote.roi if o.evaluation.quote else -1), reverse=True)
        for o in opps:
            await self._record(o)
        self.latest, self.last_scan_ts = opps, time.time()
        self.scans += 1
        accepted = [o for o in opps if o.evaluation.accepted]
        now = time.time()
        for o in accepted:
            key = (o.mapping.id, o.direction)
            if self.cfg.notify_opportunities and now - self._notified.get(key, 0) > 600:
                self._notified[key] = now
                q = o.evaluation.quote
                await self.bus.publish(events.OPPORTUNITY, title=o.mapping.kalshi.title, direction=o.direction,
                                       size=q.size, net=q.net, roi_pct=q.roi * 100,
                                       required_pct=o.evaluation.required_roi * 100, capital=q.capital,
                                       confidence=o.mapping.confidence, mapping_id=o.mapping.id)
        if execute and self.cfg.autotrade and accepted:
            ok, _ = self.risk.trading_allowed()
            if ok:
                # best risk-adjusted opportunity first; each execution re-validates on fresh
                # books and re-sizes against the capital that is actually left
                for o in accepted[:3]:
                    if not self.risk.trading_allowed()[0]:
                        break
                    await self.execution.execute(o.mapping, o.kalshi_outcome, o.opportunity_id)
                    self._logged.pop((o.mapping.id, o.direction), None)
        return opps

    async def run(self) -> None:
        while True:
            try:
                if self.screen_mode:
                    await self.screen_once()
                else:
                    await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("scan failed: %s", str(e)[:200])
            await asyncio.sleep(self.cfg.screen_interval_s if self.screen_mode else self.cfg.scan_interval_s)

    # -- settlement -------------------------------------------------------------
    async def settle_once(self) -> int:
        """Close positions whose markets have resolved and book the realised result.
        A hedged pair that does not pay exactly its size is a settlement discrepancy."""
        settled = 0
        for pos in await self.repo.open_positions(self.cfg.mode):
            legs = [(pos.a_exchange, pos.a_market_id, pos.a_outcome)]
            if pos.kind == "pair":
                legs.append((pos.b_exchange, pos.b_market_id, pos.b_outcome))
            payouts: list[Decimal] = []
            resolved = []
            for ex, mid, oc in legs:
                market = await self.repo.get_market(Exchange(ex), mid)
                venue = self.venues.get(Exchange(ex))
                if market is None or venue is None:
                    break
                try:
                    res = await venue.get_resolution(market)
                except Exception as e:
                    log.warning("resolution lookup failed for %s: %s", mid, e)
                    break
                if res is None:
                    break
                resolution, yes_value = res
                per = yes_value if Outcome(oc) is Outcome.YES else Decimal(1) - yes_value
                payouts.append(pos.size * per)
                resolved.append((venue, market, Outcome(oc), resolution, pos.size * per))
            if len(payouts) != len(legs):
                continue
            for venue, market, oc, _, payout in resolved:
                await venue.on_position_settled(market, oc, pos.size, payout)
            total = sum(payouts, ZERO)
            pnl = total - pos.cost_basis
            await self.repo.close_position(pos.id, status="settled", proceeds=total, realized_pnl=pnl,
                                           reason="settled:" + "/".join(r[3].value for r in resolved))
            settled += 1
            await self.bus.publish(events.POSITION_SETTLED, position_id=pos.id, title=pos.title, payout=total,
                                   pnl=pnl, kind=pos.kind)
            if pos.kind == "pair" and abs(total - pos.expected_payout) > Decimal("0.01"):
                await self.risk.trip(
                    "execution_discrepancy",
                    f"settlement discrepancy on position {pos.id} ({pos.title}): paid ${total:.2f}, "
                    f"expected ${pos.expected_payout:.2f}. The two markets did not settle as a hedge.",
                    data={"position_id": pos.id, "resolutions": [r[3].value for r in resolved]})
                if pos.mapping_id:
                    await self.repo.update_mapping(pos.mapping_id, status="rejected", settlement_compatible=False)
        return settled

    # -- marks ------------------------------------------------------------------
    async def unrealized(self) -> dict:
        """locked_in: profit if every open hedge settles as designed.
        mark_to_market: what closing everything at current bids would realise."""
        locked, mtm, unmarked = ZERO, ZERO, 0
        for pos in await self.repo.open_positions(self.cfg.mode):
            if pos.kind == "pair":
                locked += pos.expected_payout - pos.cost_basis
            value, ok = ZERO, True
            legs = [(pos.a_exchange, pos.a_market_id, pos.a_outcome)]
            if pos.kind == "pair":
                legs.append((pos.b_exchange, pos.b_market_id, pos.b_outcome))
            for ex, mid, oc in legs:
                book = self.store.get(Exchange(ex), mid)
                bid = book.best_bid(Outcome(oc)) if book else None
                if bid is None:
                    ok = False
                    break
                value += bid * pos.size
            if ok:
                mtm += value - pos.cost_basis
            else:
                unmarked += 1
        return {"locked_in": locked, "mark_to_market": mtm, "unmarked": unmarked}


__all__ = ["ArbitrageEngine", "Opportunity", "Resolution"]
