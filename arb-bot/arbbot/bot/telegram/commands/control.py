"""Commands that change state: trading on/off, mode, pause/resume, close,
emergency stop, capital, thresholds, settings and mapping review."""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from ....config.runtime import PROTECTED_FIELDS, RuntimeConfig
from ..format import Reply, e, mark, onoff, pct, usd

if TYPE_CHECKING:
    from ....app import App

SETTING_GROUPS = {
    "threshold": ["min_roi_pct", "max_roi_pct", "min_annualized_pct", "min_net_profit_usd"],
    "sizing": ["kelly_fraction", "loss_given_failure", "settlement_tail_prob", "max_trade_usd",
               "max_trade_bankroll_pct", "min_trade_usd", "max_book_take_pct"],
    "risk": ["max_exposure_pct", "max_daily_loss_usd", "max_drawdown_pct", "max_open_positions", "max_unhedged_usd",
             "max_unhedged_seconds", "max_execution_seconds", "max_slippage_pct", "min_liquidity_usd",
             "max_market_concentration_pct", "max_book_age_ms", "max_exec_fail_prob", "max_hedge_loss_pct",
             "unwind_floor_pct", "fee_discrepancy_tolerance_pct"],
    "costs": ["kalshi_latency_ms", "polymarket_latency_ms", "base_slippage_bps", "base_leg_fail_prob",
              "usdc_conversion_bps", "capital_cost_apr_pct", "settlement_risk_bps", "operational_risk_bps"],
    "matching": ["min_match_confidence", "close_time_tolerance_hours", "revalidate_hours"],
    "loops": ["scan_interval_s", "book_poll_interval_s", "discovery_interval_s", "settlement_check_interval_s",
              "notify_opportunities", "daily_summary_hour_utc"],
    "paper": ["paper_kalshi_balance", "paper_polymarket_balance", "paper_fail_prob", "paper_partial_prob",
              "paper_min_days", "paper_min_trades", "paper_min_net_pnl", "paper_max_fail_rate_pct"],
}


async def _set(app: "App", key: str, raw: str) -> str:
    """Validate, apply and persist one setting. Bounds live on RuntimeConfig."""
    if key in PROTECTED_FIELDS:
        raise ValueError(f"{key} is changed with its own command, not /settings")
    old = getattr(app.cfg, key)
    try:
        new = app.cfg.set_value(key, raw)
    except KeyError:
        raise ValueError(f"unknown setting '{key}'") from None
    except Exception as ex:
        setattr(app.cfg, key, old) if key in RuntimeConfig.model_fields else None
        msg = str(ex).split("\n")[-1].strip() if "validation error" in str(ex) else str(ex)
        raise ValueError(f"{key}: {msg}") from None
    await app.save_config()
    return f"{key}: {old} → <b>{new}</b>"


async def scan(app: "App", args: list[str], user: str) -> Reply:
    for ex in list(app.venues):
        try:
            await app.feed.poll_once(ex)
        except Exception as err:
            return Reply(f"Could not refresh {ex.value} order books: {e(err, 200)}")
    await app.allocator.refresh_balances()
    opps = await app.engine.scan_once()
    ok = [o for o in opps if o.evaluation.accepted]
    best = max((o.evaluation.quote.roi for o in opps if o.evaluation.quote), default=None)
    text = (f"Scanned {len(app.engine.mappings)} mapped pairs: <b>{len(ok)}</b> qualifying, "
            f"{len(opps) - len(ok)} evaluated and rejected.")
    if best is not None:
        text += f" Best net return seen: {pct(best * 100)}."
    if not app.engine.mappings:
        text += "\nNo market mappings yet. Discovery runs in the background; review matches with /mappings."
    return Reply(text, [[("Show opportunities", "opportunities")]])


