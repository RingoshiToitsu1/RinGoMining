"""Every execution pathway of the state machine, on simulated exchanges."""
from decimal import Decimal

from arbbot.core import events
from arbbot.core.execution_engine import S
from arbbot.core.types import Exchange, Outcome, Resolution

D = Decimal


async def states(h, execution_id):
    return [e.state for e in await h.repo.execution_events(execution_id)]


async def run(h, outcome=Outcome.YES):
    await h.pump()
    return await h.execution.execute(h.mapping, outcome)


async def test_happy_path_matches_both_legs(h):
    await h.add_mapping()
    h.books("0.40", "0.50")
    row = await run(h)
    assert row.state == S.COMPLETED
    assert row.matched_size == row.planned_size > 0
    st = await states(h, row.id)
    assert st[:4] == [S.DETECTED, S.VALIDATED, S.RESERVED, S.SUBMITTED]
    assert S.LEG1_FILLED in st and S.MATCHED in st and st[-1] == S.COMPLETED
    pos = await h.repo.open_positions("paper")
    assert len(pos) == 1 and pos[0].kind == "pair" and pos[0].size == row.matched_size
    # both exchanges hold exactly the hedge
    assert h.kalshi.positions[("KXBTC-100K", Outcome.YES)] == row.matched_size
    assert h.poly.positions[("btc-100k-dec-31", Outcome.NO)] == row.matched_size
    # cost + fees came out of each pool separately
    assert h.kalshi.cash < 1000 and h.poly.cash < 1000
    assert row.expected_net > 0
    done = h.of(events.TRADE_COMPLETED)[0].data
    assert done["yes_price"] == D("0.40") and done["no_price"] == D("0.50")
    assert done["quantity"] == row.matched_size and done["confidence"] == 100.0
    assert h.allocator.reserved(Exchange.KALSHI) == 0     # reservation released
    orders = await h.repo.orders_for(row.id)
    assert len({o.client_order_id for o in orders}) == 2  # unique idempotency keys


async def test_reverse_direction_and_inverted_mapping(h):
    await h.add_mapping(inverted=True)
    # inverted: Kalshi YES == Polymarket NO, so the hedge for Kalshi YES is Polymarket YES
    h.kalshi.set_book("KXBTC-100K", yes_bids=[("0.38", 200)], yes_asks=[("0.40", 200)])
    h.poly.set_book("btc-100k-dec-31", yes_bids=[("0.48", 200)], yes_asks=[("0.50", 200)])
    row = await run(h, Outcome.YES)
    assert row.state == S.COMPLETED
    assert h.poly.positions[("btc-100k-dec-31", Outcome.YES)] == row.matched_size


async def test_first_leg_unfilled_means_no_exposure(h):
    await h.add_mapping()
    h.books()
    h.kalshi.forced.append(D("0"))
    h.poly.forced.append(D("0"))   # whichever goes first gets nothing
    row = await run(h)
    assert row.state == S.FAILED and row.matched_size == 0
    assert "first leg unfilled" in row.error
    assert not await h.repo.open_positions("paper")
    assert h.kalshi.cash == 1000 and h.poly.cash == 1000
    assert h.risk.trading_allowed()[0]          # a clean miss does not pause trading
    assert h.of(events.EXECUTION_FAILED)[0].data["exposure"] is False


async def test_first_leg_partial_sizes_second_leg_down(h):
    await h.add_mapping()
    h.books()
    h.kalshi.forced.append(D("0.5"))
    h.poly.forced.append(D("0.5"))
    row = await run(h)
    # only one of the forced partials was consumed by leg 1; leg 2 may also be partial -> hedge completes it
    assert row.state == S.COMPLETED
    assert 0 < row.matched_size < row.planned_size
    assert S.LEG1_PARTIAL in await states(h, row.id)
    k = h.kalshi.positions[("KXBTC-100K", Outcome.YES)]
    p = h.poly.positions[("btc-100k-dec-31", Outcome.NO)]
    assert k == p == row.matched_size
    assert h.of(events.PARTIAL_FILL)


