"""Kalshi adapter (official Trade API v2, fixed-point dollar fields, V2 order endpoints).

Field names and paths follow Kalshi's published OpenAPI client
(kalshi-python-async 3.36): signing = RSA-PSS/Ed25519 over
timestamp_ms + METHOD + path; orders via POST /portfolio/events/orders with a
single YES-quoted book side ("bid" = buy YES, "ask" = sell YES = buy NO at 1-p).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ...config.settings import Settings
from ...infra.ratelimit import RateLimiter
from ...core.types import (CENT, ONE, ZERO, Balance, D, EligibilityReport, Exchange, FeeSpec, Level, Market,
                           OrderBook, OrderRequest, OrderResult, OrderStatus, Outcome, Resolution, Side, TIF)
from ..base import BookCallback, ExchangeAdapter, ExchangeError, ExchangeUnavailable, TradingDisabled

log = logging.getLogger(__name__)

URLS = {
    "prod": ("https://external-api.kalshi.com/trade-api/v2", "wss://api.elections.kalshi.com/trade-api/ws/v2"),
    "demo": ("https://external-api.demo.kalshi.co/trade-api/v2", "wss://demo-api.kalshi.co/trade-api/ws/v2"),
}
TAKER_RATE = Decimal("0.07")


def parse_ts(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def book_from_fp(market_id: str, fp: dict, source: str = "rest") -> OrderBook:
    """Kalshi publishes bids only: yes_dollars = YES bids, no_dollars = NO bids.
    A NO bid at p is a YES ask at 1-p."""
    def levels(rows: Any) -> list[Level]:
        return [Level(D(r[0]), D(r[1])) for r in rows or []]

    yes_bids, no_bids = levels(fp.get("yes_dollars")), levels(fp.get("no_dollars"))
    return OrderBook(
        Exchange.KALSHI, market_id, yes_bids=yes_bids, no_bids=no_bids,
        yes_asks=[Level(ONE - x.price, x.size) for x in no_bids],
        no_asks=[Level(ONE - x.price, x.size) for x in yes_bids], source=source,
    ).normalize()


class KalshiAdapter(ExchangeAdapter):
    name = Exchange.KALSHI
    supports_streaming = True

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        base, ws = URLS.get(settings.kalshi_env, URLS["prod"])
        self.base_url = (settings.kalshi_base_url or base).rstrip("/")
        self.ws_url = settings.kalshi_ws_url or ws
        self._path_prefix = urlparse(self.base_url).path
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=10.0, transport=transport)
        self._key = None
        pem = settings.kalshi_private_key()
        if pem:
            self._key = serialization.load_pem_private_key(pem.encode(), password=None)
        self.trading_enabled = False
        # Kalshi's basic tier allows 20 reads/s; stay well under it by default
        self._read_limiter = RateLimiter(settings.kalshi_reads_per_second, burst=5)
        self._series_fees: dict[str, tuple[str, Decimal]] = {}

    async def close(self) -> None:
        await self._http.aclose()

    # -- transport -------------------------------------------------------------
    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if not self._key or not self.s.kalshi_api_key_id:
            raise TradingDisabled("Kalshi API credentials are not configured")
        ts = str(int(time.time() * 1000))
        msg = (ts + method.upper() + path.split("?")[0]).encode()
        if isinstance(self._key, Ed25519PrivateKey):
            sig = self._key.sign(msg)
        else:
            sig = self._key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                  salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.s.kalshi_api_key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}

    async def _request(self, method: str, path: str, *, params: Any = None, body: dict | None = None,
                       auth: bool = False, retries: int = 5) -> dict:
        """GETs are retried on transport errors and 429/5xx with backoff. Writes are never
        retried: an unconfirmed write is reported as ExchangeUnavailable for the caller to resolve.
        Writes skip the read pacer so an order is never queued behind a discovery crawl."""
        headers = self._auth_headers(method, self._path_prefix + path) if auth else {}
        attempt = 0
        while True:
            if method == "GET":
                await self._read_limiter.acquire()
            try:
                r = await self._http.request(method, path, params=params, json=body, headers=headers)
            except httpx.HTTPError as e:
                if method == "GET" and attempt < retries:
                    attempt += 1
                    await asyncio.sleep(0.3 * attempt)
                    continue
                raise ExchangeUnavailable(f"kalshi {method} {path}: {e}") from e
            if r.status_code == 429 or r.status_code >= 500:
                if method == "GET" and attempt < retries:
                    attempt += 1
                    wait = float(r.headers.get("retry-after") or min(2 ** attempt * 0.5, 20.0))
                    if r.status_code == 429:
                        self._read_limiter.penalize(wait)
                    await asyncio.sleep(wait)
                    if auth:
                        headers = self._auth_headers(method, self._path_prefix + path)
                    continue
                raise ExchangeUnavailable(f"kalshi {method} {path}: HTTP {r.status_code} {r.text[:200]}")
            if r.status_code >= 400:
                raise ExchangeError(f"kalshi {method} {path}: HTTP {r.status_code} {r.text[:300]}")
            if method == "GET":
                self._read_limiter.success()
            return r.json() if r.content else {}

    # -- eligibility / health ----------------------------------------------------
    async def check_eligibility(self) -> EligibilityReport:
        rep = EligibilityReport(self.name)
        rep.add("adapter enabled", self.s.kalshi_enabled)
        rep.add("operator attested jurisdiction, account type and API agreement",
                self.s.kalshi_eligibility_confirmed, "set KALSHI_ELIGIBILITY_CONFIRMED=true once verified")
        rep.add("API credentials configured", bool(self._key and self.s.kalshi_api_key_id))
        try:
            st = await self._request("GET", "/exchange/status")
            rep.add("exchange active", bool(st.get("exchange_active")), json.dumps(st)[:120])
            rep.add("trading active", bool(st.get("trading_active")))
        except ExchangeError as e:
            rep.add("exchange reachable", False, str(e)[:160])
        if self._key and self.s.kalshi_api_key_id:
            try:
                bal = await self.get_balance()
                rep.add("authenticated account access", True, f"balance ${bal.available}")
                await self._request("GET", "/portfolio/orders", params={"status": "resting", "limit": 1}, auth=True)
                rep.add("order API permission", True)
            except ExchangeError as e:
                rep.add("authenticated account access", False, str(e)[:160])
        self.trading_enabled = rep.ok
        return rep

    async def health(self) -> bool:
        try:
            st = await self._request("GET", "/exchange/status", retries=0)
            return bool(st.get("exchange_active", True))
        except ExchangeError:
            return False

    # -- discovery ---------------------------------------------------------------
    async def _load_series_fees(self) -> None:
        try:
            data = await self._request("GET", "/series")
            for s in data.get("series") or []:
                self._series_fees[s["ticker"]] = (s.get("fee_type") or "quadratic", D(s.get("fee_multiplier") or 1))
        except ExchangeError as e:
            log.warning("could not load Kalshi series fees, using the general schedule: %s", e)

    def _fee(self, event: dict) -> FeeSpec | None:
        """None = fee structure this bot cannot price ('flat' table): the market is skipped."""
        ftype, mult = self._series_fees.get(event.get("series_ticker", ""), ("quadratic", ONE))
        if event.get("fee_type_override"):
            ftype, mult = event["fee_type_override"], D(event.get("fee_multiplier_override") or 1)
        if not str(ftype).startswith("quadratic"):
            return None
        return FeeSpec("kalshi_quadratic", TAKER_RATE * mult)

    def _market(self, m: dict, event: dict) -> Market | None:
        if m.get("market_type", "binary") != "binary" or m.get("mve_collection_ticker"):
            return None
        fee = self._fee(event)
        if fee is None:
            return None
        steps = [D(r["step"]) for r in m.get("price_ranges") or [] if r.get("step")]
        sources = event.get("settlement_sources") or []
        title = m.get("title") or event.get("title") or ""
        yes_label = m.get("yes_sub_title") or "Yes"
        status = "open" if m.get("status") == "active" else str(m.get("status", ""))
        return Market(
            Exchange.KALSHI, m["ticker"], title, yes_label=yes_label,
            no_label="No" if yes_label == "Yes" else f"Not {yes_label}",
            # market titles often omit the event ("Full Game: Over 215.5 points"); keep it for matching
            subtitle=" | ".join(x for x in (event.get("title") if event.get("title") and event.get("title") not in title
                                             else "", event.get("sub_title") or m.get("subtitle") or "") if x),
            rules=f"{m.get('rules_primary', '')}\n{m.get('rules_secondary', '')}".strip(),
            resolution_source="; ".join(f"{s.get('name', '')} {s.get('url', '')}".strip() for s in sources),
            category=event.get("category") or "", event_id=m.get("event_ticker") or event.get("event_ticker", ""),
            close_time=parse_ts(m.get("close_time")),
            expiration_time=parse_ts(m.get("expected_expiration_time") or m.get("latest_expiration_time")
                                     or m.get("expiration_time")),
            status=status, can_close_early=bool(m.get("can_close_early")),
            tick_size=min(steps) if steps else CENT, fee=fee, volume=D(m.get("volume_fp")),
            liquidity=D(m.get("open_interest_fp")), url=f"https://kalshi.com/markets/{m['ticker']}",
            extra={"series_ticker": event.get("series_ticker", ""), "strike_type": m.get("strike_type"),
                   "floor_strike": m.get("floor_strike"), "cap_strike": m.get("cap_strike"),
                   "early_close_condition": m.get("early_close_condition")})

    async def list_markets(self) -> list[Market]:
        if not self._series_fees:
            await self._load_series_fees()
        horizon = datetime.now(timezone.utc) + timedelta(days=self.s.discovery_max_days_to_close)
        out: list[Market] = []
        cursor = ""
        for _ in range(500):
            params: dict[str, Any] = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = await self._request("GET", "/events", params=params)
            for ev in data.get("events") or []:
                for m in ev.get("markets") or []:
                    mk = self._market(m, ev)
                    if mk and mk.status == "open" and mk.close_time and mk.close_time <= horizon \
                            and float(mk.volume) >= self.s.discovery_min_volume:
                        out.append(mk)
            cursor = data.get("cursor") or ""
            if not cursor:
                break
        return out

    async def get_market(self, market_id: str) -> Market | None:
        try:
            m = (await self._request("GET", f"/markets/{market_id}")).get("market")
            if not m:
                return None
            ev = (await self._request("GET", f"/events/{m['event_ticker']}")).get("event") or {}
        except ExchangeUnavailable:
            raise
        except ExchangeError:
            return None
        if not self._series_fees:
            await self._load_series_fees()
        return self._market(m, ev)

    # -- order books -------------------------------------------------------------
    async def get_orderbook(self, market: Market) -> OrderBook:
        data = await self._request("GET", f"/markets/{market.market_id}/orderbook")
        return book_from_fp(market.market_id, data.get("orderbook_fp") or {})

    async def get_orderbooks(self, markets: list[Market]) -> dict[str, OrderBook]:
        out: dict[str, OrderBook] = {}
        ids = [m.market_id for m in markets]
        try:
            for i in range(0, len(ids), 50):
                data = await self._request("GET", "/markets/orderbooks",
                                           params=[("tickers", t) for t in ids[i:i + 50]])
                for ob in data.get("orderbooks") or []:
                    out[ob["ticker"]] = book_from_fp(ob["ticker"], ob.get("orderbook_fp") or {})
        except ExchangeError as e:
            if isinstance(e, ExchangeUnavailable):
                raise
            log.debug("batch orderbooks rejected (%s); falling back to single requests", e)
        for m in markets:
            if m.market_id not in out:
                out[m.market_id] = await self.get_orderbook(m)
        return out

    async def stream_orderbooks(self, markets: list[Market], on_book: BookCallback) -> None:
        """orderbook_delta channel: one snapshot per market, then per-level deltas.
        Sequence gaps force a resubscribe (by returning; the feed reconnects)."""
        import websockets

        headers = self._auth_headers("GET", urlparse(self.ws_url).path)
        books: dict[str, dict[str, dict[Decimal, Decimal]]] = {}
        seqs: dict[int, int] = {}

        def price(msg: dict) -> Decimal:
            if msg.get("price_dollars") is not None:
                return D(msg["price_dollars"])
            return D(msg["price"]) / 100

        def side_levels(msg: dict, side: str) -> dict[Decimal, Decimal]:
            for key, cents in ((f"{side}_dollars_fp", False), (f"{side}_dollars", False), (side, True)):
                if msg.get(key) is not None:
                    return {(D(p) / 100 if cents else D(p)): D(q) for p, q in msg[key]}
            return {}

        async def emit(ticker: str) -> None:
            b = books[ticker]
            await on_book(book_from_fp(ticker, {
                "yes_dollars": [[p, q] for p, q in b["yes"].items() if q > 0],
                "no_dollars": [[p, q] for p, q in b["no"].items() if q > 0]}, source="ws"))

        async with websockets.connect(self.ws_url, additional_headers=headers, ping_interval=10) as ws:
            await ws.send(json.dumps({"id": 1, "cmd": "subscribe", "params": {
                "channels": ["orderbook_delta"], "market_tickers": [m.market_id for m in markets]}}))
            async for raw in ws:
                data = json.loads(raw)
                typ, msg = data.get("type"), data.get("msg") or {}
                sid, seq = data.get("sid"), data.get("seq")
                if sid is not None and seq is not None:
                    if sid in seqs and seq != seqs[sid] + 1:
                        raise ExchangeUnavailable("kalshi ws sequence gap")
                    seqs[sid] = seq
                ticker = msg.get("market_ticker")
                if typ == "orderbook_snapshot" and ticker:
                    books[ticker] = {"yes": side_levels(msg, "yes"), "no": side_levels(msg, "no")}
                    await emit(ticker)
                elif typ == "orderbook_delta" and ticker in books:
                    lv = books[ticker][msg["side"]]
                    p = price(msg)
                    delta = D(msg["delta_fp"]) if msg.get("delta_fp") is not None else D(msg.get("delta"))
                    lv[p] = lv.get(p, ZERO) + delta
                    await emit(ticker)
                elif typ == "error":
                    raise ExchangeUnavailable(f"kalshi ws error: {msg}")

    async def get_resolution(self, market: Market) -> tuple[Resolution, Decimal] | None:
        m = (await self._request("GET", f"/markets/{market.market_id}")).get("market") or {}
        if m.get("status") != "finalized":
            return None            # 'determined' can still be disputed/amended; wait for final settlement
        result = m.get("result")
        if result == "yes":
            return Resolution.YES, ONE
        if result == "no":
            return Resolution.NO, ZERO
        return Resolution.VOID, D(m.get("settlement_value_dollars") or "0.5")

    # -- account -----------------------------------------------------------------
    async def get_balance(self) -> Balance:
        data = await self._request("GET", "/portfolio/balance", auth=True)
        avail = D(data.get("balance_dollars")) if data.get("balance_dollars") is not None \
            else D(data.get("balance", 0)) / 100
        return Balance(avail, avail)

    async def get_positions(self) -> dict[tuple[str, Outcome], Decimal]:
        out: dict[tuple[str, Outcome], Decimal] = {}
        cursor = ""
        for _ in range(50):
            params: dict[str, Any] = {"count_filter": "position", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = await self._request("GET", "/portfolio/positions", params=params, auth=True)
            for p in data.get("market_positions") or []:
                qty = D(p.get("position_fp"))
                if qty > 0:
                    out[(p["ticker"], Outcome.YES)] = qty
                elif qty < 0:
                    out[(p["ticker"], Outcome.NO)] = -qty
            cursor = data.get("cursor") or ""
            if not cursor:
                break
        return out

    async def get_open_orders(self) -> list[dict]:
        data = await self._request("GET", "/portfolio/orders", params={"status": "resting", "limit": 200}, auth=True)
        return [{"order_id": o["order_id"], "market_id": o.get("ticker", "")} for o in data.get("orders") or []]

    # -- trading -----------------------------------------------------------------
    def _result_from_order(self, o: dict, req_side: Side, outcome: Outcome, size: Decimal) -> OrderResult:
        filled = D(o.get("fill_count_fp"))
        cost = D(o.get("taker_fill_cost_dollars")) + D(o.get("maker_fill_cost_dollars"))
        fee = D(o.get("taker_fees_dollars")) + D(o.get("maker_fees_dollars"))
        if filled <= 0:
            return OrderResult(OrderStatus.UNFILLED, order_id=o.get("order_id", ""), raw=o)
        yes_avg = D(o.get("yes_price_dollars"))
        avg = cost / filled if cost > 0 else (yes_avg if outcome is Outcome.YES else ONE - yes_avg)
        status = OrderStatus.FILLED if filled >= size else OrderStatus.PARTIAL
        return OrderResult(status, o.get("order_id", ""), filled, avg, fee, raw=o)

    async def place_order(self, req: OrderRequest) -> OrderResult:
        if not self.trading_enabled:
            raise TradingDisabled("Kalshi trading is disabled until eligibility checks pass")
        buying_yes = (req.side is Side.BUY) == (req.outcome is Outcome.YES)   # buy YES or sell NO
        yes_price = req.limit_price if req.outcome is Outcome.YES else ONE - req.limit_price
        body = {
            "ticker": req.market.market_id, "client_order_id": req.client_order_id,
            "side": "bid" if buying_yes else "ask", "count": f"{req.size:.2f}", "price": f"{yes_price:.4f}",
            "time_in_force": "fill_or_kill" if req.tif is TIF.FOK else "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
        }
        if req.side is Side.SELL:
            body["reduce_only"] = True
        data = await self._request("POST", "/portfolio/events/orders", body=body, auth=True)
        order_id = data.get("order_id", "")
        filled = D(data.get("fill_count"))
        if filled <= 0:
            return OrderResult(OrderStatus.UNFILLED, order_id=order_id, raw=data)
        # authoritative cost and fees come from the order record
        try:
            return await self.get_order(order_id, req.market, req=req)
        except ExchangeError as e:
            log.warning("kalshi order %s filled but detail lookup failed (%s); using create response", order_id, e)
            yes_avg = D(data.get("average_fill_price")) or yes_price
            avg = yes_avg if req.outcome is Outcome.YES else ONE - yes_avg
            from ...core.fees import estimate_fee
            return OrderResult(OrderStatus.FILLED if filled >= req.size else OrderStatus.PARTIAL, order_id, filled,
                               avg, estimate_fee(req.market.fee, avg, filled), "fee estimated", raw=data)

    async def get_order(self, order_id: str, market: Market, req: OrderRequest | None = None) -> OrderResult:
        o = (await self._request("GET", f"/portfolio/orders/{order_id}", auth=True)).get("order") or {}
        outcome = req.outcome if req else Outcome(o.get("outcome_side") or "yes")
        side = req.side if req else Side.BUY
        size = req.size if req else D(o.get("initial_count_fp"))
        res = self._result_from_order(o, side, outcome, size)
        if req and req.side is Side.SELL and res.filled_size > 0:
            # fill cost is quoted for the taker's outcome side; a sell receives the complement of what
            # the opposite side paid, i.e. the YES-quoted price for YES sells and 1 - it for NO sells.
            yes_avg = D(o.get("yes_price_dollars"))
            if yes_avg > 0:
                res.avg_price = yes_avg if req.outcome is Outcome.YES else ONE - yes_avg
        return res

    async def cancel_order(self, order_id: str, market: Market) -> None:
        await self._request("DELETE", f"/portfolio/events/orders/{order_id}",
                            params={"market_ticker": market.market_id}, auth=True)
