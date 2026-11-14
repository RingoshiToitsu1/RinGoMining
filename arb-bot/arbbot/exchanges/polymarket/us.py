"""Polymarket US adapter (CFTC-regulated venue, api.polymarket.us) on the
official `polymarket-us` SDK. One YES-quoted ("long") book per market slug.

Intents: BUY_LONG = buy YES, BUY_SHORT = buy NO, SELL_LONG / SELL_SHORT close them.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ...config.settings import Settings
from ...infra.ratelimit import RateLimiter
from ...core.types import (CENT, ONE, ZERO, Balance, D, EligibilityReport, Exchange, FeeSpec, Level, Market,
                           OrderBook, OrderRequest, OrderResult, OrderStatus, Outcome, Resolution, Side, TIF)
from ..base import ExchangeAdapter, ExchangeError, ExchangeUnavailable, TradingDisabled
from ..kalshi.client import parse_ts

log = logging.getLogger(__name__)

TERMINAL = {"ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED",
            "ORDER_STATE_REPLACED"}
INTENT = {(Side.BUY, Outcome.YES): "ORDER_INTENT_BUY_LONG", (Side.BUY, Outcome.NO): "ORDER_INTENT_BUY_SHORT",
          (Side.SELL, Outcome.YES): "ORDER_INTENT_SELL_LONG", (Side.SELL, Outcome.NO): "ORDER_INTENT_SELL_SHORT"}


_LEAD = re.compile(r"^(?:this|the) market will (?:settle|resolve) to [\"“]?yes[\"”]? if\s+", re.I)


def rules_headline(desc: str) -> str:
    """'This market will settle to Yes if X records the most rushing yards ... in Week 10 ...' ->
    'X records the most rushing yards ... in Week 10 ...'"""
    first = re.split(r"(?<=[.?!])\s", desc.strip(), maxsplit=1)[0]
    first = _LEAD.sub("", first).rstrip(".")
    return (first[:1].upper() + first[1:])[:200] if len(first.split()) >= 4 else ""


def _short(e: Exception) -> str:
    text = re.sub(r"<[^>]+>", " ", str(e))
    return re.sub(r"\s+", " ", text).strip()[:200]


def _amount(a: Any) -> Decimal:
    return D(a.get("value")) if isinstance(a, dict) else D(a)


class PolymarketUSAdapter(ExchangeAdapter):
    name = Exchange.POLYMARKET

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
        self._read_limiter = RateLimiter(settings.polymarket_reads_per_second, burst=8)
        self.fee_rate = Decimal(str(settings.polymarket_us_taker_fee_rate))
        self.short_price_is_long = settings.polymarket_us_short_price_convention == "long"

    async def close(self) -> None:
        await self.c.close()

    async def _call(self, make: Any, *, write: bool = False, retries: int = 3) -> Any:
        """`make` is a zero-argument callable returning the SDK coroutine, so reads can be retried
        after a 429. Error text is shortened: rate-limit responses are full HTML pages."""
        from polymarket_us import errors as E
        attempt = 0
        while True:
            if not write:
                await self._read_limiter.acquire()
            try:
                out = await (make() if callable(make) else make)
                if not write:
                    self._read_limiter.success()
                return out
            except E.RateLimitError as e:
                self._read_limiter.penalize(5.0)
                if not write and callable(make) and attempt < retries:
                    attempt += 1
                    continue
                raise ExchangeUnavailable("polymarket-us: rate limited (HTTP 429)") from e
            except (E.APIConnectionError, E.InternalServerError) as e:
                if not write and callable(make) and attempt < retries:
                    attempt += 1
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise ExchangeUnavailable(f"polymarket-us: {_short(e)}") from e
            except E.APIStatusError as e:
                raise ExchangeError(f"polymarket-us: {_short(e)}") from e
            except E.PolymarketUSError as e:
                if write:
                    raise ExchangeUnavailable(f"polymarket-us: {_short(e)}") from e
                raise ExchangeError(f"polymarket-us: {_short(e)}") from e

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
            await self._call(lambda: self.c.markets.list({"limit": 1, "active": True}))
            rep.add("market data reachable", True)
        except ExchangeError as e:
            rep.add("market data reachable", False, str(e)[:160])
        if self._has_creds:
            try:
                bal = await self.get_balance()
                rep.add("authenticated account access", True, f"buying power ${bal.available}")
                await self._call(lambda: self.c.orders.list())
                rep.add("order API permission", True)
            except ExchangeError as e:
                rep.add("authenticated account access", False, str(e)[:160])
        self.trading_enabled = rep.ok
        return rep

    async def health(self) -> bool:
        try:
            await self._call(lambda: self.c.markets.list({"limit": 1, "active": True}))
            return True
        except ExchangeError:
            return False

    # -- discovery -------------------------------------------------------------------
    def _fee(self, m: dict | None = None) -> FeeSpec:
        """Markets publish a `feeCoefficient`; it is modelled as coefficient * contracts * p * (1-p),
        the standard quadratic exchange fee. Until a market reports one, the configured
        notional rate applies. Real commissions are read back from every order."""
        coef = (m or {}).get("feeCoefficient")
        if coef not in (None, "", 0):
            return FeeSpec("poly_exponent", D(coef), ONE)
        return FeeSpec("notional_bps", self.fee_rate)

    def _market(self, m: dict, event: dict | None = None) -> Market | None:
        """Live shape (gateway /v1/markets): `question` is the event question, `title` the
        subject of this contract (e.g. a team), `marketSides` the long (YES) and short (NO) sides."""
        slug = m.get("slug")
        if not slug:
            return None
        event = event or {}
        question = (m.get("question") or event.get("title") or "").strip()
        subject = (m.get("title") or "").strip()
        sides = m.get("marketSides") or []
        long_label = next((str(x.get("description") or "") for x in sides if x.get("long")), "")
        short_label = next((str(x.get("description") or "") for x in sides if x.get("long") is False), "")
        if long_label.lower() in ("", "yes"):
            yes_label = subject if subject and subject != question else "Yes"
            no_label = "No" if yes_label == "Yes" else f"Not {yes_label}"
        else:
            yes_label, no_label = long_label, short_label or f"Not {long_label}"
        title = f"{question}: {subject}" if subject and subject not in question else (question or subject or slug)
        desc = (m.get("description") or event.get("description") or "").strip()
        if desc and (not question or question == subject or len(title.split()) <= 4):
            # many contracts are titled only with a name ("Ollie Gordon II"); what they are actually
            # about is in the first sentence of their own settlement rules
            title = rules_headline(desc) or title
        end = parse_ts(m.get("endDate") or event.get("endDate") or event.get("endTime"))
        is_open = (bool(m.get("active", True)) and not m.get("closed") and not m.get("archived")
                   and m.get("status", "MARKET_STATUS_OPEN") == "MARKET_STATUS_OPEN")
        return Market(
            Exchange.POLYMARKET, slug, title, yes_label=yes_label, no_label=no_label,
            subtitle=str(m.get("subtitle") or ""), rules=m.get("description") or event.get("description") or "",
            resolution_source="", category=str(m.get("category") or ""),
            event_id=str(m.get("eventSlug") or event.get("slug") or ""), close_time=end, expiration_time=end,
            status="open" if is_open else "closed", tick_size=D(m.get("orderPriceMinTickSize") or CENT),
            min_order_size=D(m.get("minimumTradeQty") or 1), fee=self._fee(m),
            volume=D(m.get("volume")), liquidity=D(m.get("liquidity")),
            url=f"https://polymarket.us/market/{slug}",
            extra={"venue": "us", "id": m.get("id"), "market_type": m.get("marketType"),
                   "subcategory": m.get("eventSubcategory")})

    async def list_markets(self) -> list[Market]:
        horizon = datetime.now(timezone.utc) + timedelta(days=self.s.discovery_max_days_to_close)
        out: list[Market] = []
        for offset in range(0, 100000, 100):
            page = (await self._call(lambda o=offset: self.c.markets.list(
                {"active": True, "closed": False, "limit": 100, "offset": o}))).get("markets") or []
            for m in page:
                mk = self._market(m)
                if mk and mk.status == "open" and mk.close_time and mk.close_time <= horizon \
                        and float(mk.volume) >= self.s.discovery_min_volume:
                    out.append(mk)
            if len(page) < 100:
                break
        return out

    async def get_market(self, market_id: str) -> Market | None:
        try:
            m = (await self._call(lambda: self.c.markets.retrieve_by_slug(market_id))).get("market")
        except ExchangeUnavailable:
            raise
        except ExchangeError:
            return None
        return self._market(m) if m else None

    # -- order books -----------------------------------------------------------------
    def _book(self, slug: str, data: dict, source: str = "rest") -> OrderBook:
        if data.get("state") not in (None, "MARKET_STATE_OPEN"):
            return OrderBook(self.name, slug, source=source)      # halted/closed: nothing executable
        bids = [Level(_amount(x["px"]), D(x["qty"])) for x in data.get("bids") or []]
        asks = [Level(_amount(x["px"]), D(x["qty"])) for x in data.get("offers") or []]
        book = OrderBook.from_single_book(self.name, slug, bids, asks)
        book.source = source
        return book

    async def get_orderbook(self, market: Market) -> OrderBook:
        data = (await self._call(lambda: self.c.markets.book(market.market_id))).get("marketData") or {}
        return self._book(market.market_id, data)

    async def get_orderbooks(self, markets: list[Market]) -> dict[str, OrderBook]:
        """Best effort: markets whose book could not be fetched are left out (they are simply
        not evaluated this round). Raises only if nothing at all could be fetched."""
        sem = asyncio.Semaphore(4)

        async def one(m: Market) -> tuple[str, OrderBook]:
            async with sem:
                return m.market_id, await self.get_orderbook(m)

        results = await asyncio.gather(*(one(m) for m in markets), return_exceptions=True)
        out = dict(r for r in results if not isinstance(r, BaseException))
        errors = [r for r in results if isinstance(r, BaseException)]
        if markets and not out and errors:
            raise errors[0] if isinstance(errors[0], ExchangeError) else ExchangeUnavailable(str(errors[0])[:200])
        return out

    @property
    def supports_streaming(self) -> bool:      # the market-data socket needs API keys
        return self._has_creds

    async def stream_orderbooks(self, markets: list[Market], on_book: Any) -> None:
        """Authenticated market-data WebSocket: full book snapshots per market. One connection
        replaces hundreds of REST polls. Returns or raises when the socket drops; the feed reconnects."""
        if not self._has_creds:
            raise NotImplementedError
        ws = self.c.ws.markets()
        queue: asyncio.Queue = asyncio.Queue()
        ws.on("market_data", lambda msg: queue.put_nowait(("data", msg)))
        ws.on("error", lambda err: queue.put_nowait(("error", err)))
        ws.on("close", lambda *a: queue.put_nowait(("close", None)))
        await ws.connect()
        try:
            slugs = [m.market_id for m in markets]
            for n, i in enumerate(range(0, len(slugs), 100)):
                await ws.subscribe_market_data(f"books-{n}", slugs[i:i + 100])
            while True:
                kind, msg = await queue.get()
                if kind == "data":
                    data = msg.get("marketData") or {}
                    if data.get("marketSlug"):
                        await on_book(self._book(data["marketSlug"], data, source="ws"))
                elif kind == "error":
                    raise ExchangeUnavailable(f"polymarket-us ws: {_short(msg)}")
                else:
                    return
        finally:
            await ws.close()

    async def get_resolution(self, market: Market) -> tuple[Resolution, Decimal] | None:
        try:
            data = await self._call(lambda: self.c.markets.settlement(market.market_id))
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
        bals = (await self._call(lambda: self.c.account.balances())).get("balances") or []
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
            data = await self._call(lambda p=params: self.c.portfolio.positions(p))
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
        orders = (await self._call(lambda: self.c.orders.list())).get("orders") or []
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
                o = (await self._call(lambda: self.c.orders.retrieve(order_id))).get("order") or {}
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
            o = (await self._call(lambda: self.c.orders.retrieve(order_id))).get("order") or {}
            return self._result(o, req, req.outcome, req.size)
        except ExchangeError as e:
            raise ExchangeUnavailable(f"polymarket-us order {order_id} not terminal: {e}") from e

    async def get_order(self, order_id: str, market: Market) -> OrderResult:
        o = (await self._call(lambda: self.c.orders.retrieve(order_id))).get("order") or {}
        outcome = Outcome.NO if "SHORT" in o.get("intent", "") else Outcome.YES
        return self._result(o, None, outcome, D(o.get("quantity")))

    async def cancel_order(self, order_id: str, market: Market) -> None:
        await self._call(self.c.orders.cancel(order_id, {"marketSlug": market.market_id}), write=True)
