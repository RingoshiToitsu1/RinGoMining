"""Profitability engine, capital allocator, risk manager, matcher."""
import time
from datetime import timedelta
from decimal import Decimal

import pytest

from arbbot.core.fees import estimate_fee
from arbbot.core.market_matcher import MarketMatcher, VerifierVerdict, dates, numbers
from arbbot.core.profitability import LegInput
from arbbot.core.types import Exchange, FeeSpec, Level, OrderBook, Outcome
from tests.conftest import CLOSE, kalshi_market, poly_market

D = Decimal


def legs(h, k_ask="0.40", p_no_ask="0.50", size="200", age=0.0, levels=None):
    k, p = kalshi_market(), poly_market()
    now = time.time()
    kb = OrderBook.from_single_book(Exchange.KALSHI, k.market_id, [Level(D(k_ask) - D("0.02"), D(size))],
                                    levels or [Level(D(k_ask), D(size))], ts=now - age)
    pb = OrderBook.from_single_book(Exchange.POLYMARKET, p.market_id, [Level(1 - D(p_no_ask), D(size))],
                                    [Level(1 - D(p_no_ask) + D("0.02"), D(size))], ts=now - age)
    return LegInput(k, Outcome.YES, kb), LegInput(p, Outcome.NO, pb)


def ev(h, a, b, **kw):
    return h.profit.evaluate(a, b, confidence=kw.pop("confidence", 100.0),
                             settlement_compatible=kw.pop("compatible", True), **kw)


# ---------------------------------------------------------------- fees
def test_fee_models():
    kalshi = FeeSpec("kalshi_quadratic", D("0.07"))
    assert estimate_fee(kalshi, D("0.50"), D(100)) == D("1.75")
    assert estimate_fee(kalshi, D("0.50"), D(1)) == D("0.02")          # rounds up to the cent
    assert estimate_fee(FeeSpec("poly_exponent", D("0.04"), D(1)), D("0.5"), D(100)) == D("1.0000")
    assert estimate_fee(FeeSpec("notional_bps", D("0.001")), D("0.5"), D(100)) == D("0.0500")
    assert estimate_fee(FeeSpec(), D("0.5"), D(100)) == 0


# ------------------------------------------------------- profitability
async def test_net_profit_formula_is_payout_minus_all_costs(h):
    a, b = legs(h)
    e = ev(h, a, b)
    q = e.quote
    assert e.accepted
    assert q.payout == q.size
    assert q.acquisition == q.size * D("0.90")
    assert q.fees == q.a.fee + q.b.fee > 0
    assert q.net == q.payout - q.acquisition - q.fees - q.conversion - q.risk_buffer
    assert q.risk_buffer > 0 and q.net < q.expected_profit
    assert q.roi == pytest.approx(float(q.net / (q.acquisition + q.fees)))


async def test_no_edge_and_thin_edge_are_rejected(h):
    assert "no gross edge" in ev(h, *legs(h, "0.50", "0.50")).reason
    thin = ev(h, *legs(h, "0.49", "0.50"))           # 1c gross, eaten by fees
    assert not thin.accepted and thin.quote is not None


async def test_confidence_and_settlement_gates(h):
    a, b = legs(h)
    assert "confidence" in ev(h, a, b, confidence=97.9).reason
    assert "settlement rules" in ev(h, a, b, compatible=False).reason
    assert ev(h, a, b, confidence=98.0).accepted


async def test_threshold_is_dynamic(h):
    deep = ev(h, *legs(h, size="2000"))
    shallow = ev(h, *legs(h, size="140"))
    stale = ev(h, *legs(h, size="2000", age=2.5))
    assert deep.accepted
    assert shallow.required_roi > deep.required_roi          # thin liquidity demands more
    assert stale.required_roi > deep.required_roi            # stale quotes demand more
    assert h.cfg.min_roi_pct / 100 <= deep.required_roi <= h.cfg.max_roi_pct / 100
    too_old = ev(h, *legs(h, age=10))
    assert "stale order book" in too_old.reason


async def test_volatility_raises_slippage_and_failure_risk(h):
    a, b = legs(h, size="2000")
    calm = ev(h, a, b)
    a.vol_per_s = b.vol_per_s = 0.02
    wild = ev(h, a, b)
    assert wild.quote.slippage_buffer > calm.quote.slippage_buffer
    assert wild.quote.fail_prob > calm.quote.fail_prob
    assert not wild.accepted


