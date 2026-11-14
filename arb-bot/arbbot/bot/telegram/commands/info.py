"""Read-only commands: dashboard, status, balances, P&L, history, positions,
opportunities, risk."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ....core.types import ZERO
from ....database.repo import as_utc
from ..format import Reply, age, e, mark, onoff, pct, usd

if TYPE_CHECKING:
    from ....app import App

HELP = """<b>Arbitrage command center</b>

<b>Monitor</b>
/dashboard - overall trading dashboard
/status - system and exchange health
/balance - balances and free capital per exchange
/positions - open positions
/profit - realized and unrealized P&amp;L
/history - recent trades
/opportunities - current arbitrage opportunities
/risk - risk exposure and limits

<b>Trade</b>
/scan - scan now
/autotrade - enable/disable automatic execution, paper/live mode
/pause - pause new trades
/resume - resume trading (clears risk pauses)
/close - close open positions
/stop - EMERGENCY STOP

<b>Configure</b>
/capital - bankroll allocation per exchange
/threshold - dynamic profitability settings
/settings - all parameters
/mappings - review and approve market matches
/help - this list"""


async def start(app: "App", args: list[str], user: str) -> Reply:
    cfg = app.cfg
    return Reply(
        f"<b>Kalshi × Polymarket arbitrage bot</b>\n"
        f"Mode: <b>{cfg.mode.upper()}</b> | autotrade {onoff(cfg.autotrade)}\n"
        f"Bankroll: {usd(cfg.bankroll)} (Kalshi {usd(cfg.kalshi_allocation)}, Polymarket "
        f"{usd(cfg.polymarket_allocation)})\n\n"
        + ("Set your capital first with /capital.\n\n" if cfg.bankroll <= 0 else "") + HELP,
        [[("Dashboard", "dashboard"), ("Status", "status")]])


async def help_(app: "App", args: list[str], user: str) -> Reply:
    return Reply(HELP)


async def dashboard(app: "App", args: list[str], user: str) -> Reply:
    d = await app.dashboard()
    st, cap, un, risk = d["stats"], d["capital"], d["unrealized"], d["risk"]
    allowed, why = d["allowed"]
    lines = [
        f"<b>Dashboard</b> | {d['mode'].upper()} | autotrade {onoff(d['autotrade'])}",
        f"Trading: {'active' if allowed and d['autotrade'] else 'idle' if allowed else '⛔ ' + e(why, 200)}",
        "",
        f"<b>Capital</b>  bankroll {usd(cap['bankroll'])}",
    ]
    for name, x in cap["exchanges"].items():
        bal = usd(x["balance"]) if x["balance"] is not None else "unverified"
        lines.append(f"  {name}: free {usd(x['available'])} | balance {bal} | deployed {usd(x['deployed'])}")
    lines += [
        f"  deployed {usd(cap['deployed'])} | utilization {pct(cap['utilization_pct'], 1)}",
        "",
        f"<b>Trades</b>  {st['total_trades']} total | {st['matched_pairs']} matched | {st['failed']} failed | "
        f"{st['partial_fills']} partial",
        f"  avg execution {st['avg_exec_s']:.2f}s | avg slippage {pct(st['avg_slippage_pct'], 3)}",
        "",
        "<b>P&amp;L</b>",
        f"  gross {usd(st['gross_profit'], True)} | fees {usd(st['fees'])}",
        f"  net realized {usd(st['net_realized'], True)} | avg/trade {usd(st['avg_profit'], True)}",
        f"  locked in (open hedges) {usd(un['locked_in'], True)} | mark-to-market {usd(un['mark_to_market'], True)}",
        f"  day {usd(d['daily'][0], True)} ({pct(d['daily'][1])}) | week {usd(d['weekly'][0], True)} "
        f"({pct(d['weekly'][1])}) | month {usd(d['monthly'][0], True)} ({pct(d['monthly'][1])})",
        f"  max drawdown {pct(risk['max_drawdown_pct'], 1)}",
        "",
        f"Open positions {st['open_positions']} | mappings {d['mappings']['verified']} verified, "
        f"{d['mappings']['candidates']} awaiting review | last scan {age(d['last_scan_age'])}",
    ]
    return Reply("\n".join(lines), [[("Refresh", "dashboard"), ("Opportunities", "opportunities")],
                                    [("Positions", "positions"), ("Risk", "risk")]])


async def status(app: "App", args: list[str], user: str) -> Reply:
    s = await app.status()
    lines = ["<b>System status</b>",
             f"Uptime {s['uptime_s'] / 3600:.1f}h | database {mark(s['database'])} | cache {mark(s['cache'])} "
             f"({s['cache_kind']})",
             f"Rule verifier: {e(s['verifier'])}", ""]
    for name, x in s["exchanges"].items():
        kind = "paper (real market data)" if x["simulated"] else "LIVE"
        lines.append(f"<b>{name}</b> {mark(x['up'])} {kind} | books {age(x['book_age'])} | "
                     f"{x['watched']} markets watched | {'stream+poll' if x['streaming'] else 'poll'}")
        lines.append(f"  live eligibility {mark(x['eligible'])}")
        for cname, ok, detail in x["checks"]:
            if not ok or "detail" in args:
                lines.append(f"    {mark(ok)} {e(cname)}" + (f": {e(detail, 140)}" if detail else ""))
    for name in s["disabled"]:
        lines.append(f"<b>{name}</b> adapter disabled")
    dc = s["discovery"]
    if dc:
        lines += ["", f"Discovery {age(s['discovery_age'])}: {dc.get('kalshi', 0)} Kalshi, {dc.get('polymarket', 0)} "
                      f"Polymarket markets | {dc.get('verified', 0)} verified, {dc.get('candidates', 0)} candidates"]
    if s["recovery"]:
        lines += ["", "<b>Startup reconciliation</b>"] + [f"  {e(n, 200)}" for n in s["recovery"][:8]]
    ok, why = app.risk.trading_allowed()
    lines += ["", f"Trading allowed: {mark(ok)} {e(why, 300)}"]
    return Reply("\n".join(lines), [[("Refresh", "status"), ("All checks", "status detail")]])


async def balance(app: "App", args: list[str], user: str) -> Reply:
    ok = await app.allocator.refresh_balances()
    cap = await app.allocator.report()
    lines = [f"<b>Balances</b> ({app.cfg.mode})"]
    for name, x in cap["exchanges"].items():
        verified = all(v for k, v in ok.items() if k.value == name)
        lines += [f"\n<b>{name}</b> {'' if verified else '⚠️ could not verify'}",
                  f"  exchange balance {usd(x['balance']) if x['balance'] is not None else 'unknown'}",
                  f"  allocation limit {usd(x['allocation'])}",
                  f"  deployed {usd(x['deployed'])} | reserved {usd(x['reserved'])}",
                  f"  <b>free to trade {usd(x['available'])}</b>"]
    lines.append(f"\nBankroll {usd(cap['bankroll'])} | utilization {pct(cap['utilization_pct'], 1)}")
    lines.append("Funds on each exchange are separate pools. The bot never assumes money can move between them.")
    return Reply("\n".join(lines))


async def profit(app: "App", args: list[str], user: str) -> Reply:
    d = await app.dashboard()
    st, un = d["stats"], d["unrealized"]
    lines = [
        f"<b>Profit &amp; loss</b> ({d['mode']})",
        f"Net realized: <b>{usd(st['net_realized'], True)}</b>",
        f"  gross {usd(st['gross_profit'], True)} | fees paid {usd(st['fees'])}",
        "Unrealized:",
        f"  locked in if open hedges settle as designed: {usd(un['locked_in'], True)}",
        f"  mark-to-market at current bids: {usd(un['mark_to_market'], True)}"
        + (f" ({un['unmarked']} positions without a bid)" if un["unmarked"] else ""),
        "",
        f"Today {usd(d['daily'][0], True)} ({pct(d['daily'][1])})",
        f"7 days {usd(d['weekly'][0], True)} ({pct(d['weekly'][1])})",
        f"30 days {usd(d['monthly'][0], True)} ({pct(d['monthly'][1])})",
        f"Average per settled trade {usd(st['avg_profit'], True)}",
        f"Max drawdown {pct(d['risk']['max_drawdown_pct'], 1)}",
    ]
    return Reply("\n".join(lines))


async def history(app: "App", args: list[str], user: str) -> Reply:
    n = min(int(args[0]), 30) if args and args[0].isdigit() else 10
    rows = await app.repo.recent_executions(app.cfg.mode, n)
    if not rows:
        return Reply("No trades yet.")
    lines = [f"<b>Last {len(rows)} executions</b> ({app.cfg.mode})"]
    for r in rows:
        when = as_utc(r.started_at).strftime("%m-%d %H:%M")
        ok = "✅" if r.state == "completed" else "❌"
        lines.append(f"{ok} {when} {e(r.title, 48)}\n    {r.matched_size:g}/{r.planned_size:g} contracts | "
                     f"cost {usd(r.total_cost)} | expected {usd(r.expected_net, True)}"
                     + (f" | realized {usd(r.realized_pnl, True)}" if r.realized_pnl else "")
                     + (f"\n    {e(r.error, 120)}" if r.error else ""))
    closed = await app.repo.closed_positions(app.cfg.mode, 5)
    if closed:
        lines.append("\n<b>Recently settled/closed</b>")
        for p in closed:
            lines.append(f"  #{p.id} {e(p.title, 44)}: {usd(p.realized_pnl, True)} ({e(p.close_reason, 40)})")
    return Reply("\n".join(lines))


async def positions(app: "App", args: list[str], user: str) -> Reply:
    rows = await app.repo.open_positions(app.cfg.mode)
    if not rows:
        return Reply("No open positions.")
    lines = [f"<b>Open positions</b> ({len(rows)})"]
    total = ZERO
    for p in rows:
        total += p.cost_basis
        if p.kind == "pair":
            lines.append(f"#{p.id} {e(p.title, 56)}\n    {p.size:g} pairs | {p.a_exchange} {p.a_outcome.upper()} + "
                         f"{p.b_exchange} {p.b_outcome.upper()} | cost {usd(p.cost_basis)} | "
                         f"locked in {usd(p.expected_payout - p.cost_basis, True)}")
        else:
            lines.append(f"⚠️ #{p.id} {e(p.title, 56)}\n    UNHEDGED {p.size:g} {p.a_outcome.upper()} on "
                         f"{p.a_exchange} | cost {usd(p.cost_basis)}")
    lines.append(f"\nTotal deployed {usd(total)}")
    return Reply("\n".join(lines), [[("Manage / close", "close")]])


async def opportunities(app: "App", args: list[str], user: str) -> Reply:
    opps = app.engine.latest
    live = [o for o in opps if o.evaluation.accepted]
    near = [o for o in opps if not o.evaluation.accepted and o.evaluation.quote is not None][:6]
    lines = [f"<b>Opportunities</b> | scanned {age(None if not app.engine.last_scan_ts else __import__('time').time() - app.engine.last_scan_ts)}"]
    if not live:
        lines.append("No opportunity clears the dynamic threshold right now.")
    for o in live[:8]:
        q = o.evaluation.quote
        lines.append(f"🟢 {e(o.mapping.kalshi.title, 60)}\n    {e(o.direction)} | {q.size:g} contracts | "
                     f"capital {usd(q.capital)}\n    net {usd(q.net, True)} = {pct(q.roi * 100)} "
                     f"(needs {pct(o.evaluation.required_roi * 100)}) | confidence {o.mapping.confidence:.0f}")
    if near:
        lines.append("\n<b>Rejected (with reason)</b>")
        for o in near:
            q = o.evaluation.quote
            lines.append(f"⚪ {e(o.mapping.kalshi.title, 52)} [{pct(q.roi * 100)}]\n    {e(o.evaluation.reason, 150)}")
    return Reply("\n".join(lines), [[("Scan now", "scan"), ("Refresh", "opportunities")]])


async def risk(app: "App", args: list[str], user: str) -> Reply:
    cfg = app.cfg
    r = await app.risk.summary()
    ok, why = r["allowed"]
    lines = [
        "<b>Risk</b>",
        f"New trades allowed: {mark(ok)} {e(why, 300)}",
        f"Emergency stop: {onoff(cfg.emergency_stop)}",
        "",
        f"Exposure {usd(r['deployed'])} = {pct(r['exposure_pct'], 1)} of bankroll (limit {cfg.max_exposure_pct:g}%)",
        f"Unhedged exposure {usd(r['unhedged'])} (limit {usd(cfg.max_unhedged_usd)} per trade, "
        f"{cfg.max_unhedged_seconds:g}s)",
        f"Open positions {r['open_positions']} (limit {cfg.max_open_positions})",
        f"Largest market {pct(r['largest_market_pct'], 1)} of bankroll (limit {cfg.max_market_concentration_pct:g}%)",
        f"Today's realized P&amp;L {usd(r['daily_pnl'], True)} (loss limit {usd(cfg.max_daily_loss_usd)})",
        f"Drawdown {pct(r['drawdown_pct'], 1)} now, {pct(r['max_drawdown_pct'], 1)} max (limit {cfg.max_drawdown_pct:g}%)",
        "",
        f"Max trade {usd(cfg.max_trade_usd)} | max slippage {cfg.max_slippage_pct:g}% | min liquidity "
        f"{usd(cfg.min_liquidity_usd)} | max book age {cfg.max_book_age_ms}ms | max execution "
        f"{cfg.max_execution_seconds:g}s",
    ]
    if r["pauses"]:
        lines.append("\n<b>Active pauses</b>")
        lines += [f"  ⛔ {e(k)}: {e(v, 200)}" for k, v in r["pauses"].items()]
    ev = await app.repo.recent_risk_events(5)
    if ev:
        lines.append("\n<b>Recent risk events</b>")
        lines += [f"  {as_utc(x.ts).strftime('%m-%d %H:%M')} {e(x.kind)}: {e(x.message, 140)}" for x in ev]
    lines.append("\nChange a limit: /settings max_daily_loss_usd 100")
    return Reply("\n".join(lines), [[("Pause", "pause"), ("Resume", "resume")], [("EMERGENCY STOP", "stop")]])
