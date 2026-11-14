"""Application wiring and lifecycle.

Startup order matters: load persisted configuration -> build adapters ->
verify eligibility -> reconcile journal against the exchanges -> only then
start scanning. Nothing can trade before reconciliation finishes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .config.runtime import RuntimeConfig
from .config.settings import Settings
from .core import events
from .core.arbitrage_engine import ArbitrageEngine
from .core.capital_allocator import CapitalAllocator
from .core.events import Event, EventBus
from .core.execution_engine import ExecutionEngine
from .core.market_matcher import MarketMatcher
from .core.profitability import ProfitabilityEngine
from .core.risk_manager import RiskManager
from .core.types import ZERO, EligibilityReport, Exchange
from .data.historical_data import BookRecorder
from .data.market_discovery import MarketDiscovery
from .data.orderbooks import BookFeed, OrderBookStore
from .database.repo import Repository, as_utc
from .exchanges.base import ExchangeAdapter
from .exchanges.sim import PaperExchange, SimExchange
from .infra.cache import Cache, make_cache

log = logging.getLogger(__name__)


def build_data_adapters(settings: Settings) -> dict[Exchange, ExchangeAdapter]:
    """Real exchange adapters. Each is independent: a disabled or unavailable
    exchange is simply absent and everything else keeps working."""
    out: dict[Exchange, ExchangeAdapter] = {}
    if settings.kalshi_enabled:
        from .exchanges.kalshi.client import KalshiAdapter
        out[Exchange.KALSHI] = KalshiAdapter(settings)
    venue = settings.polymarket_venue.lower()
    if venue == "us":
        from .exchanges.polymarket.us import PolymarketUSAdapter
        out[Exchange.POLYMARKET] = PolymarketUSAdapter(settings)
    elif venue == "intl":
        from .exchanges.polymarket.intl import PolymarketIntlAdapter
        out[Exchange.POLYMARKET] = PolymarketIntlAdapter(settings)
    return out


class App:
    def __init__(self, settings: Settings, *, repo: Repository | None = None,
                 data_adapters: dict[Exchange, ExchangeAdapter] | None = None, cache: Cache | None = None):
        self.settings = settings
        self.repo = repo or Repository(settings.database_url)
        self.cache = cache or make_cache(settings.redis_url)
        self.bus = EventBus()
        self.data_adapters = data_adapters if data_adapters is not None else build_data_adapters(settings)
        self.venues: dict[Exchange, ExchangeAdapter] = {}
        self.paper: dict[Exchange, PaperExchange] = {}
        self.eligibility: dict[Exchange, EligibilityReport] = {}
        self.cfg = RuntimeConfig()
        self.started_at = time.time()
        self.recovery_notes: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self.ready = False

    # ------------------------------------------------------------------ startup
    async def init(self) -> None:
        s = self.settings
        if s.is_sqlite:
            await self.repo.create_all()
        self.cfg = await self.repo.load_config()
        cfg = self.cfg
        for ad in self.data_adapters.values():
            if type(ad).__name__ == "PolymarketIntlAdapter":     # collateral there is a stablecoin, not USD
                ad.conversion_bps = cfg.usdc_conversion_bps
        self.store = OrderBookStore(self.cache)
        self.recorder = BookRecorder(s.data_dir)
        self.feed = BookFeed(self.venues, self.store, self.bus, lambda: self.cfg.book_poll_interval_s,
                             recorder=self.recorder.record)
        self.profit = ProfitabilityEngine(cfg)
        self.allocator = CapitalAllocator(cfg, self.repo, self.venues, self.profit)
        self.risk = RiskManager(cfg, self.repo, self.bus, self.allocator, self.store, self.venues,
                                watched=lambda: {e: len(w) for e, w in self.feed._watch.items()})
        self.execution = ExecutionEngine(cfg, self.repo, self.bus, self.venues, self.store, self.allocator,
                                         self.risk, self.profit, self.cache)
        self.risk._busy = lambda: self.execution.busy
        self.engine = ArbitrageEngine(cfg, self.repo, self.bus, self.venues, self.store, self.feed,
                                      self.allocator, self.risk, self.execution)
        verifier = None
        if s.anthropic_api_key:
            from .core.llm_verifier import ClaudeRuleVerifier
            verifier = ClaudeRuleVerifier(s.anthropic_api_key, s.matcher_llm_model)
        self.matcher = MarketMatcher(cfg, self.repo, self.bus, verifier)
        self.discovery = MarketDiscovery(self.venues, self.repo, self.matcher, self.bus, self.engine.reload_mappings)
        await self.risk.load()

        await self.check_eligibility()
        if cfg.mode == "live" and not self._live_possible()[0]:
            # never come up live on a venue that no longer passes its checks
            await self._fall_back_to_paper("eligibility checks failed at startup")
        await self._install_venues()
        for topic in (events.TRADE_COMPLETED, events.EXECUTION_FAILED, events.POSITION_SETTLED):
            self.bus.subscribe(topic, self._persist_paper)

        # reconcile before anything can trade
        await self.allocator.refresh_balances()
        await self.engine.reload_mappings()
        try:
            self.recovery_notes = await self.execution.recover()
        except Exception as e:
            log.exception("recovery failed")
            await self.risk.trip("execution_discrepancy", f"startup reconciliation failed: {e}")
        await self.risk.check_exposure()
        self.ready = True

    async def check_eligibility(self) -> dict[Exchange, EligibilityReport]:
        for ex, ad in self.data_adapters.items():
            try:
                self.eligibility[ex] = await ad.check_eligibility()
            except Exception as e:
                rep = EligibilityReport(ex)
                rep.add("eligibility check completed", False, str(e)[:200])
                self.eligibility[ex] = rep
        self.risk.eligibility = {ex: rep.ok for ex, rep in self.eligibility.items()}
        return self.eligibility

    def _live_possible(self) -> tuple[bool, str]:
        for ex in Exchange:
            rep = self.eligibility.get(ex)
            if rep is None:
                return False, f"{ex.value} adapter is disabled"
            if not rep.ok:
                bad = next((c for c in rep.checks if not c.ok), None)
                return False, f"{ex.value}: {bad.name if bad else 'not eligible'}"
        return True, ""

    async def _fall_back_to_paper(self, reason: str) -> None:
        self.cfg.mode, self.cfg.autotrade = "paper", False
        await self.repo.save_config(self.cfg)
        await self.risk.trip("mode_fallback", f"switched to paper mode: {reason}", pause=False)

    async def _install_venues(self) -> None:
        """Point the trading venues at paper wrappers or the real adapters, in place."""
        cfg = self.cfg
        self.venues.clear()
        if cfg.mode == "live":
            self.venues.update(self.data_adapters)
        else:
            for ex, ad in self.data_adapters.items():
                if ex not in self.paper:
                    start = cfg.paper_kalshi_balance if ex is Exchange.KALSHI else cfg.paper_polymarket_balance
                    lat = cfg.kalshi_latency_ms if ex is Exchange.KALSHI else cfg.polymarket_latency_ms
                    px = PaperExchange(ad, start, latency_s=lat / 1000, fail_prob=cfg.paper_fail_prob,
                                       partial_prob=cfg.paper_partial_prob)
                    px.load_state(await self.repo.kv_get(f"paper_state:{ex.value}"))
                    self.paper[ex] = px
                self.venues[ex] = self.paper[ex]
        if self.venues and self.ready:
            await self.allocator.refresh_balances()

    async def _persist_paper(self, _: Event | None = None) -> None:
        if self.cfg.mode != "paper":
            return
        for ex, px in self.paper.items():
            px.fail_prob, px.partial_prob = self.cfg.paper_fail_prob, self.cfg.paper_partial_prob
            await self.repo.kv_set(f"paper_state:{ex.value}", px.state())

    # ------------------------------------------------------------------ control
    async def save_config(self) -> None:
        await self.repo.save_config(self.cfg)

    async def reset_paper(self) -> None:
        for ex, px in self.paper.items():
            start = self.cfg.paper_kalshi_balance if ex is Exchange.KALSHI else self.cfg.paper_polymarket_balance
            px.cash, px.positions = Decimal(start), {}
        await self._persist_paper()

    async def live_readiness(self) -> list[tuple[str, bool, str]]:
        """Requirements for unlocking live trading: a completed paper period that met the
        configured performance bar, and both exchange adapters verified eligible."""
        cfg = self.cfg
        st = await self.repo.stats("paper")
        first = as_utc(st["first_trade_at"])
        days = (datetime.now(timezone.utc) - first).total_seconds() / 86400 if first else 0.0
        out = [
            ("paper trading period", days >= cfg.paper_min_days, f"{days:.1f} of {cfg.paper_min_days:g} days"),
            ("paper trades completed", st["matched_pairs"] >= cfg.paper_min_trades,
             f"{st['matched_pairs']} of {cfg.paper_min_trades}"),
            ("paper net P&L", st["net_realized"] + st["locked_in"] >= cfg.paper_min_net_pnl,
             f"${st['net_realized'] + st['locked_in']:.2f} (need ${cfg.paper_min_net_pnl})"),
            ("paper failure rate", st["fail_rate_pct"] <= cfg.paper_max_fail_rate_pct,
             f"{st['fail_rate_pct']:.0f}% (max {cfg.paper_max_fail_rate_pct:g}%)"),
            ("capital allocated on both exchanges", cfg.kalshi_allocation > 0 and cfg.polymarket_allocation > 0,
             f"kalshi ${cfg.kalshi_allocation}, polymarket ${cfg.polymarket_allocation}"),
            ("no active risk pauses", not self.risk.pauses and not cfg.emergency_stop,
             "; ".join(self.risk.pauses.values()) or "clear"),
        ]
        await self.check_eligibility()
        for ex in Exchange:
            rep = self.eligibility.get(ex)
            if rep is None:
                out.append((f"{ex.value} adapter", False, "disabled"))
                continue
            bad = [c for c in rep.checks if not c.ok]
            out.append((f"{ex.value} eligibility", rep.ok,
                        "all checks passed" if rep.ok else "; ".join(f"{c.name} ({c.detail})" for c in bad)[:300]))
        return out

    async def set_mode(self, mode: str) -> tuple[bool, str]:
        if mode == self.cfg.mode:
            return True, f"already in {mode} mode"
        if self.execution.busy:
            return False, "an execution is in flight; try again in a moment"
        if mode == "live":
            failed = [f"{n}: {d}" for n, ok, d in await self.live_readiness() if not ok]
            if failed:
                return False, "live trading stays locked:\n- " + "\n- ".join(failed)
            self.cfg.live_unlocked = True
        else:
            self.cfg.live_unlocked = False
        self.cfg.mode = mode
        self.cfg.autotrade = False          # the operator re-enables trading deliberately after a mode change
        await self.save_config()
        await self._install_venues()
        await self.allocator.refresh_balances()
        await self.engine.reload_mappings()
        self.recovery_notes = await self.execution.recover()
        await self.risk.check_exposure()
        return True, f"mode is now {mode}; autotrade is off until you enable it"

    # ---------------------------------------------------------------- dashboard
    async def dashboard(self) -> dict[str, Any]:
        cfg = self.cfg
        now = datetime.now(timezone.utc)
        st = await self.repo.stats(cfg.mode)
        cap = await self.allocator.report()
        unreal = await self.engine.unrealized()
        risk = await self.risk.summary()
        bankroll = cfg.bankroll

        async def ret(days: int) -> tuple[Decimal, float]:
            pnl = await self.repo.realized_pnl(cfg.mode, since=now - timedelta(days=days))
            return pnl, float(pnl / bankroll * 100) if bankroll > 0 else 0.0

        return {"mode": cfg.mode, "autotrade": cfg.autotrade, "allowed": self.risk.trading_allowed(),
                "stats": st, "capital": cap, "unrealized": unreal, "risk": risk,
                "daily": await ret(1), "weekly": await ret(7), "monthly": await ret(30),
                "mappings": {"verified": sum(1 for m in self.engine.mappings if m.status == "verified"),
                             "candidates": sum(1 for m in self.engine.mappings if m.status == "candidate")},
                "scans": self.engine.scans, "last_scan_age": time.time() - self.engine.last_scan_ts
                if self.engine.last_scan_ts else None}

    async def status(self) -> dict[str, Any]:
        ex = {}
        for e, venue in self.venues.items():
            try:
                up = await venue.health()
            except Exception:
                up = False
            last = self.store.last_update(e)
            ex[e.value] = {"up": up, "simulated": venue.is_simulated, "streaming": venue.supports_streaming,
                           "book_age": time.time() - last if last else None,
                           "watched": len(self.feed._watch.get(e, {})),
                           "eligible": self.eligibility[e].ok if e in self.eligibility else False,
                           "checks": [(c.name, c.ok, c.detail) for c in self.eligibility[e].checks]
                           if e in self.eligibility else []}
        return {"exchanges": ex, "database": await self.repo.ping(), "cache": await self.cache.ping(),
                "cache_kind": type(self.cache).__name__, "uptime_s": time.time() - self.started_at,
                "disabled": [e.value for e in Exchange if e not in self.data_adapters],
                "discovery": self.discovery.last_counts, "discovery_age": time.time() - self.discovery.last_run
                if self.discovery.last_run else None, "recovery": self.recovery_notes,
                "verifier": self.matcher.verifier.name if self.matcher.verifier else "manual approval only"}

    # --------------------------------------------------------------------- loops
    async def _loop(self, name: str, fn: Any, interval: Any, initial_delay: float = 0.0) -> None:
        await asyncio.sleep(initial_delay)
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s loop failed", name)
            await asyncio.sleep(interval() if callable(interval) else interval)

    async def _daily_summary(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            nxt = now.replace(hour=self.cfg.daily_summary_hour_utc, minute=0, second=5, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            await asyncio.sleep((nxt - now).total_seconds())
            try:
                await self.bus.publish(events.DAILY_SUMMARY, dashboard=await self.dashboard())
            except Exception:
                log.exception("daily summary failed")

    def start_background(self) -> None:
        mk = asyncio.create_task
        self._tasks = [
            mk(self.feed.run(), name="book-feed"),
            mk(self.engine.run(), name="scanner"),
            mk(self._loop("risk", self.risk.monitor_once, 10.0), name="risk"),
            mk(self._loop("discovery", self.discovery.run_once, lambda: self.cfg.discovery_interval_s), name="discovery"),
            mk(self._loop("settlement", self.engine.settle_once, lambda: self.cfg.settlement_check_interval_s, 30.0),
               name="settlement"),
            mk(self._loop("paper-state", self._persist_paper, 60.0, 60.0), name="paper-state"),
            mk(self._loop("recorder", self._flush_recorder, 30.0, 30.0), name="recorder"),
            mk(self._daily_summary(), name="daily-summary"),
            mk(self._loop("heartbeat", self._heartbeat, 10.0), name="heartbeat"),
        ]

    async def _heartbeat(self) -> None:
        await self.cache.set("instance", "1", ttl=30)

    async def _flush_recorder(self) -> None:
        self.recorder.flush()

    async def shutdown(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.feed.stop()
        await self._persist_paper()
        self.recorder.close()
        for ad in self.data_adapters.values():
            try:
                await ad.close()
            except Exception:
                pass
        await self.cache.close()
        await self.repo.close()


__all__ = ["App", "build_data_adapters", "SimExchange", "ZERO"]