async def autotrade(app: "App", args: list[str], user: str) -> Reply:
    cfg = app.cfg
    sub = args[0].lower() if args else ""
    if sub in ("on", "enable"):
        if cfg.emergency_stop:
            return Reply("Emergency stop is active. Clear it with /resume first.")
        if cfg.bankroll <= 0:
            return Reply("Allocate capital first: /capital kalshi 500 and /capital polymarket 500")
        cfg.autotrade = True
        await app.save_config()
        return Reply(f"Automatic execution <b>ENABLED</b> in {cfg.mode.upper()} mode. Risk limits still apply to "
                     f"every trade.")
    if sub in ("off", "disable"):
        cfg.autotrade = False
        await app.save_config()
        return Reply("Automatic execution <b>DISABLED</b>. Scanning and alerts continue; open positions are kept.")
    if sub == "paper":
        ok, msg = await app.set_mode("paper")
        return Reply(e(msg, 600))
    if sub == "live":
        checks = await app.live_readiness()
        report = "\n".join(f"{mark(ok)} {e(n)}: {e(d, 200)}" for n, ok, d in checks)
        if not all(ok for _, ok, _ in checks):
            return Reply(f"<b>Live trading is locked</b>\n{report}")
        if len(args) < 2 or args[1] != "CONFIRM":
            return Reply(f"<b>Ready for live trading</b>\n{report}\n\nThis will place real orders with real money. "
                         f"To switch, send:\n<code>/autotrade live CONFIRM</code>")
        ok, msg = await app.set_mode("live")
        return Reply(("🔴 <b>LIVE MODE</b>\n" if ok else "") + e(msg, 800)
                     + ("\nEnable execution with /autotrade on" if ok else ""))
    if sub == "resetpaper":
        if cfg.mode != "paper" or await app.repo.open_positions("paper"):
            return Reply("Paper balances can only be reset in paper mode with no open positions.")
        await app.reset_paper()
        return Reply("Paper balances reset to their configured starting values.")
    return Reply(
        f"<b>Autotrade</b>: {onoff(cfg.autotrade)} | mode <b>{cfg.mode.upper()}</b>\n"
        f"/autotrade on | off\n/autotrade paper - switch to paper trading\n"
        f"/autotrade live - check requirements and unlock live trading",
        [[("Enable", "autotrade on"), ("Disable", "autotrade off")], [("Live readiness", "autotrade live")]])


async def pause(app: "App", args: list[str], user: str) -> Reply:
    app.cfg.paused = True
    await app.save_config()
    return Reply("⏸ New trades paused. Open positions are untouched. /resume to continue.")


async def resume(app: "App", args: list[str], user: str) -> Reply:
    cfg = app.cfg
    cleared = await app.risk.clear_all()
    was_stopped = cfg.emergency_stop
    cfg.paused, cfg.emergency_stop = False, False
    await app.save_config()
    await app.risk.monitor_once()          # anything still wrong re-latches immediately
    ok, why = app.risk.trading_allowed()
    text = "▶️ Resumed."
    if cleared:
        text += "\nAcknowledged: " + ", ".join(e(c) for c in cleared)
    if was_stopped:
        text += "\nEmergency stop cleared. Autotrade stays OFF until you send /autotrade on."
    if not ok:
        text += f"\n⚠️ Still blocked: {e(why, 300)}"
    return Reply(text)


async def stop(app: "App", args: list[str], user: str) -> Reply:
    await app.risk.emergency_stop(f"operator {user}")
    return Reply("🛑 <b>EMERGENCY STOP</b>\nAutotrade is off and no new orders will be sent. Open positions are "
                 "unchanged; use /positions and /close to manage them. /resume clears the stop.")


async def close(app: "App", args: list[str], user: str) -> Reply:
    rows = await app.repo.open_positions(app.cfg.mode)
    if not rows:
        return Reply("No open positions.")
    if not args:
        lines = ["<b>Close positions</b>\nClosing sells both legs at the current bids, which usually gives up "
                 "the locked-in profit. Hedged pairs are normally held to settlement."]
        buttons = []
        for p in rows[:12]:
            lines.append(f"#{p.id} {e(p.title, 50)} | {p.size:g} | cost {usd(p.cost_basis)}")
            buttons.append([(f"Close #{p.id}", f"close {p.id}")])
        lines.append("\n/close &lt;id&gt; or /close all")
        return Reply("\n".join(lines), buttons)
    ids = [p.id for p in rows] if args[0].lower() == "all" else [int(a) for a in args if a.isdigit()]
    if not ids:
        return Reply("Usage: /close &lt;position id&gt; | all")
    out = []
    for pid in ids:
        ok, msg = await app.execution.close_position(pid)
        out.append(f"{mark(ok)} #{pid}: {e(msg, 200)}")
    await app._persist_paper()
    return Reply("\n".join(out))