async def test_sizing_walks_the_ladder_and_stops_where_edge_ends(h):
    ladder = [Level(D("0.40"), D(100)), Level(D("0.45"), D(100)), Level(D("0.52"), D(500))]
    a, b = legs(h, size="1000", levels=ladder)
    h.cfg.max_book_take_pct = 100
    e = ev(h, a, b)
    assert e.accepted
    assert e.quote.size <= 200                       # the 0.52 level has no edge against 0.50
    assert e.quote.a.limit_price <= D("0.45")
    capped = ev(h, a, b, max_size=D(30))
    assert capped.quote.size == 30


async def test_execution_history_feeds_back_into_failure_probability(h):
    a, b = legs(h, size="2000")
    base = ev(h, a, b).quote.fail_prob
    for _ in range(10):
        h.profit.record_leg_result(Exchange.POLYMARKET, failed=True)
    assert ev(h, a, b).quote.fail_prob > base


# ---------------------------------------------------------- allocator
async def test_exchange_pools_are_independent(h):
    await h.add_mapping()
    h.books(size="2000")
    h.poly.cash = D("12")                    # plenty on Kalshi, almost nothing on Polymarket
    await h.pump()
    a = h.execution.leg_input(h.mapping.kalshi, Outcome.YES, h.store.get(Exchange.KALSHI, "KXBTC-100K"))
    b = h.execution.leg_input(h.mapping.polymarket, Outcome.NO, h.store.get(Exchange.POLYMARKET, "btc-100k-dec-31"))
    e = await h.allocator.plan(h.mapping, a, b)
    assert not e.accepted or e.quote.b.capital <= D("12")
    h.poly.cash = D("1")
    await h.allocator.refresh_balances()
    e = await h.allocator.plan(h.mapping, a, b)
    assert not e.accepted and "polymarket" in e.reason


async def test_hard_limits_cap_every_trade(h):
    await h.add_mapping()
    h.books("0.30", "0.40", size="5000")     # a huge 30% edge must not lift any limit
    await h.pump()
    a = h.execution.leg_input(h.mapping.kalshi, Outcome.YES, h.store.get(Exchange.KALSHI, "KXBTC-100K"))
    b = h.execution.leg_input(h.mapping.polymarket, Outcome.NO, h.store.get(Exchange.POLYMARKET, "btc-100k-dec-31"))
    e = await h.allocator.plan(h.mapping, a, b)
    assert e.accepted
    assert e.quote.capital <= h.cfg.max_trade_usd
    assert e.quote.capital <= h.cfg.bankroll * D(str(h.cfg.max_trade_bankroll_pct / 100))
    h.cfg.max_trade_usd = D("40")
    assert (await h.allocator.plan(h.mapping, a, b)).quote.capital <= D("40")
    h.cfg.kalshi_allocation = h.cfg.polymarket_allocation = D(0)
    assert "no capital allocated" in (await h.allocator.plan(h.mapping, a, b)).reason


async def test_kelly_scales_with_edge_and_confidence(h):
    k = h.allocator.kelly_fraction
    assert k(0.005, 98.0) == 0.0                     # a 0.5% edge cannot pay for the residual doubt at 98
    assert 0 < k(0.009, 98.0) < k(0.009, 100.0)
    assert k(0.10, 100.0) == h.cfg.max_trade_bankroll_pct / 100       # clipped by the hard cap
    assert k(-0.01, 100.0) == 0.0


async def test_reservations_prevent_overcommitment(h):
    await h.add_mapping()
    await h.allocator.refresh_balances()
    await h.allocator.reserve("x", {Exchange.KALSHI: D("900")})
    from arbbot.core.capital_allocator import InsufficientCapital
    with pytest.raises(InsufficientCapital):
        await h.allocator.reserve("y", {Exchange.KALSHI: D("200")})
    h.allocator.release("x")
    await h.allocator.reserve("y", {Exchange.KALSHI: D("200")})