async def _first_exchange(h, row):
    return Exchange((row.plan or {})["first"]["exchange"])


async def test_second_leg_partial_then_emergency_hedge_completes(h):
    await h.add_mapping()
    h.books()
    # make Kalshi go first by making it look riskier (thinner book)
    h.kalshi.set_book("KXBTC-100K", yes_bids=[("0.38", 150)], yes_asks=[("0.40", 150)])
    h.poly.forced.append(D("0.25"))   # second leg only gets a quarter; liquidity stays, so the hedge can finish
    row = await run(h)
    assert (await _first_exchange(h, row)) is Exchange.KALSHI
    st = await states(h, row.id)
    assert S.LEG2_PARTIAL in st and S.HEDGE in st and S.UNWIND not in st
    assert row.state == S.COMPLETED and row.matched_size == row.planned_size
    assert [e.data["kind"] for e in h.of(events.EMERGENCY)] == ["hedge"]


async def test_second_leg_fails_then_emergency_unwind(h):
    await h.add_mapping()
    h.kalshi.set_book("KXBTC-100K", yes_bids=[("0.38", 150)], yes_asks=[("0.40", 150)])
    h.poly.set_book("btc-100k-dec-31", yes_bids=[("0.50", 500)], yes_asks=[("0.52", 500)])
    await h.pump()

    # after the first leg fills, the Polymarket side disappears: hedge is impossible
    orig = h.kalshi.place_order

    async def then_pull_liquidity(req):
        res = await orig(req)
        h.poly.set_book("btc-100k-dec-31", yes_bids=[("0.10", 500)], yes_asks=[("0.12", 500)])
        h.kalshi.place_order = orig
        return res

    h.kalshi.place_order = then_pull_liquidity
    row = await h.execution.execute(h.mapping, Outcome.YES)
    st = await states(h, row.id)
    assert S.HEDGE in st and S.UNWIND in st
    assert row.state == S.FAILED and row.matched_size == 0
    assert "unwound" in row.error
    assert row.realized_pnl < 0                      # the unwind crystallised a small loss
    assert abs(row.realized_pnl) < D("10")
    assert h.kalshi.positions.get(("KXBTC-100K", Outcome.YES), 0) == 0   # flat again
    assert not await h.repo.open_positions("paper")
    assert h.risk.trading_allowed()[0]               # exposure fully removed: no pause


async def test_unhedgeable_residual_becomes_naked_position_and_pauses(h):
    await h.add_mapping()
    h.kalshi.set_book("KXBTC-100K", yes_bids=[("0.38", 150)], yes_asks=[("0.40", 150)])
    h.poly.set_book("btc-100k-dec-31", yes_bids=[("0.50", 500)], yes_asks=[("0.52", 500)])
    await h.pump()
    orig = h.kalshi.place_order

    async def then_everything_vanishes(req):
        res = await orig(req)
        h.poly.set_book("btc-100k-dec-31", yes_bids=[("0.10", 500)], yes_asks=[("0.12", 500)])
        h.kalshi.set_book("KXBTC-100K", yes_bids=[], yes_asks=[("0.99", 1)])   # no bids to unwind into
        h.kalshi.place_order = orig
        return res

    h.kalshi.place_order = then_everything_vanishes
    row = await h.execution.execute(h.mapping, Outcome.YES)
    assert row.state == S.FAILED
    pos = await h.repo.open_positions("paper")
    assert len(pos) == 1 and pos[0].kind == "naked" and pos[0].size == row.planned_size
    ok, why = h.risk.trading_allowed()
    assert not ok and "unexpected_exposure" in h.risk.pauses
    assert h.of(events.EXECUTION_FAILED)[-1].data["exposure"] is True
    # further trades are blocked until the operator resumes
    h.books()
    again = await run(h)
    assert again.state == S.FAILED and "risk manager blocked" in again.error


