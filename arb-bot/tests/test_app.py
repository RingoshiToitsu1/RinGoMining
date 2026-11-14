"""Whole application in paper mode: startup, Telegram commands, notifications,
live-unlock gate, restart persistence, adapters, backtester."""
from decimal import Decimal

import pytest

from arbbot.app import App
from arbbot.backtesting.engine import Backtester, PairSpec, demo_markets, synthetic_books
from arbbot.bot.telegram.bot import COMMANDS, dispatch
from arbbot.bot.telegram.notifications import Notifier
from arbbot.config.runtime import RuntimeConfig
from arbbot.config.settings import Settings
from arbbot.core.types import Exchange, OrderRequest, Outcome, Side, TIF
from arbbot.database.repo import Repository
from arbbot.exchanges.sim import PaperExchange, SimExchange
from tests.conftest import kalshi_market, poly_market

D = Decimal


class FakeReal(SimExchange):
    """Stands in for a real exchange adapter: real=not simulated, counts order attempts."""
    is_simulated = False

    def __init__(self, name):
        super().__init__(name, D("5000"))
        self.real_orders = 0
        self.eligible = True

    async def check_eligibility(self):
        from arbbot.core.types import EligibilityReport
        rep = EligibilityReport(self.name)
        rep.add("account verified", self.eligible, "" if self.eligible else "jurisdiction not permitted")
        return rep

    async def place_order(self, req):
        self.real_orders += 1
        return await super().place_order(req)


async def make_app(repo=None, tmp_path=None):
    s = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:", telegram_bot_token="x",
                 telegram_admin_ids="1", data_dir=tmp_path or "./var-test")
    real = {Exchange.KALSHI: FakeReal(Exchange.KALSHI), Exchange.POLYMARKET: FakeReal(Exchange.POLYMARKET)}
    k, p = kalshi_market(), poly_market()
    real[Exchange.KALSHI].add_market(k)
    real[Exchange.POLYMARKET].add_market(p)
    real[Exchange.KALSHI].set_book(k.market_id, yes_bids=[("0.38", 300)], yes_asks=[("0.40", 300)])
    real[Exchange.POLYMARKET].set_book(p.market_id, yes_bids=[("0.50", 300)], yes_asks=[("0.52", 300)])
    repo = repo or Repository(s.database_url)
    app = App(s, repo=repo, data_adapters=real)
    await app.init()
    return app, real


@pytest.fixture
async def app(tmp_path):
    a, real = await make_app(tmp_path=tmp_path)
    a.real = real
    sent = []

    async def send(text):
        sent.append(text)

    a.notifier = Notifier(a.bus, send)
    a.sent = sent
    for px in a.paper.values():
        px.latency_s = px.fail_prob = px.partial_prob = 0
    a.cfg.paper_fail_prob = a.cfg.paper_partial_prob = 0
    yield a
    await a.repo.close()


async def cmd(app, line):
    parts = line.split()
    return (await dispatch(app, parts[0], parts[1:], "ringo")).text


async def test_every_command_renders(app):
    for name in COMMANDS:
        if name in ("stop",):
            continue
        text = await cmd(app, name)
        assert text and "failed:" not in text, (name, text)
    assert "Unknown command" in await cmd(app, "nope")