async def capital(app: "App", args: list[str], user: str) -> Reply:
    cfg = app.cfg
    if len(args) >= 2:
        which = args[0].lower()
        key = {"kalshi": "kalshi_allocation", "polymarket": "polymarket_allocation", "poly": "polymarket_allocation",
               "paper_kalshi": "paper_kalshi_balance", "paper_polymarket": "paper_polymarket_balance"}.get(which)
        if not key:
            return Reply("Usage: /capital kalshi 500 | /capital polymarket 500")
        try:
            Decimal(args[1].replace("$", "").replace(",", ""))
            msg = await _set(app, key, args[1].replace("$", "").replace(",", ""))
        except (ValueError, InvalidOperation) as err:
            return Reply(f"❌ {e(err, 200)}")
        if key.startswith("paper_") and cfg.mode == "paper" and not await app.repo.open_positions("paper"):
            await app.reset_paper()
        return Reply(f"✅ {msg}\nBankroll is now {usd(cfg.bankroll)}.")
    rep = await app.allocator.report()
    lines = [f"<b>Capital allocation</b> | bankroll {usd(cfg.bankroll)}",
             "Each exchange is its own pool. The bot uses at most the allocation, and never more than the "
             "real balance."]
    for name, x in rep["exchanges"].items():
        lines.append(f"\n<b>{name}</b>: allocation {usd(x['allocation'])} | balance "
                     f"{usd(x['balance']) if x['balance'] is not None else 'unknown'} | free {usd(x['available'])}")
    lines += ["", f"Per trade: max {usd(cfg.max_trade_usd)} and {cfg.max_trade_bankroll_pct:g}% of bankroll, "
                  f"Kelly fraction {cfg.kelly_fraction:g}",
              f"Max deployed: {cfg.max_exposure_pct:g}% of bankroll | per market {cfg.max_market_concentration_pct:g}%",
              "", "/capital kalshi 500\n/capital polymarket 500",
              f"Paper balances: /capital paper_kalshi {cfg.paper_kalshi_balance} (and paper_polymarket)"]
    return Reply("\n".join(lines))


async def threshold(app: "App", args: list[str], user: str) -> Reply:
    cfg = app.cfg
    alias = {"min": "min_roi_pct", "max": "max_roi_pct", "annualized": "min_annualized_pct",
             "minprofit": "min_net_profit_usd"}
    if len(args) >= 2:
        try:
            msg = await _set(app, alias.get(args[0].lower(), args[0].lower()), args[1])
        except ValueError as err:
            return Reply(f"❌ {e(err, 200)}")
        return Reply(f"✅ {msg}")
    lines = [
        "<b>Dynamic profitability threshold</b>",
        f"Required net return slides between <b>{cfg.min_roi_pct:g}%</b> (deep, fresh, stable books) and "
        f"<b>{cfg.max_roi_pct:g}%</b> (thin, stale or volatile), after all fees and risk buffers.",
        f"Minimum net profit per trade {usd(cfg.min_net_profit_usd)}"
        + (f" | annualized floor {cfg.min_annualized_pct:g}%" if cfg.min_annualized_pct else ""),
        "Hard rejects: confidence below "
        f"{cfg.min_match_confidence:g}, slippage above {cfg.max_slippage_pct:g}%, failure probability above "
        f"{cfg.max_exec_fail_prob:.0%}, liquidity under {usd(cfg.min_liquidity_usd)}.",
    ]
    recent = [o for o in app.engine.latest if o.evaluation.quote is not None][:5]
    if recent:
        lines.append("\n<b>Latest evaluations</b>")
        for o in recent:
            ev = o.evaluation
            c = ev.components
            lines.append(f"  {e(o.mapping.kalshi.title, 40)}: net {pct(ev.quote.roi * 100)} vs required "
                         f"{pct(ev.required_roi * 100)} (risk score {ev.risk_score:.2f}: fail {c.get('fail', 0):.2f}, "
                         f"stale {c.get('stale', 0):.2f}, thin {c.get('thin', 0):.2f}, vol {c.get('vol', 0):.2f})")
    lines.append("\n/threshold min 1.5\n/threshold max 10\n/threshold minprofit 0.50")
    return Reply("\n".join(lines))


async def settings(app: "App", args: list[str], user: str) -> Reply:
    cfg = app.cfg
    if len(args) >= 2:
        try:
            msg = await _set(app, args[0].lower(), args[1])
        except ValueError as err:
            return Reply(f"❌ {e(err, 200)}")
        return Reply(f"✅ {msg}")
    if len(args) == 1 and args[0].lower() in SETTING_GROUPS:
        g = args[0].lower()
        lines = [f"<b>Settings: {g}</b>"]
        for k in SETTING_GROUPS[g]:
            desc = cfg.describe(k)
            lines.append(f"<code>{k}</code> = <b>{getattr(cfg, k)}</b>" + (f"\n    {e(desc, 120)}" if desc else ""))
        lines.append("\nChange: /settings &lt;name&gt; &lt;value&gt;")
        return Reply("\n".join(lines))
    if len(args) == 1 and args[0].lower() in RuntimeConfig.model_fields:
        k = args[0].lower()
        return Reply(f"<code>{k}</code> = <b>{getattr(cfg, k)}</b>\n{e(cfg.describe(k), 200)}")
    return Reply(
        f"<b>Settings</b> | mode {cfg.mode.upper()} | autotrade {onoff(cfg.autotrade)} | paused {onoff(cfg.paused)}\n"
        "Groups: " + ", ".join(SETTING_GROUPS) + "\n/settings &lt;group&gt; lists every value. Set one directly:\n"
        "<code>/settings max_trade_usd 300</code>",
        [[(g, f"settings {g}") for g in list(SETTING_GROUPS)[i:i + 3]] for i in range(0, len(SETTING_GROUPS), 3)])


