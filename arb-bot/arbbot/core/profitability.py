"""Dynamic arbitrage profitability engine.

For one matched pair and one direction (buy outcome A on exchange X, buy the
complementary outcome on exchange Y) this walks both ask ladders and computes,
for every candidate size:

    net = settlement payout - acquisition cost - all trading costs - risk buffer

then derives the minimum return this particular opportunity must clear from
its quality (liquidity, staleness, volatility, leg-failure probability, time
to settlement) and picks the size with the highest net profit that clears it.

Nothing here looks at displayed "last" prices: only executable ask depth.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from ..config.runtime import RuntimeConfig
from .fees import estimate_fee
from .types import ONE, ZERO, Exchange, Level, Market, OrderBook, Outcome, floor_to

BPS = Decimal("0.0001")


@dataclass(slots=True)
class LegInput:
    market: Market
    outcome: Outcome
    book: OrderBook
    latency_s: float = 0.3
    vol_per_s: float = 0.0          # relative mid-price std-dev per second
    conversion_bps: float = 0.0     # currency conversion cost on this leg's capital

    @property
    def exchange(self) -> Exchange:
        return self.market.exchange


@dataclass(slots=True)
class LegQuote:
    exchange: Exchange
    market_id: str
    outcome: Outcome
    size: Decimal
    cost: Decimal            # sum(price*size) across swept levels
    vwap: Decimal
    best: Decimal            # top-of-book ask
    limit_price: Decimal     # worst level swept: the order's limit
    fee: Decimal
    depth_at_limit: Decimal  # contracts visible at or better than the limit
    fail_prob: float

    @property
    def capital(self) -> Decimal:
        return self.cost + self.fee


@dataclass(slots=True)
class Quote:
    size: Decimal
    a: LegQuote
    b: LegQuote
    payout: Decimal
    acquisition: Decimal
    fees: Decimal
    conversion: Decimal
    slippage_buffer: Decimal
    leg_risk_buffer: Decimal
    settlement_buffer: Decimal
    operational_buffer: Decimal
    capital_cost: Decimal
    fail_prob: float

    @property
    def trading_costs(self) -> Decimal:
        return self.fees + self.conversion

    @property
    def risk_buffer(self) -> Decimal:
        return (self.slippage_buffer + self.leg_risk_buffer + self.settlement_buffer
                + self.operational_buffer + self.capital_cost)

    @property
    def gross(self) -> Decimal:
        return self.payout - self.acquisition

    @property
    def expected_profit(self) -> Decimal:
        """Profit if both legs fill as quoted and the pair settles as a hedge."""
        return self.payout - self.acquisition - self.trading_costs

    @property
    def net(self) -> Decimal:
        """Risk-adjusted net profit: the number every decision is based on."""
        return self.expected_profit - self.risk_buffer

    @property
    def capital(self) -> Decimal:
        return self.acquisition + self.fees

    @property
    def roi(self) -> float:
        return float(self.net / self.capital) if self.capital > 0 else 0.0

    @property
    def expected_roi(self) -> float:
        return float(self.expected_profit / self.capital) if self.capital > 0 else 0.0


@dataclass(slots=True)
class Evaluation:
    accepted: bool
    reason: str
    quote: Quote | None = None
    required_roi: float = 0.0
    risk_score: float = 0.0
    days_to_settlement: float = 0.0
    components: dict[str, float] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Ranking key: risk-adjusted return per day of capital lock-up."""
        if not self.quote:
            return 0.0
        q = self.quote
        return q.roi * (1 - q.fail_prob) / max(self.days_to_settlement, 1.0) * 365


def sweep(asks: list[Level], size: Decimal) -> tuple[Decimal, Decimal, list[tuple[Decimal, Decimal]]]:
    """Cost of buying `size` from an ask ladder -> (cost, worst price, fills). size must be available."""
    remaining, cost, worst, fills = size, ZERO, ZERO, []
    for lv in asks:
        if remaining <= 0:
            break
        take = min(lv.size, remaining)
        cost += take * lv.price
        worst = lv.price
        fills.append((lv.price, take))
        remaining -= take
    if remaining > 0:
        raise ValueError("insufficient depth")
    return cost, worst, fills


