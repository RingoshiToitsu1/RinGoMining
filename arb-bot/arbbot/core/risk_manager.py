"""Independent risk-management service.

It re-checks every trade against hard limits regardless of what the
profitability engine or allocator concluded, monitors system health, and
pauses new trading automatically. A hedged position is not treated as
risk-free: exposure, concentration and loss limits apply to hedged pairs too.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from ..config.runtime import RuntimeConfig
from ..data.orderbooks import OrderBookStore
from ..database.repo import Repository
from ..exchanges.base import ExchangeAdapter
from . import events
from .capital_allocator import CapitalAllocator
from .events import EventBus
from .profitability import Evaluation
from .types import ZERO, Exchange, Mapping, Outcome

log = logging.getLogger(__name__)

# Conditions that clear themselves once the underlying problem is gone.
SELF_HEALING = ("exchange_unavailable", "stale_books", "balance_unverified", "database_unavailable")


class RiskManager:
    def __init__(self, cfg: RuntimeConfig, repo: Repository, bus: EventBus, allocator: CapitalAllocator,
                 store: OrderBookStore, venues: dict[Exchange, ExchangeAdapter],
                 watched: Callable[[], dict[Exchange, int]] = lambda: {},
                 execution_busy: Callable[[], bool] = lambda: False):
        self.cfg, self.repo, self.bus = cfg, repo, bus
        self.allocator, self.store, self.venues = allocator, store, venues
        self._watched, self._busy = watched, execution_busy
        self.pauses: dict[str, str] = {}       # condition key -> human reason
        self.eligibility: dict[Exchange, bool] = {}
        self._daily_loss_day: str = ""

    # -- persistence -----------------------------------------------------------
    async def load(self) -> None:
        st = await self.repo.kv_get("risk_state") or {}
        self.pauses = dict(st.get("pauses", {}))
        self._daily_loss_day = st.get("daily_loss_day", "")

    async def _save(self) -> None:
        await self.repo.kv_set("risk_state", {"pauses": self.pauses, "daily_loss_day": self._daily_loss_day})

    # -- state -------------------------------------------------------------------
    def trading_allowed(self) -> tuple[bool, str]:
        if self.cfg.emergency_stop:
            return False, "emergency stop is active"
        if self.cfg.paused:
            return False, "paused by operator"
        if self.pauses:
            return False, "auto-paused: " + "; ".join(self.pauses.values())
        return True, ""

    async def trip(self, key: str, message: str, *, severity: str = "critical", pause: bool = True,
                   data: dict | None = None) -> None:
        """Record a risk violation, alert, and (optionally) pause new trading."""
        is_new = key not in self.pauses
        if pause:
            self.pauses[key] = message
            await self._save()
        if is_new or not pause:
            log.warning("RISK %s: %s", key, message)
            await self.repo.add_risk_event(key, severity, message, data)
            await self.bus.publish(events.RISK_ALERT, kind=key, severity=severity, message=message,
                                   paused=pause, data=data or {})

    async def clear(self, key: str, note: str = "") -> None:
        if key in self.pauses:
            del self.pauses[key]
            await self._save()
            await self.bus.publish(events.RISK_ALERT, kind=key, severity="info",
                                   message=f"cleared: {note or key}", paused=False, data={})

    async def clear_all(self) -> list[str]:
        """Operator /resume: acknowledge and clear every latched condition."""
        keys = list(self.pauses)
        self.pauses.clear()
        await self._save()
        return keys

    async def emergency_stop(self, reason: str) -> None:
        self.cfg.emergency_stop = True
        self.cfg.autotrade = False
        await self.repo.save_config(self.cfg)
        await self.trip("emergency_stop", f"EMERGENCY STOP: {reason}", pause=False)

    # -- pre-trade gate -----------------------------------------------------------
    async def pre_trade(self, mapping: Mapping, ev: Evaluation) -> tuple[bool, str]:
        cfg = self.cfg
        ok, why = self.trading_allowed()
        if not ok:
            return False, why
        q = ev.quote
        if q is None or not ev.accepted:
            return False, "no accepted quote"
        if cfg.mode == "live":
            if not cfg.live_unlocked:
                return False, "live trading is locked"
            for ex, venue in self.venues.items():
                if venue.is_simulated:
                    return False, f"{ex.value} venue is simulated while mode is live"
                if not self.eligibility.get(ex, False):
                    return False, f"{ex.value} eligibility checks have not passed"
        else:
            for ex, venue in self.venues.items():
                if not venue.is_simulated:
                    return False, f"paper mode but {ex.value} venue is real: refusing to trade"
        if mapping.status != "verified" or not mapping.settlement_compatible:
            return False, "market mapping is not verified"
        if mapping.confidence < cfg.min_match_confidence:
            return False, f"match confidence {mapping.confidence:.1f} below {cfg.min_match_confidence:.0f}"

        bankroll = cfg.bankroll
        positions = await self.repo.open_positions(cfg.mode)
        if len(positions) >= cfg.max_open_positions:
            return False, f"max open positions ({cfg.max_open_positions}) reached"
        deployed = sum((p.cost_basis for p in positions), ZERO)
        if deployed + q.capital > bankroll * Decimal(str(cfg.max_exposure_pct / 100)):
            return False, "trade would exceed max bankroll exposure"
        if q.capital > cfg.max_trade_usd:
            return False, f"trade ${q.capital:.2f} exceeds max trade size ${cfg.max_trade_usd}"
        in_market = sum((p.cost_basis for p in positions if p.mapping_id == mapping.id), ZERO)
        if in_market + q.capital > bankroll * Decimal(str(cfg.max_market_concentration_pct / 100)):
            return False, "trade would exceed max market concentration"
        if max(q.a.capital, q.b.capital) > cfg.max_unhedged_usd:
            return False, f"single leg exceeds max unmatched exposure ${cfg.max_unhedged_usd}"
        slip = float(q.slippage_buffer / q.acquisition * 100) if q.acquisition else 0.0
        if slip > cfg.max_slippage_pct:
            return False, f"expected slippage {slip:.2f}% over limit"
        for lq in (q.a, q.b):
            if lq.depth_at_limit * lq.limit_price < cfg.min_liquidity_usd:
                return False, f"{lq.exchange.value} liquidity under minimum"
            book = self.store.get(lq.exchange, lq.market_id)
            if book is None or book.age() * 1000 > cfg.max_book_age_ms:
                return False, f"{lq.exchange.value} order book is stale"
            if self.allocator.balance_age(lq.exchange) > 120:
                return False, f"{lq.exchange.value} balance not verified recently"
        if q.net <= 0 or q.roi < ev.required_roi:
            return False, "net return below dynamic threshold"
        if await self._daily_loss_breached():
            return False, "daily loss limit reached"
        return True, ""

    async def _daily_pnl(self) -> Decimal:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.repo.realized_pnl(self.cfg.mode, since=start)

    async def _daily_loss_breached(self) -> bool:
        return self.cfg.max_daily_loss_usd > 0 and await self._daily_pnl() <= -self.cfg.max_daily_loss_usd

    # -- continuous monitoring ----------------------------------------------------
    async def monitor_once(self) -> None:
        cfg = self.cfg
        now = time.time()

        if not await self.repo.ping():
            self.pauses.setdefault("database_unavailable", "database unreachable")
            return
        await self.clear("database_unavailable", "database reachable")

        # exchange health + stale books
        watched = self._watched()
        for ex, venue in self.venues.items():
            key = f"exchange_unavailable:{ex.value}"
            try:
                up = await venue.health()
            except Exception:
                up = False
            if not up:
                await self.trip(key, f"{ex.value} API unavailable")
            else:
                await self.clear(key, f"{ex.value} API reachable again")
            skey = f"stale_books:{ex.value}"
            limit = max(cfg.max_book_age_ms / 1000 * 10, 30.0)
            last = self.store.last_update(ex)
            if watched.get(ex, 0) > 0 and last > 0 and now - last > limit:
                await self.trip(skey, f"{ex.value} order books stale for {now - last:.0f}s")
            else:
                await self.clear(skey, f"{ex.value} order books fresh")

        # balances must be verifiable
        ok = await self.allocator.refresh_balances()
        for ex, good in ok.items():
            key = f"balance_unverified:{ex.value}"
            if not good:
                await self.trip(key, f"cannot verify {ex.value} balance")
            else:
                await self.clear(key, f"{ex.value} balance verified")

        # daily loss (latched until the next UTC day)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if "daily_loss" in self.pauses and self._daily_loss_day != today:
            await self.clear("daily_loss", "new trading day")
        if await self._daily_loss_breached():
            self._daily_loss_day = today
            await self.trip("daily_loss", f"daily loss limit hit: {await self._daily_pnl():.2f} "
                                          f"(limit -{cfg.max_daily_loss_usd})")

        # equity snapshot + drawdown
        if all(ok.values()) and ok:
            dep = await self.allocator.deployed()
            bal = self.allocator.balances
            k = bal[Exchange.KALSHI].total if Exchange.KALSHI in bal else ZERO
            p = bal[Exchange.POLYMARKET].total if Exchange.POLYMARKET in bal else ZERO
            await self.repo.add_equity_snapshot(cfg.mode, k, p, sum(dep.values(), ZERO))
            dd = await self.max_drawdown_pct(current_only=True)
            if dd >= cfg.max_drawdown_pct:
                await self.trip("drawdown", f"drawdown {dd:.1f}% reached limit {cfg.max_drawdown_pct}%")

        if not self._busy():
            await self.check_exposure()

    async def max_drawdown_pct(self, current_only: bool = False) -> float:
        """Peak-to-trough equity decline. current_only: decline from peak to now."""
        curve = await self.repo.equity_curve(self.cfg.mode)
        peak, worst, last = ZERO, 0.0, 0.0
        for _, eq in curve:
            peak = max(peak, eq)
            last = float((peak - eq) / peak * 100) if peak > 0 else 0.0
            worst = max(worst, last)
        return last if current_only else worst

    async def check_exposure(self) -> list[str]:
        """Compare what the exchanges say we hold with what the journal says we should
        hold, for every market the bot has ever touched. Any difference is unexpected
        exposure and pauses trading."""
        expected: dict[tuple[Exchange, str, Outcome], Decimal] = {}
        for p in await self.repo.open_positions(self.cfg.mode):
            for ex, mid, oc in ((p.a_exchange, p.a_market_id, p.a_outcome), (p.b_exchange, p.b_market_id, p.b_outcome)):
                if ex:
                    k = (Exchange(ex), mid, Outcome(oc))
                    expected[k] = expected.get(k, ZERO) + p.size
        managed = set(await self.repo.kv_get(f"managed_markets:{self.cfg.mode}") or [])
        problems = []
        for ex, venue in self.venues.items():
            try:
                actual = await venue.get_positions()
            except Exception:
                continue  # reported through balance/health checks
            keys = {(m, o) for (e, m, o) in expected if e is ex} | {
                k for k in actual if f"{ex.value}:{k[0]}" in managed}
            for m, o in keys:
                want, have = expected.get((ex, m, o), ZERO), actual.get((m, o), ZERO)
                if abs(want - have) >= Decimal("0.01"):
                    problems.append(f"{ex.value} {m} {o.value}: journal {want}, exchange {have}")
        if problems:
            await self.trip("unexpected_exposure", "position mismatch: " + " | ".join(problems[:5]),
                            data={"problems": problems})
        return problems

    async def note_managed(self, exchange: Exchange, market_id: str) -> None:
        key = f"managed_markets:{self.cfg.mode}"
        managed = set(await self.repo.kv_get(key) or [])
        tag = f"{exchange.value}:{market_id}"
        if tag not in managed:
            managed.add(tag)
            await self.repo.kv_set(key, sorted(managed))

    async def check_fee(self, exchange: Exchange, estimated: Decimal, actual: Decimal) -> None:
        tol = Decimal(str(1 + self.cfg.fee_discrepancy_tolerance_pct / 100))
        if actual > estimated * tol + Decimal("0.02"):
            await self.trip("execution_discrepancy",
                            f"{exchange.value} charged ${actual:.4f} in fees, estimate was ${estimated:.4f}",
                            data={"exchange": exchange.value})

    async def summary(self) -> dict:
        cfg = self.cfg
        positions = await self.repo.open_positions(cfg.mode)
        deployed = sum((p.cost_basis for p in positions), ZERO)
        naked = sum((p.cost_basis for p in positions if p.kind == "naked"), ZERO)
        by_market: dict[int, Decimal] = {}
        for p in positions:
            by_market[p.mapping_id] = by_market.get(p.mapping_id, ZERO) + p.cost_basis
        bankroll = cfg.bankroll
        return {
            "allowed": self.trading_allowed(), "pauses": dict(self.pauses),
            "deployed": deployed, "exposure_pct": float(deployed / bankroll * 100) if bankroll else 0.0,
            "unhedged": naked, "open_positions": len(positions),
            "largest_market_pct": float(max(by_market.values(), default=ZERO) / bankroll * 100) if bankroll else 0.0,
            "daily_pnl": await self._daily_pnl(), "drawdown_pct": await self.max_drawdown_pct(current_only=True),
            "max_drawdown_pct": await self.max_drawdown_pct(),
        }