async def mappings(app: "App", args: list[str], user: str) -> Reply:
    sub = args[0].lower() if args else "list"
    if sub in ("approve", "approve_inverted", "reject", "show") and len(args) >= 2 and args[1].isdigit():
        mid = int(args[1])
        m = await app.repo.get_mapping(mid)
        if m is None:
            return Reply("Mapping not found.")
        if sub == "show":
            ch = m.checks or {}
            ver = ch.get("verifier") or {}
            lines = [
                f"<b>Mapping #{m.id}</b> | {e(m.status)} | confidence {m.confidence:.0f} | "
                f"{'INVERTED (Kalshi YES = Polymarket NO)' if m.inverted else 'same polarity'}",
                f"\n<b>Kalshi</b> {e(m.kalshi.market_id)}\n{e(m.kalshi.title, 200)} [{e(m.kalshi.yes_label)}]"
                f"\ncloses {m.kalshi.close_time:%Y-%m-%d %H:%M} UTC\n<i>{e(m.kalshi.rules, 500)}</i>",
                f"\n<b>Polymarket</b> {e(m.polymarket.market_id, 40)}\n{e(m.polymarket.title, 200)} "
                f"[{e(m.polymarket.yes_label)}]\ncloses {m.polymarket.close_time:%Y-%m-%d %H:%M} UTC"
                f"\n<i>{e(m.polymarket.rules, 500)}</i>",
                f"\nSimilarity {ch.get('similarity')} | close gap {ch.get('close_gap_hours')}h | sources match: "
                f"{ch.get('source_match')} | void terms match: {ch.get('void_terms_match')}",
            ]
            if ch.get("failures"):
                lines.append("⚠️ " + e("; ".join(ch["failures"]), 300))
            if ver:
                lines.append(f"Verifier: equivalent={ver.get('equivalent')} compatible="
                             f"{ver.get('settlement_compatible')}\n{e('; '.join(ver.get('differences') or []), 400)}")
            lines.append("\nApprove only if both contracts settle identically in every scenario.")
            return Reply("\n".join(lines), [
                [("Approve", f"mappings approve {mid}"), ("Approve inverted", f"mappings approve_inverted {mid}")],
                [("Reject", f"mappings reject {mid}")]])
        if sub == "reject":
            await app.matcher.reject(mid)
            await app.engine.reload_mappings()
            return Reply(f"Mapping #{mid} rejected. It will not be traded.")
        ok, msg = await app.matcher.approve(mid, user, inverted=True if sub == "approve_inverted" else None)
        await app.engine.reload_mappings()
        return Reply(f"{mark(ok)} Mapping #{mid}: {e(msg, 300)}"
                     + ("\nIt is now eligible for automated execution." if ok else ""))
    if sub == "discover":
        t0 = time.time()
        c = await app.discovery.run_once()
        return Reply(f"Discovery finished in {time.time() - t0:.0f}s: {c['kalshi']} Kalshi and {c['polymarket']} "
                     f"Polymarket markets, {c['verified']} verified, {c['candidates']} candidates, "
                     f"{c['demoted']} demoted.")
    status = sub if sub in ("verified", "candidate", "stale", "rejected") else None
    ms = await app.repo.list_mappings((status,) if status else ("verified", "candidate", "stale"), limit=200)
    ms.sort(key=lambda m: (m.status != "verified", -m.confidence))
    if not ms:
        return Reply("No mappings yet. Run /mappings discover or wait for the discovery loop.")
    lines = [f"<b>Market mappings</b> ({len(ms)}) | execution needs confidence ≥ {app.cfg.min_match_confidence:g} "
             f"and verified settlement rules"]
    buttons = []
    for m in ms[:10]:
        icon = {"verified": "✅", "candidate": "🟡", "stale": "⚠️"}.get(m.status, "⚪")
        lines.append(f"{icon} #{m.id} [{m.confidence:.0f}] {e(m.kalshi.title, 44)}\n      ↔ {e(m.polymarket.title, 44)}")
        buttons.append([(f"Review #{m.id}", f"mappings show {m.id}")])
    lines.append("\n/mappings show &lt;id&gt; | approve &lt;id&gt; | reject &lt;id&gt; | candidate | verified | discover")
    return Reply("\n".join(lines), buttons)
