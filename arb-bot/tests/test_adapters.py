"""Exchange adapters against recorded-shape responses (no network)."""
import base64
import json
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from arbbot.config.settings import Settings
from arbbot.core.types import Exchange, OrderRequest, OrderStatus, Outcome, Resolution, Side, TIF
from arbbot.exchanges.base import ExchangeUnavailable, TradingDisabled
from arbbot.exchanges.kalshi.client import KalshiAdapter, book_from_fp
from arbbot.exchanges.polymarket.intl import PolymarketIntlAdapter
from arbbot.exchanges.polymarket.us import PolymarketUSAdapter

D = Decimal
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()

EVENT = {"event_ticker": "KXBTC-26DEC31", "series_ticker": "KXBTC", "title": "Bitcoin price on Dec 31, 2026",
         "sub_title": "Dec 31", "category": "Crypto",
         "settlement_sources": [{"name": "CF Benchmarks", "url": "https://www.cfbenchmarks.com"}],
         "markets": [{"ticker": "KXBTC-26DEC31-T100000", "event_ticker": "KXBTC-26DEC31", "market_type": "binary",
                      "title": "Will Bitcoin be above $100,000 on Dec 31, 2026?", "yes_sub_title": "Above $100,000",
                      "status": "active", "close_time": "2026-12-31T22:00:00Z",
                      "latest_expiration_time": "2027-01-07T22:00:00Z", "can_close_early": False,
                      "rules_primary": "If BRTI is above 100000 at 5pm EST...", "rules_secondary": "",
                      "volume_fp": "5210.00", "open_interest_fp": "900.00",
                      "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}]},
                     {"ticker": "KXMVE-1", "market_type": "binary", "mve_collection_ticker": "X", "status": "active",
                      "close_time": "2026-12-31T22:00:00Z"}]}


def kalshi(handler, **kw):
    s = Settings(_env_file=None, kalshi_api_key_id="key-1", kalshi_private_key_pem=PEM,
                 kalshi_eligibility_confirmed=True, discovery_max_days_to_close=4000, **kw)
    return KalshiAdapter(s, transport=httpx.MockTransport(handler))


def test_kalshi_book_bids_only_become_two_sided():
    b = book_from_fp("T", {"yes_dollars": [["0.4000", "100.00"], ["0.4200", "50.00"]],
                           "no_dollars": [["0.5500", "80.00"]]})
    assert b.best_bid(Outcome.YES) == D("0.42") and b.best_ask(Outcome.YES) == D("0.45")   # 1 - best NO bid
    assert b.best_ask(Outcome.NO) == D("0.58") and b.best_bid(Outcome.NO) == D("0.55")
    assert b.yes_asks[0].size == 80


async def test_kalshi_discovery_orders_signing_and_resolution():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        path = req.url.path.removeprefix("/trade-api/v2")
        if path == "/series":
            return httpx.Response(200, json={"series": [{"ticker": "KXBTC", "fee_type": "quadratic", "fee_multiplier": 1}]})
        if path == "/events":
            return httpx.Response(200, json={"events": [EVENT], "cursor": ""})
        if path == "/exchange/status":
            return httpx.Response(200, json={"exchange_active": True, "trading_active": True})
        if path == "/portfolio/balance":
            return httpx.Response(200, json={"balance": 12345, "balance_dollars": "123.4500"})
        if path == "/portfolio/orders" and req.method == "GET":
            return httpx.Response(200, json={"orders": []})
        if path == "/portfolio/events/orders":
            return httpx.Response(201, json={"order_id": "o-1", "fill_count": "10.00", "remaining_count": "0.00",
                                             "average_fill_price": "0.4500", "ts_ms": 1})
        if path == "/portfolio/orders/o-1":
            return httpx.Response(200, json={"order": {
                "order_id": "o-1", "fill_count_fp": "10.00", "initial_count_fp": "10.00", "yes_price_dollars": "0.4500",
                "taker_fill_cost_dollars": "5.5000", "maker_fill_cost_dollars": "0", "taker_fees_dollars": "0.1800",
                "maker_fees_dollars": "0", "outcome_side": "no"}})
        if path == "/portfolio/positions":
            return httpx.Response(200, json={"market_positions": [
                {"ticker": "A", "position_fp": "12.00"}, {"ticker": "B", "position_fp": "-7.00"}], "cursor": ""})
        if path.startswith("/markets/"):
            return httpx.Response(200, json={"market": {"status": "finalized", "result": "yes"}})
        return httpx.Response(404, json={"error": path})

    k = kalshi(handler)
    markets = await k.list_markets()
    assert [m.market_id for m in markets] == ["KXBTC-26DEC31-T100000"]      # multivariate market skipped
    m = markets[0]
    assert m.fee.kind == "kalshi_quadratic" and m.fee.rate == D("0.07")
    assert "CF Benchmarks" in m.resolution_source and m.yes_label == "Above $100,000"
    assert m.close_time.tzinfo is not None

    req = OrderRequest(Exchange.KALSHI, m, Outcome.NO, Side.BUY, D("0.55"), D(10), TIF.IOC, "exec-1")
    with pytest.raises(TradingDisabled):
        await k.place_order(req)                      # nothing trades before eligibility passes
    rep = await k.check_eligibility()
    assert rep.ok, [(c.name, c.detail) for c in rep.checks if not c.ok]
    res = await k.place_order(req)
    post = next(r for r in seen if r.method == "POST")
    body = json.loads(post.content)
    # buying NO at 0.55 is an ask on the YES book at 0.45
    assert body == {"ticker": m.market_id, "client_order_id": "exec-1", "side": "ask", "count": "10.00",
                    "price": "0.4500", "time_in_force": "immediate_or_cancel",
                    "self_trade_prevention_type": "taker_at_cross"}
    assert res.status is OrderStatus.FILLED and res.filled_size == 10
    assert res.avg_price == D("0.55") and res.fee == D("0.18")
    # signature: RSA-PSS over timestamp + method + full path
    ts, sig = post.headers["KALSHI-ACCESS-TIMESTAMP"], base64.b64decode(post.headers["KALSHI-ACCESS-SIGNATURE"])
    KEY.public_key().verify(sig, (ts + "POST" + "/trade-api/v2/portfolio/events/orders").encode(),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())
    assert post.headers["KALSHI-ACCESS-KEY"] == "key-1"
    assert (await k.get_balance()).available == D("123.45")
    assert await k.get_positions() == {("A", Outcome.YES): D(12), ("B", Outcome.NO): D(7)}
    assert await k.get_resolution(m) == (Resolution.YES, D(1))
    await k.close()


async def test_kalshi_write_failures_are_unknown_not_retried_and_attestation_gates_trading():
    posts = []

    def handler(req):
        if req.method == "POST":
            posts.append(req)
            return httpx.Response(503, text="unavailable")
        if req.url.path.endswith("/exchange/status"):
            return httpx.Response(200, json={"exchange_active": True, "trading_active": True})
        return httpx.Response(200, json={"balance": 100, "orders": []})

    k = kalshi(handler)
    await k.check_eligibility()
    from tests.conftest import kalshi_market
    req = OrderRequest(Exchange.KALSHI, kalshi_market(), Outcome.YES, Side.BUY, D("0.40"), D(5), TIF.FOK, "c")
    with pytest.raises(ExchangeUnavailable):
        await k.place_order(req)
    assert len(posts) == 1                              # an order POST is never retried
    assert json.loads(posts[0].content)["time_in_force"] == "fill_or_kill"
    await k.close()

    no_attest = KalshiAdapter(Settings(_env_file=None, kalshi_api_key_id="k", kalshi_private_key_pem=PEM,
                                       kalshi_eligibility_confirmed=False), transport=httpx.MockTransport(handler))
    rep = await no_attest.check_eligibility()
    assert not rep.ok and not no_attest.trading_enabled
    await no_attest.close()


class FakeUS:
    """Shape-compatible stand-in for polymarket_us.AsyncPolymarketUS."""

    def __init__(self):
        self.created = []
        outer = self

        class Markets:
            async def list(self, params=None):
                # shape captured from the live gateway
                return {"markets": [{
                    "id": "7974", "slug": "btc-100k", "question": "Bitcoin above $100,000 on Dec 31, 2026?",
                    "title": "Bitcoin above $100,000 on Dec 31, 2026?", "subtitle": "", "outcomes": "[\"No\",\"Yes\"]",
                    "description": "Resolves per CF Benchmarks", "active": True, "closed": False, "archived": False,
                    "status": "MARKET_STATUS_OPEN", "endDate": "2026-12-31T22:00:00Z", "category": "crypto",
                    "feeCoefficient": 0.095, "minimumTradeQty": 1, "orderPriceMinTickSize": 0.001,
                    "marketType": "futures", "marketSides": [{"description": "Yes", "long": True},
                                                             {"description": "No", "long": False}]}]}

            async def book(self, slug):
                return {"marketData": {"marketSlug": slug, "state": "MARKET_STATE_OPEN",
                                       "bids": [{"px": {"value": "0.48", "currency": "USD"}, "qty": "120"}],
                                       "offers": [{"px": {"value": "0.51", "currency": "USD"}, "qty": "90"}]}}

            async def settlement(self, slug):
                return {"slug": slug, "settlement": 0.0}

        class Orders:
            async def create(self, params):
                outer.created.append(params)
                return {"id": "us-1"}

            async def retrieve(self, oid):
                return {"order": {"id": oid, "state": "ORDER_STATE_FILLED", "cumQuantity": 20, "quantity": 20,
                                  "intent": "ORDER_INTENT_BUY_SHORT", "avgPx": {"value": "0.48", "currency": "USD"},
                                  "commissionNotionalTotalCollected": {"value": "0.03", "currency": "USD"},
                                  "commissionsBasisPoints": "30"}}

            async def list(self, params=None):
                return {"orders": []}

        class Account:
            async def balances(self):
                return {"balances": [{"currentBalance": 500.0, "buyingPower": 450.0}]}

        class Portfolio:
            async def positions(self, params=None):
                return {"positions": {"btc-100k": {"netPosition": "-20"}}, "eof": True}

        self.markets, self.orders = Markets(), Orders()
        self.account, self.portfolio = Account(), Portfolio()

    async def close(self):
        pass


async def test_polymarket_us_adapter():
    fake = FakeUS()
    s = Settings(_env_file=None, polymarket_us_key_id="k", polymarket_us_secret_key="c2VjcmV0",
                 polymarket_eligibility_confirmed=True, discovery_max_days_to_close=4000)
    us = PolymarketUSAdapter(s, client=fake)
    m = (await us.list_markets())[0]
    assert m.market_id == "btc-100k" and m.close_time.year == 2026 and m.exchange is Exchange.POLYMARKET
    assert m.fee.kind == "poly_exponent" and m.fee.rate == D("0.095") and m.tick_size == D("0.001")
    assert m.yes_label == "Yes" and m.status == "open"
    team = us._market({"slug": "mls-vwh", "question": "Major League Soccer Champion", "title": "Vancouver Whitecaps FC",
                       "endDate": "2027-01-01T21:20:09Z", "status": "MARKET_STATUS_OPEN",
                       "marketSides": [{"description": "Yes", "long": True}, {"description": "No", "long": False}]})
    assert team.title == "Major League Soccer Champion: Vancouver Whitecaps FC"
    assert team.yes_label == "Vancouver Whitecaps FC"
    book = await us.get_orderbook(m)
    assert book.best_ask(Outcome.YES) == D("0.51") and book.best_ask(Outcome.NO) == D("0.52")   # 1 - YES bid
    assert (await us.check_eligibility()).ok
    res = await us.place_order(OrderRequest(Exchange.POLYMARKET, m, Outcome.NO, Side.BUY, D("0.52"), D(20),
                                            TIF.IOC, "c1"))
    sent = fake.created[0]
    assert sent["intent"] == "ORDER_INTENT_BUY_SHORT" and sent["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
    assert sent["price"] == {"value": "0.48", "currency": "USD"}     # short orders are quoted at the long price
    assert sent["quantity"] == 20 and sent["manualOrderIndicator"] == "MANUAL_ORDER_INDICATOR_AUTOMATIC"
    assert res.status is OrderStatus.FILLED and res.avg_price == D("0.52") and res.fee == D("0.03")
    assert us.fee_rate == D("0.003")                                 # learned the venue's real fee rate
    assert await us.get_positions() == {("btc-100k", Outcome.NO): D(20)}
    assert (await us.get_balance()).available == D("450.0")
    assert await us.get_resolution(m) == (Resolution.NO, D(0))


async def test_polymarket_intl_geoblock_disables_trading_and_market_data_still_works():
    def handler(req: httpx.Request) -> httpx.Response:
        if "geoblock" in req.url.path:
            return httpx.Response(200, json={"blocked": True, "country": "US", "region": "MI"})
        if req.url.path == "/time":
            return httpx.Response(200, json=1)
        if req.url.path == "/markets":
            return httpx.Response(200, json=[{
                "conditionId": "0xabc", "question": "Bitcoin above $100,000 on December 31, 2026?", "slug": "btc",
                "description": "Resolves per Binance.", "endDate": "2026-12-31T22:00:00Z", "outcomes": "[\"Yes\", \"No\"]",
                "clobTokenIds": "[\"111\", \"222\"]", "active": True, "closed": False, "enableOrderBook": True,
                "acceptingOrders": True, "orderPriceMinTickSize": 0.01, "orderMinSize": 5, "volumeNum": 1000,
                "outcomePrices": "[\"0.5\", \"0.5\"]"}])
        if req.url.path == "/books":
            return httpx.Response(200, json=[
                {"asset_id": "111", "bids": [{"price": "0.47", "size": "100"}], "asks": [{"price": "0.49", "size": "60"}]},
                {"asset_id": "222", "bids": [{"price": "0.50", "size": "40"}], "asks": [{"price": "0.53", "size": "70"}]}])
        if req.url.path.startswith("/clob-markets/"):
            return httpx.Response(200, json={"t": [{"t": "111"}, {"t": "222"}], "mts": 0.01, "mos": 5,
                                             "fd": {"r": 0.04, "e": 1}})
        return httpx.Response(404)

    s = Settings(_env_file=None, polymarket_venue="intl", polymarket_eligibility_confirmed=True,
                 polymarket_private_key="0x" + "11" * 32, discovery_max_days_to_close=4000)
    ad = PolymarketIntlAdapter(s, transport=httpx.MockTransport(handler))
    rep = await ad.check_eligibility()
    geo = next(c for c in rep.checks if "geoblock" in c.name)
    assert not geo.ok and "US" in geo.detail and not rep.ok and not ad.trading_enabled
    m = (await ad.list_markets())[0]
    assert m.extra["yes_token"] == "111" and m.fee.rate == D("0.10")      # conservative until enriched
    m = await ad.enrich_market(m)
    assert m.fee.rate == D("0.04") and m.min_order_size == 5
    book = await ad.get_orderbook(m)
    assert book.best_ask(Outcome.YES) == D("0.49") and book.best_ask(Outcome.NO) == D("0.53")
    with pytest.raises(TradingDisabled):
        await ad.place_order(OrderRequest(Exchange.POLYMARKET, m, Outcome.YES, Side.BUY, D("0.49"), D(10),
                                          TIF.IOC, "x"))
    await ad.close()