async def test_lost_acknowledgement_is_resolved_from_positions(h):
    await h.add_mapping()
    h.kalshi.set_book("KXBTC-100K", yes_bids=[("0.38", 150)], yes_asks=[("0.40", 150)])
    h.poly.set_book("btc-100k-dec-31", yes_bids=[("0.50", 500)], yes_asks=[("0.52", 500)])
    h.kalshi.forced.append("raise_after")    # order fills but the response is lost
    row = await run(h)
    assert row.state == S.COMPLETED and row.matched_size == row.planned_size
    orders = await h.repo.orders_for(row.id)
    assert "resolved from positions" in orders[0].message
    assert len(h.kalshi.order_log) == 1      # never resubmitted blindly


async def test_timeout_before_submission_is_a_clean_failure(h):
    await h.add_mapping()
    h.kalshi.set_book("KXBTC-100K", yes_bids=[("0.38", 150)], yes_asks=[("0.40", 150)])
    h.poly.set_book("btc-100k-dec-31", yes_bids=[("0.50", 500)], yes_asks=[("0.52", 500)])
    h.kalshi.forced.append("raise_before")
    row = await run(h)
    assert row.state == S.FAILED and row.matched_size == 0
    assert h.kalshi.cash == 1000 and h.poly.cash == 1000


async def test_stale_or_vanished_edge_is_rejected_on_fresh_books(h):
    await h.add_mapping()
    h.books("0.40", "0.50")
    await h.pump()
    h.books("0.55", "0.50")       # edge gone between detection and execution
    row = await h.execution.execute(h.mapping, Outcome.YES)
    assert row.state == S.FAILED and "no longer qualifies" in row.error
    assert not h.kalshi.order_log and not h.poly.order_log


async def test_paper_mode_never_reaches_a_real_exchange(h):
    await h.add_mapping()
    h.books()
    h.kalshi.is_simulated = False    # pretend a real adapter was wired in by mistake
    row = await run(h)
    assert row.state == S.FAILED and "refusing to trade" in row.error
    assert not h.kalshi.order_log and not h.poly.order_log


async def test_live_mode_requires_unlock_and_eligibility(h):
    await h.add_mapping()
    h.books()
    h.cfg.mode = "live"
    h.kalshi.is_simulated = h.poly.is_simulated = False
    row = await run(h)
    assert "live trading is locked" in row.error
    h.cfg.live_unlocked = True
    row = await run(h)
    assert "eligibility" in row.error
    h.risk.eligibility = {Exchange.KALSHI: True, Exchange.POLYMARKET: True}
    row = await run(h)
    assert row.state == S.COMPLETED and row.mode == "live"


async def test_restart_recovery_finishes_a_half_done_execution(h):
    await h.add_mapping()
    h.kalshi.set_book("KXBTC-100K", yes_bids=[("0.38", 150)], yes_asks=[("0.40", 150)])
    h.poly.set_book("btc-100k-dec-31", yes_bids=[("0.50", 500)], yes_asks=[("0.52", 500)])
    await h.pump()

    class Crash(BaseException):
        pass

    orig = h.poly.place_order

    async def crash(req):
        raise Crash()      # process dies after leg 1 filled, before leg 2 is sent

    h.poly.place_order = crash
    try:
        await h.execution.execute(h.mapping, Outcome.YES)
    except Crash:
        pass
    h.poly.place_order = orig
    h.allocator._reserved.clear()                # a new process starts with no in-memory state
    open_ex = await h.repo.open_executions("paper")
    assert len(open_ex) == 1 and open_ex[0].state == S.LEG1_FILLED
    filled = h.kalshi.positions[("KXBTC-100K", Outcome.YES)]
    assert filled > 0 and not h.poly.positions

    notes = await h.execution.recover()
    assert any("recovered" in n for n in notes)
    row = await h.repo.get_execution(open_ex[0].id)
    assert row.state == S.COMPLETED and row.matched_size == filled
    assert h.poly.positions[("btc-100k-dec-31", Outcome.NO)] == filled
    assert not await h.repo.open_executions("paper")
    assert not await h.risk.check_exposure()     # journal and exchanges agree