def _days_to_settlement(a: Market, b: Market, now: datetime) -> float:
    times = [t for m in (a, b) for t in (m.expiration_time, m.close_time) if t]
    if not times:
        return 30.0
    latest = max(times)
    return max((latest - now).total_seconds() / 86400.0, 0.02)


class ProfitabilityEngine:
    def __init__(self, cfg: RuntimeConfig):
        self.cfg = cfg
        # EWMA of observed leg failures per exchange, fed by the execution engine.
        self._hist_fail: dict[Exchange, float] = {}
        self._hist_n: dict[Exchange, int] = {}

    # -- feedback from real executions -------------------------------------
    def record_leg_result(self, exchange: Exchange, failed: bool, alpha: float = 0.1) -> None:
        prev = self._hist_fail.get(exchange, self.cfg.base_leg_fail_prob)
        self._hist_fail[exchange] = prev * (1 - alpha) + (1.0 if failed else 0.0) * alpha
        self._hist_n[exchange] = self._hist_n.get(exchange, 0) + 1

    def historical_fail_prob(self, exchange: Exchange) -> float | None:
        return self._hist_fail.get(exchange) if self._hist_n.get(exchange, 0) >= 5 else None

    # -- per-leg quote --------------------------------------------------------
    def _leg_fail_prob(self, leg: LegInput, size: Decimal, depth: Decimal, edge: Decimal, now: float) -> float:
        cfg = self.cfg
        p = cfg.base_leg_fail_prob
        age_ms = max(leg.book.age(now), 0.0) * 1000
        p += 0.20 * min(age_ms / cfg.max_book_age_ms, 1.0)
        take = float(size / depth) if depth > 0 else 1.0
        p += 0.15 * take * take
        if leg.vol_per_s > 0:
            best = float(leg.book.best_ask(leg.outcome) or ONE)
            move = leg.vol_per_s * max(leg.latency_s, 0.05) ** 0.5 * best
            p += min(0.5, 0.25 * move / max(float(edge), 0.001))
        hist = self.historical_fail_prob(leg.exchange)
        if hist is not None:
            p = 0.5 * p + 0.5 * hist
        return min(max(p, 0.0), 1.0)

    def _leg_quote(self, leg: LegInput, size: Decimal, edge: Decimal, now: float) -> LegQuote:
        asks = leg.book.asks(leg.outcome)
        cost, limit, fills = sweep(asks, size)
        fee = sum((estimate_fee(leg.market.fee, p, s) for p, s in fills), ZERO)
        # the exchange charges per order, so also bound by the fee at the limit price
        fee = max(fee, estimate_fee(leg.market.fee, cost / size, size))
        depth = sum((lv.size for lv in asks if lv.price <= limit), ZERO)
        return LegQuote(
            leg.exchange, leg.market.market_id, leg.outcome, size, cost, cost / size, asks[0].price,
            limit, fee, depth, self._leg_fail_prob(leg, size, depth, edge, now),
        )

    def quote(self, a: LegInput, b: LegInput, size: Decimal, confidence: float,
              days: float, now: float | None = None) -> Quote:
        cfg = self.cfg
        now = now if now is not None else time.time()
        best_a, best_b = a.book.best_ask(a.outcome), b.book.best_ask(b.outcome)
        edge = max(ONE - (best_a or ONE) - (best_b or ONE), ZERO)
        qa, qb = self._leg_quote(a, size, edge, now), self._leg_quote(b, size, edge, now)
        acquisition = qa.cost + qb.cost
        fees = qa.fee + qb.fee
        capital = acquisition + fees
        conversion = (qa.capital * Decimal(str(a.conversion_bps)) + qb.capital * Decimal(str(b.conversion_bps))) * BPS

        # Slippage: quotes moving against us between observation and fill.
        slip = ZERO
        for leg, q in ((a, qa), (b, qb)):
            rel = cfg.base_slippage_bps / 1e4 + leg.vol_per_s * max(leg.latency_s, 0.0) ** 0.5
            rel += cfg.base_slippage_bps / 1e4 * min(max(leg.book.age(now), 0.0) * 1000 / cfg.max_book_age_ms, 1.0)
            slip += q.cost * Decimal(str(round(rel, 8)))

        # Leg risk: probability one leg fails times the cost of unwinding the other.
        fail = 1 - (1 - qa.fail_prob) * (1 - qb.fail_prob)
        unwind = ZERO
        for leg, q in ((a, qa), (b, qb)):
            bid = leg.book.best_bid(leg.outcome) or ZERO
            exit_fee = estimate_fee(leg.market.fee, bid, size) if bid > 0 else ZERO
            unwind = max(unwind, (q.vwap - bid) * size + q.fee + exit_fee)
        leg_risk = unwind * Decimal(str(round(fail, 6)))

        conf_mult = Decimal(str(1 + max(100.0 - confidence, 0.0) / 2))
        settlement = capital * Decimal(str(cfg.settlement_risk_bps)) * BPS * conf_mult
        operational = capital * Decimal(str(cfg.operational_risk_bps)) * BPS
        capital_cost = capital * Decimal(str(cfg.capital_cost_apr_pct / 100 * days / 365))
        return Quote(size, qa, qb, size * ONE, acquisition, fees, conversion, slip, leg_risk,
                     settlement, operational, capital_cost, fail)

    # -- dynamic threshold --------------------------------------------------
    def required_roi(self, a: LegInput, b: LegInput, q: Quote, days: float,
                     now: float) -> tuple[float, float, dict[str, float]]:
        cfg = self.cfg
        stale = max(min(max(x.book.age(now), 0) * 1000 / cfg.max_book_age_ms, 1.0) for x in (a, b))
        depth_usd = min(float(q.a.depth_at_limit * q.a.limit_price), float(q.b.depth_at_limit * q.b.limit_price))
        ref = max(float(cfg.min_liquidity_usd) * 5, 1.0)
        thin = 1 - min(depth_usd / ref, 1.0)
        vol = min(max(a.vol_per_s, b.vol_per_s) / 0.01, 1.0)
        dur = min(days / 180.0, 1.0)
        failn = min(q.fail_prob / cfg.max_exec_fail_prob, 1.0)
        comp = {"fail": failn, "stale": stale, "thin": thin, "vol": vol, "duration": dur}
        score = 0.35 * failn + 0.20 * stale + 0.20 * thin + 0.15 * vol + 0.10 * dur
        req = (cfg.min_roi_pct + (cfg.max_roi_pct - cfg.min_roi_pct) * score) / 100
        if cfg.min_annualized_pct > 0:
            req = max(req, cfg.min_annualized_pct / 100 * days / 365)
        return req, score, comp

    # -- full evaluation ----------------------------------------------------
    def evaluate(self, a: LegInput, b: LegInput, *, confidence: float, settlement_compatible: bool,
                 max_size: Decimal | None = None, now: float | None = None,
                 require_confidence: bool = True) -> Evaluation:
        cfg = self.cfg
        now = now if now is not None else time.time()
        days = _days_to_settlement(a.market, b.market, datetime.fromtimestamp(now, timezone.utc))

        def reject(reason: str, q: Quote | None = None, req: float = 0.0, score: float = 0.0,
                   comp: dict | None = None) -> Evaluation:
            return Evaluation(False, reason, q, req, score, days, comp or {})

        if require_confidence:
            if not settlement_compatible:
                return reject("settlement rules not verified compatible")
            if confidence < cfg.min_match_confidence:
                return reject(f"match confidence {confidence:.1f} < {cfg.min_match_confidence:.0f}")
        for leg in (a, b):
            if leg.book.age(now) * 1000 > cfg.max_book_age_ms:
                return reject(f"stale order book on {leg.exchange.value} ({leg.book.age(now):.1f}s)")
        asks_a, asks_b = a.book.asks(a.outcome), b.book.asks(b.outcome)
        if not asks_a or not asks_b:
            return reject("no executable asks")
        if asks_a[0].price + asks_b[0].price >= ONE:
            return reject(f"no gross edge (asks sum to {asks_a[0].price + asks_b[0].price})")

        # Candidate sizes: every point where either ladder changes price while the
        # marginal pair is still worth more than it costs.
        step = max(a.market.size_step, b.market.size_step)
        min_size = max(a.market.min_order_size, b.market.min_order_size, step)
        cands: set[Decimal] = set()
        ia = ib = 0
        ra, rb = asks_a[0].size, asks_b[0].size
        cum = ZERO
        while ia < len(asks_a) and ib < len(asks_b):
            if asks_a[ia].price + asks_b[ib].price >= ONE:
                break
            take = min(ra, rb)
            cum += take
            cands.add(floor_to(cum, step))
            ra -= take
            rb -= take
            if ra <= 0:
                ia += 1
                ra = asks_a[ia].size if ia < len(asks_a) else ZERO
            if rb <= 0:
                ib += 1
                rb = asks_b[ib].size if ib < len(asks_b) else ZERO
        profitable_depth = cum
        cap = floor_to(profitable_depth * Decimal(str(cfg.max_book_take_pct / 100)), step)
        if max_size is not None:
            cap = min(cap, floor_to(max_size, step))
        cands = {min(c, cap) for c in cands} | {cap}
        cands = {c for c in cands if c >= min_size}
        if not cands:
            return reject("executable size below minimum order size")

        best: tuple[Quote, float, float, dict] | None = None
        fallback: tuple[Quote, float, float, dict] | None = None
        for size in sorted(cands):
            q = self.quote(a, b, size, confidence, days, now)
            req, score, comp = self.required_roi(a, b, q, days, now)
            if fallback is None or q.roi - req > fallback[0].roi - fallback[1]:
                fallback = (q, req, score, comp)
            if q.net > 0 and q.roi >= req and (best is None or q.net > best[0].net):
                best = (q, req, score, comp)

        q, req, score, comp = best or fallback  # type: ignore[misc]
        if best is None:
            if q.net <= 0:
                return reject(f"net profit {q.net:.4f} <= 0 after costs and risk buffer", q, req, score, comp)
            return reject(f"net return {q.roi * 100:.2f}% < required {req * 100:.2f}%", q, req, score, comp)

        slip_pct = float(q.slippage_buffer / q.acquisition) * 100 if q.acquisition > 0 else 0.0
        if slip_pct > cfg.max_slippage_pct:
            return reject(f"expected slippage {slip_pct:.2f}% > {cfg.max_slippage_pct}%", q, req, score, comp)
        if q.fail_prob > cfg.max_exec_fail_prob:
            return reject(f"execution failure probability {q.fail_prob:.0%} too high", q, req, score, comp)
        for lq in (q.a, q.b):
            if lq.depth_at_limit * lq.limit_price < cfg.min_liquidity_usd:
                return reject(f"liquidity on {lq.exchange.value} below ${cfg.min_liquidity_usd}", q, req, score, comp)
        if q.capital < cfg.min_trade_usd:
            return reject(f"trade size ${q.capital:.2f} below minimum ${cfg.min_trade_usd}", q, req, score, comp)
        if q.net < cfg.min_net_profit_usd:
            return reject(f"net profit ${q.net:.2f} below minimum ${cfg.min_net_profit_usd}", q, req, score, comp)
        return Evaluation(True, "ok", q, req, score, days, comp)
