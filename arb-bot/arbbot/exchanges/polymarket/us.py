"""Polymarket US adapter (CFTC-regulated venue, api.polymarket.us) on the
official `polymarket-us` SDK. One YES-quoted ("long") book per market slug.

Intents: BUY_LONG = buy YES, BUY_SHORT = buy NO, SELL_LONG / SELL_SHORT close them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ...config.settings import Settings
from ...core.types import (CENT, ONE, ZERO, Balance, D, EligibilityReport, Exchange, FeeSpec, Level, Market,
                           OrderBook, OrderRequest, OrderResult, OrderStatus, Outcome, Resolution, Side, TIF)
from ..base import ExchangeAdapter, ExchangeError, ExchangeUnavailable, TradingDisabled
from ..kalshi.client import parse_ts

log = logging.getLogger(__name__)

TERMINAL = {"ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED",
            "ORDER_STATE_REPLACED"}
INTENT = {(Side.BUY, Outcome.YES): "ORDER_INTENT_BUY_LONG", (Side.BUY, Outcome.NO): "ORDER_INTENT_BUY_SHORT",
          (Side.SELL, Outcome.YES): "ORDER_INTENT_SELL_LONG", (Side.SELL, Outcome.NO): "ORDER_INTENT_SELL_SHORT"}


def _amount(a: Any) -> Decimal:
    return D(a.get("value")) if isinstance(a, dict) else D(a)


class PolymarketUSAdapter(ExchangeAdapter):
    name = Exchange.POLYMARKET
    supports_streaming = False       # REST polling; books are refreshed again before every execution

    def __init__(self, settings: Settings, client: Any = None):
        self.s = settings
        if client is None:
            from polymarket_us import AsyncPolymarketUS
            client = AsyncPolymarketUS(
                key_id=settings.polymarket_us_key_id or None, secret_key=settings.polymarket_us_secret_key or None,
                gateway_base_url=settings.polymarket_us_gateway_url, api_base_url=settings.polymarket_us_api_url,
                timeout=10.0)
        self.c = client
        self.trading_enabled = False
        self.fee_rate = Decimal(str(settings.polymarket_us_taker_fee_rate))
        self.short_price_is_long = settings.polymarket_us_short_price_convention == "long"

    async def close(self) -> None:
        await self.c.close()

    async def _call(self, coro: Any, *, write: bool = False) -> Any:
        from polymarket_us import errors as E
        try:
            return await coro
        except (E.APIConnectionError, E.InternalServerError, E.RateLimitError) as e:
            raise ExchangeUnavailable(f"polymarket-us: {e}") from e
        except E.APIStatusError as e:
            raise ExchangeError(f"polymarket-us: {e}") from e
        except E.PolymarketUSError as e:
            if write:
                raise ExchangeUnavailable(f"polymarket-us: {e}") from e
            raise ExchangeError(f"polymarket-us: {e}") from e

    @property
    def _has_creds(self) -> bool:
        return bool(self.s.polymarket_us_key_id and self.s.polymarket_us_secret_key)

    # -- eligibility / health ------------------------------------------------------
    async def check_eligibility(self) -> EligibilityReport:
        rep = EligibilityReport(self.name)
        rep.add("operator attested state eligibility, KYC and API terms", self.s.polymarket_eligibility_confirmed,
                "set POLYMARKET_ELIGIBILITY_CONFIRMED=true once verified")
        rep.add("API credentials configured", self._has_creds, "create keys at polymarket.us/developer")
        try:
            await self._call(self.c.markets.list({"limit": 1, "active": True}))
            rep.add("market data reachable", True)
        except ExchangeError as e:
            rep.add("market data reachable", False, str(e)[:160])
        if self._has_creds:
            try:
                bal = await self.get_balance()
                rep.add("authenticated account access", True, f"buying power ${bal.available}")
                await self._call(self.c.orders.list())
                rep.add("order API permission", True)
            except ExchangeError as e:
                rep.add("authenticated account access", False, str(e)[:160])
        self.trading_enabled = rep.ok
        return rep

    async def health(self) -> bool:
        try:
            await self._call(self.c.markets.list({"limit": 1, "active": True}))
            return True
        except ExchangeError:
            return False

    # -- discovery -------------------------------------------------------------------
    def _fee(self) -> FeeSpec:
        return FeeSpec("notional_bps", self.fee_rate)

    def _market(self, m: dict, event: dict | None) -> Market | None:
        slug = m.get("slug")
        if not slug:
            return None
        event = event or {}
        end = parse_ts(event.get("endTime"))
        outcome = (m.get("outcome") or "").strip()
        title = m.get("title") or event.get("title") or slug
        if event.get("title") and event["title"] not in title:
            title = f"{event['title']}: {title}"
        desc = m.get("description") or event.get("description") or ""
        return Market(
            Exchange.POLYMARKET, slug, title, yes_label=outcome or "Yes",
            no_label="No" if not outcome or outcome.lower() == "yes" else f"Not {outcome}",
            rules=desc, resolution_source="", category=",".join(t.get("slug", "") for t in event.get("tags") or []),
            event_id=m.get("eventSlug") or event.get("slug", ""), close_time=end, expiration_time=end,
            status="open" if m.get("active", True) and not m.get("closed") else "closed",
            tick_size=CENT, fee=self._fee(), volume=D(m.get("volume")), liquidity=D(m.get("liquidity")),
            url=f"https://polymarket.us/market/{slug}", extra={"venue": "us", "id": m.get("id")})

    async def list_markets(self) -> list[Market]:
        horizon = datetime.now(timezone.utc) + timedelta(days=self.s.discovery_max_days_to_close)
        events: dict[str, dict] = {}
        for offset in range(0, 20000, 100):
            page = (await self._call(self.c.events.list(
                {"active": True, "closed": False, "limit": 100, "offset": offset}))).get("events") or []
            for ev in page:
                events[ev.get("slug", "")] = ev
            if len(page) < 100:
                break
        out: list[Market] = []
        for offset in range(0, 50000, 100):
            page = (await self._call(self.c.markets.list(
                {"active": True, "closed": False, "limit": 100, "offset": offset}))).get("markets") or []
            for m in page:
                mk = self._market(m, events.get(m.get("eventSlug", "")))
                if mk and mk.status == "open" and mk.close_time and mk.close_time <= horizon \
                        and float(mk.volume) >= self.s.discovery_min_volume:
                    out.append(mk)
            if len(page) < 100:
                break
        return out

    async def get_market(self, market_id: str) -> Market | None:
        try:
            m = (await self._call(self.c.markets.retrieve_by_slug(market_id))).get("market")
            if not m:
                return None
            ev = None
            if m.get("eventSlug"):
                ev = (await self._call(self.c.events.retrieve_by_slug(m["eventSlug"]))).get("event")
            return self._market(m, ev)
        except ExchangeUnavailable:
            raise
        except ExchangeError:
            return None

    # -- order books -----------------------------------------------------------------
    async def get_orderbook(self, market: Market) -> OrderBook:
        data = (await self._call(self.c.markets.book(market.market_id))).get("marketData") or {}
        if data.get("state") not in (None, "MARKET_STATE_OPEN"):
            return OrderBook(self.name, market.market_id)       # halted/closed: nothing executable
        bids = [Level(_amount(x["px"]), D(x["qty"])) for x in data.get("bids") or []]
        asks = [Level(_amount(x["px"]), D(x["qty"])) for x in data.get("offers") or []]
        return OrderBook.from_single_book(self.name, market.market_id, bids, asks)

    async def get_orderbooks(self, markets: list[Market]) -> dict[str, OrderBook]:
        sem = asyncio.Semaphore(8)

        async def one(m: Market) -> tuple[str, OrderBook]:
            async with sem:
                return m.market_id, await self.get_orderbook(m)

        return dict(await asyncio.gather(*(one(m) for m in markets)))

    async def get_resolution(self, market: Market) -> tuple[Resolution, Decimal] | None:
        try:
            data = await self._call(self.c.markets.settlement(market.market_id))
        except ExchangeUnavailable:
            raise
        except ExchangeError:
            return None                      # not settled yet
        if data is None or data.get("settlement") is None:
            return None
        v = D(data["settlement"])
        if v == ONE:
            return Resolution.YES, ONE
        if v == ZERO:
            return Resolution.NO, ZERO
        return Resolution.VOID, v

    # -- account ---------------------------------------------------------------------
    async def get_balance(self) -> Balance:
        if not self._has_creds:
            raise TradingDisabled("Polymarket US credentials are not configured")
        bals = (await self._call(self.c.account.balances())).get("balances") or []
        if not bals:
            raise ExchangeError("polymarket-us: no balances returned")
        b = bals[0]
        return Balance(D(b.get("buyingPower")), D(b.get("currentBalance")))

    async def get_positions(self) -> dict[tuple[str, Outcome], Decimal]:
        out: dict[tuple[str, Outcome], Decimal] = {}
        cursor = None
        for _ in range(100):
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = await self._call(self.c.portfolio.positions(params))
            for slug, p in (data.get("positions") or {}).items():
                qty = D(p.get("netPositionDecimal") or p.get("netPosition"))
                slug = (p.get("marketMetadata") or {}).get("slug") or slug
                if qty > 0:
                    out[(slug, Outcome.YES)] = qty
                elif qty < 0:
                    out[(slug, Outcome.NO)] = -qty
            cursor = data.get("nextCursor")
            if data.get("eof", True) or not cursor:
                break
        return out

    async def get_open_orders(self) -> list[dict]:
        orders = (await self._call(self.c.orders.list())).get("orders") or []
        return [{"order_id": o["id"], "market_id": o.get("marketSlug", "")} for o in orders]

    # -- trading ---------------------------------------------------------------------
    def _wire_price(self, outcome: Outcome, price: Decimal) -> Decimal:
        return ONE - price if outcome is Outcome.NO and self.short_price_is_long else price

    def _result(self, o: dict, req: OrderRequest | None, outcome: Outcome, size: Decimal) -> OrderResult:
        filled = D(o.get("cumQuantity"))
        state = o.get("state", "")
        bps = o.get("commissionsBasisPoints")
        if bps not in (None, ""):
            self.fee_rate = D(bps) / 10000          # learn the venue's real taker rate
        if filled <= 0:
            status = OrderStatus.REJECTED if state == "ORDER_STATE_REJECTED" else OrderStatus.UNFILLED
            return OrderResult(status, order_id=o.get("id", ""), message=state, raw=o)
        avg = self._wire_price(outcome, _amount(o.get("avgPx") or {}))
        fee = _amount(o.get("commissionNotionalTotalCollected") or {})
        status = OrderStatus.FILLED if filled >= size else OrderStatus.PARTIAL
        return OrderResult(status, o.get("id", ""), filled, avg, fee, raw=o)

    async def place_order(self, req: OrderRequest) -> OrderResult:
        if not self.trading_enabled:
            raise TradingDisabled("Polymarket US trading is disabled until eligibility checks pass")
        params = {
            "marketSlug": req.market.market_id, "intent": INTENT[(req.side, req.outcome)],
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": f"{self._wire_price(req.outcome, req.limit_price):.4f}".rstrip("0").rstrip("."),
                      "currency": "USD"},
            "quantity": int(req.size) if req.size == req.size.to_integral_value() else float(req.size),
            "tif": "TIME_IN_FORCE_FILL_OR_KILL" if req.tif is TIF.FOK else "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True,
        }
        resp = await self._call(self.c.orders.create(params), write=True)
        order_id = resp.get("id", "")
        if not order_id:
            return OrderResult(OrderStatus.REJECTED, message=str(resp)[:200], raw=resp)
        # the order is immediate; wait for its terminal state
        last: OrderResult | None = None
        for delay in (0.0, 0.15, 0.3, 0.6, 1.0, 2.0, 3.0):
            if delay:
                await asyncio.sleep(delay)
            try:
                o = (await self._call(self.c.orders.retrieve(order_id))).get("order") or {}
            except ExchangeError as e:
                log.warning("polymarket-us order %s lookup failed: %s", order_id, e)
                continue
            last = self._result(o, req, req.outcome, req.size)
            if o.get("state") in TERMINAL:
                return last
        if last is None:
            raise ExchangeUnavailable(f"polymarket-us order {order_id} state unknown")
        # still working after ~7s: an IOC should never rest; cancel and report what filled
        try:
            await self.cancel_order(order_id, req.market)
            o = (await self._call(self.c.orders.retrieve(order_id))).get("order") or {}
            return self._result(o, req, req.outcome, req.size)
        except ExchangeError as e:
            raise ExchangeUnavailable(f"polymarket-us order {order_id} not terminal: {e}") from e

    async def get_order(self, order_id: str, market: Market) -> OrderResult:
        o = (await self._call(self.c.orders.retrieve(order_id))).get("order") or {}
        outcome = Outcome.NO if "SHORT" in o.get("intent", "") else Outcome.YES
        return self._result(o, None, outcome, D(o.get("quantity")))

    async def cancel_order(self, order_id: str, market: Market) -> None:
        await self._call(self.c.orders.cancel(order_id, {"marketSlug": market.market_id}), write=True)
