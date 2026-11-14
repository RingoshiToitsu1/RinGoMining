"""Polymarket international adapter (Polygon CLOB).

Market data comes from the public Gamma and CLOB REST/WebSocket APIs. Orders
are signed and posted with Polymarket's `py_clob_client_v2` (optional extra:
`pip install arbbot[intl]`).

This venue is not open to US persons. The adapter asks Polymarket's own
geoblock endpoint and refuses to trade when it reports the location blocked;
it never attempts to route around that.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from ...config.settings import Settings
from ...core.fees import estimate_fee
from ...core.types import (CENT, ONE, ZERO, Balance, D, EligibilityReport, Exchange, FeeSpec, Level, Market,
                           OrderBook, OrderRequest, OrderResult, OrderStatus, Outcome, Resolution, Side, TIF)
from ..base import BookCallback, ExchangeAdapter, ExchangeError, ExchangeUnavailable, TradingDisabled
from ..kalshi.client import parse_ts

log = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
# Used until the market's real fee parameters are loaded from the CLOB (enrich_market).
CONSERVATIVE_FEE = FeeSpec("poly_exponent", Decimal("0.10"), ONE)


def _jlist(v: Any) -> list:
    if isinstance(v, list):
        return v
    try:
        return json.loads(v) if v else []
    except (TypeError, ValueError):
        return []


def _levels(rows: Any) -> list[Level]:
    return [Level(D(r["price"]), D(r["size"])) for r in rows or []]


class PolymarketIntlAdapter(ExchangeAdapter):
    name = Exchange.POLYMARKET
    supports_streaming = True

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None, clob: Any = None):
        self.s = settings
        self._gamma = httpx.AsyncClient(base_url=settings.polymarket_gamma_url, timeout=15.0, transport=transport)
        self._clob_http = httpx.AsyncClient(base_url=settings.polymarket_clob_url, timeout=10.0, transport=transport)
        self._misc = httpx.AsyncClient(timeout=10.0, transport=transport)
        self._clob = clob                # authenticated py_clob_client_v2.ClobClient (sync)
        self.trading_enabled = False

    async def close(self) -> None:
        for c in (self._gamma, self._clob_http, self._misc):
            await c.aclose()

    async def _get(self, client: httpx.AsyncClient, path: str, *, params: Any = None, retries: int = 2) -> Any:
        attempt = 0
        while True:
            try:
                r = await client.get(path, params=params)
            except httpx.HTTPError as e:
                if attempt < retries:
                    attempt += 1
                    await asyncio.sleep(0.3 * attempt)
                    continue
                raise ExchangeUnavailable(f"polymarket GET {path}: {e}") from e
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < retries:
                    attempt += 1
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise ExchangeUnavailable(f"polymarket GET {path}: HTTP {r.status_code}")
            if r.status_code >= 400:
                raise ExchangeError(f"polymarket GET {path}: HTTP {r.status_code} {r.text[:200]}")
            return r.json()

    async def _sdk(self, fn: Any, *args: Any, write: bool = False, **kw: Any) -> Any:
        """Run a blocking SDK call in a thread and map its errors."""
        try:
            return await asyncio.to_thread(fn, *args, **kw)
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status is not None and 400 <= int(status) < 500 and int(status) != 429:
                raise ExchangeError(f"polymarket: {e}") from e
            if write or status is not None or isinstance(e, (OSError, TimeoutError)):
                raise ExchangeUnavailable(f"polymarket: {e}") from e
            raise ExchangeError(f"polymarket: {e}") from e

    def _ensure_clob(self) -> Any:
        if self._clob is None:
            if not self.s.polymarket_private_key:
                raise TradingDisabled("Polymarket wallet key is not configured")
            try:
                from py_clob_client_v2.client import ClobClient
            except ImportError as e:
                raise TradingDisabled("install the 'intl' extra to trade on Polymarket international") from e
            c = ClobClient(self.s.polymarket_clob_url, self.s.polymarket_chain_id, key=self.s.polymarket_private_key,
                           signature_type=self.s.polymarket_signature_type,
                           funder=self.s.polymarket_funder or None)
            c.set_api_creds(c.create_or_derive_api_key())
            self._clob = c
        return self._clob

    # -- eligibility / health ------------------------------------------------------
    async def check_eligibility(self) -> EligibilityReport:
        rep = EligibilityReport(self.name)
        rep.add("operator attested eligibility for the international venue", self.s.polymarket_eligibility_confirmed,
                "set POLYMARKET_ELIGIBILITY_CONFIRMED=true once verified")
        try:
            geo = await self._get(self._misc, self.s.polymarket_geoblock_url, retries=1)
            blocked = bool(geo.get("blocked", True))
            rep.add("Polymarket geoblock check", not blocked,
                    f"country={geo.get('country')} region={geo.get('region')} blocked={blocked}")
        except ExchangeError as e:
            rep.add("Polymarket geoblock check", False, f"could not verify: {str(e)[:120]}")
        try:
            await self._get(self._clob_http, "/time", retries=1)
            rep.add("CLOB reachable", True)
        except ExchangeError as e:
            rep.add("CLOB reachable", False, str(e)[:160])
        rep.add("wallet key configured", bool(self.s.polymarket_private_key))
        if self.s.polymarket_private_key and rep.checks[1].ok:
            try:
                await asyncio.to_thread(self._ensure_clob)
                closed_only = await self._sdk(self._clob.get_closed_only_mode)
                restricted = bool((closed_only or {}).get("closed_only"))
                rep.add("account may open positions", not restricted, json.dumps(closed_only)[:120])
                bal = await self.get_balance()
                rep.add("authenticated account access", True, f"collateral ${bal.available}")
            except ExchangeError as e:
                rep.add("authenticated account access", False, str(e)[:160])
        self.trading_enabled = rep.ok
        return rep

    async def health(self) -> bool:
        try:
            await self._get(self._clob_http, "/time", retries=0)
            return True
        except ExchangeError:
            return False

    # -- discovery -------------------------------------------------------------------
    def _market(self, m: dict) -> Market | None:
        tokens, outcomes = _jlist(m.get("clobTokenIds")), _jlist(m.get("outcomes"))
        if len(tokens) != 2 or len(outcomes) != 2 or not m.get("conditionId") or not m.get("enableOrderBook", True):
            return None
        yes_label, no_label = str(outcomes[0]), str(outcomes[1])
        end = parse_ts(m.get("endDate"))
        events = m.get("events") or []
        open_ = bool(m.get("active", True)) and not m.get("closed") and m.get("acceptingOrders", True)
        return Market(
            Exchange.POLYMARKET, m["conditionId"], m.get("question") or m.get("slug", ""),
            yes_label="Yes" if yes_label.lower() == "yes" else yes_label,
            no_label="No" if no_label.lower() == "no" else no_label,
            rules=m.get("description") or "", resolution_source=m.get("resolutionSource") or "",
            category=m.get("category") or "", event_id=str(events[0].get("slug", "")) if events else "",
            close_time=end, expiration_time=end, status="open" if open_ else "closed",
            tick_size=D(m.get("orderPriceMinTickSize") or CENT), min_order_size=D(m.get("orderMinSize") or 5),
            fee=CONSERVATIVE_FEE, volume=D(m.get("volumeNum") or m.get("volume")),
            liquidity=D(m.get("liquidityNum") or m.get("liquidity")),
            url=f"https://polymarket.com/market/{m.get('slug', '')}",
            extra={"venue": "intl", "yes_token": str(tokens[0]), "no_token": str(tokens[1]),
                   "neg_risk": bool(m.get("negRisk")), "slug": m.get("slug", "")})

    async def list_markets(self) -> list[Market]:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=self.s.discovery_max_days_to_close)
        out: list[Market] = []
        for offset in range(0, 100000, 500):
            page = await self._get(self._gamma, "/markets", params={
                "active": "true", "closed": "false", "limit": 500, "offset": offset,
                "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": horizon.strftime("%Y-%m-%dT%H:%M:%SZ")})
            for m in page or []:
                mk = self._market(m)
                if mk and mk.status == "open" and mk.close_time and float(mk.volume) >= self.s.discovery_min_volume:
                    out.append(mk)
            if not page or len(page) < 500:
                break
        return out

    async def get_market(self, market_id: str) -> Market | None:
        page = await self._get(self._gamma, "/markets", params={"condition_ids": market_id})
        if not page:
            return None
        mk = self._market(page[0])
        return await self.enrich_market(mk) if mk else None

    async def enrich_market(self, market: Market) -> Market:
        """Load the real fee parameters, tick size and minimum size from the CLOB."""
        try:
            info = await self._get(self._clob_http, f"/clob-markets/{market.market_id}")
        except ExchangeError as e:
            log.warning("no CLOB info for %s, keeping conservative fee: %s", market.market_id, e)
            return market
        fd = info.get("fd") or {}
        rate = D(fd.get("r") or 0)
        market.fee = FeeSpec("poly_exponent", rate, D(fd.get("e") or 1)) if rate > 0 else FeeSpec()
        if info.get("mts"):
            market.tick_size = D(info["mts"])
        if info.get("mos"):
            market.min_order_size = D(info["mos"])
        market.extra["neg_risk"] = bool(info.get("nr", market.extra.get("neg_risk")))
        market.extra["fee_loaded"] = True
        return market

    # -- order books -----------------------------------------------------------------
    @staticmethod
    def _combine(market_id: str, yes: dict, no: dict, source: str = "rest") -> OrderBook:
        return OrderBook(Exchange.POLYMARKET, market_id, yes_bids=_levels(yes.get("bids")),
                         yes_asks=_levels(yes.get("asks")), no_bids=_levels(no.get("bids")),
                         no_asks=_levels(no.get("asks")), source=source).normalize()

    async def get_orderbook(self, market: Market) -> OrderBook:
        return (await self.get_orderbooks([market]))[market.market_id]

    async def get_orderbooks(self, markets: list[Market]) -> dict[str, OrderBook]:
        out: dict[str, OrderBook] = {}
        for i in range(0, len(markets), 100):
            chunk = markets[i:i + 100]
            body = [{"token_id": m.extra[k]} for m in chunk for k in ("yes_token", "no_token")]
            try:
                r = await self._clob_http.post("/books", json=body)
            except httpx.HTTPError as e:
                raise ExchangeUnavailable(f"polymarket POST /books: {e}") from e
            if r.status_code >= 500 or r.status_code == 429:
                raise ExchangeUnavailable(f"polymarket POST /books: HTTP {r.status_code}")
            if r.status_code >= 400:
                raise ExchangeError(f"polymarket POST /books: HTTP {r.status_code} {r.text[:200]}")
            by_asset = {b.get("asset_id"): b for b in r.json() or []}
            for m in chunk:
                out[m.market_id] = self._combine(m.market_id, by_asset.get(m.extra["yes_token"], {}),
                                                 by_asset.get(m.extra["no_token"], {}))
        return out

    async def stream_orderbooks(self, markets: list[Market], on_book: BookCallback) -> None:
        """Market channel: `book` snapshots and `price_change` level updates per token."""
        import websockets

        owner: dict[str, tuple[str, str]] = {}
        for m in markets:
            owner[m.extra["yes_token"]] = (m.market_id, "yes")
            owner[m.extra["no_token"]] = (m.market_id, "no")
        state: dict[str, dict[str, dict[str, dict[Decimal, Decimal]]]] = {}

        async def emit(market_id: str) -> None:
            st = state[market_id]

            def rows(side: dict[Decimal, Decimal]) -> list[dict]:
                return [{"price": p, "size": s} for p, s in side.items() if s > 0]

            if "yes" in st and "no" in st:
                await on_book(self._combine(
                    market_id, {"bids": rows(st["yes"]["bids"]), "asks": rows(st["yes"]["asks"])},
                    {"bids": rows(st["no"]["bids"]), "asks": rows(st["no"]["asks"])}, source="ws"))

        def apply_change(asset: str, price: Any, size: Any, side: str) -> str | None:
            if asset not in owner:
                return None
            mid, which = owner[asset]
            book = state.setdefault(mid, {}).setdefault(which, {"bids": {}, "asks": {}})
            book["bids" if side.upper() == "BUY" else "asks"][D(price)] = D(size)
            return mid

        async with websockets.connect(self.s.polymarket_ws_url, ping_interval=None) as ws:
            await ws.send(json.dumps({"assets_ids": list(owner), "type": "market"}))

            async def keepalive() -> None:
                while True:
                    await asyncio.sleep(10)
                    await ws.send("PING")

            ka = asyncio.create_task(keepalive())
            try:
                async for raw in ws:
                    if raw in ("PONG", b"PONG"):
                        continue
                    try:
                        payload = json.loads(raw)
                    except ValueError:
                        continue
                    for ev in payload if isinstance(payload, list) else [payload]:
                        typ = ev.get("event_type")
                        touched: set[str] = set()
                        if typ == "book" and ev.get("asset_id") in owner:
                            mid, which = owner[ev["asset_id"]]
                            state.setdefault(mid, {})[which] = {
                                "bids": {D(x["price"]): D(x["size"]) for x in ev.get("bids") or []},
                                "asks": {D(x["price"]): D(x["size"]) for x in ev.get("asks") or []}}
                            touched.add(mid)
                        elif typ == "price_change":
                            for ch in ev.get("price_changes") or ev.get("changes") or []:
                                mid = apply_change(ch.get("asset_id") or ev.get("asset_id", ""), ch["price"],
                                                   ch["size"], ch["side"])
                                if mid:
                                    touched.add(mid)
                        for mid in touched:
                            await emit(mid)
            finally:
                ka.cancel()

    async def get_resolution(self, market: Market) -> tuple[Resolution, Decimal] | None:
        page = await self._get(self._gamma, "/markets", params={"condition_ids": market.market_id})
        if not page:
            return None
        m = page[0]
        if not m.get("closed") or str(m.get("umaResolutionStatus", "resolved")).lower() not in ("resolved", ""):
            return None
        prices = [D(x) for x in _jlist(m.get("outcomePrices"))]
        if len(prices) != 2:
            return None
        if prices[0] == ONE and prices[1] == ZERO:
            return Resolution.YES, ONE
        if prices[0] == ZERO and prices[1] == ONE:
            return Resolution.NO, ZERO
        if prices[0] + prices[1] == ONE:
            return Resolution.VOID, prices[0]         # e.g. resolved 50-50
        return None

    # -- account ---------------------------------------------------------------------
    async def get_balance(self) -> Balance:
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
        c = await asyncio.to_thread(self._ensure_clob)
        data = await self._sdk(c.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        bal = D(data.get("balance")) / Decimal(10 ** 6)
        return Balance(bal, bal)

    def _wallet(self) -> str:
        if self.s.polymarket_funder:
            return self.s.polymarket_funder
        return self._ensure_clob().get_address()

    async def get_positions(self) -> dict[tuple[str, Outcome], Decimal]:
        rows = await self._get(self._misc, f"{DATA_API}/positions",
                               params={"user": self._wallet(), "sizeThreshold": 0, "limit": 500})
        out: dict[tuple[str, Outcome], Decimal] = {}
        for p in rows or []:
            size = D(p.get("size"))
            if size > 0 and p.get("conditionId") is not None:
                out[(p["conditionId"], Outcome.YES if int(p.get("outcomeIndex", 0)) == 0 else Outcome.NO)] = size
        return out

    async def get_open_orders(self) -> list[dict]:
        c = await asyncio.to_thread(self._ensure_clob)
        orders = await self._sdk(c.get_open_orders)
        return [{"order_id": o.get("id", ""), "market_id": o.get("market", "")} for o in orders or []]

    # -- trading ---------------------------------------------------------------------
    async def place_order(self, req: OrderRequest) -> OrderResult:
        if not self.trading_enabled:
            raise TradingDisabled("Polymarket trading is disabled until eligibility checks pass")
        from py_clob_client_v2.clob_types import OrderArgsV2, OrderType, PartialCreateOrderOptions
        c = await asyncio.to_thread(self._ensure_clob)
        token = req.market.extra["yes_token" if req.outcome is Outcome.YES else "no_token"]
        args = OrderArgsV2(token_id=token, price=float(req.limit_price), size=float(req.size),
                           side="BUY" if req.side is Side.BUY else "SELL")
        opts = PartialCreateOrderOptions(tick_size=str(req.market.tick_size.normalize()),
                                         neg_risk=bool(req.market.extra.get("neg_risk")))
        # signing is local and safe to fail; only the post is a write with an uncertain outcome
        try:
            signed = await asyncio.to_thread(c.create_order, args, opts)
        except Exception as e:
            raise ExchangeError(f"polymarket: could not build order: {e}") from e
        resp = await self._sdk(c.post_order, signed, OrderType.FOK if req.tif is TIF.FOK else OrderType.FAK,
                               write=True)
        order_id = resp.get("orderID") or resp.get("orderId") or ""
        if not resp.get("success", bool(order_id)):
            return OrderResult(OrderStatus.REJECTED, order_id=order_id, message=str(resp.get("errorMsg"))[:200], raw=resp)
        status = str(resp.get("status", "")).lower()
        making, taking = D(resp.get("makingAmount")), D(resp.get("takingAmount"))
        if status == "delayed" or (status == "matched" and (making <= 0 or taking <= 0)):
            return await self._await_order(order_id, req)
        if status != "matched":
            return OrderResult(OrderStatus.UNFILLED, order_id=order_id, message=status, raw=resp)
        shares, cash = (taking, making) if req.side is Side.BUY else (making, taking)
        avg = cash / shares
        st = OrderStatus.FILLED if shares >= req.size else OrderStatus.PARTIAL
        return OrderResult(st, order_id, shares, avg, estimate_fee(req.market.fee, avg, shares), raw=resp)

    async def _await_order(self, order_id: str, req: OrderRequest) -> OrderResult:
        last: OrderResult | None = None
        for delay in (0.3, 0.6, 1.0, 2.0, 3.0, 4.0):
            await asyncio.sleep(delay)
            try:
                last = await self.get_order(order_id, req.market)
            except ExchangeError:
                continue
            if last.raw.get("status", "").upper() in ("MATCHED", "CANCELED", "CANCELLED", "UNMATCHED"):
                last.status = (OrderStatus.UNFILLED if last.filled_size <= 0 else
                               OrderStatus.FILLED if last.filled_size >= req.size else OrderStatus.PARTIAL)
                return last
        raise ExchangeUnavailable(f"polymarket order {order_id} did not reach a terminal state")

    async def get_order(self, order_id: str, market: Market) -> OrderResult:
        c = await asyncio.to_thread(self._ensure_clob)
        o = await self._sdk(c.get_order, order_id) or {}
        filled, price = D(o.get("size_matched")), D(o.get("price"))
        status = OrderStatus.UNFILLED if filled <= 0 else (
            OrderStatus.FILLED if filled >= D(o.get("original_size")) else OrderStatus.PARTIAL)
        return OrderResult(status, order_id, filled, price, estimate_fee(market.fee, price, filled), raw=o)

    async def cancel_order(self, order_id: str, market: Market) -> None:
        from py_clob_client_v2.clob_types import OrderPayload
        c = await asyncio.to_thread(self._ensure_clob)
        await self._sdk(c.cancel_order, OrderPayload(orderID=order_id), write=True)