# ------------------------------------------------------------- risk
async def test_risk_monitor_auto_pauses_and_self_heals(h):
    await h.add_mapping()
    h.books()
    await h.pump()
    await h.risk.monitor_once()
    assert h.risk.trading_allowed()[0]
    h.kalshi.healthy = False                 # API outage: health + balance checks fail
    await h.risk.monitor_once()
    await h.risk.monitor_once()              # an outage must persist across two checks
    ok, why = h.risk.trading_allowed()
    assert not ok and "kalshi" in why
    assert "exchange_unavailable:kalshi" in h.risk.pauses and "balance_unverified:kalshi" in h.risk.pauses
    h.kalshi.healthy = True
    await h.risk.monitor_once()
    assert h.risk.trading_allowed()[0]


async def test_stale_books_pause_trading(h):
    await h.add_mapping()
    h.books()
    await h.pump()
    h.risk._watched = lambda: {Exchange.KALSHI: 1, Exchange.POLYMARKET: 1}
    for key in list(h.store._books):
        h.store._books[key].ts -= 600
    await h.risk.monitor_once()
    assert any(k.startswith("stale_books") for k in h.risk.pauses)


async def test_daily_loss_limit_latches(h):
    from arbbot.database.models import ExecutionRow
    from arbbot.database.repo import utcnow
    await h.add_mapping()
    await h.allocator.refresh_balances()
    await h.repo.create_execution(ExecutionRow(id="loss", mode="paper", mapping_id=1, state="failed",
                                               realized_pnl=D("-60"), finished_at=utcnow()))
    await h.risk.monitor_once()
    assert "daily_loss" in h.risk.pauses
    await h.risk.monitor_once()
    assert len([e for e in h.of("risk.alert") if e.data["kind"] == "daily_loss"]) == 1   # alerted once


async def test_unexpected_exposure_is_detected(h):
    await h.add_mapping()
    h.books()
    await h.pump()
    await h.execution.execute(h.mapping, Outcome.YES)
    assert not await h.risk.check_exposure()
    h.kalshi.positions[("KXBTC-100K", Outcome.YES)] += 7      # exchange disagrees with the journal
    problems = await h.risk.check_exposure()
    assert problems and "unexpected_exposure" in h.risk.pauses


async def test_emergency_stop_blocks_everything_and_survives_restart(h):
    await h.add_mapping()
    await h.risk.emergency_stop("test")
    assert not h.risk.trading_allowed()[0] and h.cfg.autotrade is False
    assert (await h.repo.load_config()).emergency_stop is True


async def test_drawdown_limit(h):
    await h.add_mapping()
    await h.allocator.refresh_balances()
    await h.repo.add_equity_snapshot("paper", D(1000), D(1000), D(0))
    h.kalshi.cash = D(700)                   # equity 2000 -> 1700 = 15% drawdown
    await h.risk.monitor_once()
    assert "drawdown" in h.risk.pauses


# ----------------------------------------------------------- matcher
def test_text_extraction():
    assert numbers("Bitcoin above $100,000 on Dec 31, 2026?") == {100000.0}
    assert numbers("Will BTC hit 100k by 2026?") == {100000.0}
    assert numbers("Fed cuts by 25 bps in March") == {25.0}
    assert (2026, 12, 31) in dates("on December 31, 2026")
    assert (0, 12, 31) in dates("on Dec 31")


async def test_matcher_finds_equivalent_and_rejects_different_strikes(h):
    k = kalshi_market()
    same = poly_market()
    other_strike = poly_market("btc-120k", "Bitcoin above $120,000 on December 31, 2026?")
    other_date = poly_market("btc-100k-jun", "Bitcoin above $100,000 on June 30, 2026?")
    other_date.close_time = CLOSE - timedelta(days=180)
    unrelated = poly_market("fed", "Will the Fed cut rates in December 2026?")
    cands = h.matcher.candidates([k], [same, other_strike, other_date, unrelated])
    assert cands[0][1].market_id == same.market_id
    good = h.matcher.assess(k, same)
    assert good.deterministic_ok and not good.inverted and 60 < good.confidence <= 90
    assert good.settlement_compatible is False        # heuristics never grant tradeability
    assert not h.matcher.assess(k, other_strike).deterministic_ok
    assert not h.matcher.assess(k, other_date).deterministic_ok