async def test_full_paper_flow_through_telegram(app):
    # nothing can trade before capital is allocated and a mapping is approved
    assert "Allocate capital first" in await cmd(app, "autotrade on")
    assert "500" in await cmd(app, "capital kalshi 500")
    await cmd(app, "capital polymarket 500")
    assert "polymarket" in await cmd(app, "balance")
    out = await cmd(app, "mappings discover")
    assert "1 candidates" in out
    assert "🟡 #1" in await cmd(app, "mappings")
    assert "CF Benchmarks" in await cmd(app, "mappings show 1")
    await cmd(app, "scan")
    assert "not verified" in await cmd(app, "opportunities")
    assert "eligible for automated execution" in await cmd(app, "mappings approve 1")
    await cmd(app, "autotrade on")
    assert "1</b> qualifying" in await cmd(app, "scan") or "qualifying" in await cmd(app, "scan")
    pos = await app.repo.open_positions("paper")
    assert pos and pos[0].kind == "pair"
    # paper mode never touched the real adapters
    assert app.real[Exchange.KALSHI].real_orders == 0 and app.real[Exchange.POLYMARKET].real_orders == 0
    trade = next(t for t in app.sent if "ARBITRAGE PAIR COMPLETED" in t)
    for needle in ("Kalshi: BUY YES", "Polymarket: BUY NO", "YES price", "NO price", "Quantity matched",
                   "Total acquisition cost", "Fees", "Expected settlement payout", "Expected net profit",
                   "Expected return", "Capital deployed", "Execution time", "Matching confidence"):
        assert needle in trade, needle
    assert any("New opportunity" in t for t in app.sent)
    assert "#1" in await cmd(app, "positions")
    assert "locked in" in await cmd(app, "profit")
    assert "✅" in await cmd(app, "history")
    dash = await cmd(app, "dashboard")
    assert "1 matched" in dash and "utilization" in dash
    assert "✅" in await cmd(app, f"close {pos[0].id}")
    assert not await app.repo.open_positions("paper")


async def test_settings_are_validated_and_persisted(app):
    assert "→ <b>300</b>" in await cmd(app, "settings max_trade_usd 300")
    assert (await app.repo.load_config()).max_trade_usd == D("300")
    assert "❌" in await cmd(app, "settings max_trade_usd -5")
    assert "❌" in await cmd(app, "settings min_match_confidence 90")       # cannot go below 98
    assert "❌" in await cmd(app, "settings mode live")                     # protected
    assert "❌" in await cmd(app, "settings live_unlocked on")
    assert "❌" in await cmd(app, "threshold min 20")                       # above max
    assert app.cfg.min_roi_pct == 1.0
    assert "→ <b>2.5</b>" in await cmd(app, "threshold min 2.5")
    assert "risk" in (await cmd(app, "settings")).lower()
    assert "max_daily_loss_usd" in await cmd(app, "settings risk")


async def test_pause_resume_and_emergency_stop(app):
    await cmd(app, "capital kalshi 500")
    await cmd(app, "capital polymarket 500")
    await cmd(app, "autotrade on")
    await cmd(app, "pause")
    assert app.cfg.paused and not app.risk.trading_allowed()[0]
    await cmd(app, "resume")
    assert app.risk.trading_allowed()[0]
    assert "EMERGENCY STOP" in await cmd(app, "stop")
    assert app.cfg.emergency_stop and not app.cfg.autotrade
    assert "Emergency stop is active" in await cmd(app, "autotrade on")
    assert any("EMERGENCY STOP" in t for t in app.sent)
    assert "Autotrade stays OFF" in await cmd(app, "resume")
    assert not app.cfg.emergency_stop and not app.cfg.autotrade


async def test_live_stays_locked_until_paper_requirements_and_eligibility_pass(app):
    out = await cmd(app, "autotrade live")
    assert "Live trading is locked" in out and "paper trading period" in out
    assert app.cfg.mode == "paper"
    # meet the paper requirements
    for k, v in dict(paper_min_days=0, paper_min_trades=0, kalshi_allocation=D(500), polymarket_allocation=D(500)).items():
        setattr(app.cfg, k, v)
    app.real[Exchange.POLYMARKET].eligible = False            # exchange not available to this user
    out = await cmd(app, "autotrade live CONFIRM")
    assert "locked" in out and "jurisdiction not permitted" in out and app.cfg.mode == "paper"
    app.real[Exchange.POLYMARKET].eligible = True
    assert "CONFIRM" in await cmd(app, "autotrade live")       # ready, but needs explicit confirmation
    assert app.cfg.mode == "paper"
    out = await cmd(app, "autotrade live CONFIRM")
    assert "LIVE MODE" in out and app.cfg.mode == "live" and app.cfg.live_unlocked
    assert app.cfg.autotrade is False                          # must be re-enabled deliberately
    assert not app.venues[Exchange.KALSHI].is_simulated
    await cmd(app, "autotrade paper")
    assert app.cfg.mode == "paper" and not app.cfg.live_unlocked and app.venues[Exchange.KALSHI].is_simulated


