"""Event bus -> Telegram alerts. Every alert type the operator needs is
rendered here; delivery failures never propagate into trading code."""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from ...core import events
from ...core.events import Event, EventBus
from .format import e, pct, usd

log = logging.getLogger(__name__)
Sender = Callable[[str], Awaitable[None]]


def trade_message(d: dict) -> str:
    k, p = d["kalshi"], d["polymarket"]
    head = "🟡 <b>PARTIAL ARBITRAGE FILLED</b>" if d.get("partial") else "✅ <b>ARBITRAGE PAIR COMPLETED</b>"
    lines = [
        f"{head} ({d['mode'].upper()})",
        f"<b>{e(d['title'], 160)}</b>",
        "",
        f"Kalshi: BUY {k['outcome'].upper()} {e(k['market'])} @ {k['avg']:.4f} (orders {e(', '.join(k['orders']))})",
        f"Polymarket: BUY {p['outcome'].upper()} {e(p['market'], 40)} @ {p['avg']:.4f} "
        f"(orders {e(', '.join(p['orders']))})",
        "",
        f"YES price: {d['yes_price']:.4f} ({d['yes_exchange']})",
        f"NO price: {d['no_price']:.4f} ({d['no_exchange']})",
        f"Quantity matched: {d['quantity']:g}" + (f" of {d['planned']:g} planned" if d.get("partial") else ""),
        f"Total acquisition cost: {usd(d['acquisition'])}",
        f"Fees: {usd(d['fees'])}",
        f"Expected settlement payout: {usd(d['payout'])}",
        f"Expected net profit: <b>{usd(d['expected_net'], True)}</b>",
        f"Expected return: <b>{pct(d['roi_pct'])}</b>",
        f"Capital deployed: {usd(d['capital'])}",
        f"Execution time: {d['exec_seconds']:.2f}s",
        f"Matching confidence: {d['confidence']:.0f}",
    ]
    if d.get("unwind_pnl"):
        lines.append(f"Unwound excess leg: {usd(d['unwind_pnl'], True)}")
    return "\n".join(lines)


def summary_message(dash: dict) -> str:
    st, cap, un, risk = dash["stats"], dash["capital"], dash["unrealized"], dash["risk"]
    return "\n".join([
        f"📊 <b>Daily summary</b> ({dash['mode'].upper()})",
        f"Today: {usd(dash['daily'][0], True)} ({pct(dash['daily'][1])}) | 7d {usd(dash['weekly'][0], True)} | "
        f"30d {usd(dash['monthly'][0], True)}",
        f"Net realized all-time {usd(st['net_realized'], True)} | fees {usd(st['fees'])}",
        f"Locked in on open hedges {usd(un['locked_in'], True)}",
        f"Trades {st['total_trades']} | matched {st['matched_pairs']} | failed {st['failed']} | partial "
        f"{st['partial_fills']}",
        f"Bankroll {usd(cap['bankroll'])} | deployed {usd(cap['deployed'])} ({pct(cap['utilization_pct'], 1)})",
        f"Open positions {st['open_positions']} | max drawdown {pct(risk['max_drawdown_pct'], 1)}",
        f"Trading: {'allowed' if dash['allowed'][0] else 'BLOCKED: ' + e(dash['allowed'][1], 200)}",
    ])