async def test_matcher_detects_inverted_polarity(h):
    k = kalshi_market("KXBTC-B", "Will Bitcoin be above $100,000 on Dec 31, 2026?")
    p = poly_market("btc-below", "Will Bitcoin be below $100,000 on December 31, 2026?")
    assert h.matcher.assess(k, p).inverted is True
    k2 = kalshi_market("KXNBA", "Lakers vs Celtics winner")
    k2.yes_label = "Lakers"
    p2 = poly_market("nba", "Lakers vs Celtics")
    p2.yes_label, p2.no_label = "Celtics", "Lakers"
    assert h.matcher.assess(k2, p2).inverted is True


class FakeVerifier:
    name = "llm"

    def __init__(self, verdict):
        self.verdict, self.calls = verdict, 0

    async def verify(self, k, p, heuristic):
        self.calls += 1
        return self.verdict


async def test_verifier_can_verify_but_cannot_override_deterministic_failure(h):
    k, same = kalshi_market(), poly_market()
    await h.repo.upsert_markets([k, same])
    ok = VerifierVerdict(True, False, 99.5, True, [], "identical source and threshold")
    m = MarketMatcher(h.cfg, h.repo, h.bus, FakeVerifier(ok))
    mid, res = await m.evaluate_pair(k, same)
    assert res.confidence == 99.0 and res.settlement_compatible        # capped below manual approval
    assert (await h.repo.get_mapping(mid)).status == "verified"
    # deterministic failure: the verifier is not even consulted
    bad = poly_market("btc-120k", "Bitcoin above $120,000 on December 31, 2026?")
    await h.repo.upsert_markets([bad])
    v = FakeVerifier(ok)
    mid2, res2 = await MarketMatcher(h.cfg, h.repo, h.bus, v).evaluate_pair(k, bad)
    assert v.calls == 0 and (await h.repo.get_mapping(mid2)).status != "verified"
    # verifier says not compatible -> stays untradeable
    k3, p3 = kalshi_market("KX3"), poly_market("p3")
    await h.repo.upsert_markets([k3, p3])
    no = VerifierVerdict(True, False, 99.0, False, ["different oracle"], "")
    mid3, res3 = await MarketMatcher(h.cfg, h.repo, h.bus, FakeVerifier(no)).evaluate_pair(k3, p3)
    assert not res3.settlement_compatible and (await h.repo.get_mapping(mid3)).status == "candidate"


async def test_manual_approval_and_revalidation_demotes_changed_rules(h):
    k, p = kalshi_market(), poly_market()
    await h.repo.upsert_markets([k, p])
    mid, _ = await h.matcher.evaluate_pair(k, p)
    assert (await h.repo.get_mapping(mid)).status == "candidate"
    ok, _ = await h.matcher.approve(mid, "ringo")
    m = await h.repo.get_mapping(mid)
    assert ok and m.status == "verified" and m.confidence == 100 and m.checks["approved_by"] == "ringo"
    # unchanged -> stays verified
    assert await h.matcher.revalidate({k.key: k, p.key: p}) == []
    # exchange edits the rules -> match becomes uncertain -> demoted, alert raised
    p.rules = "Resolves Yes per Binance spot price at 12:00 ET."
    assert await h.matcher.revalidate({k.key: k, p.key: p}) == [mid]
    m = await h.repo.get_mapping(mid)
    assert m.status == "stale" and not m.settlement_compatible
    assert any(e.data["kind"] == "matching_uncertain" for e in h.of("risk.alert"))


async def test_slow_exchange_does_not_stall_the_other_feed(h):
    import asyncio
    await h.add_mapping()
    h.books()
    orig = h.poly.get_orderbooks

    async def slow(markets):
        await asyncio.sleep(0.5)
        return await orig(markets)

    h.poly.get_orderbooks = slow
    task = asyncio.create_task(h.feed.run())
    await asyncio.sleep(0.3)
    kb = h.store.get(Exchange.KALSHI, "KXBTC-100K")
    assert kb is not None and h.store.get(Exchange.POLYMARKET, "btc-100k-dec-31") is None
    await asyncio.sleep(0.5)
    assert h.store.get(Exchange.POLYMARKET, "btc-100k-dec-31") is not None
    task.cancel()