async def test_restart_restores_config_paper_account_and_positions(tmp_path):
    app1, _ = await make_app(tmp_path=tmp_path)
    for px in app1.paper.values():
        px.latency_s = px.fail_prob = px.partial_prob = 0
    await cmd(app1, "capital kalshi 500")
    await cmd(app1, "capital polymarket 500")
    await cmd(app1, "mappings discover")
    await cmd(app1, "mappings approve 1")
    await cmd(app1, "autotrade on")
    await cmd(app1, "scan")
    cash = {ex: px.cash for ex, px in app1.paper.items()}
    assert cash[Exchange.KALSHI] < 1000
    await app1._persist_paper()

    app2, _ = await make_app(repo=app1.repo, tmp_path=tmp_path)     # same database, new process
    assert app2.cfg.autotrade and app2.cfg.kalshi_allocation == D(500)
    assert {ex: px.cash for ex, px in app2.paper.items()} == cash
    assert len(await app2.repo.open_positions("paper")) == 1
    assert app2.risk.trading_allowed()[0]                           # journal and paper account reconcile
    assert len(app2.engine.mappings) == 1
    await app1.repo.close()


async def test_paper_exchange_cannot_reach_real_order_methods():
    real = FakeReal(Exchange.KALSHI)
    k = kalshi_market()
    real.add_market(k)
    real.set_book(k.market_id, yes_bids=[("0.38", 100)], yes_asks=[("0.40", 100)])
    paper = PaperExchange(real, D(100))
    assert not any("place_order" in name or "cancel" in name for name in vars(paper) if name.startswith("_data"))
    res = await paper.place_order(OrderRequest(Exchange.KALSHI, k, Outcome.YES, Side.BUY, D("0.40"), D(10),
                                               TIF.IOC, "c1"))
    assert res.filled_size == 10 and real.real_orders == 0 and real.cash == 5000
    assert paper.cash < 100
    assert len(real.books[k.market_id].yes_asks) == 1 and real.books[k.market_id].yes_asks[0].size == 100


# ------------------------------------------------------------- backtests
async def run_bt(**kw):
    cfg = RuntimeConfig(kalshi_allocation=D(1000), polymarket_allocation=D(1000), max_trade_usd=D(200),
                        max_unhedged_usd=D(200), max_unhedged_seconds=0.2)
    for k, v in kw.pop("cfg", {}).items():
        setattr(cfg, k, v)
    pairs, books = [], []
    for i in range(2):
        k, p = demo_markets(i)
        pairs.append(PairSpec(k, p))
        books += synthetic_books(k, p, 250, seed=11 + i)
    return await Backtester(cfg, pairs, books, **kw).run()


async def test_backtest_clean_conditions_is_profitable_and_fully_hedged():
    r = await run_bt(latency_s=0.0)
    assert r.matched > 0 and r.naked == 0
    assert r.net > 0 and r.end_equity == r.start_equity + r.realized


async def test_backtest_latency_partials_and_failures_exercise_emergency_paths():
    r = await run_bt(latency_s=0.6, fail_prob=0.15, partial_prob=0.4, seed=3)
    assert r.executions > 0 and (r.failed > 0 or r.partial > 0)
    assert r.hedges + r.unwinds > 0
    # losses from failed hedges stay small relative to the bankroll
    assert r.net > -D(60)


async def test_backtest_dynamic_threshold_and_capital_limits():
    loose = await run_bt(latency_s=0.0, cfg={"min_roi_pct": 0.5, "max_roi_pct": 3.0})
    strict = await run_bt(latency_s=0.0, cfg={"min_roi_pct": 6.0, "max_roi_pct": 20.0})
    assert strict.accepted < loose.accepted
    small = await run_bt(latency_s=0.0, cfg={"max_trade_usd": D(25), "max_unhedged_usd": D(25),
                                             "max_exposure_pct": 5.0})
    assert small.capital_peak <= D(2000) * D("0.05") + D(1)