class Notifier:
    def __init__(self, bus: EventBus, send: Sender):
        self._send = send
        self._last: dict[str, float] = {}
        self._pending_verified: list[dict] = []
        bus.subscribe(events.OPPORTUNITY, self.on_opportunity)
        bus.subscribe(events.TRADE_COMPLETED, self.on_trade)
        bus.subscribe(events.PARTIAL_FILL, self.on_partial)
        bus.subscribe(events.EXECUTION_FAILED, self.on_failed)
        bus.subscribe(events.EMERGENCY, self.on_emergency)
        bus.subscribe(events.EXCHANGE_STATUS, self.on_exchange)
        bus.subscribe(events.RISK_ALERT, self.on_risk)
        bus.subscribe(events.POSITION_SETTLED, self.on_settled)
        bus.subscribe(events.MAPPING_CHANGED, self.on_mapping)
        bus.subscribe(events.MARKETS_DISCOVERED, self.on_discovered)
        bus.subscribe(events.DAILY_SUMMARY, self.on_summary)

    async def send(self, text: str, throttle_key: str = "", min_gap: float = 0.0) -> None:
        if throttle_key:
            now = time.time()
            if now - self._last.get(throttle_key, 0.0) < min_gap:
                return
            self._last[throttle_key] = now
        try:
            await self._send(text[:4000])
        except Exception:
            log.exception("telegram notification failed")

    async def on_opportunity(self, ev: Event) -> None:
        d = ev.data
        await self.send(
            f"🔎 <b>New opportunity</b>\n{e(d['title'], 140)}\n{e(d['direction'])} | {d['size']:g} contracts | "
            f"capital {usd(d['capital'])}\nNet {usd(d['net'], True)} = {pct(d['roi_pct'])} "
            f"(required {pct(d['required_pct'])}) | confidence {d['confidence']:.0f}")

    async def on_trade(self, ev: Event) -> None:
        await self.send(trade_message(ev.data))

    async def on_partial(self, ev: Event) -> None:
        d = ev.data
        await self.send(f"🟡 <b>Partial fill</b> ({d['leg']} leg, {e(d['exchange'])})\n{e(d['title'], 140)}\n"
                        f"Filled {d['filled']:g} of {d['wanted']:g}. Managing the difference now.")

    async def on_failed(self, ev: Event) -> None:
        d = ev.data
        if not d.get("exposure") and "no longer qualifies" in d.get("reason", ""):
            # routine: the edge vanished before submission. Journaled, not worth a ping each time.
            await self.send(f"⚪ Skipped: {e(d['title'], 100)}\n{e(d['reason'], 200)}", "skip", 300)
            return
        icon = "🚨" if d.get("exposure") else "❌"
        text = f"{icon} <b>Execution failed</b>\n{e(d['title'], 140)}\n{e(d.get('reason', ''), 400)}"
        if d.get("unwind_pnl"):
            text += f"\nRealized on unwind: {usd(d['unwind_pnl'], True)}"
        if d.get("exposure"):
            text += "\n<b>Unhedged exposure remains.</b> Trading is paused. See /positions."
        await self.send(text)

    async def on_emergency(self, ev: Event) -> None:
        d = ev.data
        what = "EMERGENCY HEDGE" if d["kind"] == "hedge" else "EMERGENCY UNWIND"
        await self.send(f"🚨 <b>{what}</b> on {e(d['exchange'])}\n{e(d['title'], 140)}\n"
                        f"{d['unmatched']:g} contracts unmatched. Limiting exposure.")

    async def on_exchange(self, ev: Event) -> None:
        d = ev.data
        name = d["exchange"].value if hasattr(d["exchange"], "value") else str(d["exchange"])
        if d.get("up"):
            await self.send(f"🟢 {e(name)} reconnected.")
        else:
            await self.send(f"🔌 <b>Exchange disconnection</b>: {e(name)}\n{e(d.get('error', ''), 300)}",
                            f"down:{name}", 600)

    async def on_risk(self, ev: Event) -> None:
        d = ev.data
        if d["severity"] == "info":
            await self.send(f"🟢 Risk {e(d['message'], 300)}", f"risk-info:{d['kind']}", 300)
            return
        icon = "⚠️" if d["severity"] == "warning" else "⛔"
        await self.send(f"{icon} <b>Risk alert: {e(d['kind'])}</b>\n{e(d['message'], 600)}"
                        + ("\nNew trades are paused." if d.get("paused") else ""))

    async def on_settled(self, ev: Event) -> None:
        d = ev.data
        await self.send(f"🏁 <b>Position settled</b> #{d['position_id']}\n{e(d['title'], 140)}\n"
                        f"Payout {usd(d['payout'])} | realized {usd(d['pnl'], True)}")

    async def on_mapping(self, ev: Event) -> None:
        d = ev.data
        if d["status"] == "verified":
            # structural matches arrive in batches of dozens: one line each would flood the chat
            self._pending_verified.append(d)
            if len(self._pending_verified) > 1:
                return
            await self.send(f"🔗 <b>Market pair verified</b> #{d['mapping_id']} (confidence {d['confidence']:.0f})\n"
                            f"{e(d['kalshi'], 100)}\n↔ {e(d['polymarket'], 100)}")
        elif d.get("new") and d["confidence"] >= 75:
            await self.send(f"🟡 <b>Possible match needs review</b> #{d['mapping_id']} "
                            f"(confidence {d['confidence']:.0f})\n{e(d['kalshi'], 100)}\n↔ {e(d['polymarket'], 100)}\n"
                            f"/mappings show {d['mapping_id']}", "mapping", 20)

    async def on_discovered(self, ev: Event) -> None:
        batch, self._pending_verified = self._pending_verified, []
        if len(batch) > 1:
            await self.send(f"🔗 <b>{len(batch)} market pairs verified</b> and now eligible for trading\n"
                            + "\n".join(f"#{d['mapping_id']} {e(d['polymarket'], 80)}" for d in batch[:12])
                            + (f"\n…and {len(batch) - 12} more (/mappings verified)" if len(batch) > 12 else ""))

    async def on_summary(self, ev: Event) -> None:
        await self.send(summary_message(ev.data["dashboard"]))