# ------------------------------------------------------- crypto matching / screening
def _crypto_pair(strike="65250", kstrike="65249.99", asset="btc", rounding=2, hour="5:00PM", series="KXBTCD"):
    from datetime import datetime, timezone
    from arbbot.core.types import Market
    inst = datetime(2026, 11, 14, 22, 0, tzinfo=timezone.utc)
    idx = {"btc": "Bitcoin Real-Time Index (BRTI)", "xrp": "Ripple-Dollar Real Time Index (XRPUSD_RTI)"}[asset]
    k = Market(Exchange.KALSHI, f"{series}-26NOV1417-T{kstrike}", "price on Nov 14", close_time=inst,
               expiration_time=inst, event_id=f"{series}-26NOV1417",
               rules=f"If the simple average of the sixty seconds of CF Benchmarks' {idx} before 5 PM EST is above "
                     f"{kstrike} at 5 PM EST on Nov 14, 2026, then the market resolves to Yes.",
               extra={"series_ticker": series, "strike_type": "greater", "floor_strike": float(kstrike)})
    pre = "cpc" if asset == "btc" else f"cpc{asset}"
    p = Market(Exchange.POLYMARKET, f"{pre}-{asset}-above-day-2026-11-14-{strike.replace('.', 'p')}", "price",
               close_time=inst, expiration_time=inst,
               rules=f"This market will settle to Yes if the price is at or above ${strike} at {hour} ET on Nov 14, "
                     f"2026. Outcome sourced from CF Benchmarks' {idx}. In the last minute 60 prices are collected. "
                     f"The official and final value is the simple average of these prices, rounded to the nearest "
                     f"{rounding} decimal places.")
    return k, p


def test_crypto_matcher_accepts_identical_contracts_and_measures_the_edge():
    from arbbot.core.crypto_matcher import match_crypto
    k, p = _crypto_pair()
    (m,) = match_crypto([k], [p])
    assert m.compatible and m.checks["disagreement_window"] == "0.005" and m.checks["disagreement_prob_max"] < 1e-5


def test_crypto_matcher_rejects_time_rounding_and_strike_mismatches():
    from arbbot.core.crypto_matcher import match_crypto
    k, p = _crypto_pair(hour="4:00PM")
    assert not match_crypto([k], [p])                          # different settlement instant: no pair
    k, p = _crypto_pair(strike="0.9", kstrike="0.8995", asset="xrp", rounding=5, series="KXXRPD")
    (m,) = match_crypto([k], [p])                              # 0.05 cents apart on a $0.90 coin: too wide
    assert not m.compatible and "strike edges differ" in m.reasons[0]
    k, p = _crypto_pair()
    p.rules = p.rules.replace("CF Benchmarks' Bitcoin Real-Time Index (BRTI)", "Coinbase spot price")
    (m,) = match_crypto([k], [p])
    assert not m.compatible and any("index" in r for r in m.reasons)


async def test_structural_pairs_need_opt_in_to_trade_and_demote_on_rule_change(h):
    k, p = _crypto_pair()
    await h.repo.upsert_markets([k, p])
    await h.matcher.run([k], [p])
    (row,) = await h.repo.mapping_rows(("candidate",))
    assert row.method == "crypto-structural" and row.settlement_compatible and row.confidence == 99
    h.cfg.auto_verify_structural = True
    await h.matcher.run([k], [p])
    assert (await h.repo.get_mapping(row.id)).status == "verified"
    p.rules = p.rules.replace("rounded to the nearest 2", "rounded to the nearest 0")
    await h.repo.upsert_markets([p])
    assert await h.matcher.revalidate({k.key: k, p.key: p}) == [row.id]


async def test_screen_mode_finds_gap_from_quotes_and_paper_trades_it(h):
    await h.add_mapping()
    h.engine.screen_mode = True
    h.books("0.50", "0.50")                     # no gap
    await h.allocator.refresh_balances()
    assert not await h.engine.screen_once()
    assert h.engine.screen_stats["best_gross"] == 1
    h.books("0.40", "0.50")                     # 10c gap
    opps = await h.engine.screen_once()
    assert any(o.evaluation.accepted for o in opps)
    assert h.engine.screen_stats["deep"] == 1
    assert len(await h.repo.open_positions("paper")) == 1
