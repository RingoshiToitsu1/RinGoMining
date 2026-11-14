"""Exchange-neutral domain types. All money and sizes are Decimal."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Any

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")


def D(x: Any) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if x is None or x == "":
        return ZERO
    return Decimal(str(x))


def floor_to(x: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return x
    return (x / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_to(x: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return x
    return (x / step).to_integral_value(rounding=ROUND_CEILING) * step


class Exchange(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class Outcome(StrEnum):
    YES = "yes"
    NO = "no"

    @property
    def opposite(self) -> "Outcome":
        return Outcome.NO if self is Outcome.YES else Outcome.YES


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TIF(StrEnum):
    FOK = "fok"  # fill entirely or cancel
    IOC = "ioc"  # fill what is available at the limit, cancel the rest


class OrderStatus(StrEnum):
    PENDING = "pending"      # journaled, not yet acknowledged by the exchange
    FILLED = "filled"
    PARTIAL = "partial"      # terminal: partly filled, remainder cancelled
    UNFILLED = "unfilled"    # terminal: nothing filled
    REJECTED = "rejected"
    UNKNOWN = "unknown"      # submission outcome could not be determined


class Resolution(StrEnum):
    YES = "yes"
    NO = "no"
    VOID = "void"


@dataclass(frozen=True, slots=True)
class Level:
    price: Decimal
    size: Decimal


@dataclass(slots=True)
class FeeSpec:
    """Taker fee model for one market.

    kind:
      kalshi_quadratic  fee = ceil_cent(rate * size * p * (1-p))
      poly_exponent     fee = size * rate * (p*(1-p)) ** exponent
      notional_bps      fee = size * p * rate   (rate is a fraction, e.g. 0.001)
      flat_per_contract fee = size * rate
      none              no fee
    """

    kind: str = "none"
    rate: Decimal = ZERO
    exponent: Decimal = ONE

    def to_dict(self) -> dict:
        return {"kind": self.kind, "rate": str(self.rate), "exponent": str(self.exponent)}

    @classmethod
    def from_dict(cls, d: dict | None) -> "FeeSpec":
        if not d:
            return cls()
        return cls(d.get("kind", "none"), D(d.get("rate", 0)), D(d.get("exponent", 1)))


@dataclass(slots=True)
class Market:
    exchange: Exchange
    market_id: str                 # kalshi ticker / polymarket slug or condition id
    title: str
    yes_label: str = "Yes"
    no_label: str = "No"
    subtitle: str = ""
    rules: str = ""                # full resolution criteria text
    resolution_source: str = ""
    category: str = ""
    event_id: str = ""
    close_time: datetime | None = None        # trading stops
    expiration_time: datetime | None = None   # latest expected settlement
    status: str = "open"
    can_close_early: bool = False
    tick_size: Decimal = CENT
    min_order_size: Decimal = ONE
    size_step: Decimal = ONE
    fee: FeeSpec = field(default_factory=FeeSpec)
    volume: Decimal = ZERO
    liquidity: Decimal = ZERO
    url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # exchange specific ids

    @property
    def key(self) -> str:
        return f"{self.exchange.value}:{self.market_id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["exchange"] = self.exchange.value
        d["fee"] = self.fee.to_dict()
        for k in ("close_time", "expiration_time"):
            d[k] = d[k].isoformat() if d[k] else None
        for k in ("tick_size", "min_order_size", "size_step", "volume", "liquidity"):
            d[k] = str(d[k])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Market":
        d = dict(d)
        d["exchange"] = Exchange(d["exchange"])
        d["fee"] = FeeSpec.from_dict(d.get("fee"))
        for k in ("close_time", "expiration_time"):
            d[k] = datetime.fromisoformat(d[k]) if d.get(k) else None
        for k in ("tick_size", "min_order_size", "size_step", "volume", "liquidity"):
            d[k] = D(d.get(k, 0))
        return cls(**d)


@dataclass(slots=True)
class OrderBook:
    """Executable quotes for both outcomes. asks ascending, bids descending."""

    exchange: Exchange
    market_id: str
    yes_bids: list[Level] = field(default_factory=list)
    yes_asks: list[Level] = field(default_factory=list)
    no_bids: list[Level] = field(default_factory=list)
    no_asks: list[Level] = field(default_factory=list)
    ts: float = field(default_factory=time.time)   # local receipt time
    source: str = "rest"

    def asks(self, outcome: Outcome) -> list[Level]:
        return self.yes_asks if outcome is Outcome.YES else self.no_asks

    def bids(self, outcome: Outcome) -> list[Level]:
        return self.yes_bids if outcome is Outcome.YES else self.no_bids

    def best_ask(self, outcome: Outcome) -> Decimal | None:
        a = self.asks(outcome)
        return a[0].price if a else None

    def best_bid(self, outcome: Outcome) -> Decimal | None:
        b = self.bids(outcome)
        return b[0].price if b else None

    def mid(self, outcome: Outcome = Outcome.YES) -> Decimal | None:
        a, b = self.best_ask(outcome), self.best_bid(outcome)
        if a is None or b is None:
            return None
        return (a + b) / 2

    def age(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.ts

    def normalize(self) -> "OrderBook":
        for name, rev in (("yes_bids", True), ("no_bids", True), ("yes_asks", False), ("no_asks", False)):
            lv = [x for x in getattr(self, name) if x.size > 0 and ZERO < x.price < ONE]
            lv.sort(key=lambda x: x.price, reverse=rev)
            setattr(self, name, lv)
        return self

    @staticmethod
    def from_single_book(
        exchange: Exchange, market_id: str, yes_bids: list[Level], yes_asks: list[Level], **kw: Any
    ) -> "OrderBook":
        """Venues with one YES book: buying NO at p is selling YES at 1-p."""
        return OrderBook(
            exchange,
            market_id,
            yes_bids=list(yes_bids),
            yes_asks=list(yes_asks),
            no_bids=[Level(ONE - x.price, x.size) for x in yes_asks],
            no_asks=[Level(ONE - x.price, x.size) for x in yes_bids],
            **kw,
        ).normalize()

    def to_dict(self) -> dict:
        def lv(xs: list[Level]) -> list[list[str]]:
            return [[str(x.price), str(x.size)] for x in xs]

        return {
            "exchange": self.exchange.value, "market_id": self.market_id, "ts": self.ts,
            "source": self.source, "yes_bids": lv(self.yes_bids), "yes_asks": lv(self.yes_asks),
            "no_bids": lv(self.no_bids), "no_asks": lv(self.no_asks),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OrderBook":
        def lv(xs: list) -> list[Level]:
            return [Level(D(p), D(s)) for p, s in xs]

        return cls(
            Exchange(d["exchange"]), d["market_id"], lv(d["yes_bids"]), lv(d["yes_asks"]),
            lv(d["no_bids"]), lv(d["no_asks"]), ts=float(d["ts"]), source=d.get("source", "rest"),
        )


@dataclass(slots=True)
class OrderRequest:
    exchange: Exchange
    market: Market
    outcome: Outcome
    side: Side
    limit_price: Decimal
    size: Decimal
    tif: TIF
    client_order_id: str


@dataclass(slots=True)
class OrderResult:
    status: OrderStatus
    order_id: str = ""
    filled_size: Decimal = ZERO
    avg_price: Decimal = ZERO      # per contract, for the outcome traded
    fee: Decimal = ZERO
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def notional(self) -> Decimal:
        return self.filled_size * self.avg_price


@dataclass(slots=True)
class Balance:
    available: Decimal
    total: Decimal


@dataclass(slots=True)
class EligibilityCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class EligibilityReport:
    exchange: Exchange
    checks: list[EligibilityCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(EligibilityCheck(name, ok, detail))


@dataclass(slots=True)
class Mapping:
    """A verified (or candidate) equivalence between one Kalshi and one Polymarket market."""

    id: int
    kalshi: Market
    polymarket: Market
    inverted: bool            # True: Kalshi YES pays when Polymarket NO pays
    confidence: float         # 0-100
    settlement_compatible: bool
    status: str               # candidate | verified | rejected | stale
    method: str = "heuristic"
    checks: dict[str, Any] = field(default_factory=dict)

    def poly_outcome_for(self, kalshi_outcome: Outcome) -> Outcome:
        """Polymarket outcome that pays in exactly the same world as kalshi_outcome."""
        return kalshi_outcome.opposite if self.inverted else kalshi_outcome