async def test_recovery_of_execution_interrupted_before_orders(h):
    await h.add_mapping()
    from arbbot.database.models import ExecutionRow
    await h.repo.create_execution(ExecutionRow(id="dead", mode="paper", mapping_id=h.mapping.id,
                                               state=S.DETECTED, plan={}))
    notes = await h.execution.recover()
    assert (await h.repo.get_execution("dead")).state == S.FAILED
    assert any("interrupted" in n for n in notes)


async def test_settlement_books_profit_and_credits_paper_balances(h):
    await h.add_mapping()
    h.books("0.40", "0.50")
    row = await run(h)
    size = row.matched_size
    h.kalshi.resolutions["KXBTC-100K"] = (Resolution.YES, D(1))
    assert await h.engine.settle_once() == 0     # only one side resolved so far
    h.poly.resolutions["btc-100k-dec-31"] = (Resolution.YES, D(1))
    assert await h.engine.settle_once() == 1
    pos = (await h.repo.closed_positions("paper"))[0]
    assert pos.status == "settled" and pos.proceeds == size
    assert pos.realized_pnl == row.expected_net > 0
    assert h.kalshi.cash + h.poly.cash == 2000 + pos.realized_pnl
    assert await h.repo.realized_pnl("paper") == pos.realized_pnl
    assert h.risk.trading_allowed()[0]


async def test_settlement_discrepancy_pauses_and_rejects_mapping(h):
    await h.add_mapping()
    h.books("0.40", "0.50")
    await run(h)
    # the "equivalent" markets resolve differently: Kalshi NO, Polymarket YES -> both legs lose
    h.kalshi.resolutions["KXBTC-100K"] = (Resolution.NO, D(0))
    h.poly.resolutions["btc-100k-dec-31"] = (Resolution.YES, D(1))
    await h.engine.settle_once()
    assert "execution_discrepancy" in h.risk.pauses
    assert (await h.repo.get_mapping(h.mapping.id)).status == "rejected"
    assert await h.repo.realized_pnl("paper") < 0


async def test_close_position_sells_both_legs(h):
    await h.add_mapping()
    h.books("0.40", "0.50")
    row = await run(h)
    pos = (await h.repo.open_positions("paper"))[0]
    h.books("0.40", "0.50")      # replenish bids
    ok, msg = await h.execution.close_position(pos.id)
    assert ok, msg
    assert not await h.repo.open_positions("paper")
    assert h.kalshi.positions[("KXBTC-100K", Outcome.YES)] == 0
    assert h.poly.positions[("btc-100k-dec-31", Outcome.NO)] == 0
    closed = (await h.repo.closed_positions("paper"))[0]
    assert closed.status == "closed" and closed.realized_pnl < row.expected_net


async def test_autotrade_scan_executes_and_respects_pause(h):
    await h.add_mapping()
    h.books("0.40", "0.50")
    await h.pump()
    h.cfg.paused = True
    await h.engine.scan_once()
    assert not await h.repo.open_positions("paper")
    assert h.of(events.OPPORTUNITY)                     # still detected and reported
    h.cfg.paused = False
    await h.engine.scan_once()
    assert len(await h.repo.open_positions("paper")) >= 1
    opps = await h.repo.recent_opportunities("paper")
    assert any(o.status == "executed" for o in opps)


async def test_unverified_mapping_is_never_executed(h):
    await h.add_mapping(confidence=97.0, status="candidate", compatible=False)
    h.books("0.40", "0.50")
    await h.pump()
    opps = await h.engine.scan_once()
    assert opps and not any(o.evaluation.accepted for o in opps)
    assert "not verified" in opps[0].evaluation.reason or "confidence" in opps[0].evaluation.reason
    assert not h.kalshi.order_log
    row = await h.execution.execute(h.mapping, Outcome.YES)
    assert row.state == S.FAILED
    rejected = await h.repo.recent_opportunities("paper", ["rejected"])
    assert rejected and rejected[0].reason           # the rejection and its reason are journaled
