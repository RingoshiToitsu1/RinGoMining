"""Capital allocation and position sizing.

Kalshi and Polymarket balances are separate pools: a trade needs free capital
on *both* venues and nothing is ever assumed to move between them. Sizing is
fractional-Kelly, then clipped by every hard limit; no theoretical return can
lift a hard limit.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal

from ..config.runtime import RuntimeConfig
from ..database.repo import Repository
from ..exchanges.base import ExchangeAdapter
from .profitability import Evaluation, LegInput, ProfitabilityEngine
from .types import ZERO, Balance, Exchange, Mapping

log = logging.getLogger(__name__)


class InsufficientCapital(Exception):
    pass


class CapitalAllocator:
    def __init__(self, cfg: RuntimeConfig, repo: Repository, venues: dict[Exchange, ExchangeAdapter],
                 profit: ProfitabilityEngine):
        self.cfg, self.repo, self.venues, self.profit = cfg, repo, venues, profit
        self.balances: dict[Exchange, Balance] = {}
        self.balance_ts: dict[Exchange, float] = {}
        self._reserved: dict[str, dict[Exchange, Decimal]] = {}

    # -- balances ------------------------------------------------------------
    async def refresh_balances(self) -> dict[Exchange, bool]:
        ok = {}
        for ex, venue in self.venues.items():
            try:
                self.balances[ex] = await venue.get_balance()
                self.balance_ts[ex] = time.time()
                ok[ex] = True
            except Exception as e:
                log.warning("balance check failed on %s: %s", ex.value, e)
                ok[ex] = False
        return ok

    def balance_age(self, ex: Exchange) -> float:
        return time.time() - self.balance_ts.get(ex, 0.0)

    def allocation(self, ex: Exchange) -> Decimal:
        return self.cfg.kalshi_allocation if ex is Exchange.KALSHI else self.cfg.polymarket_allocation

    def reserved(self, ex: Exchange) -> Decimal:
        return sum((r.get(ex, ZERO) for r in self._reserved.values()), ZERO)

    async def deployed(self) -> dict[Exchange, Decimal]:
        out = {ex: ZERO for ex in Exchange}
        for p in await self.repo.open_positions(self.cfg.mode):
            for ex in Exchange:
                out[ex] += p.exchange_cost(ex.value)
        return out

    async def available(self) -> dict[Exchange, Decimal]:
        """Free capital per exchange: bounded by the real balance AND the allocation."""
        dep = await self.deployed()
        out = {}
        for ex in self.venues:
            bal = self.balances.get(ex)
            cash = bal.available if bal else ZERO
            out[ex] = max(min(cash - self.reserved(ex), self.allocation(ex) - dep[ex] - self.reserved(ex)), ZERO)
        return out

    # -- reservations ----------------------------------------------------------
    async def reserve(self, execution_id: str, amounts: dict[Exchange, Decimal]) -> None:
        avail = await self.available()
        for ex, amt in amounts.items():
            if amt > avail.get(ex, ZERO):
                raise InsufficientCapital(f"{ex.value}: need ${amt:.2f}, free ${avail.get(ex, ZERO):.2f}")
        self._reserved[execution_id] = dict(amounts)

    def release(self, execution_id: str) -> None:
        self._reserved.pop(execution_id, None)

    # -- sizing ----------------------------------------------------------------
    def kelly_fraction(self, roi: float, confidence: float) -> float:
        """Fraction of bankroll for a hedge that returns `roi` when it settles as a
        hedge and loses `loss_given_failure` of the stake when it does not."""
        cfg = self.cfg
        p_bad = min(cfg.settlement_tail_prob + max(100.0 - confidence, 0.0) / 100 * 0.5, 0.99)
        if roi <= 0:
            return 0.0
        full = ((1 - p_bad) * roi - p_bad * cfg.loss_given_failure) / (roi * cfg.loss_given_failure)
        return max(min(full * cfg.kelly_fraction, cfg.max_trade_bankroll_pct / 100), 0.0)

    async def plan(self, mapping: Mapping, a: LegInput, b: LegInput, *, now: float | None = None,
                   require_confidence: bool = True) -> Evaluation:
        """Size an opportunity. Returns an accepted Evaluation with the final size,
        or a rejection carrying the reason."""
        cfg = self.cfg
        bankroll = cfg.bankroll
        if bankroll <= 0:
            return Evaluation(False, "no capital allocated (use /capital)")
        best_a, best_b = a.book.best_ask(a.outcome), b.book.best_ask(b.outcome)
        if best_a is None or best_b is None:
            return Evaluation(False, "no executable asks")

        avail = await self.available()
        dep = await self.deployed()
        total_dep = sum(dep.values(), ZERO) + sum((self.reserved(ex) for ex in self.venues), ZERO)
        open_pos = await self.repo.open_positions(cfg.mode)
        in_market = sum((p.cost_basis for p in open_pos if p.mapping_id == mapping.id), ZERO)

        caps = {
            "per-trade limit": min(cfg.max_trade_usd, bankroll * Decimal(str(cfg.max_trade_bankroll_pct / 100))),
            "bankroll exposure limit": bankroll * Decimal(str(cfg.max_exposure_pct / 100)) - total_dep,
            "market concentration limit": bankroll * Decimal(str(cfg.max_market_concentration_pct / 100)) - in_market,
        }
        name, cap_usd = min(caps.items(), key=lambda kv: kv[1])
        if cap_usd < cfg.min_trade_usd:
            return Evaluation(False, f"capital: {name} leaves ${max(cap_usd, ZERO):.2f}")
        for leg in (a, b):
            if avail.get(leg.exchange, ZERO) < cfg.min_trade_usd / 2:
                return Evaluation(False, f"capital: no free capital on {leg.exchange.value} "
                                         f"(${avail.get(leg.exchange, ZERO):.2f})")

        max_size = min(cap_usd / (best_a + best_b), avail[a.exchange] / best_a, avail[b.exchange] / best_b,
                       cfg.max_unhedged_usd / max(best_a, best_b))
        ev = Evaluation(False, "capital: could not size")
        for _ in range(6):
            ev = self.profit.evaluate(a, b, confidence=mapping.confidence,
                                      settlement_compatible=mapping.settlement_compatible,
                                      max_size=max_size, now=now, require_confidence=require_confidence)
            if not ev.accepted or ev.quote is None:
                return ev
            q = ev.quote
            # shrink until every hard limit holds at real (VWAP + fee) cost
            ratios = [
                cap_usd / q.capital,
                avail[a.exchange] / q.a.capital,
                avail[b.exchange] / q.b.capital,
                cfg.max_unhedged_usd / max(q.a.capital, q.b.capital),
            ]
            budget = Decimal(str(self.kelly_fraction(q.roi, mapping.confidence))) * bankroll
            if budget < cfg.min_trade_usd:
                return Evaluation(False, f"sizing: Kelly stake ${budget:.2f} below minimum trade "
                                         f"(return {q.roi * 100:.2f}% too thin for confidence "
                                         f"{mapping.confidence:.0f})", q, ev.required_roi, ev.risk_score,
                                  ev.days_to_settlement, ev.components)
            ratios.append(budget / q.capital)
            worst = min(ratios)
            if worst >= 1:
                return ev
            max_size = q.size * worst * Decimal("0.995")
        return Evaluation(False, "capital: could not fit trade inside limits", ev.quote, ev.required_roi,
                          ev.risk_score, ev.days_to_settlement, ev.components)

    # -- reporting -----------------------------------------------------------
    async def report(self) -> dict:
        dep = await self.deployed()
        avail = await self.available()
        bankroll = self.cfg.bankroll
        total_dep = sum(dep.values(), ZERO)
        return {
            "bankroll": bankroll,
            "exchanges": {
                ex.value: {
                    "allocation": self.allocation(ex),
                    "balance": self.balances[ex].available if ex in self.balances else None,
                    "deployed": dep[ex], "reserved": self.reserved(ex), "available": avail.get(ex, ZERO),
                } for ex in self.venues
            },
            "deployed": total_dep,
            "utilization_pct": float(total_dep / bankroll * 100) if bankroll > 0 else 0.0,
        }
