"""Live paper dry run: real Kalshi and Polymarket US prices, simulated money.

    python -m arbbot.dryrun                       # run until the first paper arbitrage trade
    python -m arbbot.dryrun --trades 3 --hours 8
    python -m arbbot.dryrun --telegram            # also run your Telegram bot from .env

It discovers the BTC/ETH/SOL/XRP daily price contracts on both venues, pairs them field by field,
screens every pair by quote every few seconds, pulls full order books whenever a pair costs less
than $1, and runs the real profitability engine, risk manager and execution state machine on
them. Fills are simulated against the live order books (with latency, failures and partial
fills); no order ever reaches an exchange and no account or API key is needed.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import logging
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .app import App
from .bot.telegram.bot import dispatch
from .bot.telegram.notifications import Notifier
from .config.settings import Settings
from .core import events
from .core.events import Event
from .core.types import Exchange
from .infra.cache import MemoryCache

D = Decimal


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


class Log:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a")

    def __call__(self, text: str) -> None:
        line = f"[{datetime.now(timezone.utc):%H:%M:%S}Z] {text}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()


async def amain(a: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message).200s")
    workdir = Path(a.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = Log(workdir / "dryrun.log")
    base = dict(database_url=f"sqlite+aiosqlite:///{workdir}/dryrun.db", redis_url="", data_dir=str(workdir),
                discovery_scope="crypto", crypto_assets=a.assets, scan_mode="screen",
                discovery_max_days_to_close=a.days)
    settings = Settings(**base) if a.telegram else \
        Settings(_env_file=None, telegram_bot_token="dryrun", telegram_admin_ids="1", **base)
    if a.telegram and (not settings.telegram_bot_token or not settings.admin_ids):
        print("--telegram needs TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_IDS")
        return 2

    app = App(settings, cache=MemoryCache())
    await app.init()
    cfg = app.cfg
    if cfg.mode != "paper":
        print("refusing to run: the dry-run database is not in paper mode")
        return 2
    half = D(str(a.bankroll)) / 2
    for k, v in dict(kalshi_allocation=half, polymarket_allocation=half, paper_kalshi_balance=half,
                     paper_polymarket_balance=half, autotrade=True, auto_verify_structural=True,
                     screen_interval_s=a.interval, max_trade_usd=D(str(a.max_trade)),
                     max_unhedged_usd=D(str(a.max_trade))).items():
        setattr(cfg, k, v)
    await app.save_config()
    if not await app.repo.open_positions("paper") and not await app.repo.recent_executions("paper", 1):
        await app.reset_paper()

    trades: list[dict] = []

    async def on_trade(ev: Event) -> None:
        trades.append(ev.data)

    app.bus.subscribe(events.TRADE_COMPLETED, on_trade)

    async def console(text: str) -> None:
        out("ALERT\n" + plain(text))

    Notifier(app.bus, console)

    out("=" * 70)
    out("LIVE PAPER DRY RUN | real Kalshi + Polymarket US prices | simulated money | no orders sent")
    out(f"assets {a.assets} | bankroll ${a.bankroll:,.0f} (${half:,.0f} per exchange) | max trade ${a.max_trade} "
        f"| stop after {a.trades} paper trade(s) or {a.hours}h")
    t0 = time.time()
    c = await app.discovery.run_once()
    out(f"discovery {time.time() - t0:.1f}s: {c['kalshi']} Kalshi + {c['polymarket']} Polymarket crypto contracts, "
        f"{c['verified']} pairs verified field by field, {c['rejected']} rejected")
    for m in app.engine.mappings[:3]:
        ch = m.checks
        out(f"  e.g. {m.kalshi.market_id} <-> {m.polymarket.market_id} | settles {ch.get('settles_utc')} | "
            f"{ch.get('index')} | edge window {ch.get('disagreement_window')}")

    tasks = [asyncio.create_task(app.engine.run(), name="screen"),
             asyncio.create_task(app._loop("risk", app.risk.monitor_once, 15.0, 5.0)),
             asyncio.create_task(app._loop("discovery", app.discovery.run_once, 600.0, 600.0)),
             asyncio.create_task(app._loop("settlement", app.engine.settle_once, 300.0, 60.0)),
             asyncio.create_task(app._loop("paper-state", app._persist_paper, 60.0, 60.0))]
    if a.telegram:
        from .bot.telegram import bot as tg
        tasks.append(asyncio.create_task(tg.run(app)))

    end = time.time() + a.hours * 3600
    best_ever: tuple | None = None
    last_beat = 0.0
    try:
        while time.time() < end and len(trades) < a.trades:
            await asyncio.sleep(2)
            st = app.engine.screen_stats or {}
            g = st.get("best_gross")
            if g is not None and (best_ever is None or g < best_ever[0]):
                best_ever = (g, st.get("best_pair"), datetime.now(timezone.utc))
                if g < 1:
                    out(f"GAP on quotes: ${g:.4f} per $1 | {st.get('best_pair')}")
            if time.time() - last_beat >= a.heartbeat:
                last_beat = time.time()
                acc = sum(o.evaluation.accepted for o in app.engine.latest)
                rej = [o for o in app.engine.latest if not o.evaluation.accepted and o.evaluation.quote]
                out(f"screen #{app.engine.scans}: {st.get('quoted', 0)}/{st.get('pairs', 0)} pairs quoted, "
                    f"{st.get('deep', 0)} deep-checked | cheapest now ${g if g is not None else '-'} | "
                    f"best so far ${best_ever[0] if best_ever else '-'} | accepted {acc} | paper trades {len(trades)}"
                    f" | allowed: {app.risk.trading_allowed()[0]}")
                for o in rej[:2]:
                    q = o.evaluation.quote
                    out(f"    rejected {o.mapping.polymarket.market_id} {o.direction}: gross ${q.gross:.2f} on "
                        f"{q.size:g}, fees ${q.fees:.2f}, net ${q.net:.2f} -> {o.evaluation.reason[:110]}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    out("=" * 70)
    out(f"STOPPED after {(time.time() - (end - a.hours * 3600)) / 60:.0f} min | paper trades: {len(trades)}")
    if best_ever:
        out(f"cheapest pair seen: ${best_ever[0]:.4f} per $1 at {best_ever[2]:%H:%M:%S}Z | {best_ever[1]}")
    for name in ("dashboard", "positions", "history"):
        out("\n" + plain((await dispatch(app, name, [], "dryrun")).text))
    opp = await app.repo.recent_opportunities("paper", limit=100000)
    out(f"evaluations journaled: {len(opp)} ({sum(o.status == 'rejected' for o in opp)} rejected, "
        f"{sum(o.status == 'executed' for o in opp)} executed). Database: {workdir / 'dryrun.db'}")
    await app.shutdown()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", default="BTC,ETH,SOL,XRP")
    ap.add_argument("--trades", type=int, default=1, help="stop after this many paper arbitrage trades")
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--bankroll", type=float, default=2000.0)
    ap.add_argument("--max-trade", type=float, default=200.0)
    ap.add_argument("--interval", type=float, default=4.0, help="seconds between quote screens")
    ap.add_argument("--days", type=int, default=3, help="contracts settling within this many days")
    ap.add_argument("--heartbeat", type=float, default=300.0, help="seconds between status lines")
    ap.add_argument("--workdir", default="var/dryrun")
    ap.add_argument("--telegram", action="store_true")
    sys.exit(asyncio.run(amain(ap.parse_args())))


if __name__ == "__main__":
    main()


__all__ = ["Exchange"]
